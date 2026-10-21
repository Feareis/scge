"""Bonus A: find where the game data says a location has an item bank or a freight elevator.

Sources: DataCore amenities (data_json/<build>/raw/) and object containers (.socpak) read from Data.p4k.
Writes data_json/<build>/bonus/exploration_stockage.md; containers are cached by containers.py.
Usage: python explore_storage.py --dry-run          show what would be read from Data.p4k, write nothing
       python explore_storage.py --roots-only       extract the system-layer containers only
       python explore_storage.py                    system layer + the sample locations' containers + report
"""

import argparse
import collections
import json
import sys
from pathlib import Path

import explore
from containers import (
    CACHE_SUBDIR,
    FREIGHT_AMENITIES,
    MAX_PARENT_DEPTH,
    ROOT_CONTAINER,
    WHITELIST,
    WHITELIST_PREFIX,
    console_hits,
    container_root,
    load_consoles,
    load_places,
    mb,
    open_store,
)
from decode import EXTRACTION_ROOT
from extract import ExtractionError

MAX_CONTAINER_DEPTH = 30
# StarMapObject GUIDs. Sample = containers actually read; the table adds five places judged on the DataCore only.
SAMPLE = (
    "c6535844-c615-494e-9dc1-c2d0c6e7190a",  # Area18 (city)
    "164ca676-83eb-448d-ab0b-1ddde8a9c0bd",  # Baijini Point (orbital station, missing from catalog.json)
    "59485d8c-7947-4e00-871c-d35f6451aab8",  # ARC-L1 Wide Forest Station (rest stop)
    "dc34b5af-e417-48d2-b47b-98b56eedf0a7",  # Shubin Mining Facility SCD-1 (outpost)
    "255ed755-f835-4522-8679-9a8b0501ba03",  # Kudre Ore (outpost)
)
TABLE_EXTRA = (
    "f1759f46-e3c0-4dc1-94bc-c94488b58913",  # Lorville
    "ab29f65e-c792-4b1f-b23d-5810cb0ef416",  # Everus Harbor
    "07463f54-4ecd-4323-8a28-fb65b8a4ebca",  # Checkmate (Pyro)
    "c1405795-f6f7-4c5a-bec5-8cba4595712c",  # Jumptown
    "c7e21380-93e0-4ada-ac91-9684bd94d313",  # Nuen Waste Management
)
# Hangars are instanced interiors, outside the location tree; one medium hangar each for Area18 and the ARC-L1 rest stop
# (paths from raw/instancedinterior/hangar/hangar_mediumfront_area18.json and hangar_mediumfront_reststop.json).
HANGAR_PROBES = (
    "data/objectcontainers/pu/loc/mod/common/hangar/util_a/hangar_medfrnt_001_a18.socpak",
    "data/objectcontainers/pu/loc/mod/common/hangar/util_a/hangar_medfrnt_001_rest_occu.socpak",
)
LIMITS = (
    "- **Lieux non relies**: un lieu sans `<Child starMapRecord>` dans la couche systeme n'a pas de conteneur connu "
    "(tableau ci-dessous): ce sera `unknown`, sauf preuve DataCore pour le fret.",
    "- **DataCore et conteneurs ne disent pas la meme chose**: l'amenity fret d'une ville ou d'une aire de repos "
    "designe le monte-charge du hangar, absent de l'arbre du lieu (Area18 et ARC-L1: 0 console fret dans l'arbre; "
    "1 dans le hangar d'Area18 et 1 dans le hangar generique des aires de repos `*_rest_occu`). Le lien "
    "hangar -> lieu n'existe que par le nom des classes `Hangar_*_<lieu>`, parfois generique (`*_reststop`).",
    "- **Consoles peut-etre desactivees**: `_editor.xml` n'a aucun drapeau d'activation par entite; seuls les "
    "calques (4.3) et les variantes de classe (`Itembank_TEMPLATE`, `*_ExtractOnly` = retrait seul) donnent un "
    "indice. Un calque d'evenement ou de mission peut n'etre charge que sous condition: non verifiable hors du jeu.",
    "- **Comptes = presence, pas instances**: un conteneur reutilise plusieurs fois dans un lieu (module d'hab, "
    "boutique) n'est lu et compte qu'une fois. Les sous-lieux (ex. `Area18 Central` dans Area18) partagent les "
    "consoles de leur parent.",
    "- **Hors liste blanche**: les sous-conteneurs `ObjectContainers/Setup/**` (ascenseurs, placards de spawn) ne "
    "sont pas lus; les plateformes `*_FreightElevator_CargoGrid*` y sont peut-etre (non verifie).",
)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", help="build folder in data_json/; default: latest exported build")
    parser.add_argument("--game-dir", type=Path, default=Path(r"D:\Roberts Space Industries\StarCitizen"))
    parser.add_argument("--channel", default="LIVE")
    parser.add_argument("--dry-run", action="store_true", help="list containers and sizes; extract nothing")
    parser.add_argument("--roots-only", action="store_true", help="stop after the system-layer containers")
    return parser.parse_args(argv)


