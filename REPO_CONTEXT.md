# TSM Backend — Contexte du repo

> Carte complète du repo : chaque dossier/fichier, son rôle, et les options de lancement des scripts.
> ⚠️ **Doc vivante — mise à jour OBLIGATOIRE** : toute IA qui ajoute/modifie/déplace un script ou change ses options doit mettre à jour ce fichier dans la même session.
> Repo frère : `tsm-frontend` (site React + API FastAPI sur Vercel). Flux global :
> **tsm-backend (collecte locale, Task Scheduler) → R2 (`taylor-data`) → API tsm-frontend → site React.**

## Arbre général

```
tsm-backend/
├── tsm/                    ← CLI unifiée : python -m tsm <commande>
├── collectors/             ← tout le code de collecte
│   ├── spotify/
│   │   ├── streams/        ← pipeline streams quotidien (update_streams.py, 114 Ko, cœur du repo)
│   │   ├── charts/         ← charts par région (global, fr, us, uk, worldwide, artists_global)
│   │   ├── core/           ← modules partagés Spotify (twitter.py, history, download, notify…)
│   │   └── website/        ← ancien export site (legacy)
│   ├── apple_music/        ← charts Apple Music (pays, genres, albums, vidéos)
│   ├── billboard/          ← scrape Billboard + TayBoard (Swift Top 100/albums/eras)
│   ├── youtube/            ← vues YouTube
│   ├── comp/               ← composants visuels partagés pour les images générées
│   └── website/            ← miroir legacy (ne pas toucher)
├── db/                     ← données métier source de vérité (CSV/JSON, discography/)
├── data/                   ← archives datées + _archive/ + _tmp/
├── snapshots/              ← snapshots opérationnels datés écrits par les collectors
├── runtime/                ← sorties runtime (exports/web/ avant upload R2, tmp, logs) — non versionné
├── scripts/                ← outils one-shot/maintenance (backfills, R2, enrichissement)
├── dev/                    ← scripts ad-hoc de debug/vérification (jetables)
├── docs/                   ← runbook.md + data-layout-audit.md
├── website/                ← ⚠️ site statique LEGACY — INTERDIT sauf demande explicite
├── .github/workflows/      ← workflows GitHub (peu utilisés : tout tourne en local)
├── .claude/skills/         ← skills Claude Code (tsm-map, pipeline-ops, data-rules, image-gen, deploy, admin-work, style-rules)
├── run_daily.bat           ← launcher : python -m tsm daily
└── run_all_charts.bat      ← launcher : python -m tsm collect charts
```

---

## 1. `tsm/` — CLI unifiée

| Fichier | Rôle |
|---|---|
| `cli.py` | Toute la CLI (argparse + dispatch vers les scripts) |
| `__main__.py` | Permet `python -m tsm` |

Commandes (les args inconnus sont **passés tels quels** au script sous-jacent) :

```powershell
python -m tsm daily [YYYY-MM-DD] [--no-post|--post]        # charts PUIS streams
python -m tsm collect streams [YYYY-MM-DD] [--no-post|--post]   # → update_streams.py
python -m tsm collect charts  [YYYY-MM-DD] [--no-post|--post]   # → run_all_charts.py
python -m tsm collect apple-music [--no-post]                   # → run_apple_music.py
python -m tsm collect youtube [YYYY-MM-DD] [--no-post] [--force] # → update_youtube.py
python -m tsm export web [--date YYYY-MM-DD] [--dry-run]        # → scripts/export_for_web.py
python -m tsm audit data [--write]        # audit du layout → docs/data-layout-audit.*
python -m tsm migrate layout --dry-run|--apply   # archive .bak + website/ avec checksums
```

---

## 2. `collectors/spotify/streams/` — pipeline streams (le cœur)

