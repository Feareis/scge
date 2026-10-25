"""Phase 2A: explore data_json/<build>/raw/ and write data_json/<build>/exploration.md.

Usage: python -m scripts.explore [--build 4.10.197.2878]   (default: latest build with raw_manifest.json)
"""

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

from .decode import DATA_JSON_ROOT, EXTRACTION_ROOT, version_key
from .extract import ExtractionError

RECORDS_SUBDIR = Path("raw/libs/foundry/records")
LOCALIZATION = Path("Data/Localization/english/global.ini")
PLACEHOLDER_KEYS = {"", "loc_placeholder", "loc_empty", "loc_uninitialized"}
MAX_WALK_DEPTH = 200
QUALITY_KEY = re.compile(r"quality", re.IGNORECASE)
# global.ini stores line breaks as a literal backslash-n.
CLASS_IN_DESCRIPTION = re.compile(r"(?:^|\\n)Class: ([^\\]+)")
WIKELO_GENERATOR = re.compile(r"^contracts/contractgenerator/.*thecollector.*\.json$")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", help="build folder in data_json/; default: latest exported build")
    return parser.parse_args(argv)


def find_out_dir(build):
    done = [path.parent for path in DATA_JSON_ROOT.glob("*/raw_manifest.json")]
    if build:
        done = [path for path in done if path.name == build]
    if not done:
        raise ExtractionError("Aucun data_json/<build>/raw_manifest.json: lance python -m scripts.decode d'abord")
    return max(done, key=lambda path: version_key(path.name))


def ref_path(value):
    """`file://./../../libs/foundry/records/a/b.json` -> `a/b.json` (the by_path key); anything else -> None."""
    if not isinstance(value, str) or not value.startswith("file://"):
        return None
    return value.partition("libs/foundry/records/")[2].lower() or None


def ref_guid(value):
    """Inline-record references are dicts carrying the target `_RecordId_`."""
    return value.get("_RecordId_") if isinstance(value, dict) else None


def loc_key(value):
    """`@item_NameFoo` -> `item_namefoo`; placeholders and non-keys -> None."""
    if not isinstance(value, str) or not value.startswith("@"):
        return None
    key = value[1:].lower()
    return None if key in PLACEHOLDER_KEYS else key


def load_localization(ini_path):
    """global.ini lines are `key=text`; some keys carry a `,P` suffix. Lookups are case-insensitive."""
    texts = {}
    for line in ini_path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, text = line.partition("=")
        if sep:
            texts[key.split(",")[0].strip().lower()] = text
    return texts


def walk(node, visit, depth=0):
    """Depth-first visit of every dict in a JSON tree; depth is bounded so a malformed file cannot run away."""
    if depth > MAX_WALK_DEPTH:
        raise ExtractionError(f"Arbre JSON plus profond que {MAX_WALK_DEPTH} niveaux")
    if isinstance(node, dict):
        visit(node)
        for child in node.values():
            walk(child, visit, depth + 1)
    elif isinstance(node, list):
        for child in node:
            walk(child, visit, depth + 1)


def scu_of(volume):
    """inventoryOccupancyVolume is a cargo-unit struct whose single numeric field gives the unit."""
    if not isinstance(volume, dict):
        return None
    for field, per_scu in (("microSCU", 1e6), ("centiSCU", 100), ("standardCargoUnits", 1), ("SCU", 1)):
        if isinstance(volume.get(field), (int, float)):
            return volume[field] / per_scu
    return None


def entity_info(relpath, record):
    """Fields the catalog needs from an EntityClassDefinition; `is_item` only if it has an AttachDef."""
    value = record["_RecordValue_"]
    info = {"path": relpath, "guid": record["_RecordId_"], "name": record["_RecordName_"].partition(".")[2]}
    for component in value.get("Components") or []:
        if not isinstance(component, dict):  # the DataCore holds null slots in some component lists
            continue
        kind = component.get("_Type_")
        if kind == "SAttachableComponentParams":
            attach = component.get("AttachDef") or {}
            localization = attach.get("Localization") or {}
            volume = attach.get("inventoryOccupancyVolume")
            info |= {
                "is_item": True,
                "type": attach.get("Type"),
                "subtype": attach.get("SubType"),
                "manufacturer": ref_path(attach.get("Manufacturer")),
                "size": attach.get("Size"),
                "grade": attach.get("Grade"),
                "scu": scu_of(volume),
                "volume_type": volume.get("_Type_") if isinstance(volume, dict) else None,
                "name_key": loc_key(localization.get("Name")),
                "desc_key": loc_key(localization.get("Description")),
            }
        elif kind == "VehicleComponentParams" and not info.get("name_key"):
            info["name_key"] = loc_key(component.get("vehicleName"))
    return info


