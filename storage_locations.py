"""Phase 2C: write data_json/<build>/storage_locations.json and storage_report.md - places with an inventory.

Listed place = catalog place whose parent is a star, planet or moon; places under it are sub-places counted for it.
Inventory = an item bank (found in its containers) or freight (DataCore amenity or a freight console in its
containers, hangars included). ExtractOnly kiosks (withdraw only) do not count; unknown places are left out.
Containers come from Data.p4k through the containers.py whitelist, after a dry-run.
Usage: python storage_locations.py --dry-run     show what would be extracted, write nothing
       python storage_locations.py               extract the whitelist, then write the JSON and the report
"""

import argparse
import collections
import json
import sys
import zipfile
from pathlib import Path

import explore
import containers as storage
from decode import EXTRACTION_ROOT
from extract import ExtractionError

UNKNOWN = "unknown"
HANGAR_DIR = "instancedinterior/hangar/"
GATEWAY_MEMBER = "instancedinteriorgateway.xml"
KINDS = ("itemInventory", "freight")
BODIES = ("SolarSystem", "Star", "Planet", "Moon")  # never listed: they frame the path (Stanton > ArcCorp > Area18)
RULE = ("listed place = catalog place whose parent is a star, planet or moon; sub-places count for it; "
        "inventory = itemInventory (item bank) or freight, sub-places and hangars included")
MAX_XML_NODES = 5_000_000
EXTRACT_ONLY = "_ExtractOnly"  # withdraw-only freight kiosk: not a storage (user rule)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", help="build folder in data_json/; default: latest exported build")
    parser.add_argument("--game-dir", type=Path, default=Path(r"D:\Roberts Space Industries\StarCitizen"))
    parser.add_argument("--channel", default="LIVE")
    parser.add_argument("--dry-run", action="store_true", help="list containers and sizes; extract nothing")
    return parser.parse_args(argv)


def load_hangars(records_root):
    """Hangar class GUID -> its container (lower-cased p4k path), from instancedinterior/hangar/*."""
    hangars = {}
    for relpath, record in storage.load_records(records_root, "SObjectContainerComponentParams"):
        if not relpath.startswith(HANGAR_DIR):
            continue
        for component in record["_RecordValue_"].get("Components") or []:
            if isinstance(component, dict) and component.get("objectContainer"):
                hangars[record["_RecordId_"]] = "data/" + component["objectContainer"].lower()
    return hangars


def gateway_hangars(socpak, hangars):
    """Hangar containers named by the container's instanced-interior gateway (CryXmlB, searched as bytes)."""
    found = set()
    with zipfile.ZipFile(socpak) as archive:
        for info in archive.infolist():
            if info.filename.endswith(GATEWAY_MEMBER):
                data = storage.read_member(archive, info)
                found |= {path for guid, path in hangars.items() if guid.encode() in data}
    return found


def owned_children(element, catalog_guids):
    """External .socpak under `element`, stopping at any <Child> that is another catalog place (it gets its own entry)."""
    names, pending = set(), list(element)
    for _ in range(MAX_XML_NODES):
        if not pending:
            return names
        node = pending.pop()
        if node.tag == "Child":
            if node.get("starMapRecord") in catalog_guids:
                continue
            if (node.get("name") or "").endswith(".socpak"):
                names.add(node.get("name").lower())
        pending.extend(node)
    raise ExtractionError(f"XML de conteneur de plus de {MAX_XML_NODES:,} noeuds")


def place_links(root, catalog_guids):
    """Catalog GUID -> socpaks that place owns, for every <Child starMapRecord> in this container XML."""
    links = {}
    for child in root.iter("Child"):
        guid = child.get("starMapRecord")
        if guid in catalog_guids and guid not in links:
            own = {child.get("name").lower()} if (child.get("name") or "").endswith(".socpak") else set()
            links[guid] = own | owned_children(child, catalog_guids)
    return links


def has_editor_xml(socpak):
    with zipfile.ZipFile(socpak) as archive:
        return any("/" not in name and name.endswith("_editor.xml") for name in archive.namelist())


