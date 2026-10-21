"""Phase 1: extract the DataCore, English localization and PU object containers from Star Citizen's Data.p4k.

The game folder is read-only: Data.p4k is read in place, nothing is ever written there.
Usage: python -m scripts.extract [--dry-run] [--force] [--game-dir DIR] [--channel LIVE]
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

ROOT = Path(__file__).resolve().parents[1]  # GAME_EXTRACT, one level above scripts/
STARBREAKER = ROOT / "tools" / "starbreaker-cli-v0.3.2-windows-x86_64" / "starbreaker.exe"
UNP4K = ROOT / "tools" / "unp4k-suite-win-x64-v4.0.87" / "publish" / "unp4k.exe"
# unp4k has no --version flag: release tag from the zip name, build from the exe ProductVersion.
UNP4K_VERSION = "v4.0.87 (unp4k.exe 1.0.0+b492ab14d262)"

# Each pattern must match at least one p4k entry; nothing outside these leaves the p4k.
# Object containers (.socpak) place item banks and freight elevators in each location (read by storage_locations.py).
WHITELIST = ("Data/Game*.dcb", "Data/Localization/english/global.ini", "Data/ObjectContainers/PU/**/*.socpak")
BATCH_SIZE = 150  # exact paths per starbreaker call; keeps the regex far below the Windows command-line limit
REGEX_META = set(".+*?()|[]{}^$")
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
    """Whitelist pattern -> {p4k path: size}; every pattern must match."""
    planned = {}
    for pattern in WHITELIST:
        stdout = run_tool([str(STARBREAKER), "p4k", "list", "--p4k", str(p4k), "--filter", pattern], LIST_TIMEOUT_S)
        matches = parse_listing(stdout)
        if not matches:
            raise ExtractionError(f"Aucune entree du p4k ne correspond a {pattern}")
        planned[pattern] = matches
    return planned


def path_regex(paths):
    """Exact, case-insensitive match of p4k paths; the p4k stores backslashes, callers pass slashes."""
    separator = "[\\\\/]"
    escaped = ("".join(separator if char in "/\\" else ("\\" + char if char in REGEX_META else char) for char in path)
               for path in paths)
    return "(?i)^(?:" + "|".join(escaped) + ")$"


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


def extract_batches(p4k, planned, staging):
    """One starbreaker call per BATCH_SIZE exact paths; a failed batch is only reported (its entries are retried alone)."""
    entries = sorted(planned)
    for start in range(0, len(entries), BATCH_SIZE):
        batch = entries[start:start + BATCH_SIZE]
        args = [str(STARBREAKER), "p4k", "extract", "--p4k", str(p4k), "-o", str(staging), "--regex", path_regex(batch)]
        try:
            run_tool(args, EXTRACT_TIMEOUT_S, cwd=staging)
        except (ExtractionError, subprocess.TimeoutExpired) as error:
            print(f"AVERTISSEMENT: lot {start // BATCH_SIZE + 1} en echec, reprise fichier par fichier: {error}")
        print(f"  {min(start + BATCH_SIZE, len(entries)):,} / {len(entries):,} fichiers")


def extract_all(p4k, planned, staging):
    before = p4k_fingerprint(p4k)
    extract_batches(p4k, planned, staging)
    files = []
    for entry, size in sorted(planned.items()):
        target = staging / entry
        if target.is_file() and target.stat().st_size == size:
            tool = "starbreaker"
        else:  # missing or truncated after its batch: retry alone, with the unp4k fallback
            tool = extract_entry(p4k, entry, size, staging)
        files.append({"p4k_path": entry, "path": entry, "size": size, "sha256": sha256_of(target), "tool": tool})
    if p4k_fingerprint(p4k) != before:
        raise ExtractionError("Data.p4k a change pendant l'extraction (mise a jour ?) - relance avec --force")
    # Lower-cased: Windows folds p4k folders that differ only by case (Outpost/ and outpost/) into one.
    produced = {path.relative_to(staging).as_posix().lower() for path in staging.rglob("*") if path.is_file()}
    expected = {entry.lower() for entry in planned}
    if produced != expected:
        raise ExtractionError(f"Fichiers hors liste blanche produits: {sorted(produced - expected)[:10]}")
    return files


def build_manifest(channel, build, p4k, files, tool_versions):
    size, mtime_ns = p4k_fingerprint(p4k)
    used = sorted({entry["tool"] for entry in files})
    return {
        "channel": channel,
        "build": build,
        "whitelist": list(WHITELIST),
        "source": {"p4k": str(p4k), "size": size,
                   "mtime": datetime.datetime.fromtimestamp(mtime_ns / 1e9, datetime.UTC).isoformat()},
        "tools": {name: tool_versions[name] for name in used},
        "extracted_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "files": files,
    }


def print_plan(by_pattern, build_dir, already_done):
    for pattern, matches in by_pattern.items():
        size = sum(matches.values())
        label = next(iter(matches)) if len(matches) == 1 else pattern
        count = "" if len(matches) == 1 else f", {len(matches):,} fichiers"
        print(f"  {label:<45} {size:>14,} o  ({size / 1024**2:.1f} Mo{count})")
    total = sum(sum(matches.values()) for matches in by_pattern.values())
    print(f"  Total: {total:,} o ({total / 1024**2:.1f} Mo)")
    print(f"Sortie: {build_dir}" + ("  [deja extrait - rien a faire sans --force]" if already_done else ""))


def is_done(build_dir):
    """A build is done only if its manifest was written with the current whitelist."""
    manifest_path = build_dir / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        whitelist = json.loads(manifest_path.read_text(encoding="utf-8")).get("whitelist")
    except (OSError, ValueError) as error:
        raise ExtractionError(f"manifest.json illisible ({manifest_path}): {error}") from error
    if whitelist != list(WHITELIST):
        print(f"Manifest existant fait avec une autre liste blanche ({whitelist}): extraction a refaire.")
        return False
    return True


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

    by_pattern = plan_entries(p4k)
    planned = {entry: size for matches in by_pattern.values() for entry, size in matches.items()}
    already_done = is_done(build_dir)
    print_plan(by_pattern, build_dir, already_done)
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
    singles = [entry for entry in files if "/ObjectContainers/" not in entry["path"]]
    for entry in singles:
        print(f"OK {entry['path']}  {entry['size']:,} o  sha256={entry['sha256']}  ({entry['tool']})")
    containers = [entry for entry in files if entry not in singles]
    print(f"OK {len(containers):,} conteneurs, {sum(entry['size'] for entry in containers):,} o "
          "(taille et SHA-256 de chacun dans le manifest)")
    print(f"Manifest: {build_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ExtractionError as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        sys.exit(1)