def quality_family(resource):
    """Folder of the ResourceType's quality distribution (shipmineables, fpsmineables, harvestables...), else None."""
    for prop in resource.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        record = (prop.get("qualityDistribution") or {}).get("qualityDistributionRecord")
        if record:
            return record.rsplit("/", 2)[-2]
    return None


class Scan:
    """Everything explore needs, gathered in one pass over the 60k+ raw files."""

    def __init__(self):
        self.files = 0
        self.type_counts = collections.Counter()
        self.type_folders = collections.defaultdict(collections.Counter)
        self.by_path = {}  # relpath -> {guid, name, type}
        self.by_guid = {}  # GUID -> {name, type, label, path}, top-level and inline records
        self.entities = {}  # relpath -> entity_info
        self.manufacturers, self.locations, self.commodities = [], [], []
        self.quality = {}  # (record type, key) -> [count, min, max]

    def add(self, relpath, record):
        value = record["_RecordValue_"]
        kind = value.get("_Type_")
        self.files += 1
        self.type_counts[kind] += 1
        self.type_folders[kind][
            "/".join(relpath.split("/")[:2]) if relpath.startswith("entities/") else relpath.split("/")[0]
        ] += 1
        self.by_path[relpath] = {"guid": record["_RecordId_"], "name": record["_RecordName_"], "type": kind}
        self.by_guid[record["_RecordId_"]] = {"name": record["_RecordName_"], "type": kind, "label": None, "path": relpath}
        walk(value, lambda node: self.visit(kind, relpath, node))
        if kind == "EntityClassDefinition":
            self.entities[relpath] = entity_info(relpath, record)
        elif kind == "SCItemManufacturer":
            self.add_manufacturer(relpath, record)
        elif kind == "StarMapObject":
            self.add_location(relpath, record)
        elif kind == "ResourceTypeDatabase":
            self.add_commodities(value.get("groups") or [], [])

    def add_commodities(self, groups, parents):
        """ResourceTypes sit in nested groups; the export repeats some structs, so GUIDs are deduplicated."""
        if len(parents) > MAX_WALK_DEPTH:
            raise ExtractionError("resourcetypedatabase: groupes imbriques trop profonds")
        known = {commodity["guid"] for commodity in self.commodities}
        for group in groups:
            if not isinstance(group, dict):
                continue
            path = [*parents, group.get("_RecordName_", "?").partition(".")[2]]
            for resource in group.get("resources") or []:
                if isinstance(resource, dict) and resource.get("_RecordId_") and resource["_RecordId_"] not in known:
                    known.add(resource["_RecordId_"])
                    self.commodities.append(
                        {
                            "guid": resource["_RecordId_"],
                            "name": resource["_RecordName_"].partition(".")[2],
                            "name_key": loc_key(resource.get("displayName")),
                            "group": "/".join(path),
                            "refined": ref_guid(resource.get("refinedVersion")),
                            "quality_family": quality_family(resource),
                        }
                    )
            self.add_commodities(group.get("groups") or [], path)

    def visit(self, kind, relpath, node):
        # A reference ({_RecordPath_, _RecordName_, _RecordId_}, no _Type_) must not overwrite the record it points to.
        if "_RecordId_" in node and "_RecordName_" in node and "_Type_" in node:
            label = node.get("name") if isinstance(node.get("name"), str) else None
            self.by_guid[node["_RecordId_"]] = {
                "name": node["_RecordName_"],
                "type": node.get("_Type_"),
                "label": label,
                "path": relpath,
            }
        for key, number in node.items():
            if QUALITY_KEY.search(key) and isinstance(number, (int, float)) and not isinstance(number, bool):
                stats = self.quality.setdefault((kind, key), [0, number, number])
                stats[0] += 1
                stats[1], stats[2] = min(stats[1], number), max(stats[2], number)

    def add_manufacturer(self, relpath, record):
        value = record["_RecordValue_"]
        localization = value.get("Localization") or {}
        self.manufacturers.append(
            {
                "path": relpath,
                "guid": record["_RecordId_"],
                "name": record["_RecordName_"].partition(".")[2],
                "code": value.get("Code") or "",
                "name_key": loc_key(localization.get("Name")),
            }
        )

    def add_location(self, relpath, record):
        value = record["_RecordValue_"]
        self.locations.append(
            {
                "path": relpath,
                "guid": record["_RecordId_"],
                "name": record["_RecordName_"].partition(".")[2],
                "name_key": loc_key(value.get("name")),
                "type_guid": ref_guid(value.get("type")),
                "parent": ref_path(value.get("parent")),
            }
        )