def read_graph(store, guid_by_class, hangars, catalog_guids):
    """Every whitelisted container -> (child containers, console hits, gateway hangars, catalog places it links).

    Also returns the containers without an _editor.xml: they place no entity, so they hold no console (reported).
    """
    graph, no_editor = {}, []
    for count, lower in enumerate(sorted(store.by_lower), 1):
        socpak = store.local(lower)
        root = storage.container_root(socpak)
        links = place_links(root, catalog_guids)
        if has_editor_xml(socpak):
            hits = storage.console_hits(socpak, guid_by_class)
        else:
            hits = collections.Counter()
            no_editor.append(store.by_lower[lower])
        graph[lower] = (owned_children(root, catalog_guids), hits, gateway_hangars(socpak, hangars), links)
        if count % 1000 == 0:
            print(f"  {count:,} / {len(store.by_lower):,} conteneurs lus")
    return graph, no_editor


def index_all(graph):
    """Catalog GUID -> (container holding its <Child starMapRecord>, child socpaks); system layer wins, then path order."""
    ordered = sorted(graph, key=lambda lower: (not storage.ROOT_CONTAINER.match(lower), lower))
    found = {}
    for lower in ordered:
        found |= {guid: (lower, names) for guid, names in graph[lower][3].items() if guid not in found}
    return found


def closure(graph, start):
    """Containers reachable from `start` through children and gateway hangars; returns (reached, reached as hangar)."""
    reached, via_hangar = set(), set()
    pending = [(lower, False) for lower in start]
    for _ in range(len(start) + 2 * len(graph) ** 2 + 1):  # each container is expanded once; pushes are bounded
        if not pending:
            return reached, via_hangar
        lower, is_hangar = pending.pop()
        if lower not in graph or lower in reached:
            continue
        reached.add(lower)
        if is_hangar:
            via_hangar.add(lower)
        children, _, hangar_paths, _ = graph[lower]
        pending += [(child, is_hangar) for child in children] + [(path, True) for path in hangar_paths]
    raise ExtractionError("Parcours des conteneurs non borne (graphe incoherent)")


def container_proofs(store, graph, consoles, reached, via_hangar):
    """kind -> proofs {file, entity, count[, viaHangar]} for every console class in the reached containers."""
    proofs = {kind: [] for kind in KINDS}
    for lower in sorted(reached):
        per_class = collections.Counter()
        for (guid, _), count in graph[lower][1].items():
            per_class[guid] += count
        for guid, count in sorted(per_class.items(), key=lambda item: consoles[item[0]][1]):
            kind, name, _ = consoles[guid]
            proof = {"file": store.by_lower[lower], "entity": name, "count": count}
            proofs[kind].append(proof | ({"viaHangar": True} if lower in via_hangar else {}))
    return proofs


def decide(place, linked, proofs):
    """true with proof / false (containers read, nothing found) / unknown (no container linked, no DataCore proof)."""
    amenities = [{"record": place["record"], "amenity": amenity}
                 for amenity in place["amenities"] if amenity in storage.FREIGHT_AMENITIES]
    evidence = {"itemInventory": proofs["itemInventory"], "freight": amenities + proofs["freight"]}
    values = {kind: True if evidence[kind] else (False if linked else UNKNOWN) for kind in KINDS}
    return values, evidence


def lineage(row, by_guid):
    """Catalog rows from the top ancestor down to `row`."""
    rows = []
    for _ in range(storage.MAX_PARENT_DEPTH):
        rows.append(row)
        row = by_guid.get(row["parent"])
        if row is None:
            return list(reversed(rows))
    raise ExtractionError(f"Chaine de parents trop longue depuis {rows[0]['name']}")


def build_entries(ctx):
    """Values and proofs for every catalog place, plus every container some place reaches."""
    entries, claimed = {}, set()
    for row in ctx["catalog"]["locations"]:
        linked = ctx["located"].get(row["guid"])
        reached, via_hangar = closure(ctx["graph"], linked[1]) if linked else (set(), set())
        claimed |= reached
        proofs = container_proofs(ctx["store"], ctx["graph"], ctx["consoles"], reached, via_hangar)
        values, evidence = decide(ctx["places"][row["guid"]], linked is not None, proofs)
        entries[row["guid"]] = values | {"proof": evidence}
    return entries, claimed


def listed_place(row, by_guid):
    """The place a row counts for: itself if its parent is a body, else its nearest ancestor that is."""
    for _ in range(storage.MAX_PARENT_DEPTH):
        parent = by_guid.get(row["parent"])
        if parent is None or parent["type"] in BODIES:
            return row
        row = parent
    raise ExtractionError(f"Chaine de parents trop longue depuis {row['name']}")


def merged_value(values):
    if True in values:
        return True
    return UNKNOWN if UNKNOWN in values else False