### `update_streams.py` (script principal, ~114 Ko)
Scrape les streams totaux de tout le catalogue, calcule les daily, écrit `streams_history.csv`, exporte pour le web, upload R2, poste sur Twitter, commit git. Log : `run_update_streams.log` à côté.
Périmètre actif : `db/discography/albums/*.json`, `songs.json`, `features.json`, `misc.json` dès qu'une entrée a une URL Spotify ; les IDs listés dans `historical_track_ids` et les entrées `exclude_from_stream_collection=true` restent exclus du périmètre actif. Les entrées `music_track=false` restent collectables mais sont traitées comme `chart_extra` / hors stats publiques.

```
python update_streams.py                     # run normal pour hier
python update_streams.py YYYY-MM-DD          # run normal pour une date précise
  --no-post          pipeline complet mais aucun post Twitter
  --dry-run          scrape seulement, aucune écriture
  --debug-daily [D]  retente les tracks inachevées, écrit l'history, pas de Twitter/git/forecast/images
  --debug-total D    re-scrape et remplace les TOTAUX d'une date, recalcule les daily
  --local-test D     force le re-scrape même si la date existe ; aucune écriture history/R2/Twitter/git
  --test [D]         rejoue les scripts de finalisation sur l'history existante (rien d'externe)
  --post-only S[,S]  poste uniquement les étapes choisies depuis l'history existante (pas de scrape/export/git ;
                     guards, locks et règles de jour respectés) : top-eras (hors week-end), all-albums (fusionné/no-op), top45, recap,
                     best-day-since, debut, gainers, album-updates ; avec --no-post = images seulement
  --throwback --throwback-action announced|released --throwback-event "..."   # thread throwback
  --reset-last-date  supprime les lignes de la dernière date avant de tourner
  --reset-date D     supprime les lignes de cette date avant de tourner
  --force            (avec --throwback) régénère les images ; sinon force le reprocess
  --quiet / --verbose / --help
```

### Scripts du dossier

| Fichier | Rôle / lancement |
|---|---|
| `best_day_since.py` | Notes « meilleur jour depuis X » depuis l'history. `date` (déf. dernière), `--limit 50`, `--min-days 21`, `--include-extras`, `--output`, `--no-write` |
| `spotlight.py` | Image « spotlight » carrée d'une chanson + post. `title` ou `--url`, `date`, `--no-post`, `--no-scrape`, `--combined/--no-combined`, `--compare yesterday\|last-week`, `--highlight vs\|total`, `--account flame\|tsm`, `--session` |
| `fix_one.py` | Corrige manuellement le daily (ou total avec `--total`) d'UNE chanson pour UN jour. `song day value`, `--dry-run`, `--track-id`, `--pick N`, `--all-matches`, `--no-git` |
| `fix_streams.py` | Re-scrape le total actuel de toutes les chansons et corrige l'history |

### `streams/tools/scripts/` — outillage du pipeline