def scan_raw(records_root):
    scan = Scan()
    for path in sorted(records_root.rglob("*.json")):
        relpath = path.relative_to(records_root).as_posix().lower()
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ExtractionError(f"JSON illisible: {path}: {error}") from error
        scan.add(relpath, record)
    return scan


def load_record(records_root, relpath):
    try:
        return json.loads((records_root / relpath).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ExtractionError(f"Enregistrement reference illisible: {relpath}: {error}") from error


def collect_orders(tree):
    """What the player must hand in: HaulingOrder_* in templates, HaulingOrderContent_* in contract paramOverrides.
    Items count in units (minAmount); resources count in SCU (minSCU)."""
    orders = []

    def visit(node):
        kind = node.get("_Type_") or ""
        if kind.startswith(("HaulingOrder_", "HaulingOrderContent_")) and kind != "HaulingOrder_Property":
            in_scu = "minSCU" in node
            orders.append(
                {
                    "order_type": kind,
                    "entity": ref_path(node.get("entityClass")),
                    "resource": ref_guid(node.get("resource")),
                    "min": node.get("minSCU" if in_scu else "minAmount"),
                    "max": node.get("maxSCU" if in_scu else "maxAmount"),
                    "unit": "scu" if in_scu else "unit",
                }
            )

    walk(tree, visit)
    return orders


def wikelo_trades(records_root, scan):
    """Wikelo (The Collector) trades: generator contracts -> template hauling orders + contractResults rewards."""
    trades = []
    for relpath in sorted(path for path in scan.by_path if WIKELO_GENERATOR.match(path)):
        generator = load_record(records_root, relpath)["_RecordValue_"]
        for group in generator.get("generators") or []:
            for contract in group.get("contracts") or []:
                template_path = ref_path(contract.get("template"))
                template = load_record(records_root, template_path)["_RecordValue_"] if template_path in scan.by_path else {}
                results = (contract.get("contractResults") or {}).get("contractResults") or []
                trades.append(
                    {
                        "generator": relpath,
                        "id": contract.get("id"),
                        "debug_name": contract.get("debugName"),
                        "template": template_path,
                        "not_for_release": contract.get("notForRelease"),
                        "title_key": override_title_key(contract.get("paramOverrides")),
                        "orders": collect_orders(template) + collect_orders(contract.get("paramOverrides")),
                        "rewards": [
                            reward for result in results if isinstance(result, dict) for reward in collect_rewards(result)
                        ],
                    }
                )
    return trades


def override_title_key(overrides):
    """The player-facing contract title is a `Title` string override: `@TheCollector_..._Title`."""
    for param in (overrides or {}).get("stringParamOverrides") or []:
        if isinstance(param, dict) and param.get("param") == "Title":
            return loc_key(param.get("value"))
    return None


def collect_rewards(result):
    """Three reward shapes: ContractResult_Item (entityClass+amount), ContractResult_ItemsWeighting
    (itemAwardStructure[] groups, one picked by weighting) and BlueprintRewards (blueprintPool). Others (reputation, tags)
    are kept as a bare result_type."""
    kind = result.get("_Type_")
    if kind == "ContractResult_Item":
        return [
            {"result_type": kind, "entity": ref_path(result.get("entityClass")), "amount": result.get("amount"), "choice": None}
        ]
    if kind == "ContractResult_ItemsWeighting":
        groups = [group for group in result.get("itemAwardStructure") or [] if isinstance(group, dict)]
        return [
            {
                "result_type": kind,
                "entity": ref_path(award.get("entityClass")),
                "amount": award.get("amountToAward"),
                "choice": f"{index + 1}/{len(groups)} (poids {group.get('weighting')})" if len(groups) > 1 else None,
            }
            for index, group in enumerate(groups)
            for award in group.get("awards") or []
            if isinstance(award, dict)
        ]
    if kind == "BlueprintRewards":
        return [
            {
                "result_type": kind,
                "entity": None,
                "blueprint_pool": ref_path(result.get("blueprintPool")),
                "amount": 1,
                "choice": None,
            }
        ]
    return [{"result_type": kind, "entity": None, "amount": None, "choice": None}]


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ") if value not in (None, "") else "-"


def md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(value) for value in row) + " |" for row in rows]
    return [*lines, ""]


