"""Object containers (.socpak) read from Data.p4k, and the DataCore records that describe places and consoles.

Shared by storage_locations.py (pipeline step) and explore_storage.py (exploration tool).
Containers are cached under extraction_brute/<channel>/<build>/Data/ObjectContainers/, next to the phase 1 files;
`extract.py --force` wipes that build folder, cache included (it is re-extracted on the next run).
"""

import collections
import json
import re
import xml.etree.ElementTree as ET
import zipfile

import explore
from decode import EXTRACTION_ROOT
from extract import (
    EXTRACT_TIMEOUT_S,
    LIST_TIMEOUT_S,
    STARBREAKER,
    ExtractionError,
    check_budget,
    ensure_outside_game_dir,
    free_space,
    parse_listing,
    read_build,
    run_tool,
    warn_if_game_running,
)

# Nothing outside this pattern leaves the p4k; it is checked again on every file written.
WHITELIST = "Data/ObjectContainers/PU/**/*.socpak"
WHITELIST_PREFIX = "data/objectcontainers/pu/"
CACHE_SUBDIR = "Data/ObjectContainers"
# The system layer (systems, planets, moons, Lagrange points) is where <Child starMapRecord> links places to containers.
ROOT_CONTAINER = re.compile(r"^data/objectcontainers/pu/system/.+\.socpak$")
FREIGHT_AMENITIES = ("Commodity Trading - Freight Elevator", "Commodity Trading - Loading Dock")
FREIGHT_ENTITY_DIR = "entities/scitem/freightelevators/"
BATCH_SIZE = 150  # paths per starbreaker call; keeps the regex far below the Windows command-line limit
MAX_PARENT_DEPTH = 50
REGEX_META = set(".+*?()|[]{}^$")


def mb(size):
    return f"{size / 1024**2:.1f} Mo"


def path_regex(paths):
    """Exact, case-insensitive match of p4k paths; the p4k stores backslashes, callers pass slashes."""
    separator = "[\\\\/]"
    escaped = ("".join(separator if char in "/\\" else ("\\" + char if char in REGEX_META else char) for char in path)
               for path in paths)
    return "(?i)^(?:" + "|".join(escaped) + ")$"


def read_member(archive, info):
    """socpak local headers use backslashes where the central directory has slashes; zipfile rejects the mismatch."""
    info.orig_filename = info.filename.replace("/", "\\")
    try:
        return archive.open(info).read()
    except zipfile.BadZipFile:
        info.orig_filename = info.filename
        return archive.open(info).read()


def top_xml(socpak, editor):
    """Plain-XML root of <name>.xml (child containers) or <name>_editor.xml (every placed entity, by layer)."""
    with zipfile.ZipFile(socpak) as archive:
        found = [info for info in archive.infolist() if "/" not in info.filename and info.filename.endswith(".xml")
                 and info.filename.endswith("_editor.xml") == editor]
        if len(found) != 1:
            raise ExtractionError(f"{socpak.name}: {len(found)} fichiers {'_editor.xml' if editor else '.xml'}, 1 attendu")
        return ET.fromstring(read_member(archive, found[0]))


def container_root(socpak):
    return top_xml(socpak, editor=False)


def console_hits(socpak, guid_by_class):
    """Counter of (console class GUID, layer name) over the <Object type=class> entries of the editor XML."""
    hits = collections.Counter()
    stack = [(top_xml(socpak, editor=True), None)]
    while stack:  # one pass over a finite tree; each element carries its nearest layer
        element, layer = stack.pop()
        if element.tag == "Layer":
            layer = element.get("fullname") or element.get("name")
        elif element.tag == "Object" and (element.get("type") or "").lower() in guid_by_class:
            hits[guid_by_class[element.get("type").lower()], layer] += 1
        stack.extend((child, layer) for child in element)
    return hits