def child_names(element):
    """Every external .socpak referenced by this element and its nested children, lower-cased."""
    return {child.get("name").lower() for child in element.iter("Child") if (child.get("name") or "").endswith(".socpak")}


def index_locations(trees, place_guids):
    """StarMapObject GUID -> (system-layer container, child socpaks), from <Child starMapRecord=...>."""
    found = {}
    for lower, root in sorted(trees.items()):
        for child in root.iter("Child"):
            guid = child.get("starMapRecord")
            if guid in place_guids and guid not in found:
                found[guid] = (lower, child_names(child))
    return found


def walk_containers(store, start, guid_by_class):
    """Breadth-first over child containers from `start`; returns (containers read, hits, names not readable)."""
    seen, hits, absent = set(), collections.Counter(), set()
    frontier = set(start)
    for _ in range(MAX_CONTAINER_DEPTH):
        absent |= {lower for lower in frontier if lower not in store.by_lower}
        frontier = {lower for lower in frontier if lower in store.by_lower} - seen
        if not frontier:
            return seen, hits, absent
        store.extract(frontier)
        seen |= frontier
        following = set()
        for lower in frontier:
            hits += console_hits(store.local(lower), guid_by_class)
            following |= child_names(container_root(store.local(lower)))
        frontier = following
    raise ExtractionError(f"Conteneurs imbriques sur plus de {MAX_CONTAINER_DEPTH} niveaux (cycle ?)")


def location_path(guid, places):
    names = []
    for _ in range(MAX_PARENT_DEPTH):
        place = places.get(guid)
        if place is None:
            return " > ".join(reversed(names))
        names.append(place["name"])
        guid = place["parent"]
    raise ExtractionError(f"Chaine de parents trop longue depuis {names[0]}")


def hits_by_kind(hits, consoles):
    kinds = collections.Counter()
    for (guid, _), count in hits.items():
        kinds[consoles[guid][0]] += count
    return kinds


def table_rows(ctx):
    places, located, results, consoles = ctx["places"], ctx["located"], ctx["results"], ctx["consoles"]
    rows = ["| Lieu | Type | Chemin | Dans catalog.json | Amenity fret (DataCore) | Relie a un conteneur "
            "| Conteneurs lus | Item banks | Consoles/plateformes fret |", "|---|---|---|---|---|---|---|---|---|"]
    for guid in SAMPLE + TABLE_EXTRA:
        place = places[guid]
        freight = ", ".join(a.removeprefix("Commodity Trading - ") for a in place["amenities"] if a in FREIGHT_AMENITIES)
        linked = Path(located[guid][0]).name if guid in located else "non"
        if guid in results:
            seen, hits, _ = results[guid]
            kinds = hits_by_kind(hits, consoles)
            read, items, cargo = str(len(seen)), str(kinds["itemInventory"]), str(kinds["freight"])
        else:
            read = items = cargo = "non lu"
        rows.append(f"| {place['name']} | {place['type']} | {location_path(guid, places)} | "
                    f"{'oui' if place['in_catalog'] else 'NON'} | {freight or '-'} | {linked} | {read} | {items} | {cargo} |")
    return rows


