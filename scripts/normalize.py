"""Phase 2B: turn data_json/<build>/raw/ into catalog.json, lists/*.json, wikelo.json and report.md.

Output is sorted by GUID and carries no run timestamp: two runs on the same raw/ give identical files.
Usage: python -m scripts.normalize [--build 4.10.197.2878]   (default: latest build with raw_manifest.json)
"""

import argparse
import collections
import datetime
import hashlib
import json
import re
import sys

from . import explore
from .catalog_rules import classify
from .extract import ExtractionError

CLASS_CATEGORIES = {"composants de vaisseau", "armes"}  # validated: class read from the English description
EXCLUDED_LOCATION_TYPES = {None, "CardinalPoint", "YouAreHere", "QuantumTracePoint", "NavPoint"}  # UI markers, not places
NO_ENGLISH_NAME = "sans nom anglais"
DISABLED_MARKER = "DO_NOT_USE"
VEHICLE_PATHS = ("entities/spaceships/", "entities/groundvehicles/")
CATALOG_LISTS = ("items", "commodities", "manufacturers", "locations")
LISTS_DIR = "lists"
EXPECTED_SIZES = range(1, 12)  # 1..11 per the spec; the game also ships S00 components and an S12 gun, so this only warns


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", help="build folder in data_json/; default: latest exported build")
    return parser.parse_args(argv)


def header(raw_manifest):
    build = raw_manifest["build"]
    patch = re.search(r"(\d+(?:\.\d+)+)$", build["Branch"] or "")
    try:
        date = datetime.datetime.strptime(build["BuildDateStamp"], "%a %b %d %Y").date().isoformat()  # "Mon Oct 12 2026"
    except (TypeError, ValueError) as error:
        raise ExtractionError(f"BuildDateStamp inattendu: {build.get('BuildDateStamp')!r}") from error
    return {
        "patch": patch.group(1) if patch else None,
        "channel": raw_manifest["channel"],
        "build": build["Version"],
        "build_id": build["BuildId"],
        "branch": build["Branch"],
        "date": date,
    }


def item_row(scan, texts, info, verdict):
    _, category, subcategory = verdict
    manufacturer = scan.by_path.get(info["manufacturer"]) if info["manufacturer"] else None
    return {
        "guid": info["guid"],
        "name": info["name"],
        "name_en": explore.english(texts, info["name_key"]),
        "category": category,
        "subcategory": subcategory,
        "type": info["type"],
        "subtype": info["subtype"],
        "manufacturer": manufacturer["guid"] if manufacturer else None,
        "size": info["size"],
        "grade": info["grade"],
        "class": explore.item_class(texts, info) if category in CLASS_CATEGORIES else None,
        "unit": "unit",
    }


def build_items(scan, texts):
    """Kept items, exclusion counts by motive, and the items excluded only for lacking an English name."""
    items, excluded, unnamed = [], collections.Counter(), []
    for info in scan.entities.values():
        if not info.get("is_item"):
            continue
        verdict = classify(info)
        if verdict[0] != "category":
            excluded[verdict[1] or "non classe"] += 1
        elif not explore.english(texts, info["name_key"]):
            excluded[NO_ENGLISH_NAME] += 1
            unnamed.append(info | {"category": verdict[1]})
        else:
            items.append(item_row(scan, texts, info, verdict))
    return sorted(items, key=lambda item: item["guid"]), excluded, sorted(unnamed, key=lambda info: info["guid"])


def build_manufacturers(scan, texts, items):
    """Manufacturers with a code, plus any an item points to; English name, else the technical name (counted)."""
    referenced = {item["manufacturer"] for item in items if item["manufacturer"]}
    rows, fallbacks = [], 0
    for manufacturer in scan.manufacturers:
        if not manufacturer["code"] and manufacturer["guid"] not in referenced:
            continue
        name = explore.english(texts, manufacturer["name_key"])
        fallbacks += not name
        rows.append({"guid": manufacturer["guid"], "code": manufacturer["code"] or None, "name": name or manufacturer["name"]})
    return sorted(rows, key=lambda row: row["guid"]), fallbacks