def english(texts, key):
    return texts.get(key) if key else None


def percent(part, whole):
    return f"{100 * part / whole:.1f} %" if whole else "-"


def section_types(scan):
    lines = [
        "## 1. Types d'enregistrements",
        "",
        f"{scan.files:,} enregistrements, {len(scan.type_counts)} types. Dossier principal = sous-dossier de `records/`.",
        "",
    ]
    rows = [
        (kind, count, ", ".join(f"{folder} ({n})" for folder, n in scan.type_folders[kind].most_common(2)))
        for kind, count in scan.type_counts.most_common()
    ]
    return lines + md_table(["Type", "Nombre", "Dossiers principaux"], rows)


def item_class(texts, item):
    """No DataCore field holds the item class: it only appears as `Class: X` in the English description."""
    match = CLASS_IN_DESCRIPTION.search(english(texts, item.get("desc_key")) or "")
    return match.group(1).strip() if match else None


def section_items(scan, texts):
    items = [item for item in scan.entities.values() if item.get("is_item")]
    by_type = collections.defaultdict(list)
    for item in items:
        by_type[item["type"]].append(item)
    lines = [
        "## 2. Objets (EntityClassDefinition avec SAttachableComponentParams)",
        "",
        f"{len(items):,} objets sur {len(scan.entities):,} entites, {len(by_type)} types.",
        "Presence = valeur renseignee: fabricant non nul, taille > 0, grade non nul, classe lue dans la description "
        "anglaise (`Class: X`, aucun champ DataCore dedie), volume SCU > 0, cle de nom hors placeholder.",
        "Attention: `Grade` est toujours present (valeur par defaut 1), sa presence ne prouve rien. `Class:` designe la "
        "classe d'un composant de vaisseau (Military, Civilian...) mais le type d'arme pour les armes FPS (Assault Rifle...).",
        "",
    ]
    rows = []
    for kind, group in sorted(by_type.items(), key=lambda pair: -len(pair[1])):
        n = len(group)
        subtypes = collections.Counter(item["subtype"] for item in group).most_common(4)
        rows.append(
            (
                kind,
                n,
                ", ".join(f"{sub} ({count})" for sub, count in subtypes),
                percent(sum(bool(i["manufacturer"]) for i in group), n),
                percent(sum((i["size"] or 0) > 0 for i in group), n),
                percent(sum(i["grade"] is not None for i in group), n),
                percent(sum(bool(item_class(texts, i)) for i in group), n),
                percent(sum((i["scu"] or 0) > 0 for i in group), n),
                percent(sum(bool(i["name_key"]) for i in group), n),
            )
        )
    lines += md_table(["Type", "N", "Sous-types principaux", "Fabricant", "Taille", "Grade", "Classe", "SCU", "Cle nom"], rows)
    lines += ["### Exemples (3 par type)", ""]
    rows = [
        (kind, item["name"], english(texts, item["name_key"]), item["subtype"], item["size"], item["grade"], item["scu"])
        for kind, group in sorted(by_type.items())
        for item in sorted(group, key=lambda i: i["path"])[:3]
    ]
    lines += md_table(["Type", "Nom technique", "Nom anglais", "Sous-type", "Taille", "Grade", "SCU"], rows)
    volume_types = collections.Counter(item["volume_type"] for item in items)
    lines += [f"Structures de volume rencontrees: {dict(volume_types)}.", ""]
    return lines


