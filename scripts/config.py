"""Local settings for every step: GAME_EXTRACT/config.ini, or config.example.ini when config.ini is absent.

Relative paths are resolved against GAME_EXTRACT, so the defaults work from any working directory.
"""

import configparser
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]  # GAME_EXTRACT, one level above scripts/
FILES = ("config.ini", "config.example.ini")
REQUIRED = {"game": ("dir", "channel"), "tools": ("starbreaker", "unp4k"), "output": ("extraction", "data_json")}


class ConfigError(Exception):
    pass


class Settings(NamedTuple):
    source: Path
    game_dir: Path
    channel: str
    starbreaker: Path
    unp4k: Path
    extraction_root: Path
    data_json_root: Path


def load(root=ROOT):
    source = next((root / name for name in FILES if (root / name).is_file()), None)
    if source is None:
        raise ConfigError(f"ni {FILES[0]} ni {FILES[1]} dans {root}")
    parser = configparser.ConfigParser(interpolation=None)  # Windows paths may contain '%'
    parser.read(source, encoding="utf-8")
    missing = [f"[{section}] {key}" for section, keys in REQUIRED.items() for key in keys
               if not parser.get(section, key, fallback="").strip()]  # fmt: skip
    if missing:
        raise ConfigError(f"{source}: valeur manquante pour {', '.join(missing)}")

    def path(section, key):
        value = Path(parser.get(section, key).strip())
        return value if value.is_absolute() else root / value

    return Settings(
        source=source,
        game_dir=path("game", "dir"),
        channel=parser.get("game", "channel").strip(),
        starbreaker=path("tools", "starbreaker"),
        unp4k=path("tools", "unp4k"),
        extraction_root=path("output", "extraction"),
        data_json_root=path("output", "data_json"),
    )


SETTINGS = load()
