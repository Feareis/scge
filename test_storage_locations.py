"""Run: python test_storage_locations.py - checks closure, true/false/unknown decisions and report checks."""

import storage_locations as sl

# a -> b (child) ; b's gateway -> hangar h ; c is never reached ; "outside" is referenced but not in the graph.
GRAPH = {
    "a": ({"b", "outside"}, {}, set(), {}),
    "b": ({"a"}, {("G-ITEM", "L"): 2}, {"h"}, {}),
    "h": (set(), {("G-FREIGHT", "cargo"): 1}, set(), {}),
    "c": (set(), {("G-ITEM", "L"): 1}, set(), {}),
}


def test_closure_follows_children_and_hangars_once():
    reached, via_hangar = sl.closure(GRAPH, {"a"})
    assert reached == {"a", "b", "h"} and via_hangar == {"h"}


def test_place_links_stop_at_other_places():
    import xml.etree.ElementTree as ET

    root = ET.fromstring(
        '<ObjectContainer><ChildObjectContainers>'
        '<Child name="Data/planet.socpak" starMapRecord="planet"><ChildObjectContainers>'
        '<Child name="Data/Rock.socpak"/>'
        '<Child name="" starMapRecord="outpost"><ChildObjectContainers><Child name="Data/hab.socpak"/></ChildObjectContainers></Child>'
        '</ChildObjectContainers></Child>'
        '<Child name="Data/skybox.socpak"/>'
        '</ChildObjectContainers></ObjectContainer>')
    links = sl.place_links(root, {"planet", "outpost"})
    assert links == {"planet": {"data/planet.socpak", "data/rock.socpak"}, "outpost": {"data/hab.socpak"}}
    assert sl.owned_children(root, {"planet", "outpost"}) == {"data/skybox.socpak"}


def test_decide():
    place = {"record": "starmap/x.json", "amenities": ["Commodity Trading - Freight Elevator", "Garage"]}
    empty = {"itemInventory": [], "freight": []}
    values, evidence = sl.decide(place, linked=False, proofs=empty)
    assert values == {"itemInventory": "unknown", "freight": True}
    assert evidence["freight"] == [{"record": "starmap/x.json", "amenity": "Commodity Trading - Freight Elevator"}]
    values, _ = sl.decide({"record": "r", "amenities": []}, linked=True, proofs=empty)
    assert values == {"itemInventory": False, "freight": False}
    values, _ = sl.decide({"record": "r", "amenities": []}, linked=True, proofs={"itemInventory": [{"file": "f"}], "freight": []})
    assert values == {"itemInventory": True, "freight": False}  # an item bank alone makes a storage place


def test_checks_catch_missing_and_unproven():
    catalog = {"locations": [{"guid": "1"}, {"guid": "2"}]}
    entry = {"guid": "1", "name": "A", "itemInventory": True, "freight": False, "proof": {"itemInventory": [], "freight": []}}
    report = "\n".join(sl.check_rows([entry], catalog))
    assert report.count("ECHEC") == 2
    assert sl.is_unknown({"itemInventory": "unknown", "freight": False})
    assert not sl.is_unknown({"itemInventory": "unknown", "freight": True})


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"ok {name}")