def section_sources(scan, texts):
    lines = [
        "## 3. Ou se trouvent commodites, fabricants et lieux",
        "",
        "### Commodites",
        "",
        f"{len(scan.commodities)} `ResourceType` imbriques dans `resourcetypedatabase/resourcetypedatabase.json` "
        "(arbre `groups[].groups[].resources[]`, chaque ressource a son propre `_RecordId_`). "
        f"Lien vers la version raffinee (`refinedVersion`): {sum(bool(c['refined']) for c in scan.commodities)}.",
        "",
    ]
    groups = collections.Counter(commodity["group"] for commodity in scan.commodities)
    lines += md_table(
        ["Groupe", "Ressources", "Exemples"],
        [
            (group, n, ", ".join(c["name"] for c in scan.commodities if c["group"] == group)[:120])
            for group, n in sorted(groups.items())
        ],
    )
    with_code = [m for m in scan.manufacturers if m["code"]]
    lines += [
        "### Fabricants",
        "",
        f"{len(scan.manufacturers)} `SCItemManufacturer` dans `scitemmanufacturer/`, dont {len(with_code)} avec un `Code` "
        "(les autres sont surtout des logos de peintures `Paint_*`). Les objets y pointent par chemin "
        "(`AttachDef.Manufacturer` = `file://.../scitemmanufacturer/<x>.json`).",
        "",
    ]
    lines += md_table(
        ["GUID", "Code", "Nom technique", "Nom anglais"],
        [
            (m["guid"], m["code"], m["name"], english(texts, m["name_key"]))
            for m in sorted(with_code, key=lambda m: m["code"])[:8]
        ],
    )
    return lines + section_locations(scan, texts)


def section_locations(scan, texts):
    types = collections.Counter()
    resolved = 0
    for location in scan.locations:
        types[(scan.by_guid.get(location["type_guid"]) or {}).get("label")] += 1
        resolved += location["parent"] in scan.by_path
    with_parent = sum(bool(location["parent"]) for location in scan.locations)
    lines = [
        "### Lieux",
        "",
        f"{len(scan.locations)} `StarMapObject` dans `starmap/`. Type = reference vers un `StarMapObjectType` imbrique dans "
        "`starmap/starmapobjecttypes.json` (champ `name`). Parent = `parent` (chemin `file://` vers un autre StarMapObject): "
        f"{with_parent} lieux ont un parent, {resolved} parents resolus.",
        "",
    ]
    lines += md_table(["Type de lieu", "Nombre"], types.most_common())
    examples = {}
    for location in sorted(scan.locations, key=lambda loc: loc["path"]):
        kind = (scan.by_guid.get(location["type_guid"]) or {}).get("label")
        parent = scan.by_path.get(location["parent"]) if location["parent"] else None
        if kind not in examples:
            examples[kind] = (
                kind,
                english(texts, location["name_key"]),
                location["name"],
                parent["name"].partition(".")[2] if parent else None,
            )
    return lines + md_table(["Type", "Lieu (anglais)", "Nom technique", "Parent (nom technique)"], examples.values())


def section_wikelo(trades, scan, texts):
    def label(path):
        entity = scan.entities.get(path) or {}
        return english(texts, entity.get("name_key")) or (path or "-").rsplit("/", 1)[-1]

    order_types = collections.Counter(order["order_type"] for trade in trades for order in trade["orders"])
    reward_types = collections.Counter(reward["result_type"] for trade in trades for reward in trade["rewards"])
    lines = [
        "## 4. Echanges Wikelo",
        "",
        "Generateur: `contracts/contractgenerator/thecollector.json` -> `generators[].contracts[]`. Chaque contrat pointe "
        "vers un `ContractTemplate` (`contracts/contracttemplates/thecollector_*.json`) dont les "
        "`objectiveTokens[].objectiveHandler.haulingOrders[]` donnent les objets demandes (`entityClass` + `minAmount`/"
        "`maxAmount`). La recompense est dans `contractResults.contractResults[]` du contrat "
        "(`ContractResult_Item`: `entityClass` + `amount`).",
        "",
        f"{len(trades)} contrats; types de commande: {dict(order_types)}; types de resultat: {dict(reward_types)}.",
        "",
    ]
    is_vehicle = lambda path: bool(path) and path.startswith(("entities/spaceships/", "entities/groundvehicles/"))  # noqa: E731
    flags = {
        "avec objet(s) demande(s)": sum(any(o["entity"] or o["resource"] for o in t["orders"]) for t in trades),
        "avec recompense objet": sum(any(r["entity"] for r in t["rewards"]) for t in trades),
        "dont vaisseau/vehicule": sum(any(is_vehicle(r["entity"]) for r in t["rewards"]) for t in trades),
        "avec recompense blueprint": sum(any(r.get("blueprint_pool") for r in t["rewards"]) for t in trades),
        "avec recompense au choix (poids)": sum(any(r["choice"] for r in t["rewards"]) for t in trades),
        "notForRelease ou DO_NOT_USE": sum(bool(t["not_for_release"]) or "DO_NOT_USE" in (t["debug_name"] or "") for t in trades),
    }
    lines += md_table(["Contrats", "Nombre"], flags.items())
    examples, seen_kinds = [], set()
    for trade in trades:
        kind = next((r["result_type"] for r in trade["rewards"] if r["entity"] or r.get("blueprint_pool")), None)
        if kind and (kind, any(is_vehicle(r["entity"]) for r in trade["rewards"])) not in seen_kinds and len(examples) < 5:
            seen_kinds.add((kind, any(is_vehicle(r["entity"]) for r in trade["rewards"])))
            examples.append(trade)
    for trade in examples:
        wanted = "; ".join(
            f"{order['min']}x {label(order['entity'])}"
            if order["entity"]
            else f"{order['min']} SCU {(scan.by_guid.get(order['resource']) or {}).get('name', order['resource'])}"
            for order in trade["orders"]
            if order["entity"] or order["resource"]
        )
        given = "; ".join(
            f"{reward['amount']}x {label(reward['entity']) if reward['entity'] else reward.get('blueprint_pool')}"
            + (f" [choix {reward['choice']}]" if reward["choice"] else "")
            for reward in trade["rewards"]
            if reward["entity"] or reward.get("blueprint_pool")
        )
        lines += [f"- **{trade['debug_name']}**: demande {wanted or '-'} -> recompense {given}"]
    return [*lines, ""]