| Fichier | Rôle / lancement |
|---|---|
| `history_store.py` | Lecture/écriture de `streams_history.csv` (module central) |
| `spotify_api.py` | Client API/scraping Spotify (GraphQL getTrack) |
| `finalize_update.py` | Étapes de finalisation post-collecte (exports, indexes) |
| `reconcile_gap_catchup.py` | **Rattrapage d'un jour manqué** (raison `manual_trusted`). `date`, `--track-ids a,b` / `--album X` / `--all-pending`, `--apply` (sinon dry-run), `--rapidapi-budget 500`, `--out CSV`, `--verbose`, `--relabel-track ID` |
| `gap_estimate.py` | Estimateur re-jouable pour les tracks `chart_extra=true`. `--track-ids`, `--apply` |
| `catalog_gap_report.py` | Rapport JSON des trous du catalogue. `--date` |
| `seed_streams.py` | Seed initial des totaux depuis songs.json/albums. `--dry-run`, `--track-id`, `--new-only` |
| `forecast_milestones.py` | Prévisions de milestones de streams |
| `generate_streams_image.py` | PNG top daily streams (top configurable, déf. 10) |
| `generate_albums_image.py` | PNG « Top Albums by Daily Streams » |
| `generate_album_update_image.py` | PNG « Album Daily Update » pour un album |
| `generate_weekend_streams_image.py` | PNG récap week-end |
| `post_streams_twitter.py` | Poste l'image top streams. `date`, `--no-post`, `--top-n 15` |
| `post_albums_twitter.py` | Poste l'image « Albums on Spotify » / top eras, hors week-end |
| `post_all_albums_thread.py` | Ancien thread avec tous les albums, fusionné dans top eras en finalisation ; direct limité lundi/vendredi. `date`, `--no-post` |
| `post_best_day_since_twitter.py` | Posts « best day since » avec spotlights. `date`, `--limit 5`, `--min-days`, `--album-limit 1`, `--no-albums`, `--no-recap`, `--only-track ID`, `--exclude-tracks`, `--force`, `--post-spacing-seconds` |
| `post_debut_releases.py` | Posts des nouvelles sorties. `date`, `--no-post`, `--snapshot-collected-date`, `--force-track-id`, `--force-song` |
| `post_gainer_thread.py` | Thread top gainers en %. `date`, `--period`, `--limit`, `--min-baseline`, `--no-post` |
| `post_stream_highlights_thread.py` | Tweets highlights (daily+weekly+best-day). `date`, `--limit`, `--best-limit`, `--min-baseline`, `--min-days`, `--no-post` |
| `post_throwback_thread.py` | Thread throwback. `date`, `--action`, `--event`, `--label`, `--top-n`, `--force`, `--no-post` |
| `post_weekend_streams_twitter.py` | Poste le récap week-end. `date`, `--no-post`, `--force-weekday` |
| `post_locks.py` | Gestion des locks `*_posted.lock` (anti-double-post) |
| `artist_metadata.py` / `update_artist_metadata.py` | Métadonnées artiste (followers, listeners) |
| `update_release_dates.py` | Rafraîchit les release_date depuis l'API web-player |
| `git_ops.py` | Commit/push du pipeline streams |
| `run_logs.py` / `reporting.py` | Logs de run et reporting |
| `rebuild_site.py`, `generate_history_index.py`, `split_history.py`, `migrate_streams_to_csv.py`, `scenario_new_song_versions.py`, `release_targets.py`, `stream_utils.py`, `page_scraper.py`, `config.py` | Utilitaires ponctuels/support |

### `streams/extras/` — imports & backfills

| Fichier | Rôle / lancement |
|---|---|
| `import_daily_streams.py` | Importe des CSV pivots dans l'history. `csv_files…`, `--dry-run`, `--output` |
| `backfill_from_kworb.py` | Ajoute les tracks présents sur kworb mais absents de songs.json. `--dry-run` |
| `export_for_web.py` | Export web complet (67 Ko — le vrai exporteur). `--new-date`, `--dry-run` |
| `delete_history_day.py` | Supprime un jour de l'history |
| `fill_album_covers.py` / `fill_track_images.py` / `update_all_track_images.py` | Remplissage covers/images |
| `enrich_json.py`, `build_song_index.py`, `merge_streams_csv.py`, `daily.py` (alias) | Divers one-shot |

---

## 3. `collectors/spotify/charts/` — charts par région

### `run_all_charts.py` — orchestrateur (appelé par `python -m tsm collect charts`)
```
--no-post              désactive tout le posting Twitter
--post artists cards fr global us    parties à poster (extras explicites : best-day-since, regions)
--force / --force-cards   relance la collecte / régénère les cards worldwide
--dry-run  --stop-on-error  --skip-uk  --no-verbose
--watch-release        attend la publication Spotify avec polling adaptatif
  --watch-max-seconds --watch-base-seconds --watch-late-seconds --watch-hot-seconds --watch-error-seconds
--no-warp              désactive le fallback Cloudflare WARP
--backfill --backfill-from D --backfill-to D    rattrapage de dates manquantes sans poster
```

