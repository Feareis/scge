# SCGE: Star Citizen Game Extract

SCGE pulls clean, GUID-keyed JSON out of a local Star Citizen install. It produces the item catalog, the mineable commodities, manufacturers, locations, Wikelo trades, and the list of places where you can actually store things (item bank or freight), all with their real English names.

The game ships its data in one 160 GB archive (`Data.p4k`) in formats that were never meant to be read from outside the game. Most of the work here was figuring out *where* each fact actually lives. The code is the smaller part.

> **Tested on:** 4.10.2 LIVE, build `4.10.197.2878`.

---

## What you get

A full run on 4.10.2 LIVE gives you:

| Output | Content | Count (4.10.197.2878) |
|---|---|---|
| `catalog.json` → `lists/items.json` | Weapons, armor, clothing, ship components, tools, consumables… | **8,351** items in 11 categories |
| `lists/commodities.json` | Mineable ores and their refined versions, with unit, quality and mining method | **66** |
| `lists/manufacturers.json` | Manufacturer code + name | **157** |
| `lists/locations.json` | Systems, planets, moons, stations, outposts… with `parent` links | **1,976** |
| `wikelo.json` | Wikelo trades: what he asks for, what he gives back | **71** trades |
| `storage_locations.json` + `storage/*.json` | Places with an item bank and/or freight | **364** of 1,481 listed places |
| `report.md`, `storage_report.md`, `exploration.md` | Human-readable checks and the research notes for that build | n/a |
| `pipeline_manifest.json` | SHA-256 of every JSON output | n/a |

A typical item record looks like this:

```json
{
  "guid": "00126e44-fac3-45a6-b2c4-9bc584e1d0ef",
  "name": "gys_pants_01_01_14",
  "name_en": "Carnifex Armor Legs Brassy",
  "category": "armures",
  "subcategory": "jambes",
  "type": "Char_Armor_Legs",
  "subtype": "Light",
  "manufacturer": "d56a3985-2f29-4095-b2f2-542376cc74f4",
  "size": 1,
  "grade": 1,
  "class": null,
  "unit": "unit"
}
```

Each record keeps its original DataCore GUID. Links between records (`manufacturer`, `parent`, `systemGuid`, `bodyGuid`, `parentGuid`) are GUIDs too, so a consumer can match records by GUID from one patch to the next.

> Heads-up: category and subcategory labels (`armures`, `jambes`…) are currently in **French**, and so are the generated `report.md` files. The labels come from `scripts/catalog_rules.py`.

---

## Requirements

- **Windows** (the game and both extraction tools are Windows-only)
- **Python 3.11+**. Tested on 3.14. The pipeline uses only the **standard library**, with no `pip install` needed to run it.
- A local **Star Citizen install** (LIVE, PTU or EPTU)
- Two community tools, unzipped into `tools/` (not versioned, because they are not ours to redistribute):
  - **starbreaker CLI 0.3.2**: the main tool. It reads `Data.p4k` and exports the DataCore to JSON.
  - **unp4k 4.0.87**: fallback only, for files starbreaker fails to extract.
- Disk space: about **3.5 GB** per build (≈ 0.35 GB of raw files plus ≈ 3.2 GB of decoded JSON). Phase 1 refuses to extract more than 15 GB.
- For development: `pytest` and `ruff`.

Expected layout of `tools/` (these are the default paths in `config.example.ini`):

```
tools/
  starbreaker-cli-v0.3.2-windows-x86_64/starbreaker.exe
  unp4k-suite-win-x64-v4.0.87/publish/unp4k.exe
```

The versions are pinned on purpose. The scripts were written against each tool's real `--help` output, not from memory. If you upgrade a tool, re-check its flags before you trust a run.

---

## Install

```sh
git clone https://github.com/Feareis/SCGE.git
cd SCGE
cp config.example.ini config.ini
```

Then edit `config.ini`:

```ini
[game]
; The folder that contains LIVE/, PTU/... It is read only and never written.
dir = C:\Program Files\Roberts Space Industries\StarCitizen
channel = LIVE

[tools]
starbreaker = tools/starbreaker-cli-v0.3.2-windows-x86_64/starbreaker.exe
unp4k = tools/unp4k-suite-win-x64-v4.0.87/publish/unp4k.exe

[output]
extraction = raw_extraction
data_json = data_json
```

`config.ini` is git-ignored because it holds machine-specific paths. Without it, the pipeline falls back to `config.example.ini`. Relative paths start at the repo root.

---

