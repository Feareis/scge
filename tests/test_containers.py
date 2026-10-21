"""Run: python -m tests.test_containers - checks the socpak readers of containers.py on synthetic containers."""

import re
import tempfile
import zipfile
from pathlib import Path

from scripts import containers
from scripts import extract
from scripts import explore_storage

CONTAINER_XML = b"""<ObjectContainer name="base"><ChildObjectContainers>
 <Child name="Data/ObjectContainers/PU/loc/Base_Hab.socpak" starMapRecord="aaaa-1">
  <ChildObjectContainers><Child name="" class="RastarPlacedElement">
   <ChildObjectContainers><Child name="Data/objectcontainers/pu/loc/crate.socpak"/></ChildObjectContainers>
  </Child></ChildObjectContainers>
 </Child>
 <Child name="Data/objectcontainers/pu/loc/other.socpak" starMapRecord="not-in-catalog"/>
</ChildObjectContainers></ObjectContainer>"""

EDITOR_XML = b"""<ObjectContainer><Layers><Layer fullname="Main">
 <Object name="Itembank_A-001" type="Itembank_A" guid="1"/>
 <Object name="wall-003" type="Brush" guid="2"/>
 <Layer name="Cargo"><Object name="kiosk-000" type="FreightKiosk" guid="3"/><Object name="b" type="itembank_a" guid="4"/></Layer>
</Layer></Layers></ObjectContainer>"""


def write_socpak(folder):
    """Zip whose local headers hold backslashes while the central directory holds slashes, like the game's."""
    path = Path(folder) / "base.socpak"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("base.xml", CONTAINER_XML)
        archive.writestr("base_editor.xml", EDITOR_XML)
        archive.writestr("base/entdata/1.entxml", b"CryXmlB")
    data = path.read_bytes()
    path.write_bytes(data.replace(b"base/entdata/1.entxml", b"base\\entdata\\1.entxml", 1))  # first hit = local header
    return path


def test_readers():
    with tempfile.TemporaryDirectory() as folder:
        socpak = write_socpak(folder)
        with zipfile.ZipFile(socpak) as archive:
            assert containers.read_member(archive, archive.getinfo("base/entdata/1.entxml")) == b"CryXmlB"
            assert containers.read_member(archive, archive.getinfo("base.xml")) == CONTAINER_XML
        root = containers.container_root(socpak)
        hits = containers.console_hits(socpak, {"itembank_a": "G-ITEM", "freightkiosk": "G-FREIGHT"})
    assert hits == {("G-ITEM", "Main"): 1, ("G-FREIGHT", "Cargo"): 1, ("G-ITEM", "Cargo"): 1}
    located = explore_storage.index_locations({"base.socpak": root}, {"aaaa-1"})
    assert located == {"aaaa-1": ("base.socpak", {"data/objectcontainers/pu/loc/base_hab.socpak",
                                                  "data/objectcontainers/pu/loc/crate.socpak"})}


def test_path_regex():
    pattern = re.compile(extract.path_regex(["Data/PU/a_{B1}.socpak", "Data/PU/c.d+e.socpak"]))
    assert pattern.match("Data\\PU\\A_{b1}.socpak") and pattern.match("data/pu/c.d+e.socpak")
    assert not pattern.match("Data\\PU\\a_{B1}.socpak.bak") and not pattern.match("Data\\PU\\cxd+e.socpak")


if __name__ == "__main__":
    test_readers()
    test_path_regex()
    print("OK")