def build_commodities(scan, texts):
    rows = [
        {"guid": c["guid"], "name": c["name"], "name_en": explore.english(texts, c["name_key"]), "unit": "scu"}
        for c in scan.commodities
    ]
    named = [row for row in rows if row["name_en"]]
    return sorted(named, key=lambda row: row["guid"]), sorted(row["name"] for row in rows if not row["name_en"])


def nearest_kept_parent(location, by_path, kept_guid_by_path):
    """Climb `parent` links until a kept location; dropped markers in between are skipped."""
    parent = location["parent"]
    for _ in range(explore.MAX_WALK_DEPTH):
        if parent is None:
            return None
        if parent in kept_guid_by_path:
            return kept_guid_by_path[parent]
        parent = by_path[parent]["parent"] if parent in by_path else None
    raise ExtractionError(f"Chaine de parents trop longue (cycle ?) depuis {location['path']}")


def build_locations(scan, texts):
    """Named places of a real type; returns rows, dropped count and how many got a grand-parent as parent."""
    by_path = {location["path"]: location for location in scan.locations}
    kept = {}
    for location in scan.locations:
        kind = (scan.by_guid.get(location["type_guid"]) or {}).get("label")
        name = explore.english(texts, location["name_key"])
        if kind not in EXCLUDED_LOCATION_TYPES and name:
            kept[location["path"]] = {"guid": location["guid"], "name": name, "type": kind}
    kept_guid_by_path = {path: row["guid"] for path, row in kept.items()}
    rows, reparented = [], 0
    for path, row in kept.items():
        parent = nearest_kept_parent(by_path[path], by_path, kept_guid_by_path)
        reparented += parent is not None and parent != kept_guid_by_path.get(by_path[path]["parent"])
        rows.append(row | {"parent": parent})
    return sorted(rows, key=lambda row: row["guid"]), len(scan.locations) - len(rows), reparented


def entity_ref(scan, texts, path):
    entity = scan.entities.get(path) or {}
    guid = entity.get("guid") or (scan.by_path.get(path) or {}).get("guid")
    return guid, explore.english(texts, entity.get("name_key")) or entity.get("name") or path


def requested_rows(trade, scan, texts, context):
    rows = []
    for order in trade["orders"]:
        if order["entity"]:
            guid, name = entity_ref(scan, texts, order["entity"])
            rows.append({"kind": "item", "guid": guid, "name": name, "quantity": order["min"], "unit": "unit"})
        elif order["resource"]:
            name = context["commodity_names"].get(order["resource"]) or (scan.by_guid.get(order["resource"]) or {}).get("name")
            rows.append({"kind": "commodity", "guid": order["resource"], "name": name, "quantity": order["min"], "unit": "scu"})
    return [row | {"in_catalog": row["guid"] in context["catalog_guids"]} for row in rows]


def reward_rows(trade, scan, texts, context):
    """Item and blueprint-pool rewards; reputation and completion tags are not inventory and are left out."""
    rows = []
    for reward in trade["rewards"]:
        if reward["entity"]:
            guid, name = entity_ref(scan, texts, reward["entity"])
            kind = "vehicle" if reward["entity"].startswith(VEHICLE_PATHS) else "item"
            rows.append({"kind": kind, "guid": guid, "name": name, "quantity": reward["amount"], "choice": reward["choice"]})
        elif reward.get("blueprint_pool"):
            pool = scan.by_path.get(reward["blueprint_pool"]) or {}
            rows.append({"kind": "blueprint", "guid": pool.get("guid"), "name": (pool.get("name") or "").partition(".")[2],
                         "quantity": 1, "choice": None})  # fmt: skip
    return [row | {"in_catalog": row["guid"] in context["catalog_guids"]} for row in rows]


def build_wikelo(trades, scan, texts, context):
    """Split into (active, disabled); disabled = notForRelease or DO_NOT_USE in the contract name."""
    active, disabled = [], []
    for trade in trades:
        row = {
            "guid": trade["id"],
            "name": trade["debug_name"],
            "title": explore.english(texts, trade["title_key"]),
            "requested": requested_rows(trade, scan, texts, context),
            "rewards": reward_rows(trade, scan, texts, context),
        }
        is_disabled = bool(trade["not_for_release"]) or DISABLED_MARKER in (trade["debug_name"] or "")
        (disabled if is_disabled else active).append(row)
    return sorted(active, key=lambda row: row["guid"]), sorted(disabled, key=lambda row: row["guid"])


