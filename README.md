# SCGE — Star Citizen Game Extract

Extrait de Star Citizen (`Data.p4k`) les objets, commodités, fabricants, lieux et lieux de stockage (item bank / fret), en JSON.

## Démarrage

```sh
cp config.example.ini config.ini      # puis adapter le chemin du jeu
python pipeline.py --dry-run          # ce qui serait lu dans Data.p4k, rien n'est écrit
python pipeline.py                    # tout le pipeline ; --force refait les phases 1 et 2A
```

Prérequis : Python 3.11+, Windows, et dans `tools/` (non versionné) **starbreaker CLI 0.3.2** et **unp4k 4.0.87** (repli). Sans `config.ini`, `config.example.ini` est utilisé.

## Pipeline

| Phase | Script | Lit | Produit |
|---|---|---|---|
| 1 | `extract` | `Data.p4k` (liste blanche) | `raw_extraction/<canal>/<build>/` : `Game2.dcb`, `global.ini`, conteneurs `.socpak`, `manifest.json` |
| 2A | `decode` | `Game*.dcb` | `data_json/<build>/raw/` (DataCore en JSON) |
| 2A | `explore` | `raw/` | `exploration.md` |
| 2B | `normalize` | `raw/`, `global.ini` | `catalog.json`, `lists/{items,commodities,manufacturers,locations}.json`, `wikelo.json`, `report.md` |
| 2C | `storage_locations` | `raw/`, `.socpak` | `storage_locations.json`, `storage/{systems,bodies,locations}.json`, `storage_report.md` |

Fin de run : `pipeline_manifest.json` (SHA-256 de chaque sortie JSON). Chaque étape saute ce qui est déjà fait pour le build ; un nouveau build a ses propres dossiers.

Une étape seule : `python -m scripts.<nom> --help`.

## Architecture

```
pipeline.py            enchaîne les étapes, crée raw_extraction/ et data_json/
config.ini             chemins locaux (non versionné) ; modèle : config.example.ini
scripts/
  config.py            lecture de config.ini, sinon config.example.ini
  extract.py           phase 1, seul lecteur de Data.p4k
  decode.py            phase 2A, export JSON du DataCore
  explore.py           phase 2A, carte des types de records
  normalize.py         phase 2B, catalogue + listes
  catalog_rules.py     règles de catégorie des objets
  storage_locations.py phase 2C, lieux avec item bank / fret
  containers.py        lecture des .socpak (partagé)
  explore_storage.py   outil d'exploration du stockage (hors pipeline)
tests/                 pytest
```

## Technique

- **Lecture seule** : le dossier du jeu n'est jamais écrit, `Data.p4k` n'est jamais copié. La phase 1 avertit si le jeu ou le launcher tourne et plafonne l'extraction à 15 Go.
- **Liste blanche** : `Data/Game*.dcb`, `Localization/english/global.ini`, `ObjectContainers/PU/**/*.socpak`. Les chemins exacts sont passés à starbreaker par lots de 150 (regex).
- **GUID** : chaque record garde son GUID du DataCore. Les listes `lists/` et `storage/` sont des découpes de `catalog.json` reliées par GUID (`systemGuid`, `bodyGuid`, `parentGuid`).
- **Stockage** : un lieu stocke s'il a un item bank (`Itembank_*` dans les `_editor.xml` des `.socpak`) ou du fret (console, plateforme ou amenity « Freight Elevator »). Les sous-lieux remontent au lieu dont le parent est un astre. Les kiosques `ExtractOnly` et les lieux indécidables (`unknown`) sont exclus.
- **Commodités = minerais minables** : un `ResourceType` dont la `qualityDistribution` est rangée dans `shipmineables/` (minage vaisseau → `unit: scu`, `quality: 500` par défaut) ou `fpsmineables/` (minage à la main → `unit: unit`, `quality: null`, propre à chaque caillou), plus leur version raffinée (`refinedVersion`, même unité). Le reste (biens, gaz, récoltes `harvestables/`) est écarté. Les caisses SCU du DataCore ne prouvent rien : il en génère même pour la Carinite.
- **`.socpak`** : ce sont des zip dont les en-têtes locaux utilisent `\` ; `containers.read_member` contourne ce défaut.
- **Reproductible** : à données égales, sorties identiques octet pour octet (clés triées, listes triées par GUID).

## Tests

```sh
pytest            # ou un seul : python -m tests.test_config
ruff check .
```
