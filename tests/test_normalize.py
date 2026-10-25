"""Run: python -m tests.test_normalize - checks normalize.py logic on synthetic data (no raw/ needed)."""

from scripts import normalize
from scripts.extract import ExtractionError


def test_header_from_manifest():
    manifest = {"channel": "LIVE", "build": {"Version": "4.10.197.2878", "BuildId": "b", "Branch": "sc-alpha-4.10.2",
                                             "BuildDateStamp": "Mon Oct 12 2026"}}  # fmt: skip
    assert normalize.header(manifest) == {"patch": "4.10.2", "channel": "LIVE", "build": "4.10.197.2878", "build_id": "b",
                                          "branch": "sc-alpha-4.10.2", "date": "2026-10-12"}  # fmt: skip
    manifest["build"]["BuildDateStamp"] = "12/10/2026"
    try:
        normalize.header(manifest)
    except ExtractionError:
        return
    raise AssertionError("an unknown date format must stop the run, not be guessed")


def test_parent_skips_dropped_markers_and_detects_cycles():
    by_path = {"moon": {"path": "moon", "parent": "marker"}, "marker": {"path": "marker", "parent": "planet"},
               "planet": {"path": "planet", "parent": None}}  # fmt: skip
    assert normalize.nearest_kept_parent(by_path["moon"], by_path, {"moon": "g-moon", "planet": "g-planet"}) == "g-planet"
    assert normalize.nearest_kept_parent(by_path["planet"], by_path, {"planet": "g-planet"}) is None
    loop = {"a": {"path": "a", "parent": "b"}, "b": {"path": "b", "parent": "a"}}
    try:
        normalize.nearest_kept_parent(loop["a"], loop, {})
    except ExtractionError:
        return
    raise AssertionError("a parent cycle must raise")


def test_checks_catch_broken_catalog():
    catalog = {
        "items": [
            {"guid": "2", "name_en": "Rifle", "manufacturer": "missing"},
            {"guid": "1", "name_en": " ", "manufacturer": None},
        ],
        "commodities": [{"guid": "2", "name_en": "Gold"}],
        "manufacturers": [],
        "locations": [{"guid": "9", "name": "Moon", "parent": "gone"}],
    }
    failed = {label for label, ok, _ in normalize.run_checks(catalog, [{"guid": "t"}, {"guid": "t"}]) if not ok}
    assert failed == {
        "GUID uniques dans le catalogue", "Chaque fabricant d'objet existe", "Chaque parent de lieu existe",
        "Aucun nom vide", "Listes triees par GUID", "GUID Wikelo uniques",
    }  # fmt: skip


def test_commodities_are_mineable_ores_only():
    class Scan:
        commodities = [
            {"guid": "c-car", "name": "Carinite", "name_key": "k_car", "quality_family": "fpsmineables"},
            {"guid": "c-qua", "name": "Raw_Quantainium", "name_key": "k_qua", "quality_family": "shipmineables",
             "refined": "c-qref"},  # fmt: skip
            {"guid": "c-qref", "name": "Quantanium", "name_key": "k_qref", "quality_family": None},
            {"guid": "c-suit", "name": "RS1OdyseySpacesuits", "name_key": "k_suit", "quality_family": None},
            {"guid": "c-horn", "name": "KopionHorn", "name_key": "k_horn", "quality_family": "harvestables"},
            {"guid": "c-dark", "name": "Ore_Hidden", "name_key": None, "quality_family": "shipmineables"},
        ]

    texts = {"k_car": "Carinite", "k_qua": "Quantainium (Raw)", "k_qref": "Quantainium",
             "k_suit": "RS1 Odysey Spacesuits", "k_horn": "Kopion Horn"}  # fmt: skip
    rows, unnamed = normalize.build_commodities(Scan, texts)
    assert rows == [
        {"guid": "c-car", "name": "Carinite", "name_en": "Carinite", "unit": "unit", "quality": None},
        {"guid": "c-qref", "name": "Quantanium", "name_en": "Quantainium", "unit": "scu", "quality": 500},
        {"guid": "c-qua", "name": "Raw_Quantainium", "name_en": "Quantainium (Raw)", "unit": "scu", "quality": 500},
    ]
    assert unnamed == ["Ore_Hidden"]


def test_lists_split_the_catalog_without_changing_rows():
    import json
    import tempfile
    from pathlib import Path

    catalog = {"header": {"build": "x"}, "items": [{"guid": "i1"}], "commodities": [{"guid": "c1"}],
               "manufacturers": [], "locations": [{"guid": "l1", "parent": None}]}  # fmt: skip
    with tempfile.TemporaryDirectory() as folder:
        normalize.write_lists(Path(folder), catalog)
        for key in normalize.CATALOG_LISTS:
            data = json.loads((Path(folder) / normalize.LISTS_DIR / f"{key}.json").read_text(encoding="utf-8"))
            assert data == {"header": catalog["header"], key: catalog[key]}


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"ok {name}")
