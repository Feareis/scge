"""Phase 1: extract the DataCore and English localization from Star Citizen's Data.p4k.

The game folder is read-only: Data.p4k is read in place, nothing is ever written there.
Usage: python extract.py [--dry-run] [--force] [--game-dir DIR] [--channel LIVE]
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STARBREAKER = ROOT / "tools" / "starbreaker-cli-v0.3.2-windows-x86_64" / "starbreaker.exe"
UNP4K = ROOT / "tools" / "unp4k-suite-win-x64-v4.0.87" / "publish" / "unp4k.exe"
# unp4k has no --version flag: release tag from the zip name, build from the exe ProductVersion.
UNP4K_VERSION = "v4.0.87 (unp4k.exe 1.0.0+b492ab14d262)"

# Each pattern must match at least one p4k entry; nothing outside these leaves the p4k.
WHITELIST = ("Data/Game*.dcb", "Data/Localization/english/global.ini")
MAX_PLANNED_BYTES = 15 * 1024**3
WATCHED_PROCESSES = ("rsi launcher.exe", "starcitizen.exe")
LIST_TIMEOUT_S = 600
EXTRACT_TIMEOUT_S = 1800
BUILD_ID_PATTERN = re.compile(r"^[0-9A-Za-z._-]+$")


class ExtractionError(Exception):
    pass


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game-dir", type=Path, default=Path(r"D:\Roberts Space Industries\StarCitizen"))
    parser.add_argument("--channel", default="LIVE")
    parser.add_argument("--out-root", type=Path, default=ROOT / "extraction_brute")
    parser.add_argument("--dry-run", action="store_true", help="show targets, sizes and output dir; write nothing")
    parser.add_argument("--force", action="store_true", help="re-extract even if this build is already done")
    return parser.parse_args(argv)


def read_build(channel_dir):
    manifest_path = channel_dir / "build_manifest.id"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))["Data"]
        version = data["Version"]
    except (OSError, ValueError, KeyError) as error:
        raise ExtractionError(f"build_manifest.id illisible ({manifest_path}): {error}") from error
    if not BUILD_ID_PATTERN.match(version):
        raise ExtractionError(f"Version de build inattendue: {version!r}")
    keys = ("Version", "BuildId", "Branch", "RequestedP4ChangeNum", "BuildDateStamp", "Tag")
    return {key: data.get(key) for key in keys}


def ensure_outside_game_dir(path, game_dir):
    path, game_dir = path.resolve(), game_dir.resolve()
    if path.is_relative_to(game_dir) or game_dir.is_relative_to(path):
        raise ExtractionError(f"Sortie refusee: {path} chevauche le dossier du jeu {game_dir}")


def warn_if_game_running():
    try:
        tasklist = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tasklist.exe"
        result = subprocess.run([str(tasklist), "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"AVERTISSEMENT: impossible de verifier les processus ({error})")
        return
    running = {line.split('","')[0].strip('"').lower() for line in result.stdout.splitlines() if line}
    for name in WATCHED_PROCESSES:
        if name in running:
            print(f"AVERTISSEMENT: {name} tourne - une mise a jour pourrait modifier Data.p4k pendant la lecture.")


def run_tool(args, timeout, cwd=None):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, cwd=cwd, check=False)
    if result.returncode != 0:
        raise ExtractionError(f"{Path(args[0]).name} rc={result.returncode}: {result.stderr.strip()[-500:]}")
    return result.stdout


def starbreaker_version():
    if not STARBREAKER.is_file():
        raise ExtractionError(f"starbreaker introuvable: {STARBREAKER}")
    return run_tool([str(STARBREAKER), "--version"], 60).strip().removeprefix("starbreaker ").strip()


def parse_listing(stdout):
    """starbreaker `p4k list` prints `Data\\Game2.dcb<TAB>size`; other lines are messages."""
    entries = {}
    for line in stdout.splitlines():
        path, sep, size = line.rpartition("\t")
        if sep and size.strip().isdigit():
            entries[path.replace("\\", "/")] = int(size)
    return entries


def plan_entries(p4k):
    planned = {}
    for pattern in WHITELIST:
        stdout = run_tool([str(STARBREAKER), "p4k", "list", "--p4k", str(p4k), "--filter", pattern], LIST_TIMEOUT_S)
        matches = parse_listing(stdout)
        if not matches:
            raise ExtractionError(f"Aucune entree du p4k ne correspond a {pattern}")
        planned.update(matches)
    return planned


def check_budget(planned_bytes, free_bytes):
    if planned_bytes > MAX_PLANNED_BYTES:
        raise ExtractionError(f"Taille prevue {planned_bytes / 1024**3:.2f} Go > plafond de 15 Go")
    if planned_bytes > free_bytes:
        raise ExtractionError(f"Espace libre insuffisant: {free_bytes / 1024**3:.2f} Go pour {planned_bytes / 1024**3:.2f} Go")


def free_space(path):
    existing = path
    while not existing.exists():
        existing = existing.parent
    return shutil.disk_usage(existing).free


def sha256_of(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def extract_entry(p4k, entry, expected_size, staging):
    """Extract one exact p4k path with starbreaker, falling back to unp4k; return the tool used."""
    target = staging / entry
    attempts = [
        ("starbreaker", [str(STARBREAKER), "p4k", "extract", "--p4k", str(p4k), "-o", str(staging), "--filter", entry]),
        ("unp4k", [str(UNP4K), str(p4k), entry]),  # unp4k writes into its working directory
    ]
    for tool, args in attempts:
        if tool == "unp4k" and not UNP4K.is_file():
            print(f"AVERTISSEMENT: repli unp4k indisponible ({UNP4K} absent)")
            break
        try:
            run_tool(args, EXTRACT_TIMEOUT_S, cwd=staging)
        except (ExtractionError, subprocess.TimeoutExpired) as error:
            print(f"AVERTISSEMENT: {tool} a echoue sur {entry}: {error}")
        # starbreaker exits 0 even when nothing matched, so the file on disk is the only proof.
        if target.is_file() and target.stat().st_size == expected_size:
            return tool
        target.unlink(missing_ok=True)
        print(f"AVERTISSEMENT: {tool} n'a pas produit {entry} a la bonne taille")
    raise ExtractionError(f"Echec d'extraction de {entry}")


def p4k_fingerprint(p4k):
    stat = p4k.stat()
    return stat.st_size, stat.st_mtime_ns


def extract_all(p4k, planned, staging):
    before = p4k_fingerprint(p4k)
    files = []
    for entry, size in sorted(planned.items()):
        print(f"Extraction de {entry} ({size / 1024**2:.1f} Mo)...")
        tool = extract_entry(p4k, entry, size, staging)
        files.append({"p4k_path": entry, "path": entry, "size": size, "sha256": sha256_of(staging / entry), "tool": tool})
    if p4k_fingerprint(p4k) != before:
        raise ExtractionError("Data.p4k a change pendant l'extraction (mise a jour ?) - relance avec --force")
    produced = {path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file()}
    if produced != set(planned):
        raise ExtractionError(f"Fichiers hors liste blanche produits: {sorted(produced - set(planned))}")
    return files


def build_manifest(channel, build, p4k, files, tool_versions):
    size, mtime_ns = p4k_fingerprint(p4k)
    used = sorted({entry["tool"] for entry in files})
    return {
        "channel": channel,
        "build": build,
        "source": {"p4k": str(p4k), "size": size,
                   "mtime": datetime.datetime.fromtimestamp(mtime_ns / 1e9, datetime.UTC).isoformat()},
        "tools": {name: tool_versions[name] for name in used},
        "extracted_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "files": files,
    }


def print_plan(planned, build_dir, already_done):
    for entry, size in sorted(planned.items()):
        print(f"  {entry:<45} {size:>14,} o  ({size / 1024**2:.1f} Mo)")
    total = sum(planned.values())
    print(f"  Total: {total:,} o ({total / 1024**2:.1f} Mo)")
    print(f"Sortie: {build_dir}" + ("  [deja extrait - rien a faire sans --force]" if already_done else ""))


def main(argv):
    args = parse_args(argv)
    channel_dir = args.game_dir / args.channel
    p4k = channel_dir / "Data.p4k"
    if not p4k.is_file():
        raise ExtractionError(f"Data.p4k introuvable: {p4k}")
    build = read_build(channel_dir)
    build_dir = (args.out_root / args.channel / build["Version"]).resolve()
    staging = build_dir.with_name(build_dir.name + ".partial")
    ensure_outside_game_dir(build_dir, args.game_dir)
    warn_if_game_running()
    tool_versions = {"starbreaker": starbreaker_version(), "unp4k": UNP4K_VERSION}
    print(f"Build {build['Version']} ({build['Branch']}), starbreaker {tool_versions['starbreaker']}")

    planned = plan_entries(p4k)
    already_done = (build_dir / "manifest.json").is_file()
    print_plan(planned, build_dir, already_done)
    check_budget(sum(planned.values()), free_space(build_dir))
    if args.dry_run:
        print("Dry-run: rien n'a ete ecrit.")
        return 0
    if already_done and not args.force:
        print("Build deja extrait; utilise --force pour refaire.")
        return 0

    # Staging + rename: a crash never leaves a half-written build that looks complete.
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    files = extract_all(p4k, planned, staging)
    manifest = build_manifest(args.channel, build, p4k, files, tool_versions)
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if build_dir.exists():
        shutil.rmtree(build_dir)
    staging.rename(build_dir)
    for entry in files:
        print(f"OK {entry['path']}  {entry['size']:,} o  sha256={entry['sha256']}  ({entry['tool']})")
    print(f"Manifest: {build_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ExtractionError as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        sys.exit(1)