### Par région (chaque dossier a `daily.py` + `tools/` avec `filter.py` scraper, `generate_chart_image.py` PNG, `git_ops.py`)

| Dossier | Contenu notable |
|---|---|
| `global/` | `daily.py` / `daily_no_post.py` ; `tools/script/` : `refresh_session.py` (relogin Playwright → `spotify_session.json`, `--output`), `import_cookies.py` (Cookie-Editor JSON → session, `input`, `--output`), `fix_missing.py` (reconstruit les jours manquants sans poster), `rebuild.py`, `rebuild_history_from_logs.py`, `migrate_charts_to_csv.py`, `daily_test.py` (dry-run) |
| `fr/` | `daily.py` / `daily_no_post.py` ; `tools/rebuild.py`, `tools/rebuild_pop_history.py` |
| `us/`, `uk/` | `daily.py` ; `tools/scripts/backfill_charts_history_{us,uk}.py` (backfill massif avec resume/retry : `--start --end --resume --retry-at-end --until-complete --cache --failed-dates --only-failed --min-delay --base-rate-limit-sleep --max-rate-limit-retries --no-retry --verbose --log-http`) |
| `worldwide/` | `daily.py` (79 Ko) : tous les pays en parallèle. `date`, `--dates`, `--dates-file`, `--backfill-from/to`, `--no-post`, `--post-song-updates`, `--post-priority-global-new`, `--post-priority-region R`, `--post-multi-song-regions[-only]`, `--force`, `--force-priority-global-new` ; `tools/scripts/generate_card_images.py` (cards par chanson : `--date`, `--theme`, `--min-countries 3`, `--force`, `--post`), `post_global_new_releases.py` (card NEW prioritaire : `date`, `--post`, `--post-worldwide`, `--force`, `--force-song`), `backfill_total_days.py`, `profile_daily.py` |
| `artists_global/` | `artist_global_daily.py` : chart artistes global. `--period daily\|weekly`, `--date`, `--no-wait`, `--retry-seconds`, `--no-csv`, `--no-upload`, `--no-post`, `--force`, `--force-post`, `--no-warp` ; `tools/scripts/generate_artist_chart_image.py` (`date`, `--period`, `--no-post`, `--session`, `--force`), `generate_artist_worldwide_card.py` (`date`, `--post`, `--force`, `--limit`) |

### `collectors/spotify/core/` — modules partagés (pas des scripts)

| Fichier | Rôle |
|---|---|
| `twitter.py` (49 Ko) | Post Twitter via Playwright (profil Chrome persistant), espacement entre posts, sessions par compte |
| `data_paths.py` | **Source de vérité de tous les chemins** (REPO_ROOT, runtime, snapshots, exports web, legacy) |
| `download.py` | Téléchargement CSV Spotify Charts |
| `history.py` | Gestion `ts_history.json` |
| `chart_comment.py` | Commentaire de chart partagé entre régions |
| `notify.py` | Notifications mobiles via ntfy.sh |
| `fmt.py`, `logger.py`, `git_ops.py`, `retention.py`, `album_emoji.py`, `swift_top_gate.py` | Formatage, log, git, rétention, emojis d'albums, gating TayBoard |

---

## 4. `collectors/apple_music/` — charts Apple Music

Orchestrateur : `run_apple_music.py` (appelé par `python -m tsm collect apple-music`) — `--no-post`, `--no-images`, `--force-images`. Launcher : `run_apple_music.bat`.

