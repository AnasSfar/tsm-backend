# Contexte Collector Apple Music

## Role

`collectors/apple_music` collecte les charts Apple Music Taylor Swift:

- global songs;
- Taylor Swift artist page;
- country songs/albums/music-videos via `country_all.py`;
- genre songs/albums via `genre_all.py`;
- images de snapshots et cards pays;
- export JSON puis upload R2.

Le pipeline ne poste pas sur X. `--no-post` existe dans le runner mais n'est pas
un controle de publication Twitter.

## Entrypoint

Commande principale:

```powershell
python .\collectors\apple_music\run_apple_music.py
```

Le runner lance, avec le meme `--scraped-at`:

1. `global.py`
2. `ts_page.py`
3. `country_all.py`
4. `genre_all.py`
5. `scripts/export_apple_music.py`
6. `generate_country_card_images.py`
7. `generate_snapshot_images.py`
8. `scripts/upload_ap_r2.py`, sauf `UPLOAD_TO_R2=0`

Options runner:

- `--no-post`: flag legacy sans effet Twitter reel.
- `--no-images`: saute les images.
- `--force-images`: regenere les images.

## Scripts

Scripts combines quotidiens:

- `country_all.py`: songs + albums + music-videos par pays en un appel quand
  possible; fallback per-type si l'appel combine est rejete.
- `genre_all.py`: songs + albums par genre.

Scripts legacy/manuels:

- `country_charts.py`
- `country_albums.py`
- `music_video_charts.py`
- `genre_charts.py`
- `genre_album_charts.py`
- `global_albums.py`
- `top_music_videos.py`

Outils partages:

- `core/http.py`: session HTTP/retries.
- `core/token.py`: MusicKit token cache/refresh.
- `core/csv_utils.py`: previous ranks et rewrite idempotent.
- `core/export.py`: lancement optionnel export.
- `core/storefronts.py`: decouverte storefronts.
- `core/r2.py`: upload R2 si change.

## Donnees et sorties

CSV principaux dans `db/`:

- `apple_music_ts_top_songs.csv`
- `apple_music_global.csv`
- `apple_music_genre_charts.csv`
- `apple_music_country_charts.csv`
- `apple_music_country_albums.csv`
- `apple_music_genre_album_charts.csv`
- `apple_music_music_video_charts.csv`
- `apple_music_ts_top_videos.csv`

Exports:

- `runtime/exports/web/site/data/applemusic.json`
- `runtime/exports/web/site/data/applemusic_history.json`
- objets R2 `apple-music/snapshots/`
- objets R2 `history-by-song/`

Les CSV sont la source complete. Les JSON frontend peuvent etre fenetres ou
precalcules.

## Variables

- `APPLE_MUSIC_COUNTRIES`: limite les storefronts (`us,fr,gb,...`).
- `APPLE_MUSIC_CHART_LIMIT`: profondeur des charts, souvent 200.
- `APPLE_MUSIC_WORKERS`: concurrence.
- `APPLE_MUSIC_TIMEOUT`
- `APPLE_MUSIC_RETRY_TOTAL`
- `APPLE_MUSIC_RETRY_BACKOFF`
- `APPLE_MUSIC_SKIP_EXPORT`: mis a `1` par le runner pendant les sous-scripts.
- `UPLOAD_TO_R2=0`: skip upload R2.

Token cache:

```text
collectors/apple_music/tools/json/apple_music_token.json
```

## Regles data

- Donnee absente ou ambigue: bloquer/loguer, ne pas publier comme complete.
- `previous_rank` doit venir du dernier snapshot d'un jour distinct precedent,
  pas d'un rerun du meme jour.
- `rewrite_for_snapshot` doit rester idempotent par `scraped_at`.
- `apple_music_id` prime sur le titre; ne pas elargir a "meme titre = meme
  chanson" pour les donnees modernes.
- Comme l'historique Apple Music est incomplet, une chanson deja sortie ne doit
  pas etre marquee `NEW` par inference. Le code limite NEW aux releases
  recentes selon la fenetre explicite.

## Commandes utiles

Run complet:

```powershell
python .\collectors\apple_music\run_apple_music.py
```

Sandbox sans export:

```powershell
$env:TSM_DATA_DATE="2020-01-01"
$env:APPLE_MUSIC_SKIP_EXPORT="1"
$env:PYTHONPATH="$PWD;$PWD\collectors\apple_music"
python .\collectors\apple_music\country_all.py --countries fr us --scraped-at 2020-01-01T12:00:00
```

Export/upload:

```powershell
python .\scripts\export_apple_music.py
python .\scripts\upload_ap_r2.py --dry-run
```

## Highlights Charts Gallery

Depuis 2026-07-28, `run_apple_music.py` appelle en best-effort (jamais
bloquant) `scripts/generate_home_highlights.py --quiet` juste apres
`maybe_upload_to_r2()`. Regenere `cache/home_highlights.json` et
`cache/version.json` sur R2 (lus par `tsm-frontend/api`).

## Pieges

- Les imports `from core...` dependent du `PYTHONPATH` injecte.
- `TSM_DATA_DATE` et chemins de config peuvent etre evalues a l'import.
- Un subset de pays sur la vraie date peut produire un snapshot partiel.
- Un changement de schema CSV doit etre reporte dans export, upload R2, images
  et frontend si necessaire.