class P4kContainers:
    """The whitelisted part of Data.p4k, extracted on demand under `dest` (p4k paths mirrored)."""

    def __init__(self, p4k, dest):
        self.p4k, self.dest = p4k, dest
        stdout = run_tool([str(STARBREAKER), "p4k", "list", "--p4k", str(p4k), "--filter", WHITELIST], LIST_TIMEOUT_S)
        self.sizes = parse_listing(stdout)
        if not self.sizes:
            raise ExtractionError(f"Aucune entree du p4k ne correspond a {WHITELIST}")
        self.by_lower = {path.lower(): path for path in self.sizes}
        self.written = 0

    def local(self, lower):
        return self.dest / self.by_lower[lower]

    def present(self, lower):
        path = self.local(lower)
        return path.is_file() and path.stat().st_size == self.sizes[self.by_lower[lower]]

    def size(self, lowers):
        return sum(self.sizes[self.by_lower[lower]] for lower in lowers)

    def extract(self, lowers):
        missing = sorted(lower for lower in lowers if not self.present(lower))
        check_budget(self.written + self.size(missing), free_space(self.dest))
        for start in range(0, len(missing), BATCH_SIZE):
            batch = missing[start:start + BATCH_SIZE]
            run_tool([str(STARBREAKER), "p4k", "extract", "--p4k", str(self.p4k), "-o", str(self.dest),
                      "--regex", path_regex(batch)], EXTRACT_TIMEOUT_S)
            # starbreaker exits 0 even when nothing matched, so the files on disk are the only proof.
            absent = [lower for lower in batch if not self.present(lower)]
            if absent:
                raise ExtractionError(f"{len(absent)} conteneurs non extraits, ex. {absent[0]}")
            self.written += self.size(batch)
        self.check_whitelist()

    def check_whitelist(self):
        cache = self.dest / CACHE_SUBDIR
        produced = {path.relative_to(self.dest).as_posix().lower() for path in cache.rglob("*") if path.is_file()}
        stray = produced - set(self.by_lower)
        if stray:
            raise ExtractionError(f"Fichiers hors liste blanche dans {cache}: {sorted(stray)[:5]}")


def open_store(game_dir, channel, build):
    """Container cache of one build; refuses a game on another build than the decoded data."""
    channel_dir = game_dir / channel
    p4k = channel_dir / "Data.p4k"
    if not p4k.is_file():
        raise ExtractionError(f"Data.p4k introuvable: {p4k}")
    version = read_build(channel_dir)["Version"]
    if version != build:
        raise ExtractionError(f"Le jeu est en {version}, data_json en {build}: relance les phases 1 et 2")
    dest = EXTRACTION_ROOT / channel / build
    if not (dest / "manifest.json").is_file():
        raise ExtractionError(f"Phase 1 absente pour {build} ({dest}): lance extract.py d'abord")
    ensure_outside_game_dir(dest, game_dir)
    warn_if_game_running()
    return P4kContainers(p4k, dest)


def load_records(records_root, marker):
    """(relpath, record) for every raw JSON whose text contains `marker`; the filter skips most json.loads."""
    for path in sorted(records_root.rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        if marker in text:
            yield path.relative_to(records_root).as_posix().lower(), json.loads(text)


def load_type_names(records_root):
    """StarMapObjectType GUID -> name, read where types are defined (references elsewhere carry no name)."""
    names = {}
    tree = json.loads((records_root / "starmap/starmapobjecttypes.json").read_text(encoding="utf-8"))
    explore.walk(tree, lambda node: node.get("_Type_") == "StarMapObjectType" and names.update({node["_RecordId_"]: node.get("name")}))
    return names


def load_places(records_root, texts, catalog_guids):
    """Every StarMapObject: GUID -> name, type, parent GUID, record path, amenity names, in catalog.json or not."""
    table = json.loads((records_root / "starmapamenitytypes/starmapamenitytypes.json").read_text(encoding="utf-8"))
    amenity_names = {entry["_RecordId_"]: entry["name"] for entry in table["_RecordValue_"]["amenityTypes"]}
    type_names = load_type_names(records_root)
    places, guid_by_path, parent_paths = {}, {}, {}
    for relpath, record in load_records(records_root, '"StarMapObject"'):
        value = record["_RecordValue_"]
        if value.get("_Type_") != "StarMapObject":
            continue
        guid = record["_RecordId_"]
        guid_by_path[relpath], parent_paths[guid] = guid, explore.ref_path(value.get("parent"))
        english = explore.english(texts, explore.loc_key(value.get("name")))
        places[guid] = {
            "name": english or record["_RecordName_"].partition(".")[2],
            "named": bool(english),
            "type": type_names.get(explore.ref_guid(value.get("type"))),
            "record": relpath,
            "amenities": sorted(amenity_names.get(ref["_RecordId_"], "?") for ref in value.get("amenities") or []),
            "in_catalog": guid in catalog_guids,
        }
    for guid, place in places.items():
        place["parent"] = guid_by_path.get(parent_paths[guid])
    return places


def load_consoles(records_root):
    """Entity class GUID -> (kind, class name, record path): item banks, freight kiosks and freight platforms."""
    consoles = {}
    for relpath, record in load_records(records_root, '"EntityClassDefinition"'):
        components = [c for c in record["_RecordValue_"].get("Components") or [] if isinstance(c, dict)]
        provider = next((c for c in components if c.get("_Type_") == "SFreightElevatorUIProviderParams"), None)
        name = record["_RecordName_"].partition(".")[2]
        if provider is not None:
            kind = "itemInventory" if provider.get("isItemBank") else "freight"
            consoles[record["_RecordId_"]] = (kind, name, relpath)
        elif relpath.startswith(FREIGHT_ENTITY_DIR):
            consoles[record["_RecordId_"]] = ("freight", name, relpath)
    return consoles
