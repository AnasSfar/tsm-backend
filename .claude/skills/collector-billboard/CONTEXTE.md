# Contexte Collector Billboard / TayBoard

## Role

`collectors/billboard` couvre deux familles:

- scrape Billboard officiel Taylor Swift chart history;
- generation des charts internes TayBoard / Swift Top a partir des donnees TSM
  (streams Spotify, charts Spotify, Apple Music, metadata discographie).

## Scripts principaux

Scrape Billboard:

- `scrape_billboard.py`: scrape Playwright de pages Billboard Taylor Swift,
  ecrit history CSV/snapshots, puis upload R2 si autorise.

TayBoard / Swift Top:

- `swift_top_100.py`: moteur principal Swift Top 100 weekly.
- `swift_top_seperate.py`: variant not-combined songs chart.
- `swift_top_combined.py`: variant combine.
- `swift_top_albums.py`
- `swift_top_album.py`
- `swift_top_era.py`
- `swift_top_100_image.py`
- `tayboard_explainer_images.py`

## Commandes

Swift Top 100:

```powershell
python .\collectors\billboard\swift_top_100.py --date 2026-04-03
python .\collectors\billboard\swift_top_100.py --dry-run
python .\collectors\billboard\swift_top_100.py --backfill
python .\collectors\billboard\swift_top_100.py --rebuild-index
python .\collectors\billboard\swift_top_100.py --generate-songs
```

Options recurrentes:

- `--date YYYY-MM-DD`
- `--backfill`
- `--force`
- `--streams-csv`
- `--rebuild-index`
- `--generate-songs`
- `--dry-run`
- `--skip-r2`
- `--skip-images`

Scrape Billboard:

```powershell
python .\collectors\billboard\scrape_billboard.py
```

## Donnees lues

Swift Top lit notamment:

- `db/streams_history.csv`
- `db/streams_history_full.csv`
- `db/charts_history_global.csv`
- `db/charts_history_us.csv`
- `db/charts_history_uk.csv`
- `db/charts_history_fr.csv`
- Spotify worldwide snapshots via `core.data_paths`
- Apple Music CSV/snapshots
- discographie DB

Le code prefere les snapshots worldwide Spotify quand ils existent, car ils
contiennent toutes les apparitions pays; les CSV regionaux servent de fallback.

## Donnees ecrites

Histories:

- `db/swift_top_100_history.csv`
- `db/swift_top_songs_history.csv`
- variants selon `CHART_SLUG`

Exports:

- `runtime/exports/web/site/data/swift_top_100.json`
- snapshots dates `swift_top_100_YYYY-MM-DD.json`
- index `swift_top_100_index.json`
- per-song history JSON selon le script.

Snapshots/images:

```text
snapshots/billboard/YYYY/MM/YYYY-MM-DD/
```

## Regles data

- Ne pas ecrire/upload un snapshot vide.
- Ne pas changer les coefficients ou fallbacks de scoring sans verifier leur
  effet sur l'historique.
- Les semaines avec jours streams manquants peuvent etre estimees uniquement si
  le code le fait explicitement depuis l'historique recent; ne pas inventer une
  estimation manuelle.
- Les mappings track IDs/historical IDs doivent rester explicites.
- En dry-run, ne pas ecrire history/export/R2.

## R2

Les scripts utilisent les helpers `scripts/r2.py` ou fonctions d'upload selon le
chart. `--skip-r2` doit etre prefere pendant debug/backfill local.

## Pieges

- `swift_top_seperate.py` garde une faute dans le nom de fichier; ne pas le
  renommer sans traiter les references.
- `scrape_billboard.py` est network/Playwright et peut etre fragile au DOM.
- Les variants Swift Top partagent le moteur principal via import/module; verifier
  les arguments transmis avant de modifier un wrapper.
