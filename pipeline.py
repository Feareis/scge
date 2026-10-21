"""Full extraction pipeline: phase 1 (extract) -> 2A (decode) -> 2B (normalize) -> 2C (storage_locations).

Each step skips work already done for the current game build; a new build gets its own folders.
Outputs in data_json/<build>/: catalog.json, wikelo.json, report.md, storage_locations.json, storage_report.md,
pipeline_manifest.json (SHA-256 of the outputs).
Usage: python pipeline.py --dry-run    phase 1 and 2C dry-runs (2C only if this build is already decoded)
       python pipeline.py [--force]    run every step; --force redoes phases 1 and 2A
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

import decode
import extract
import normalize
import storage_locations
from extract import ExtractionError, read_build, sha256_of

OUTPUTS = ("catalog.json", "wikelo.json", "storage_locations.json")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game-dir", type=Path, default=Path(r"D:\Roberts Space Industries\StarCitizen"))
    parser.add_argument("--channel", default="LIVE")
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
        ("phase 2B - normalize", normalize, ["--build", build]),
        ("phase 2C - storage_locations", storage_locations, game + ["--build", build]),
    ]


def run_step(name, module, argv):
    print(f"\n=== {name} ===")
    if module.main(argv) != 0:
        raise ExtractionError(f"{name} a echoue")


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
    build = read_build(args.game_dir / args.channel)["Version"]
    decoded = (decode.DATA_JSON_ROOT / build / "raw_manifest.json").is_file()
    print(f"Build du jeu: {build} ({'deja decode' if decoded else 'nouveau build: tout sera produit'})")
    plan = steps(args, build)
    if args.dry_run:
        run_step(plan[0][0] + " (dry-run)", extract, plan[0][2] + ["--dry-run"])
        if decoded:
            run_step(plan[3][0] + " (dry-run)", storage_locations, plan[3][2] + ["--dry-run"])
        else:
            print("\nPhase 2C: dry-run possible apres les phases 1-2B de ce build.")
        print("\nDry-run: rien n'a ete ecrit (2A/2B ne lisent pas Data.p4k).")
        return 0
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