| Fichier | Rôle / lancement |
|---|---|
| `country_charts.py` | Top songs par pays. `--countries`, `--date`, `--scraped-at` |
| `country_albums.py` | Top albums par pays. idem |
| `genre_charts.py` / `genre_album_charts.py` | Charts par genre (songs/albums). idem |
| `global.py` / `global_albums.py` | Top 100 global (songs/albums). `--date`, `--scraped-at` |
| `music_video_charts.py` / `top_music_videos.py` | Charts vidéos. (`storefront` pour top_music_videos) |
| `ts_page.py` | Top songs de la page artiste TS. `storefront`, `--date`, `--scraped-at` |
| `generate_country_card_images.py` | Cards PNG par pays. `date`, `--min-countries`, `--limit`, `--force` |
| `generate_snapshot_images.py` | PNG des snapshots. `--date`, `--region us\|fr\|global…`, `--genre` (avec `--region`), `--list-genres`, `--out-dir` |
| `core/` | `token.py` (jeton API), `http.py`, `storefronts.py`, `filters.py`, `csv_utils.py`, `r2.py`, `config.py`, `export.py`, `models.py` |

---

## 5. `collectors/billboard/` — Billboard & TayBoard

| Fichier | Rôle / lancement |
|---|---|
| `scrape_billboard.py` | Scrape Billboard → `billboard_history.csv` |
| `swift_top_100.py` (72 Ko) | **Chart hebdo Swift Top 100** (style Billboard). `--date` (fin de semaine), `--backfill` (+`--force`), `--streams-csv`, `--rebuild-index`, `--generate-songs`, `--dry-run`, `--skip-r2`, `--skip-images`, `--variant combined\|not-combined\|full-tayboard` |
| `swift_top_combined.py` / `swift_top_seperate.py` | Wrappers variante combinée / non-combinée (mêmes flags) |
| `swift_top_albums.py` | TayBoard Albums hebdo. `--date`, `--backfill`, `--force`, `--dry-run`, `--rebuild-index`, `--skip-r2`, `--variant albums\|eras\|both` |
| `swift_top_album.py` / `swift_top_era.py` | Wrappers chart albums / eras |
| `swift_top_100_image.py` | PNG du chart. `--week YYYY-MM-DD` (ou `--input`), `--output`, `--limit`, `--offset`, `--width`, `--scale` |
| `tayboard_explainer_images.py` | Cards méthodologie pour threads. `--input`, `--output-dir`, `--width`, `--height`, `--scale` |
| `config/links/` | Config de liens ; `history/` + `logs/` : données datées |

---

## 6. `collectors/youtube/` — vues YouTube

| Fichier | Rôle / lancement |
|---|---|
| `update_youtube.py` | Collecteur principal (appelé par `python -m tsm collect youtube`). `--date`, `--dry-run`, `--debug` (écrit mais pas de git/notif), `--no-post` (pas de ntfy), `--bootstrap` (découverte complète de la chaîne, une seule fois), `--commit` (git désactivé par défaut !), `--force` (remplace les lignes du jour) |
| `videos/update_youtube.py` | Point d'entrée canonique (wrapper) |
| `core/` | `api.py` (YouTube Data API v3, stdlib), `channel.py` (catalogue vidéos), `csv_utils.py` (deltas), `title_groups.py` (agrégation par chanson), `git_ops.py`, `config.py` |
| `run_youtube.bat` | Launcher |

---

## 7. `collectors/comp/` — composants visuels partagés

Modules importés par les générateurs d'images (pas des scripts, sauf preview) :
`discography.py` (accès catalogue), `song_card.py`, `tables_image.py`, `fmt.py`, `track_cover_cache.py`.
`preview.py` = galerie de previews de tous les styles : `--date`, `--only FAMILLE`, `--output-dir`, `--limit`, `--min-days`, `--keep-html/--no-keep-html`.

---

## 8. `scripts/` — outils one-shot & maintenance