## Usage

### The whole pipeline, one command

```sh
python pipeline.py --dry-run   # shows what would be read from Data.p4k (files, sizes, output dir) and writes nothing
python pipeline.py             # runs every phase for the current build
python pipeline.py --force     # redoes phases 1 and 2A even if this build is already done
```

You can override the config from the command line with `--game-dir` and `--channel`.

**Close the game and the RSI Launcher first.** Phase 1 warns you if either one is running, because a patch download could change `Data.p4k` while we read it.

The pipeline is incremental. Each step skips work already done for the current build, and a new patch gets its own folders. Re-running on the same build is cheap, and old builds stay where they are.

### One step at a time

Each phase is a standalone module with its own `--help`:

```sh
python -m scripts.extract --dry-run           # phase 1
python -m scripts.decode --dry-run            # phase 2A: measures the export, keeps nothing
python -m scripts.explore                     # phase 2A
python -m scripts.normalize                   # phase 2B
python -m scripts.storage_locations           # phase 2C
python -m scripts.explore_storage --dry-run   # research tool, not part of the pipeline
```

Steps from phase 2 onward take `--build <version>` and default to the latest extracted build.

---

## How the pipeline works

```
Data.p4k ──(1) extract──▶ raw_extraction/<channel>/<build>/
                              Game2.dcb, global.ini, *.socpak, manifest.json
                                   │
                           (2A) decode ──▶ data_json/<build>/raw/        DataCore as JSON (~62k files)
                           (2A) explore ─▶ data_json/<build>/exploration.md
                                   │
                           (2B) normalize ──────────▶ catalog.json, lists/*.json, wikelo.json, report.md
                           (2C) storage_locations ──▶ storage_locations.json, storage/*.json, storage_report.md
                                   │
                                   ▼
                       pipeline_manifest.json (SHA-256 of every output)
```

| Phase | Module | Reads | Writes |
|---|---|---|---|
| 1 | `extract` | `Data.p4k` (whitelist only) | `raw_extraction/<channel>/<build>/`: `Game2.dcb`, `global.ini`, `.socpak` containers, `manifest.json` |
| 2A | `decode` | `Game*.dcb` | `data_json/<build>/raw/` |
| 2A | `explore` | `raw/` | `exploration.md` |
| 2B | `normalize` | `raw/`, `global.ini` | `catalog.json`, `lists/{items,commodities,manufacturers,locations}.json`, `wikelo.json`, `report.md` |
| 2C | `storage_locations` | `raw/`, `.socpak` | `storage_locations.json`, `storage/{systems,bodies,locations}.json`, `storage_report.md` |

Phase 1 is the **only** code that touches `Data.p4k`. Everything after it works from `raw_extraction/`, so you can iterate on normalization as many times as you like without going back to the 160 GB archive.

### Project layout

```
pipeline.py              runs the steps in order and creates raw_extraction/ and data_json/
config.example.ini       config template (copy it to config.ini)
scripts/
  config.py              reads config.ini, falls back to config.example.ini
  extract.py             phase 1, the only reader of Data.p4k
  decode.py              phase 2A, DataCore → JSON
  explore.py             phase 2A, map of record types, fields and name coverage
  normalize.py           phase 2B, catalog and lists
  catalog_rules.py       item type → category / subcategory, plus exclusion rules
  storage_locations.py   phase 2C, places with an item bank or freight
  containers.py          shared .socpak reader
  explore_storage.py     research tool used to find where storage is defined
tests/                   pytest, one file per module
```

---

## The research behind it

None of the rules below came from documentation, because there isn't any. Each one came from digging through a real build, writing the finding down in `exploration.md` / `storage_report.md`, and only then turning it into code. These are the parts most likely to break on a future patch, so here is the reasoning behind each one.

### Finding the right files in a 160 GB archive

- The DataCore file is `Game2.dcb` on recent builds. Phase 1 matches `Data/Game*.dcb` against the archive listing instead of hard-coding the name.
- Only three patterns ever leave the archive: `Data/Game*.dcb`, `Data/Localization/english/global.ini` and `Data/ObjectContainers/PU/**/*.socpak`. That whitelist keeps one build at about 0.35 GB of raw files out of 160 GB.
- starbreaker takes exact paths as a regex. With about 16k container paths, a single call would blow past the Windows command-line limit, so they are sent in batches of 150.
- The build id comes from the channel folder (`4.10.197.2878`, branch `sc-alpha-4.10.2`) and is recorded in every manifest, together with the SHA-256 of each extracted file.

