"""Phase 2A: export the extracted DataCore (Game*.dcb) to JSON with starbreaker.

Input: raw_extraction/<channel>/<build>/ (latest build by default). Output: data_json/<build>/raw/.
Usage: python -m scripts.decode [--dry-run] [--force] [--channel LIVE] [--build 4.10.197.2878]
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from fnmatch import fnmatch
from pathlib import Path

from .config import SETTINGS
from .extract import BUILD_ID_PATTERN, MAX_PLANNED_BYTES, STARBREAKER, ExtractionError, sha256_of, starbreaker_version

EXTRACTION_ROOT = SETTINGS.extraction_root
DATA_JSON_ROOT = SETTINGS.data_json_root
EXPORT_TIMEOUT_S = 3600
# starbreaker `dcb extract --filter` matches record file paths, not record names.
USEFUL_FAMILIES = (
    "libs/foundry/records/entities/scitem/**",  # items
    "libs/foundry/records/entities/spaceships/**",  # Wikelo ship rewards
    "libs/foundry/records/entities/groundvehicles/**",
    "libs/foundry/records/scitemmanufacturer/**",
    "libs/foundry/records/resourcetypedatabase/**",
    "libs/foundry/records/starmap/**",
    "libs/foundry/records/contracts/**",  # Wikelo trades live in contractgenerator + contracttemplates
    "libs/foundry/records/crafting/**",  # blueprints
)


class CapExceededError(ExtractionError):
    pass


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--channel", default=SETTINGS.channel)
    parser.add_argument("--build", help="build folder name; default: latest extracted build")
    parser.add_argument("--dry-run", action="store_true", help="measure file count and size, keep nothing")
    parser.add_argument("--force", action="store_true", help="re-export even if raw/ is already complete")
    return parser.parse_args(argv)


def version_key(name):
    return tuple(int(part) if part.isdigit() else 0 for part in name.split("."))


def find_build_dir(channel, build):
    channel_dir = EXTRACTION_ROOT / channel
    done = [path.parent for path in channel_dir.glob("*/manifest.json")]
    if build:
        done = [path for path in done if path.name == build]
    if not done:
        raise ExtractionError(f"Aucun build extrait (manifest.json) dans {channel_dir}" + (f" pour {build}" if build else ""))
    return max(done, key=lambda path: version_key(path.name))


def locate_dcb(build_dir):
    """Return the DataCore path listed in the phase 1 manifest, after checking its SHA-256."""
    manifest = json.loads((build_dir / "manifest.json").read_text(encoding="utf-8"))
    entries = [entry for entry in manifest["files"] if fnmatch(entry["path"], "Data/Game*.dcb")]
    if len(entries) != 1:
        raise ExtractionError(f"Le manifest liste {len(entries)} fichiers Data/Game*.dcb, 1 attendu")
    dcb = build_dir / entries[0]["path"]
    if sha256_of(dcb) != entries[0]["sha256"]:
        raise ExtractionError(f"SHA-256 de {dcb} different du manifest de phase 1")
    return manifest, dcb, entries[0]["sha256"]


def run_export(dcb, output, glob_filter, budget_left):
    """Run one starbreaker export; kill it if disk usage grows past budget_left bytes."""
    args = [str(STARBREAKER), "dcb", "extract", "--dcb", str(dcb), "-o", str(output), "--format", "json"]
    if glob_filter:
        args += ["--filter", glob_filter]
    free_before = shutil.disk_usage(output).free
    deadline = time.monotonic() + EXPORT_TIMEOUT_S
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    while True:
        try:
            _, stderr = process.communicate(timeout=1)
            break
        except subprocess.TimeoutExpired:
            # ponytail: disk-free delta also counts other processes' writes; good enough for a 15 GB guard.
            grown = free_before - shutil.disk_usage(output).free
            if grown > budget_left or time.monotonic() > deadline:
                process.kill()
                process.communicate()
                raise CapExceededError(f"Export arrete: ~{grown / 1024**3:.2f} Go ecrits (budget ou delai depasse)") from None
    if process.returncode != 0:
        raise ExtractionError(f"starbreaker dcb extract rc={process.returncode}: {stderr.strip()[-500:]}")


def measure(folder):
    count = size = 0
    for directory, _, names in os.walk(folder):
        for name in names:
            count += 1
            size += (Path(directory) / name).stat().st_size
    return count, size


def export_raw(dcb, output):
    """Full export if it fits in 15 GB, otherwise only the useful families. Returns the mode used."""
    output.mkdir(parents=True)
    try:
        run_export(dcb, output, None, MAX_PLANNED_BYTES)
    except CapExceededError as error:
        print(f"Export complet > 15 Go ({error}); repli sur les familles utiles.")
        shutil.rmtree(output)
        output.mkdir(parents=True)
        for family in USEFUL_FAMILIES:
            run_export(dcb, output, family, MAX_PLANNED_BYTES - measure(output)[1])
        return "families"
    return "full"


def main(argv):
    args = parse_args(argv)
    build_dir = find_build_dir(args.channel, args.build)
    if not BUILD_ID_PATTERN.match(build_dir.name):
        raise ExtractionError(f"Nom de build inattendu: {build_dir.name!r}")
    phase1_manifest, dcb, dcb_sha256 = locate_dcb(build_dir)
    out_dir = DATA_JSON_ROOT / build_dir.name
    raw_dir, staging = out_dir / "raw", out_dir / ("raw.dryrun" if args.dry_run else "raw.partial")
    done_marker = out_dir / "raw_manifest.json"
    version = starbreaker_version()
    print(f"Build {build_dir.name}, DCB {dcb.name} ({dcb.stat().st_size:,} o), starbreaker {version}")
    print(f"Sortie: {raw_dir}")
    if done_marker.is_file() and not args.force and not args.dry_run:
        print("raw/ deja exporte pour ce build; utilise --force pour refaire.")
        return 0

    if staging.exists():
        shutil.rmtree(staging)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = export_raw(dcb, staging)
    measured = measure(staging)
    print(f"Mode: {mode} | {measured[0]:,} fichiers | {measured[1]:,} o ({measured[1] / 1024**3:.2f} Go)")
    if args.dry_run:
        shutil.rmtree(staging)
        print("Dry-run: export mesure dans un dossier temporaire puis supprime; raw/ inchange.")
        return 0

    # Marker goes first and comes back last, so an interrupted run never looks complete.
    done_marker.unlink(missing_ok=True)
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    staging.rename(raw_dir)
    manifest = {
        "channel": args.channel,
        "build": phase1_manifest["build"],
        "source_dcb": {"path": str(dcb), "sha256": dcb_sha256},
        "tools": {"starbreaker": version},
        "mode": mode,
        "files": measured[0],
        "bytes": measured[1],
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    done_marker.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"OK raw/ ecrit; manifest: {done_marker}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ExtractionError as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        sys.exit(1)