| Fichier | Rôle / lancement |
|---|---|
| `r2.py` (34 Ko) | **Upload R2 principal** (data/history/db/images). `--bucket`, `--new-date D` (une seule date), `--slugs a,b`, `--streams-daily`, `--charts-only`, `--skip-{history,static,db,images}-upload`, `--dry-run` |
| `export_for_web.py` | Wrapper → `streams/extras/export_for_web.py`. `--new-date`, `--dry-run` |
| `check_r2_storage.py` | Alerte ntfy si le stockage R2 dépasse les seuils. `--dry-run`, `--bucket-limits b=size,…`, `--warning-percent`, `--topic` |
| `migrate_app_r2.py` | Copie bucket public → bucket app. `--dry-run`, `--overwrite`, `--key`, `--prefix` |
| `upload_ap_r2.py` | Upload Apple Music vers R2. `--bucket`, `--prefix`, `--dry-run` |
| `chartr2.py` | Upload R2 des charts |
| `fetch_issues.py` | Récupère les signalements du site depuis R2. `--save`, `--images`, `--delete` |
| `fetch_hiring.py` | Récupère les candidatures (préfixe `hiring/`). `--json`, `--role` |
| `backfill_discography_from_spotify.py` | Backfill de la discographie depuis Spotify. `--apply` (déf. dry-run), `--no-backup`, tracks musicales par défaut, `--include-non-songs` pour ajouter commentary/karaoke/instrumental en DB et les collecter comme `chart_extra`, `--exclude-non-songs` (défaut), `--skip-api`, `--recent-releases N`, `--target-release-date`, `--quiet` |
| `backfill_spotify_track_metadata.py` | Backfill métadonnées tracks. `--apply`, `--track`, `--limit`, `--skip-existing`, `--sleep` |
| `backfill_global_charts.py` | Backfill `charts_history_global/fr.csv`. `--charts`, `--start`, `--end`, `--dl-workers`, `--filter-workers`, `--headless`, `--dry-run` |
| `backfill_track_cover_cache.py` | Pré-chauffe `track_cover_cache.json` |
| `enrich_genres.py` | Genres par chanson. `--apply`, `--track`, `--set-genres`, `--sources`, `--refresh-cache`, `--limit`, `--skip-existing` |
| `infer_track_flags.py` | Déduit les `filter_tags` de la discographie. `--apply`, `--track`, `--limit`, `--csv`, `--json` |
| `enrich.py` / `add_display.py` / `fix_songs_json.py` / `fix_song_images.py` / `fill_images.py` / `split_albums.py` / `list_songs.py` | Maintenance discographie/données |
| `fill_streams_from_archive.py` | Backfill `streams_history.csv` depuis les Daily Archive |
| `sync_spotify_country_charts_from_worldwide.py` | Resynchronise les charts pays depuis worldwide. `--charts`, `--dry-run` |
| `repair_charts_fr_archive_from_history.py` | Répare l'archive FR. `dates…`, `--archive`, `--history-root`, `--dry-run` |
| `reset_swift_top_100_history.py` | ⚠️ Reset de l'historique Swift Top 100. Dry-run par défaut ; `--yes` pour supprimer, `--remove-bonuses`, `--skip-r2` |
| `refresh_spotify_session.py` | Rafraîchit `spotify_session.json` |
| `download_apple_music_images.py` / `merge_am_csv.py` / `export_apple_music.py` | Outils Apple Music |
| `generate_glitter_images.py` | Images glitter pour les boutons du site |
| `explore_streams_api.py` / `test_streams_api.py` / `probe_gettrack_response.py` | Exploration API (ne touchent pas la DB) |
| `migrate_daily_data_layout.py` | Migration du layout data. `--apply`, `--move` |
| `run_daily_collectors.sh` / `check_vm_sessions.sh` | Wrappers shell (VM/legacy) |
| `.backfill_browser_cache/` | Caches Chrome des workers de backfill (ignorer) |
| `issues/` | Signalements téléchargés par `fetch_issues.py --save` |

---

## 9. Dossiers de données

