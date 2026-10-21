"""Run: python -m tests.test_config - checks config.ini / config.example.ini loading on temporary folders."""

import tempfile
from pathlib import Path

from scripts import config

EXAMPLE = """[game]
dir = C:\\Games\\StarCitizen
channel = LIVE
[tools]
starbreaker = tools/starbreaker.exe
unp4k = D:\\unp4k\\unp4k.exe
[output]
extraction = raw_extraction
data_json = data_json
"""


def expect_error(root):
    try:
        config.load(root)
    except config.ConfigError:
        return
    raise AssertionError(f"config.load({root}) should have raised ConfigError")


def test_example_is_the_fallback_and_relative_paths_hang_off_the_root():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "config.example.ini").write_text(EXAMPLE, encoding="utf-8")
        settings = config.load(root)
        assert settings.source == root / "config.example.ini"
        assert settings.starbreaker == root / "tools" / "starbreaker.exe"
        assert settings.unp4k == Path(r"D:\unp4k\unp4k.exe")
        assert (settings.game_dir, settings.channel) == (Path(r"C:\Games\StarCitizen"), "LIVE")
        assert (settings.extraction_root, settings.data_json_root) == (root / "raw_extraction", root / "data_json")


def test_config_ini_wins_over_the_example():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "config.example.ini").write_text(EXAMPLE, encoding="utf-8")
        (root / "config.ini").write_text(EXAMPLE.replace("LIVE", "PTU"), encoding="utf-8")
        settings = config.load(root)
        assert (settings.source.name, settings.channel) == ("config.ini", "PTU")


def test_missing_file_or_key_stops_the_run():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        expect_error(root)
        (root / "config.ini").write_text(EXAMPLE.replace("unp4k = D:\\unp4k\\unp4k.exe", "unp4k ="), encoding="utf-8")
        expect_error(root)


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"ok {name}")
