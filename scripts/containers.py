"""Object containers (.socpak) extracted by phase 1, and the DataCore records that describe places and consoles.

Shared by storage_locations.py (phase 2C) and explore_storage.py (exploration tool). Nothing here reads Data.p4k:
containers are read from extraction_brute/<channel>/<build>/Data/ObjectContainers/, as listed in the phase 1 manifest.
"""

import collections
import json
import re
import xml.etree.ElementTree as ET
import zipfile

from . import explore
from .decode import EXTRACTION_ROOT
from .extract import ExtractionError

WHITELIST = "Data/ObjectContainers/PU/**/*.socpak"  # the phase 1 whitelist entry for containers
WHITELIST_PREFIX = "data/objectcontainers/pu/"
CACHE_SUBDIR = "Data/ObjectContainers"
# The system layer (systems, planets, moons, Lagrange points) is where <Child starMapRecord> links places to containers.
ROOT_CONTAINER = re.compile(r"^data/objectcontainers/pu/system/.+\.socpak$")
FREIGHT_AMENITIES = ("Commodity Trading - Freight Elevator", "Commodity Trading - Loading Dock")
FREIGHT_ENTITY_DIR = "entities/scitem/freightelevators/"
MAX_PARENT_DEPTH = 50


def mb(size):
    return f"{size / 1024**2:.1f} Mo"


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


class ContainerCache:
    """The containers phase 1 extracted for one build, keyed by lower-cased p4k path."""

    def __init__(self, build_dir):
        manifest_path = build_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ExtractionError(f"Phase 1 absente ({manifest_path}): lance python -m scripts.extract d'abord")
        files = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
        self.dest = build_dir
        self.sizes = {entry["path"]: entry["size"] for entry in files if entry["path"].lower().startswith(WHITELIST_PREFIX)}
        if not self.sizes:
            raise ExtractionError(f"{manifest_path} ne liste aucun conteneur: relance python -m scripts.extract (liste blanche a jour)")
        self.by_lower = {path.lower(): path for path in self.sizes}

    def local(self, lower):
        return self.dest / self.by_lower[lower]

    def present(self, lower):
        path = self.local(lower)
        return path.is_file() and path.stat().st_size == self.sizes[self.by_lower[lower]]

    def size(self, lowers):
        return sum(self.sizes[self.by_lower[lower]] for lower in lowers)

    def require(self, lowers):
        """Every container is extracted by phase 1; a missing or truncated one means the cache is broken."""
        missing = sorted(lower for lower in lowers if not self.present(lower))
        if missing:
            raise ExtractionError(f"{len(missing)} conteneurs absents ou tronques, ex. {missing[0]}: relance python -m scripts.extract --force")


def open_store(channel, build):
    return ContainerCache(EXTRACTION_ROOT / channel / build)


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