def section_quality(scan, records_root):
    lines = ["## 5. Champs de qualite", "", "Tout champ numerique dont le nom contient `quality`:", ""]
    rows = [
        (kind, key, count, low, high) for (kind, key), (count, low, high) in sorted(scan.quality.items(), key=lambda p: -p[1][0])
    ]
    lines += md_table(["Type d'enregistrement", "Champ", "Occurrences", "Min", "Max"], rows)
    band_sets = collections.Counter()
    for relpath in scan.by_path:
        if relpath.startswith("crafting/qualityquantization/"):
            bands = load_record(records_root, relpath)["_RecordValue_"]["qualityQuantization"]["bands"]
            band_sets[tuple((band["start"], band["end"]) for band in bands)] += 1
    lines += ["Paliers `CraftingQualityQuantizationBand` (debut-fin) par fichier de quantification:", ""]
    lines += md_table(
        ["Paliers", "Fichiers"],
        [(" / ".join(f"{start}-{end}" for start, end in bands), n) for bands, n in band_sets.most_common()],
    )
    return lines


def section_names(scan, texts):
    items = [item for item in scan.entities.values() if item.get("is_item")]
    found = [item for item in items if english(texts, item["name_key"])]
    missing = sorted((item for item in items if not english(texts, item["name_key"])), key=lambda item: item["path"])
    lines = [
        "## 6. Noms retrouves dans global.ini",
        "",
        f"`global.ini`: {len(texts):,} cles. Objets: {len(found):,}/{len(items):,} ({percent(len(found), len(items))}). "
        f"Commodites: {sum(bool(english(texts, c['name_key'])) for c in scan.commodities)}/{len(scan.commodities)}. "
        f"Fabricants avec code: {sum(bool(english(texts, m['name_key'])) for m in scan.manufacturers if m['code'])}/"
        f"{sum(bool(m['code']) for m in scan.manufacturers)}. "
        f"Lieux: {sum(bool(english(texts, loc['name_key'])) for loc in scan.locations)}/{len(scan.locations)}.",
        "",
        "Taux par categorie proposee (voir section 7):",
        "",
    ]
    return lines, missing