def run_checks(catalog, wikelo_trades):
    """Each check: (label, passed, detail). A failed check makes the script exit 1."""
    guids = [row["guid"] for key in CATALOG_LISTS for row in catalog[key]]
    duplicates = sorted(guid for guid, count in collections.Counter(guids).items() if count > 1)
    manufacturer_guids = {row["guid"] for row in catalog["manufacturers"]}
    orphans = sorted(
        {i["manufacturer"] for i in catalog["items"] if i["manufacturer"] and i["manufacturer"] not in manufacturer_guids}
    )
    location_guids = {row["guid"] for row in catalog["locations"]}
    lost_parents = sorted(row["guid"] for row in catalog["locations"] if row["parent"] and row["parent"] not in location_guids)
    name_fields = {"items": "name_en", "commodities": "name_en", "manufacturers": "name", "locations": "name"}
    empty = [
        f"{key}:{row['guid']}" for key, field in name_fields.items() for row in catalog[key] if not (row[field] or "").strip()
    ]
    unsorted = [
        key for key in CATALOG_LISTS if [row["guid"] for row in catalog[key]] != sorted(row["guid"] for row in catalog[key])
    ]
    trade_duplicates = sorted(guid for guid, n in collections.Counter(t["guid"] for t in wikelo_trades).items() if n > 1)
    return [
        ("GUID uniques dans le catalogue", not duplicates, f"{len(guids):,} GUID, doublons: {duplicates[:10] or 'aucun'}"),
        ("Chaque fabricant d'objet existe", not orphans, f"manquants: {orphans[:10] or 'aucun'}"),
        ("Chaque parent de lieu existe", not lost_parents, f"orphelins: {lost_parents[:10] or 'aucun'}"),
        ("Aucun nom vide", not empty, f"vides: {empty[:10] or 'aucun'}"),
        ("Listes triees par GUID", not unsorted, f"non triees: {unsorted or 'aucune'}"),
        ("GUID Wikelo uniques", not trade_duplicates, f"doublons: {trade_duplicates or 'aucun'}"),
    ]


def dump_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_lists(out_dir, catalog):
    """lists/<key>.json: each catalog list on its own, same header and rows (catalog.json stays the reference)."""
    return {key: dump_json(out_dir / LISTS_DIR / f"{key}.json", {"header": catalog["header"], key: catalog[key]})
            for key in CATALOG_LISTS}