def console_rows(ctx):
    rows = ["| Type | Classe d'entite | GUID | Enregistrement | Vue dans l'echantillon |", "|---|---|---|---|---|"]
    seen = collections.Counter()
    for _, hits, _ in ctx["results"].values():
        for (guid, _), count in hits.items():
            seen[guid] += count
    for guid, (kind, name, relpath) in sorted(ctx["consoles"].items(), key=lambda item: (item[1][0], item[1][1])):
        rows.append(f"| {kind} | {name} | `{guid}` | `{relpath}` | {seen[guid]} |")
    return rows


def coverage_rows(ctx):
    """Catalog places per type: how many are linked to a container, how many carry a freight amenity."""
    rows = ["| Type | Lieux du catalogue | Relies a un conteneur | Avec amenity fret |", "|---|---|---|---|"]
    counts = collections.defaultdict(collections.Counter)
    for guid, place in ctx["places"].items():
        if place["in_catalog"]:
            counts[place["type"]]["total"] += 1
            counts[place["type"]]["linked"] += guid in ctx["located"]
            counts[place["type"]]["freight"] += any(a in FREIGHT_AMENITIES for a in place["amenities"])
    for kind, count in sorted(counts.items(), key=lambda item: -item[1]["total"]):
        rows.append(f"| {kind} | {count['total']} | {count['linked']} | {count['freight']} |")
    return rows


def lost_rows(ctx):
    """Named places with amenities that phase 2 dropped (type label lost), i.e. absent from catalog.json."""
    lost = [place for place in ctx["places"].values()
            if not place["in_catalog"] and place["named"] and place["amenities"]]
    rows = [f"{len(lost)} lieux nommes avec des amenities sont absents de `catalog.json`, ex.:", ""]
    for place in sorted(lost, key=lambda place: place["name"])[:15]:
        freight = any(a in FREIGHT_AMENITIES for a in place["amenities"])
        rows.append(f"- {place['name']} (type `{place['type']}`, fret: {'oui' if freight else 'non'}, `{place['record']}`)")
    return rows


def unread_lines(ctx):
    """Containers referenced by the sample but not read: outside the whitelist, or named but missing from the p4k."""
    unread = set().union(*(result[2] for result in [*ctx["results"].values(), *ctx["hangars"].values()]))
    outside = sorted(name for name in unread if not name.startswith(WHITELIST_PREFIX))
    missing = sorted(unread - set(outside))
    return [f"- References hors liste blanche (non lues): {len(outside)}" + (f", ex. `{outside[0]}`." if outside else "."),
            f"- References dans `PU/` mais absentes du p4k: {len(missing)}" + (f", ex. `{missing[0]}`." if missing else ".")]


def layer_rows(ctx):
    """Editor layers holding the consoles found: the only on/off switch visible in the files."""
    layers = collections.Counter()
    for _, hits, _ in [*ctx["results"].values(), *ctx["hangars"].values()]:
        for (guid, layer), count in hits.items():
            layers[ctx["consoles"][guid][0], layer] += count
    rows = ["| Type | Calque | Consoles |", "|---|---|---|"]
    rows += [f"| {kind} | `{layer}` | {count} |" for (kind, layer), count in layers.most_common(15)]
    return rows


def hangar_rows(ctx):
    rows = ["| Hangar | Conteneurs lus | Item banks | Consoles/plateformes fret |", "|---|---|---|---|"]
    for name, (seen, hits, _) in ctx["hangars"].items():
        kinds = hits_by_kind(hits, ctx["consoles"])
        rows.append(f"| `{Path(name).name}` | {len(seen)} | {kinds['itemInventory']} | {kinds['freight']} |")
    return rows


