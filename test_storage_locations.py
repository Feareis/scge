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


CATALOG = {"locations": [
    {"guid": "s", "name": "Stanton", "type": "Star", "parent": None},
    {"guid": "p", "name": "Hurston", "type": "Planet", "parent": "s"},
    {"guid": "lz", "name": "Lorville", "type": "LandingZone", "parent": "p"},
    {"guid": "h", "name": "Maria Pure of Heart", "type": "Outpost", "parent": "lz"},
    {"guid": "m", "name": "Bloom", "type": "Moon", "parent": "p"},
    {"guid": "o", "name": "Narena's Rest", "type": "Outpost", "parent": "m"},
    {"guid": "x", "name": "Kudre Ore", "type": "Outpost", "parent": "m"},
]}


def entry(item, freight, proof_item=(), proof_freight=()):
    return {"itemInventory": item, "freight": freight, "proof": {"itemInventory": list(proof_item), "freight": list(proof_freight)}}


def test_roll_up_merges_sub_places_into_the_listed_place():
    entries = {
        "lz": entry(False, True, proof_freight=[{"file": "kiosk"}]),
        "h": entry(True, False, proof_item=[{"file": "hosp"}]),
        "o": entry(True, False, proof_item=[{"file": "shack"}]),
        "x": entry("unknown", False),
    }
    places = sl.roll_up(entries, CATALOG)
    # sorted by system, planet, moon, name: places on the planet itself come before its moons
    assert [place["name"] for place in places] == ["Lorville", "Kudre Ore", "Narena's Rest"]
    lorville, kudre, narena = places
    assert (lorville["storage"], lorville["subLocations"]) == ("both", [{"guid": "h", "name": "Maria Pure of Heart"}])
    assert lorville["proof"]["itemInventory"] == [{"file": "hosp", "subLocation": "Maria Pure of Heart"}]
    assert (lorville["system"], lorville["planet"], lorville["moon"]) == ("Stanton", "Hurston", None)
    assert (narena["storage"], narena["moon"], narena["path"]) == ("itemInventory", "Bloom", "Stanton > Hurston > Bloom > Narena's Rest")
    assert kudre["storage"] is None and sl.is_unknown(kudre)
    assert "ECHEC" not in "\n".join(sl.check_rows(places, CATALOG))

    systems, bodies, locations = sl.split_storage([lorville, narena], CATALOG)  # only places that store
    assert systems == [{"guid": "s", "name": "Stanton"}]
    assert bodies == [{"guid": "m", "name": "Bloom", "type": "Moon", "systemGuid": "s", "parentGuid": "p"},
                      {"guid": "p", "name": "Hurston", "type": "Planet", "systemGuid": "s", "parentGuid": "s"}]
    assert [(row["guid"], row["systemGuid"], row["bodyGuid"]) for row in locations] == [("lz", "s", "p"), ("o", "s", "m")]
    assert "planet" not in locations[0] and locations[0]["proof"] == lorville["proof"]


def test_checks_catch_missing_and_unproven():
    place = {"guid": "o", "name": "A", "itemInventory": True, "freight": False, "subLocations": [],
             "proof": {"itemInventory": [], "freight": []}}
    report = "\n".join(sl.check_rows([place], CATALOG))
    assert report.count("ECHEC") == 2
    assert sl.is_unknown({"itemInventory": "unknown", "freight": False})
    assert not sl.is_unknown({"itemInventory": "unknown", "freight": True})


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"ok {name}")
