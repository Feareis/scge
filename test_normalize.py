"""Run: python test_normalize.py - checks normalize.py logic on synthetic data (no raw/ needed)."""

import normalize
from extract import ExtractionError


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


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"ok {name}")