def report_head(ctx):
    places, store = ctx["places"], ctx["store"]
    catalog = [place for place in places.values() if place["in_catalog"]]
    with_freight = [place for place in catalog if any(a in FREIGHT_AMENITIES for a in place["amenities"])]
    extracted = list((store.dest / CACHE_SUBDIR).rglob("*.socpak"))
    return [
        f"# Exploration stockage - {ctx['out_dir'].name}",
        "",
        "## 1. Source retenue",
        "",
        "- **Fret (SCU)**: les deux sources. Le DataCore le declare par lieu (`StarMapObject.amenities` -> "
        "`Commodity Trading - Freight Elevator` / `Loading Dock`, table `starmapamenitytypes`); les conteneurs "
        "montrent les consoles placees (`SoftLock_Terminal_*_FreightElevatorKiosk_*`, `*_FreightElevator_CargoGrid*`).",
        "- **Inventaire d'items**: conteneurs seulement. Aucune amenity ni aucun autre enregistrement du DataCore ne "
        "relie un item bank a un lieu: les classes `Itembank_*` (`SFreightElevatorUIProviderParams.isItemBank = true`) "
        "ne sont referencees nulle part ailleurs dans `raw/`.",
        "- **Lien lieu -> conteneur**: attribut `starMapRecord` (= GUID du `StarMapObject`, celui de `catalog.json`) "
        "sur les `<Child>` du XML des conteneurs de la couche systeme (`PU/system/**`). Le contenu du lieu = le "
        "`.socpak` de ce `<Child>` + ses enfants imbriques, recursivement.",
        "- **Hangars**: interieurs instancies, hors de l'arbre du lieu (voir 4.2). Le DataCore les decrit "
        "(`instancedinterior/hangar/*`, `SObjectContainerComponentParams.objectContainer`) mais ne les relie au lieu "
        "que par le nom (`*_area18`, `*_reststop`, `*_lorville`...).",
        "",
        f"Lieux de `catalog.json` avec une amenity fret: {len(with_freight)} / {len(catalog)}.",
        "",
        "## 2. Dry-run et extraction",
        "",
        *[f"- {line}" for line in ctx["plan"]],
        f"- Conteneurs en cache dans `{(store.dest / CACHE_SUBDIR).relative_to(EXTRACTION_ROOT.parent).as_posix()}`: "
        f"{len(extracted)} conteneurs, "
        f"{mb(sum(path.stat().st_size for path in extracted))} (couche systeme + {len(SAMPLE)} lieux + "
        f"{len(HANGAR_PROBES)} hangars).",
        *unread_lines(ctx),
        "",
        "## 3. Enregistrements et entites qui identifient chaque console",
        "",
        "Consoles = classes d'entite du DataCore (`raw/`). Dans un conteneur, chaque entite placee est un "
        "`<Object name=... type=CLASSE guid=...>` du fichier `<conteneur>_editor.xml` (XML en clair, range par "
        "`<Layer>`); `type` est compare au nom de classe. Les `entdata/*.entxml` ne couvrent pas ces entites.",
        "",
        *console_rows(ctx),
        "",
    ]


def report_tail(ctx):
    return [
        "## 4. Dix lieux connus",
        "",
        f"### 4.1 Lieux ({len(SAMPLE)} lus dans les conteneurs, les autres juges sur le DataCore seul)",
        "",
        *table_rows(ctx),
        "",
        "### 4.2 Hangars sondes",
        "",
        *hangar_rows(ctx),
        "",
        "### 4.3 Calques des consoles trouvees",
        "",
        *layer_rows(ctx),
        "",
        "## 5. Limites",
        "",
        *LIMITS,
        "",
        "### Lieux du catalogue relies a un conteneur, par type",
        "",
        *coverage_rows(ctx),
        "",
        "### Lieux perdus par la phase 2",
        "",
        "`explore.py` (`Scan.visit`) ecrase la definition d'un type par chaque *reference* a ce type rencontree plus "
        "loin (une reference n'a pas de `name`): `starmap/starmarine/oxygendispenser.json` efface ainsi `Manmade`. "
        "Ces lieux ont un type `None` et sont ecartes de `catalog.json`.",
        "",
        *lost_rows(ctx),
        "",
    ]


def render_report(ctx):
    return "\n".join(report_head(ctx) + report_tail(ctx)) + "\n"