def frame(row, by_guid):
    """System / planet / moon names framing a listed place, from its catalog ancestors."""
    rows = lineage(row, by_guid)
    first = {kind: next((item["name"] for item in rows[:-1] if item["type"] == kind), None) for kind in BODIES}
    return {"system": first["Star"] or first["SolarSystem"], "planet": first["Planet"], "moon": first["Moon"],
            "path": " > ".join(item["name"] for item in rows)}


def roll_up(entries, catalog):
    """One entry per listed place; sub-places' values and proofs merged into it (proofs tagged subLocation)."""
    by_guid = {row["guid"]: row for row in catalog["locations"]}
    groups = collections.defaultdict(list)
    for row in catalog["locations"]:
        if row["type"] not in BODIES:
            groups[listed_place(row, by_guid)["guid"]].append(row)
    places = []
    for guid, members in groups.items():
        row = by_guid[guid]
        values = {kind: merged_value([entries[member["guid"]][kind] for member in members]) for kind in KINDS}
        proof = {kind: [item | ({"subLocation": member["name"]} if member["guid"] != guid else {})
                        for member in members for item in entries[member["guid"]]["proof"][kind]] for kind in KINDS}
        both = values["itemInventory"] is True and values["freight"] is True
        storage_kind = "both" if both else next((kind for kind in KINDS if values[kind] is True), None)
        places.append({"guid": guid, "name": row["name"], "type": row["type"], **frame(row, by_guid),
                       "storage": storage_kind, **values,
                       "subLocations": sorted(member["name"] for member in members if member["guid"] != guid),
                       "proof": proof})
    return sorted(places, key=sort_key)


def sort_key(place):
    return tuple(place[key] or "" for key in ("system", "planet", "moon", "name")) + (place["guid"],)


def orphan_consoles(ctx, claimed):
    """(container, Counter by kind) for containers holding consoles that no catalog place reaches."""
    rows = []
    for lower, (_, hits, _, _) in sorted(ctx["graph"].items()):
        if lower in claimed or not hits:
            continue
        kinds = collections.Counter()
        for (guid, _), count in hits.items():
            kinds[ctx["consoles"][guid][0]] += count
        rows.append((ctx["store"].by_lower[lower], kinds))
    return rows


def stores(entry):
    return entry["itemInventory"] is True or entry["freight"] is True


def is_unknown(entry):
    return not stores(entry) and UNKNOWN in (entry["itemInventory"], entry["freight"])


def breakdown_rows(entries, key, label):
    counts = collections.defaultdict(collections.Counter)
    for entry in entries:
        group = counts[entry[key] or "-"]
        group["total"] += 1
        group["stores"] += stores(entry)
        group["items"] += entry["itemInventory"] is True
        group["freight"] += entry["freight"] is True
        group["unknown"] += is_unknown(entry)
    rows = [f"| {label} | Lieux | Avec stockage | Item bank | Fret | unknown |", "|---|---|---|---|---|---|"]
    for name, group in sorted(counts.items(), key=lambda item: (-item[1]["stores"], item[0])):
        rows.append(f"| {name} | {group['total']} | {group['stores']} | {group['items']} | {group['freight']} | {group['unknown']} |")
    return rows


def check_rows(places, catalog):
    listed = {place["guid"] for place in places}
    bodies = sum(row["type"] in BODIES for row in catalog["locations"])
    covered = sum(1 + len(place["subLocations"]) for place in places)
    unproven = [place["name"] for place in places for kind in KINDS if place[kind] is True and not place["proof"][kind]]
    checks = [
        ("Chaque lieu du catalogue est un astre, un lieu liste ou un sous-lieu d'un seul lieu liste",
         bodies + covered == len(catalog["locations"]) and len(listed) == len(places),
         f"{bodies} astres + {len(places)} lieux listes + {covered - len(places)} sous-lieux = {bodies + covered} "
         f"/ {len(catalog['locations'])}"),
        ("Chaque `true` a une preuve", not unproven, f"sans preuve: {unproven[:5] or 'aucun'}"),
        ("Valeurs dans true/false/unknown", all(place[kind] in (True, False, UNKNOWN) for place in places for kind in KINDS), ""),
    ]
    return ["| Controle | Resultat | Detail |", "|---|---|---|"] + [
        f"| {name} | {'OK' if ok else 'ECHEC'} | {detail} |" for name, ok, detail in checks]


