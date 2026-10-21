"""SCGE extraction pipeline v1: phase 1 (extract) -> 2A (decode, explore) -> 2B (normalize) -> 2C (storage_locations).

Phase 1 is the only step that reads Data.p4k (whitelist, 15 GB cap); phases 2x read raw_extraction/ only.
Each step skips work already done for the current game build; a new build gets its own folders.
Outputs in data_json/<build>/ (one GUID per record everywhere):
  raw/, exploration.md                          2A
  catalog.json + lists/{items,commodities,manufacturers,locations}.json, wikelo.json, report.md     2B
  storage_locations.json + storage/{systems,bodies,locations}.json, storage_report.md               2C
  pipeline_manifest.json                        SHA-256 of every JSON output
Usage: python pipeline.py --dry-run    phase 1 dry-run: files, sizes and output folder; nothing written
       python pipeline.py [--force]    run every step; --force redoes phases 1 and 2A
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

from scripts import decode, explore, extract, normalize, storage_locations
from scripts.config import SETTINGS
from scripts.extract import ExtractionError, read_build, sha256_of

OUTPUTS = (
    "catalog.json", "lists/items.json", "lists/commodities.json", "lists/manufacturers.json", "lists/locations.json",
    "wikelo.json", "storage_locations.json", "storage/systems.json", "storage/bodies.json", "storage/locations.json",
)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game-dir", type=Path, default=SETTINGS.game_dir)
    parser.add_argument("--channel", default=SETTINGS.channel)
    parser.add_argument("--dry-run", action="store_true", help="show what would be read from Data.p4k; write nothing")
    parser.add_argument("--force", action="store_true", help="re-extract and re-decode even if this build is done")
    return parser.parse_args(argv)


def steps(args, build):
    """(name, module, argv) in run order; every step is pinned to the same game build."""
    game = ["--game-dir", str(args.game_dir), "--channel", args.channel]
    force = ["--force"] if args.force else []
    return [
        ("phase 1 - extract", extract, game + force),
        ("phase 2A - decode", decode, ["--channel", args.channel, "--build", build] + force),
        ("phase 2A - explore", explore, ["--build", build]),
        ("phase 2B - normalize", normalize, ["--build", build]),
        ("phase 2C - storage_locations", storage_locations, ["--build", build]),
    ]


def run_step(name, module, argv):
    print(f"\n=== {name} ===")
    if module.main(argv) != 0:
        raise ExtractionError(f"{name} a echoue")


def make_output_dirs():
    """The output roots named in config.ini; created here so a fresh clone runs without manual setup."""
    for folder in (decode.EXTRACTION_ROOT, decode.DATA_JSON_ROOT):
        folder.mkdir(parents=True, exist_ok=True)


def write_manifest(build, channel, ran):
    out_dir = decode.DATA_JSON_ROOT / build
    manifest = {
        "build": build,
        "channel": channel,
        "steps": ran,
        "outputs": {name: sha256_of(out_dir / name) for name in OUTPUTS},
        "finished_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "pipeline_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return out_dir / "pipeline_manifest.json"


def main(argv):
    args = parse_args(argv)
    print(f"Config: {SETTINGS.source}")
    build = read_build(args.game_dir / args.channel)["Version"]
    decoded = (decode.DATA_JSON_ROOT / build / "raw_manifest.json").is_file()
    print(f"Build du jeu: {build} ({'deja decode' if decoded else 'nouveau build: tout sera produit'})")
    plan = steps(args, build)
    if args.dry_run:
        run_step(plan[0][0] + " (dry-run)", extract, plan[0][2] + ["--dry-run"])
        print("\nDry-run: rien n'a ete ecrit; les phases 2x ne lisent pas Data.p4k.")
        return 0
    make_output_dirs()
    for name, module, step_argv in plan:
        run_step(name, module, step_argv)
    print(f"\nManifest: {write_manifest(build, args.channel, [name for name, _, _ in plan])}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ExtractionError as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        sys.exit(1)