### Names: `global.ini` is not complete

`global.ini` has about 91k keys, but only **68 %** of raw item records resolve to an English name (18,090 / 26,531). Commodities (204 / 207) and locations (1,980 / 2,132) resolve much better. The full breakdown by category is in `exploration.md`, section 6, so you can see exactly where names go missing before you decide what to exclude.

### What counts as a "commodity"

This took a few attempts, and the git history shows each one. The DataCore has hundreds of `ResourceType` records: trade goods, gases, harvestables, ores… In SCGE, a commodity is **something you mine**:

- A `ResourceType` counts if its `qualityDistribution` lives under `shipmineables/` or `fpsmineables/`.
  - Ship-mined ores are sold by the SCU: `unit: "scu"`, `quality: 500` by default.
  - Hand-mined gems are counted per piece: `unit: "unit"`, `quality: null` (each rock has its own quality).
- Their **refined** versions (`refinedVersion`, e.g. Gold, Laranite) count too, with the same unit, and `mining: []`.
- `mining` (`hand`, `ship`, `vehicle`) is read from the actual rocks in `entities/mineable/`. Each rock's `MineableParams.globalParams` (`miningglobalparamsfps|ship|groundvehicle`) gives the mining mode, and its composition gives the ores it contains. Destructible scenery (`entities/destruction/`) and test params are skipped.
- Goods, gases and harvestables (`harvestables/`) are left out.
- A false lead worth recording: the DataCore has **SCU crate entities even for Carinite**, so a crate proves nothing about how a commodity is traded. It was tried and dropped.

### Where can you store things?

This was the hardest question, and the DataCore alone can't answer it. A place counts as storage if it has:

- an **item bank**: an `Itembank_*` entity placed in the `_editor.xml` of that place's `.socpak` object container, or
- **freight**: a freight console, a cargo platform, or a "Freight Elevator" amenity on the location record.

Things learned along the way:

- `.socpak` files are ZIP archives whose local headers use `\` as the path separator, which the stdlib `zipfile` doesn't expect. `containers.read_member` works around that.
- Sub-locations (clinics, hospitals, zones) are rolled up into the nearest place whose parent is a celestial body. The result is one entry per place a player would actually pick: 39 bodies, 1,481 listed places and 456 sub-locations account for all 1,976 locations.
- `ExtractOnly` kiosks are excluded.
- If no container is linked and no freight amenity exists, the place is `unknown`. Unknown places are excluded rather than guessed. In 4.10.2 that is 527 places, 365 of them in Nyx.

Final result for 4.10.2: **364 storage places**, of which 209 have an item bank and freight, 15 have an item bank only, and 140 have freight only. The split by system is Stanton 237, Pyro 79 and Nyx 48.

### Design rules

- **The game folder is never written.** `Data.p4k` is read in place and never copied, and any output path inside the game folder is refused.
- **Reproducible output.** With the same input you get byte-identical output: keys are sorted and lists are sorted by GUID. `pipeline_manifest.json` lets you check this with two hashes.
- **Every check is written down.** `report.md` checks for unique GUIDs, existing manufacturers, no orphan locations, no empty names and sorted lists. `storage_report.md` checks that every location is accounted for exactly once and that every `true` has evidence.

---

## Development

```sh
pytest                              # the whole suite (32 tests)
python -m pytest tests/test_normalize.py
ruff check .                        # must stay at zero warnings
```

`pyproject.toml` puts the repo root on `sys.path`, so a bare `pytest` can import `scripts.*`.

Conventions:

- Small, atomic commits with prefixes: `feat:`, `fix:`, `refactor:`, `docs:`.
- New game-data rules start as research, not code. Explore the build first (`explore.py`, `explore_storage.py`), write down what you found and why, then encode the rule and add a test.
- `raw_extraction/`, `data_json/` and `tools/` are git-ignored: they are large, reproducible and third-party.

### When a new patch drops

1. Close the game and the launcher.
2. Run `python pipeline.py --dry-run` and check that the build id and file list look sane.
3. Run `python pipeline.py`.
4. Read `report.md` and `storage_report.md`. Every check should say `OK`.
5. Compare the counts with the previous build. A big jump or drop usually means CIG moved something, and the matching rule above needs another look.

---

## Disclaimer

SCGE is an unofficial fan project. It is not affiliated with or endorsed by Cloud Imperium Games or Roberts Space Industries. Star Citizen and all game data belong to CIG. This repository contains no game files: you run it against your own install.
