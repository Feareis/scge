"""Run: python -m tests.test_explore - checks explore.py parsers and catalog_rules.classify on synthetic records."""

import tempfile
from pathlib import Path

from scripts import explore
from scripts.catalog_rules import classify


def test_references():
    ref = "file://./../../../../libs/foundry/records/SCItemManufacturer/scitemmanufacturer.acom.json"
    assert explore.ref_path(ref) == "scitemmanufacturer/scitemmanufacturer.acom.json"
    assert explore.ref_path(None) is None
    assert explore.ref_path("_PointsTo_:ptr:4") is None
    assert explore.ref_guid({"_RecordId_": "abc", "_RecordName_": "StarMapObjectType.abc"}) == "abc"


def test_localization_keys():
    assert explore.loc_key("@item_NameFoo") == "item_namefoo"
    for placeholder in ("@LOC_PLACEHOLDER", "@LOC_EMPTY", "@LOC_UNINITIALIZED", "@", "", None, "plain text"):
        assert explore.loc_key(placeholder) is None
    with tempfile.TemporaryDirectory() as folder:
        ini = Path(folder) / "global.ini"
        ini.write_bytes("﻿Item_NameFoo=Foo Rifle\nitem_DescFoo,P=Class: Military\\nSize: 1\nbroken line\n".encode())
        texts = explore.load_localization(ini)
    assert texts == {"item_namefoo": "Foo Rifle", "item_descfoo": "Class: Military\\nSize: 1"}
    assert explore.CLASS_IN_DESCRIPTION.search(texts["item_descfoo"]).group(1) == "Military"


def test_scu_units():
    assert explore.scu_of({"_Type_": "SMicroCargoUnit", "microSCU": 84000}) == 0.084
    assert explore.scu_of({"_Type_": "SCentiCargoUnit", "centiSCU": 250}) == 2.5
    assert explore.scu_of(None) is None


def test_wikelo_orders_cover_templates_and_overrides():
    tree = {
        "orders": [
            {
                "_Type_": "HaulingOrder_EntityClass",
                "entityClass": "file://x/libs/foundry/records/entities/a.json",
                "minAmount": 5,
            },
            {"_Type_": "HaulingOrderContent_Resource", "resource": {"_RecordId_": "r1"}, "minSCU": 24.0, "maxSCU": 24.0},
            {"_Type_": "HaulingOrder_Property", "haulingOrdersProperty": "_PointsTo_:ptr:6"},
        ]
    }
    orders = explore.collect_orders(tree)
    assert [(o["entity"], o["resource"], o["min"], o["unit"]) for o in orders] == [
        ("entities/a.json", None, 5, "unit"),
        (None, "r1", 24.0, "scu"),
    ]


def test_reference_does_not_erase_inline_definition():
    definition = {"_RecordId_": "t1", "_RecordName_": "StarMapObjectType.t1", "_Type_": "StarMapObjectType", "name": "Manmade"}
    reference = {"_RecordPath_": "file://./starmap/starmapobjecttypes.json", "_RecordName_": "StarMapObjectType.t1", "_RecordId_": "t1"}
    scan = explore.Scan()
    scan.add("starmap/starmapobjecttypes.json", {"_RecordId_": "f1", "_RecordName_": "Types.f1", "_RecordValue_": {"_Type_": "Types", "types": [definition]}})
    scan.add("starmap/starmarine/oxygendispenser.json", {"_RecordId_": "f2", "_RecordName_": "StarMapObject.f2", "_RecordValue_": {"_Type_": "Other", "type": reference}})
    assert scan.by_guid["t1"]["label"] == "Manmade"


def test_walk_is_bounded():
    deep = current = {}
    for _ in range(explore.MAX_WALK_DEPTH + 5):
        current["next"] = {}
        current = current["next"]
    try:
        explore.walk(deep, lambda _node: None)
    except explore.ExtractionError:
        return
    raise AssertionError("walk should refuse trees deeper than MAX_WALK_DEPTH")


def test_classify_order():
    def item(path, kind, subtype="UNDEFINED", name="x"):
        return {"path": path, "type": kind, "subtype": subtype, "name": name}

    assert classify(item("entities/scitem/suit/test_suitarmor.json", "Suit")) == ("excluded", "objets de test", None)
    assert classify(item("entities/scitem/weapons/behr_rifle.json", "WeaponPersonal", "Medium")) == (
        "category",
        "armes",
        "fusils",
    )
    assert classify(item("entities/scitem/carryables/1h/food_bar.json", "Food")) == ("category", "consommables", "nourriture")
    assert classify(item("entities/scitem/carryables/1h/favour.json", "Cargo", "Cargo"))[:2] == (
        "category",
        "objets transportables",
    )
    assert classify(item("entities/scitem/cargo/box.json", "Cargo", "Cargo")) == ("excluded", "objets non portables", None)
    assert classify(item("entities/spaceships/aegs_gladius.json", "NOITEM_Vehicle", "Vehicle_Spaceship")) == (
        "excluded",
        "vaisseaux",
        None,
    )
    assert classify(item("entities/rocks/rock.json", "Misc", "Mineable")) == ("excluded", "objets non portables", None)
    assert classify(item("entities/x.json", "WeaponPersonal", "Medium", "EntitySpawner_Behr"))[1] == "objets non portables"
    assert classify(item("entities/x.json", "BrandNewType")) == ("unmapped", None, None)
    ship_gun_part = item("entities/scitem/ships/weapons/parts/banu_barrel_s1.json", "WeaponAttachment", "Barrel")
    assert classify(ship_gun_part) == ("excluded", "objets non portables", None)
    fps_barrel = item("entities/scitem/weapons/weapon_modifier/barrel_comp.json", "WeaponAttachment", "Barrel")
    assert classify(fps_barrel) == ("category", "armes", "accessoires d'arme")
    assert classify(item("entities/scitem/ships/weapons/behr_gun_s4.json", "WeaponGun", "Gun"))[1] == "composants de vaisseau"


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"ok {name}")