def render_report(catalog, checks, stats):
    hdr = catalog["header"]
    lines = [f"# Rapport de normalisation - {hdr['build']} (patch {hdr['patch']}, {hdr['channel']}, {hdr['date']})", ""]
    check_rows = [(label, "OK" if ok else "ECHEC", detail) for label, ok, detail in checks]
    lines += ["## Controles", "", *explore.md_table(["Controle", "Resultat", "Detail"], check_rows)]
    per_category = collections.Counter(item["category"] for item in catalog["items"])
    per_sub = collections.Counter((item["category"], item["subcategory"]) for item in catalog["items"])
    lines += ["## Objets par categorie", "", f"{len(catalog['items']):,} objets.", ""]
    lines += explore.md_table(["Categorie", "Objets"], sorted(per_category.items()))
    lines += explore.md_table(["Categorie", "Sous-categorie", "Objets"], [(*key, n) for key, n in sorted(per_sub.items())])
    lines += ["## Exclus par motif", "", *explore.md_table(["Motif", "Objets"], sorted(stats["excluded"].items()))]
    unnamed_commodities = ", ".join(stats["unnamed_commodities"]) or "aucune"
    lines += [
        "## Autres listes",
        "",
        f"- Commodites: {len(catalog['commodities'])} (sans nom anglais, ecartees: {unnamed_commodities}).",
        f"- Fabricants: {len(catalog['manufacturers'])} "
        f"(nom technique en repli faute de nom anglais: {stats['manufacturer_fallbacks']}).",
        f"- Lieux: {len(catalog['locations'])} ({stats['dropped_locations']} ecartes: marqueurs d'interface ou sans nom; "
        f"{stats['reparented']} rattaches a l'ancetre retenu le plus proche).",
        f"- Wikelo: {stats['wikelo_active']} echanges dans `wikelo.json`, {stats['wikelo_disabled']} desactives dans "
        "`wikelo_disabled/wikelo.json`.",
        f"- SHA-256 de `catalog.json`: `{stats['catalog_sha256']}`.",
        f"- Listes separees (memes lignes, memes GUID): `{LISTS_DIR}/` " + ", ".join(f"`{key}.json`" for key in CATALOG_LISTS) + ".",
        "",
    ]
    off_size = [i for i in catalog["items"] if i["size"] not in EXPECTED_SIZES]
    lines += ["## Tailles hors 1-11 (information, valeurs gardees telles quelles)", "", f"{len(off_size)} objets.", ""]
    lines += explore.md_table(["GUID", "Nom technique", "Categorie", "Type", "Taille"],
                              [(i["guid"], i["name"], i["category"], i["type"], i["size"]) for i in off_size])  # fmt: skip
    lines += ["## Objets sans nom (exclus du catalogue)", ""]
    lines += explore.md_table(["GUID", "Nom technique", "Type", "Categorie proposee"],
                              [(i["guid"], i["name"], i["type"], i["category"]) for i in stats["unnamed_items"]])  # fmt: skip
    return "\n".join(lines)


def main(argv):
    args = parse_args(argv)
    out_dir = explore.find_out_dir(args.build)
    raw_manifest = json.loads((out_dir / "raw_manifest.json").read_text(encoding="utf-8"))
    records_root = out_dir / explore.RECORDS_SUBDIR
    texts = explore.load_localization(explore.EXTRACTION_ROOT / raw_manifest["channel"] / out_dir.name / explore.LOCALIZATION)
    scan = explore.scan_raw(records_root)

    items, excluded, unnamed_items = build_items(scan, texts)
    manufacturers, manufacturer_fallbacks = build_manufacturers(scan, texts, items)
    commodities, unnamed_commodities = build_commodities(scan, texts)
    locations, dropped_locations, reparented = build_locations(scan, texts)
    catalog = {"header": header(raw_manifest), "items": items, "commodities": commodities,
               "manufacturers": manufacturers, "locations": locations}  # fmt: skip
    context = {
        "catalog_guids": {row["guid"] for key in CATALOG_LISTS for row in catalog[key]},
        "commodity_names": {row["guid"]: row["name_en"] for row in commodities},
    }
    active, disabled = build_wikelo(explore.wikelo_trades(records_root, scan), scan, texts, context)

    catalog_sha256 = dump_json(out_dir / "catalog.json", catalog)
    write_lists(out_dir, catalog)
    dump_json(out_dir / "wikelo.json", {"header": catalog["header"], "trades": active})
    dump_json(out_dir / "wikelo_disabled" / "wikelo.json", {"header": catalog["header"], "trades": disabled})
    checks = run_checks(catalog, active + disabled)
    stats = {"excluded": excluded, "unnamed_items": unnamed_items, "unnamed_commodities": unnamed_commodities,
             "manufacturer_fallbacks": manufacturer_fallbacks, "dropped_locations": dropped_locations, "reparented": reparented,
             "wikelo_active": len(active), "wikelo_disabled": len(disabled), "catalog_sha256": catalog_sha256}  # fmt: skip
    report = render_report(catalog, checks, stats)
    (out_dir / "report.md").write_text(report + "\n", encoding="utf-8", newline="\n")
    for label, ok, detail in checks:
        print(f"{'OK   ' if ok else 'ECHEC'} {label}: {detail}")
    print(f"catalog.json sha256={catalog_sha256} | {len(items):,} objets | rapport: {out_dir / 'report.md'}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ExtractionError as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        sys.exit(1)
