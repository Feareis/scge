"""Run: python -m tests.test_extract - checks the guard rails of extract.py without touching Data.p4k."""

import json
import tempfile
from pathlib import Path

from scripts import extract


def expect_error(function, *args):
    try:
        function(*args)
    except extract.ExtractionError:
        return
    raise AssertionError(f"{function.__name__}{args} should have raised ExtractionError")


def test_parse_listing():
    stdout = "Data\\Game2.dcb\t331932921\nNo files matched filter: x\nData\\Localization\\english\\global.ini\t10491521\n"
    assert extract.parse_listing(stdout) == {"Data/Game2.dcb": 331932921, "Data/Localization/english/global.ini": 10491521}
    assert extract.parse_listing("No files matched filter: <regex>\n") == {}


def test_output_inside_game_dir_is_refused():
    game = Path(r"D:\Roberts Space Industries\StarCitizen")
    expect_error(extract.ensure_outside_game_dir, game / "LIVE" / "out", game)
    expect_error(extract.ensure_outside_game_dir, game, game)
    expect_error(extract.ensure_outside_game_dir, game.parent, game)
    extract.ensure_outside_game_dir(Path(r"C:\GAME_EXTRACT\extraction_brute\LIVE\4.10"), game)


def test_budget():
    expect_error(extract.check_budget, extract.MAX_PLANNED_BYTES + 1, 10**15)
    expect_error(extract.check_budget, 1000, 999)
    extract.check_budget(1000, 1000)


def test_build_version_must_be_path_safe():
    with tempfile.TemporaryDirectory() as folder:
        manifest = Path(folder) / "build_manifest.id"
        manifest.write_text(json.dumps({"Data": {"Version": "4.10.193.11644", "Branch": "sc-alpha-4.10.0"}}))
        assert extract.read_build(Path(folder))["Version"] == "4.10.193.11644"
        manifest.write_text(json.dumps({"Data": {"Version": "..\\..\\evil"}}))
        expect_error(extract.read_build, Path(folder))
        manifest.write_text("not json")
        expect_error(extract.read_build, Path(folder))


def test_build_is_done_only_with_the_current_whitelist():
    with tempfile.TemporaryDirectory() as folder:
        build_dir = Path(folder)
        assert not extract.is_done(build_dir)
        (build_dir / "manifest.json").write_text(json.dumps({"files": []}))  # phase 1 manifest from before v1
        assert not extract.is_done(build_dir)
        (build_dir / "manifest.json").write_text(json.dumps({"whitelist": list(extract.WHITELIST)}))
        assert extract.is_done(build_dir)
        (build_dir / "manifest.json").write_text("{broken")
        expect_error(extract.is_done, build_dir)


def test_messages_are_ascii():
    # Accents print garbled in Git Bash consoles, so the script stays pure ASCII.
    Path(extract.__file__).read_text(encoding="utf-8").encode("ascii")


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"ok {name}")