def section_proposal(scan, texts, name_lines, missing):
    from .catalog_rules import classify  # noqa: PLC0415 - kept local: only the report needs the proposal

    items = [item for item in scan.entities.values() if item.get("is_item")]
    categories, excluded, unmapped = collections.defaultdict(list), collections.defaultdict(list), collections.Counter()
    for item in items:
        verdict, label, _ = classify(item)
        if verdict == "category":
            categories[label].append(item)
        elif verdict == "excluded":
            excluded[label].append(item)
        else:
            unmapped[(item["type"], item["subtype"])] += 1
    name_lines += md_table(
        ["Categorie", "Objets", "Avec nom anglais"],
        [
            (cat, len(group), percent(sum(bool(english(texts, i["name_key"])) for i in group), len(group)))
            for cat, group in sorted(categories.items())
        ],
    )
    lines = ["## 7. Proposition", "", "### Categories (regles dans `catalog_rules.py`)", ""]
    lines += md_table(
        ["Categorie", "Objets", "Sous-categories", "Types d'origine"],
        [
            (
                cat,
                len(group),
                ", ".join(sorted({sub for *_, sub in (classify(i) for i in group)})),
                ", ".join(sorted({str(i["type"]) for i in group})),
            )
            for cat, group in sorted(categories.items())
        ],
    )
    lines += ["### Exclus (motif)", ""]
    lines += md_table(
        ["Motif", "Objets", "Types d'origine"],
        [(motive, len(group), ", ".join(sorted({str(i["type"]) for i in group}))) for motive, group in sorted(excluded.items())],
    )
    lines += ["### Non classes (a trancher)", ""]
    lines += (
        md_table(["Type", "Sous-type", "Objets"], [(kind, sub, n) for (kind, sub), n in unmapped.most_common()])
        if unmapped
        else ["Aucun.", ""]
    )
    lines += QUALITY_PROPOSAL
    kept = {id(item) for group in categories.values() for item in group}
    missing_kept = [item for item in missing if id(item) in kept]
    missing_lines = [
        "## Annexe - objets sans nom anglais",
        "",
        f"{len(missing):,} objets sans nom, dont {len(missing_kept):,} dans une categorie retenue (liste ci-dessous); "
        "les autres sont exclus par la proposition.",
        "",
        "<details><summary>Liste</summary>",
        "",
    ]
    missing_lines += md_table(
        ["Type", "Nom technique", "Cle de nom"], [(i["type"], i["name"], i["name_key"]) for i in missing_kept]
    )
    return name_lines + lines + [*missing_lines, "</details>", ""]


QUALITY_PROPOSAL = [
    "### Echelle de qualite",
    "",
    "Retenir l'echelle native du jeu: **entier de 0 a 1000**. C'est celle des blueprints (`startQuality`/`endQuality`/"
    "`minQuality`), de `CraftingGlobalParams.defaultCompositionQuality` (500) et des paliers de quantification ci-dessus. "
    "`MineableComposition.qualityScale` (0,49-1,0) est un multiplicateur de gisement, pas une qualite d'objet: a garder "
    "comme attribut de minage, pas comme echelle. Affichage propose: la valeur 0-1000 telle quelle, avec le palier de "
    "quantification comme regroupement.",
    "",
]


def build_report(out_dir, raw_manifest):
    records_root = out_dir / RECORDS_SUBDIR
    ini = EXTRACTION_ROOT / raw_manifest["channel"] / out_dir.name / LOCALIZATION
    texts = load_localization(ini)
    started = time.monotonic()
    scan = scan_raw(records_root)
    trades = wikelo_trades(records_root, scan)
    name_lines, missing = section_names(scan, texts)
    build = raw_manifest["build"]
    header = [
        f"# Exploration DataCore - {build['Version']} ({build['Branch']}, {raw_manifest['channel']})",
        "",
        f"Source: `{out_dir.name}/raw/` ({raw_manifest['files']:,} fichiers, starbreaker "
        f"{raw_manifest['tools']['starbreaker']}), noms: `{ini.relative_to(EXTRACTION_ROOT.parent).as_posix()}`. "
        f"Scan: {time.monotonic() - started:.0f} s.",
        "",
    ]
    sections = (
        header
        + section_types(scan)
        + section_items(scan, texts)
        + section_sources(scan, texts)
        + section_wikelo(trades, scan, texts)
        + section_quality(scan, records_root)
        + section_proposal(scan, texts, name_lines, missing)
    )
    return "\n".join(sections)


def main(argv):
    args = parse_args(argv)
    out_dir = find_out_dir(args.build)
    raw_manifest = json.loads((out_dir / "raw_manifest.json").read_text(encoding="utf-8"))
    report = build_report(out_dir, raw_manifest)
    target = out_dir / "exploration.md"
    target.write_text(report, encoding="utf-8")
    print(f"OK {target} ({len(report):,} caracteres)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ExtractionError as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        sys.exit(1)