def render_report(places, orphans, catalog, no_editor):
    unknown = [place for place in places if is_unknown(place)]
    storing = [place for place in places if stores(place)]
    kinds = collections.Counter(place["storage"] for place in storing)
    lines = [
        f"# Lieux avec inventaire - {catalog['header']['build']}",
        "",
        "Regle: un lieu est liste si son parent est un astre (etoile, planete, lune); un lieu sous un autre lieu "
        "(clinique, hopital, zone) est un sous-lieu compte pour son lieu liste. Inventaire = item bank (conteneurs) "
        "ou fret (amenity DataCore ou console dans les conteneurs), sous-lieux et hangars compris. "
        "`false` = conteneurs lus sans console; `unknown` = aucun conteneur relie et aucune amenity fret.",
        "",
        "## Controles",
        "",
        *check_rows(places, catalog),
        "",
        f"Lieux avec inventaire (dans `storage_locations.json`): **{len(storing)}** / {len(places)} lieux listes "
        f"(item bank + fret: {kinds['both']}, item bank seul: {kinds['itemInventory']}, fret seul: {kinds['freight']}; "
        f"unknown: {len(unknown)}).",
        "",
        f"Conteneurs sans `_editor.xml` (aucune entite placee, comptes sans console): "
        f"{', '.join(f'`{path}`' for path in no_editor) or 'aucun'}.",
        "",
        "## Par type de lieu",
        "",
        *breakdown_rows(places, "type", "Type"),
        "",
        "## Par systeme",
        "",
        *breakdown_rows(places, "system", "Systeme"),
        "",
        f"## Consoles trouvees sans lieu correspondant ({len(orphans)} conteneurs)",
        "",
        "| Conteneur | Item banks | Consoles/plateformes fret |",
        "|---|---|---|",
        *[f"| `{path}` | {kinds['itemInventory']} | {kinds['freight']} |" for path, kinds in orphans],
        "",
        f"## Lieux unknown ({len(unknown)})",
        "",
        "| Type | Chemin |",
        "|---|---|",
        *[f"| {place['type']} | {place['path']} |" for place in sorted(unknown, key=lambda place: (place["type"], place["path"]))],
    ]
    return "\n".join(lines) + "\n"


def main(argv):
    args = parse_args(argv)
    out_dir = explore.find_out_dir(args.build)
    records_root = out_dir / explore.RECORDS_SUBDIR
    raw_manifest = json.loads((out_dir / "raw_manifest.json").read_text(encoding="utf-8"))
    texts = explore.load_localization(EXTRACTION_ROOT / raw_manifest["channel"] / out_dir.name / explore.LOCALIZATION)
    catalog = json.loads((out_dir / "catalog.json").read_text(encoding="utf-8"))
    catalog_guids = {row["guid"] for row in catalog["locations"]}
    store = storage.open_store(args.game_dir, args.channel, out_dir.name)
    missing = [lower for lower in store.by_lower if not store.present(lower)]
    print(f"Liste blanche `{storage.WHITELIST}`: {len(store.by_lower):,} conteneurs, {storage.mb(store.size(store.by_lower))}")
    print(f"Deja extraits: {len(store.by_lower) - len(missing):,}; a extraire: {len(missing):,}, {storage.mb(store.size(missing))}")
    print(f"Sortie: {store.dest}")
    if args.dry_run:
        print("Dry-run: rien n'a ete extrait.")
        return 0

    store.extract(missing)
    consoles = {guid: console for guid, console in storage.load_consoles(records_root).items()
                if not console[1].endswith(EXTRACT_ONLY)}
    guid_by_class = {name.lower(): guid for guid, (_, name, _) in consoles.items()}
    graph, no_editor = read_graph(store, guid_by_class, load_hangars(records_root), catalog_guids)
    print(f"Conteneurs sans _editor.xml (aucune entite placee): {no_editor or 'aucun'}")
    ctx = {"catalog": catalog, "store": store, "graph": graph, "consoles": consoles,
           "places": storage.load_places(records_root, texts, catalog_guids), "located": index_all(graph)}
    entries, claimed = build_entries(ctx)
    places = roll_up(entries, catalog)
    storing = [place for place in places if stores(place)]
    payload = {"header": catalog["header"], "rule": RULE, "locations": storing}
    (out_dir / "storage_locations.json").write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    report = render_report(places, orphan_consoles(ctx, claimed), catalog, no_editor)
    (out_dir / "storage_report.md").write_text(report, encoding="utf-8")
    print(f"OK {len(storing)} lieux avec inventaire / {len(places)} listes -> {out_dir / 'storage_locations.json'}; "
          f"rapport: {out_dir / 'storage_report.md'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ExtractionError as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        sys.exit(1)