def plan_lines(store, roots, located, places):
    """Dry-run summary; also copied into the report."""
    lines = [f"Liste blanche `{WHITELIST}`: {len(store.sizes):,} conteneurs, {mb(store.size(store.by_lower))}.",
             f"Couche systeme (`PU/system/**/*.socpak`): {len(roots)} conteneurs, {mb(store.size(roots))}."]
    if located is None:
        return lines + ["Lieux -> conteneurs: inconnu tant que la couche systeme n'est pas extraite (--roots-only)."]
    catalog = {guid for guid, place in places.items() if place["in_catalog"]}
    direct = set().union(*(names for guid, (_, names) in located.items() if guid in catalog)) & set(store.by_lower)
    lines.append(f"Lieux de catalog.json relies a un conteneur de la couche systeme: {len(catalog & set(located))} / {len(catalog)}; "
                 f"conteneurs directs correspondants: {len(direct)}, {mb(store.size(direct))}.")
    for guid in SAMPLE:
        names = located.get(guid, (None, set()))[1] & set(store.by_lower)
        lines.append(f"Echantillon {places[guid]['name']}: {len(names)} conteneurs directs, {mb(store.size(names))}.")
    lines.append(f"Hangars sondes: {len(HANGAR_PROBES)} conteneurs directs, {mb(store.size(HANGAR_PROBES))}.")
    return lines + ["Les sous-conteneurs ne sont connus qu'en lisant chaque niveau; le plafond de 15 Go reste applique."]


def main(argv):
    args = parse_args(argv)
    out_dir = explore.find_out_dir(args.build)
    records_root = out_dir / explore.RECORDS_SUBDIR
    raw_manifest = json.loads((out_dir / "raw_manifest.json").read_text(encoding="utf-8"))
    texts = explore.load_localization(EXTRACTION_ROOT / raw_manifest["channel"] / out_dir.name / explore.LOCALIZATION)
    catalog = json.loads((out_dir / "catalog.json").read_text(encoding="utf-8"))
    places = load_places(records_root, texts, {location["guid"] for location in catalog["locations"]})
    unknown = [guid for guid in SAMPLE + TABLE_EXTRA if guid not in places]
    if unknown:
        raise ExtractionError(f"GUID de lieu inconnus dans raw/: {unknown}")
    store = open_store(args.game_dir, args.channel, out_dir.name)
    if not set(HANGAR_PROBES) <= set(store.by_lower):
        raise ExtractionError(f"Hangars sondes absents du p4k: {sorted(set(HANGAR_PROBES) - set(store.by_lower))}")
    roots = {lower for lower in store.by_lower if ROOT_CONTAINER.match(lower)}
    roots_ready = all(store.present(lower) for lower in roots)
    located = None
    if roots_ready:
        located = index_locations({lower: container_root(store.local(lower)) for lower in roots}, set(places))
    print("\n".join(plan_lines(store, roots, located, places)))
    if args.dry_run:
        print("Dry-run: rien n'a ete extrait.")
        return 0
    if not roots_ready:
        store.extract(roots)
        located = index_locations({lower: container_root(store.local(lower)) for lower in roots}, set(places))
    if args.roots_only:
        print(f"Couche systeme extraite; {len(located)} lieux relies. Relance --dry-run pour voir l'echantillon.")
        return 0

    consoles = load_consoles(records_root)
    guid_by_class = {name.lower(): guid for guid, (_, name, _) in consoles.items()}
    results = {}
    for guid in SAMPLE:
        results[guid] = walk_containers(store, located.get(guid, (None, set()))[1], guid_by_class)
        kinds = hits_by_kind(results[guid][1], consoles)
        print(f"{places[guid]['name']}: {len(results[guid][0])} conteneurs lus, consoles {dict(kinds)}")
    hangars = {name: walk_containers(store, {name}, guid_by_class) for name in HANGAR_PROBES}
    context = {"out_dir": out_dir, "places": places, "consoles": consoles, "located": located, "store": store,
               "results": results, "hangars": hangars, "plan": plan_lines(store, roots, located, places)}
    report = out_dir / "bonus" / "exploration_stockage.md"
    report.write_text(render_report(context), encoding="utf-8")
    print(f"Rapport: {report}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ExtractionError as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        sys.exit(1)