| Dossier | Contenu | Règle |
|---|---|---|
| `db/` | Source de vérité : `discography/artist.json` (catalogue maître), `discography/albums/`, `track_cover_cache.json`, `charts_history_*.csv`, `streams_history.csv`, headers d'albums | Modifier via les scripts, pas à la main |
| `data/` | Archives datées (`data/2024/…`) + `_archive/` (migrations) + `_tmp/` (scénarios de test) | Lecture seule en pratique |
| `snapshots/` | Snapshots datés : `spotify_charts/`, `spotify_streams/`, `apple_music_charts/`, `billboard/`, `tayboard/`, `recap/` | Écrits par les collectors |
| `runtime/` | `exports/web/` (payloads générés avant upload R2), `tmp/`, `spotify_streams/` | Non versionné, régénérable |
| `website/` | ⚠️ **Site statique LEGACY** — ne JAMAIS y écrire sauf demande explicite (`website/site/data`, `website/site/history` = anciens exports) | Interdit (règle CLAUDE.md) |

---

## 10. Racine, docs, CI

| Fichier | Rôle |
|---|---|
| `run_daily.bat` | Launcher Task Scheduler : `python -m tsm daily` (python3.13 WindowsApps) |
| `run_all_charts.bat` | Launcher : `python -m tsm collect charts` |
| `docs/runbook.md` | Runbook : layout canonique + commandes principales + règles de sécurité |
| `docs/data-layout-audit.md` | Audit généré par `python -m tsm audit data --write` |
| `README.md` / `README_FULL.md` / `CONTRIBUTING.md` / `AGENTS.md` / `CLAUDE.md` | Docs générales / instructions IA |
| `DEPLOYMENT_AUDIT.md`, `GITHUB_SECRETS_SETUP.md`, `add_github_secrets.py` | Setup GitHub Actions (secrets) |
| `setup.py`, `requirements.txt`, `.python-version` | Packaging/deps Python |
| `.github/workflows/` | `run-all-charts.yml`, `update-streams.yml`, `run-apple-music.yml`, `check-r2-storage.yml`, `keepalive.yml` — **en pratique la prod tourne en LOCAL via Task Scheduler**, pas via ces workflows |
| `.claude/skills/` | Skills Claude Code : `tsm-map`, `pipeline-ops`, `data-rules`, `image-gen`, `deploy`, `admin-work`, `style-rules` |

Les `.bat` du Task Scheduler vivent dans **`tsm-frontend/tasks/`** (`run_spotify_streams.bat`, `run_spotify_charts_global.bat`, `run_spotify_charts_fr.bat`, `watch_logs.bat`) et font `cd` vers ce repo.

## 11. `dev/` — ad-hoc (jetable)

Scripts de vérification/debug ponctuels, non maintenus : `adhoc/checks/` (vérifs CSV/formules), `adhoc/debug/`, `adhoc/verify/`, `maintenance/` (fix de colonnes CSV, régénération d'images d'albums), tests rapides `test_*.py` à la racine. Exceptions utiles :
- `adhoc/period_streams_recap.py` — thread récap de période. `--period`, `--date`, `--current`, `--start/--end`, `--top`, `--no-images`, `--post --yes`
- `adhoc/post_test_tweet.py` / `adhoc/schedule_test_tweet.py` — smoke-tests Twitter (`--text`, `--at`, `--yes` requis pour poster)
- `adhoc/render_album_ranking_cards.py`, `adhoc/update_album_rankings_r2.py` — cards Album Ranking figées + upload R2

---

## Conventions & pièges

- **Dry-run d'abord** : quasiment tous les scripts destructifs ont `--dry-run` ou exigent `--apply`/`--yes` — toujours utiliser le dry-run avant.
- **Locks anti-double-post** : fichiers `*_posted.lock` ; les flags `--force` les ignorent.
- Commits data : `charts run all YYYY-MM-DD`, `youtube views YYYY-MM-DD` (voir `git log`).
- Panne connue : **WARP instable → lecture infinie** pendant la collecte ; tuer le process et relancer. Jour manqué → `reconcile_gap_catchup.py`.
- `collectors/spotify/core/data_paths.py` = source de vérité des chemins ; ne pas hardcoder.
- Buckets R2 : prod site = `taylor-data` ; le `.env` local du frontend pointe `taylor-app`.
