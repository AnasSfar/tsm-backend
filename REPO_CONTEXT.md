# TSM Backend — Contexte du repo

> Carte complète du repo : chaque dossier/fichier, son rôle, et les options de lancement des scripts.
> ⚠️ **Doc vivante — mise à jour OBLIGATOIRE** : toute IA qui ajoute/modifie/déplace un script ou change ses options doit mettre à jour ce fichier dans la même session.
> Repo frère : `tsm-frontend` (site React + API FastAPI sur Vercel). Flux global :
> **tsm-backend (collecte locale, Task Scheduler) → R2 (`taylor-data`) → API tsm-frontend → site React.**
> Le VPS OVH qui a fait tourner YouTube + Apple Music du 2026-07-30 au
> 2026-08-17 a été décommissionné (coût) — tout tourne de nouveau via le
> Task Scheduler local, voir `OVH.md` et la section « Déploiement VPS OVH »
> plus bas. Spotify (streams/charts) et Billboard restent en local de toute
> façon (Spotify bloqué par le WAF depuis une IP OVH, testé avec et sans
> WARP — voir `OVH.md`).

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
│   ├── deezer/              ← charts Deezer (global + top tracks TS) + fans
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
├── docs/                   ← runbook.md + data-layout-audit.md + apple-music-script-context.md
├── website/                ← ⚠️ site statique LEGACY — INTERDIT sauf demande explicite
├── .github/workflows/      ← workflows GitHub (peu utilisés : tout tourne en local ; run-data-only-collectors.yml = escape hatch manuel Apple Music/YouTube)
├── .claude/skills/         ← skills Claude Code (tsm-map, pipeline-ops, data-rules, image-gen, deploy, admin-work, style-rules, collector-apple-music, collector-deezer)
├── run_daily.bat           ← launcher : python -m tsm daily
├── run_all_charts.bat      ← launcher : python -m tsm collect charts
├── run_discography_editor.bat ← launcher : GUI locale d'édition de db/discography/ (scripts/discography_editor/)
├── run_youtube_grouping_editor.bat ← launcher : GUI locale de combinaison manuelle des vidéos YouTube (scripts/youtube_grouping_editor/)
└── run_header_editor.bat  ← launcher : GUI locale d'édition des headers d'albums/targets globales (scripts/header_editor/)
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
python -m tsm collect deezer [--no-post]                        # → run_deezer.py
python -m tsm collect youtube [YYYY-MM-DD] [--no-post] [--force] # → update_youtube.py (YYYY-MM-DD = jour d'activité voulu ; sinon run−1j)
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
                     guards, locks et règles de jour respectés) : top-eras (hors week-end),
                     all-albums (chaque album posté indépendamment — pas un thread —, ordre par
                     score score_album_update, chaque jour de semaine, toujours en dernier),
                     top45, recap, best-day-since, debut, gainers,
                     album-updates (ALBUM_UPDATE_TARGETS + scan gainers + scan +10% de gain agrégé tous
                     les jours de semaine, scan majorité de tracks en hausse mardi/mercredi/jeudi
                     seulement, en premier dans le run normal) ; avec --no-post = images seulement
  --throwback --throwback-action announced|released --throwback-event "..."   # thread throwback
  --reset-last-date  supprime les lignes de la dernière date avant de tourner
  --reset-date D     supprime les lignes de cette date avant de tourner
  --force            (avec --throwback) régénère les images ; sinon force le reprocess
  --quiet / --verbose / --help
```

### Scripts du dossier

| Fichier | Rôle / lancement |
|---|---|
| `best_day_since.py` | Notes « meilleur jour depuis X » depuis l'history. `date` (déf. dernière), `--limit 50`, `--min-days 21`, `--include-extras`, `--output`, `--no-write`. Helpers partagés : `era_key(album)` (normalisation d'ère, Red + Red TV → `red`), `era_recap_groups(date, …)` (groupes par ère ≥ N chansons best-day post-éligibles — utilisé par la card recap par ère et l'export web), `best_day_marker_text(row)` / `best_day_marker_labels(track_ids, date)` (marqueur `★ · since …` pour Top Songs/Eras/Gainers). |
| `spotlight.py` | Image « spotlight » carrée d'une chanson + post. `title` ou `--url`, `date`, `--no-post`, `--no-scrape`, `--combined/--no-combined`, `--compare yesterday\|last-week`, `--highlight vs\|total`, `--account flame\|tsm`, `--session` |
| `fix_one.py` | Corrige manuellement le daily (ou total avec `--total`) d'UNE chanson pour UN jour. `song day value`, `--dry-run`, `--track-id`, `--pick N`, `--all-matches`, `--no-git` |
| `fix_streams.py` | Re-scrape le total actuel de toutes les chansons et corrige l'history |

### `streams/tools/scripts/` — outillage du pipeline

> **Thème « masthead » selon le jour (décision propriétaire 2026-08-26)** — les cards à en-tête masthead / corps ledger rendent le thème **light (blanc propre)** sur les posts de semaine (lun-ven) et **dark** le week-end (sam/dim). Helper unique `comp.tables_image.masthead_theme_for_date(date)`. Concerne : Top Songs (`generate_streams_image.py`), Top Eras (`generate_albums_image.py`), Spotlight Gainers (`post_stream_highlights_thread.py`), récap quotidien (`generate_weekend_streams_image.py`), recap Best Day Since global (`post_best_day_since_twitter.py`). Le recap Best Day Since **global** n'a plus de theming par album depuis le 03/09/2026 (header fixe « all eras »). Seule exception : la **card recap best-day-since par ère** (`--only-era-recap`, `_generate_recap_image(era_display=…)`), qui utilise le pool de headers de l'ère + titre `{Ère} - Best Day Recap` (théming volontaire, 03/09/2026). Palette light partagée = `_LEDGER_THEME_TOKENS["light"]` (blanc `#ffffff`, plus le beige d'avant).

> **Marqueur `★ · since MM/DD/YYYY` (best-day-since) sur les cards ledger (03/09/2026)** — Top Songs, Top Eras et GAINERS affichent le marqueur `★ Titre · since <date longue>` (ou `· of the year` / `· of the month`) dans la colonne Track/Album, comme les images d'album update. Helper `comp.tables_image.ledger_name_with_best_day(name_html, marker_label)` + `best_day_since.best_day_marker_text(row)` / `best_day_since.best_day_marker_labels(track_ids, date)`. Top Songs / GAINERS = record **solo** par track (records combined non affichés, même règle que `generate_album_update_image._best_day_labels_for_sections`) ; Top Eras = record **combined** de l'ère (`compute_album_best_day_since` par `era_key`). Lookup toujours en try/except : jamais bloquant.

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
| `generate_streams_image.py` | PNG top daily streams (top configurable, déf. 10). Masthead « SONGS », thème light/dark selon le jour (cf. note plus haut). |
| `generate_albums_image.py` | PNG « Top Albums by Daily Streams ». Masthead « ERAS », thème light/dark selon le jour. Ère « Non-Album » (misc.json) toujours hors ranking en bas + ligne « Total » ; sa cover est un collage 2x2 de 4 covers hors-album aléatoires. |
| `generate_album_update_image.py` | PNG « Album Daily Update » pour un album. `--style=table-dark` (auto pour les ères de `TABLE_DARK_DEFAULT_ALBUMS` : `the life of a showgirl`, `reputation`) / `--style=table-light` (variant clair ; palettes light dédiées : `reputation`, `tortured poets`). Suffixes `_update_table_dark.png` / `_update_table_light.png`. Palette par ère + variant dans `_table_dark_theme`. Header via `db/discography/headers/preferences.json` (clé = nom d'album exact ; valeur = string ou objet `{"dark":…, "light":…}` pour un header par variant). Les styles `table-*` ne sont pas postables (le post daily reste `default` → dark). `--header=<path-or-name>`, `--all-headers`, `--all-albums`. **Caption (`_build_album_post_text`) : première ligne réécrite quand l'album total décroche un best-day (03/09/2026)** — `_album_best_day_row` (best-day-since ≥ 30 j via `ALBUM_BEST_DAY_MIN_DAYS`, OU biggest day of year/month, OU best day ever) → `📈| "X" earned its <LABEL> with N streams on <date>.` au lieu de `received N streams`. `has once again earned` quand `best_day_since.is_recent_repeat_record` (jour battu ≤ 60 j). Même verbe appliqué à la note best-day chanson (`It once again earned…` / `once again had its…`). Plus de card best-day-since album séparée (voir `post_best_day_since_twitter.py`). |
| `generate_weekend_streams_image.py` | PNG récap streams **quotidien** (nom « weekend » historique — posté chaque jour). Design « Masthead » ledger, **thème selon le jour** : light (blanc) lun-ven, dark (`#131417`) sam/dim — palettes `_MH_THEME["light"/"dark"]` (dark calqué sur `_LEDGER_THEME_TOKENS["dark"]`), police Inter + Big Shoulders (rangs). Header = image du pool `headers/top_eras/` + badge logo Spotify + wordmark fantôme « STREAMS » + filet (dominante photo) ; bande haute = total daily + chg jour/semaine/all-time **à gauche**, monthly listeners (+ rang monde) et followers **à droite** (followers seulement si `db/discography/artist.json` les contient — jamais de placeholder) ; puis top 5 eras + top 5 songs. `date` + `--light`/`--dark` (force le thème pour tester). `_header_style`/`CSS`/`_row_html`/`_section_html` restés intacts pour `post_throwback_thread.py` + `post_debut_releases.py`. |
| `post_streams_twitter.py` | Poste l'image top streams. `date`, `--no-post`, `--top-n 15` |
| `post_albums_twitter.py` | Poste l'image « Albums on Spotify » / top eras, hors week-end |
| *(all-albums)* | Pas de script dédié : `finalize_update._post_all_albums` poste chaque album (hors Misc/standalone) indépendamment via `generate_album_update_image.py --post`, ordre = **score `score_album_update.py`** (fallback tri par daily total), **chaque jour de semaine** (passé de lundi/vendredi à quotidien le 2026-08-29 ; aucune card le week-end), toujours en dernier parmi les étapes de post. Remplace l'ancien thread groupé (`post_all_albums_thread.py`, lock `all_albums_thread_posted.lock`) — supprimé le 2026-08-05. **Best-day-since album (03/09/2026) : fondu dans la première ligne de cette card** (`_build_album_post_text`), plus de card séparée → conséquence : pas de best-day album le week-end. `_exceptional_primary_albums` continue d'utiliser `compute_album_best_day_since` pour prioriser l'ordre des cards early. |
| `score_album_update.py` | Score par album (read-only) pour l'**ordre** de post des cards album — calqué sur `score_best_day_since.py` (mêmes sous-scores agrégés au niveau album + bonus biggest-day-of-year/best-ever/majorité-positive/nb de tracks record). Cache process-local (`_history`, `_SCORE_CACHE`). `score_albums(names, target)`, `rank_albums(names, target_date)`. `date`, `--limit`, `--json`, `--explain`. Utilisé par `finalize_update._rank_albums_for_posting`. |
| `post_best_day_since_twitter.py` | Posts « best day since » avec spotlights. **Batch finalize** : `POST_COLLECTION_STANDARD_SONG_POSTS = 3` slots standard, `POST_COLLECTION_MAX_SONG_POSTS = 5` max ; slots 4-5 réservés aux records exceptionnels (écart >90 j `_is_priority_best_day_since`, ou score `score_best_day_since` ≥ `EARLY_BEST_DAY_EXCEPTIONAL_MIN_SCORE` 90) — dans `_validated_song_rows_for_post`. **Early lane** inchangée (3 standard → 5 exceptionnels, score ≥ 90 pour les slots 4-5). **Recap global** (`_generate_recap_image`) : header fixe = bande « all eras » du site Eras Tour (`tools/headers/best_day_recap/all-eras.jpg`), masthead « BEST DAY », titre `Best Day Since - Full Recap`, thème du jour (light lun-ven / dark sam-dim) — **plus de theming par album** (`_album_recap_theme` supprimé, 03/09/2026). **Best-day-since ALBUM : plus de card séparée (03/09/2026)** — le record de l'album total est réécrit dans la première ligne de sa card update quotidienne (`generate_album_update_image._build_album_post_text` / `_album_best_day_row`). `--only-album`, `--album-limit`, `--album-min-days`, `--no-albums`, `_pick_album_rows`, `_post_album_best_day_rows`, `_build_album_best_day_tweet`, `_generate_album_best_day_since_image` supprimés. `finalize_update.ReadyAlbumBestDaySincePoster` → `ReadyEraRecapPoster` (ne fait plus que la card recap par ère). **`best_day_since.is_recent_repeat_record(row)` / `RECENT_REPEAT_RECORD_DAYS = 60`** : jour battu ≤ 60 j ⇒ caption « has once again earned its best day since … » (chanson `best_day_since_tweet(repeat=…)` + album). **Card recap best-day-since PAR ÈRE (03/09/2026)** : quand ≥ `ERA_RECAP_MIN_SONGS = 5` chansons d'une même ère (`best_day_since.era_key` : Red + Red TV comptent ensemble) décrochent un best-day-since **et** passent le gate de post individuel ce jour-là (`best_day_since.era_recap_groups`), une card recap dédiée à cette ère part **avant la card album update de l'ère** (`_post_era_recaps_batch` en finalize, ou `--only-era-recap ALBUM` en early). Header = théming de l'ère. Additive : les chansons restent dans le recap global. **Cap early = 2** (`EARLY_ERA_RECAP_MAX_POSTS`, locks `best_day_since_era_recap_locks/`), ne consomme pas le quota chanson early. **Suppression** : une fois la card recap d'une ère postée, aucune card best-day-since chanson individuelle de cette ère ce jour-là (`_era_recap_posted_for` dans `_find_all_rows` + `_post_single_track_early`) — **sauf** un biggest day of the year (carte inconditionnelle maintenue). **Biggest day of the year = post inconditionnel** : sa propre card, en early, sans cap album/ère/quotidien ni gate de score (`_is_unconditional_best_day`, décision 2026-08-29) — **sauf** un titre de « The Taylor Swift Holiday Collection » hors saison de Noël (25 nov-7 jan) : bloqué dans toutes les voies via `_holiday_collection_out_of_season`, le blocage saisonnier bat la règle inconditionnelle. `--only-track` exit codes : 0 posté, **2 posté (record annuel inconditionnel)**, 3 pas encore qualifié, 1 erreur. `--only-era-recap ALBUM` exit codes : 0 posté, 3 pas encore ≥ 5 chansons, 1 erreur. `date`, `--limit 5`, `--min-days`, `--no-recap`, `--no-era-recap`, `--only-track ID`, `--only-era-recap ALBUM`, `--exclude-tracks`, `--force`, `--post-spacing-seconds` |
| `post_debut_releases.py` | Posts des nouvelles sorties. `date`, `--no-post`, `--snapshot-collected-date`, `--force-track-id`, `--force-song` |
| `post_song_overtakes.py` | Poste les overtakes de total streams entre chansons non-extra (image sans colonne Gap). Regroupe les overtakes proches en rang (`GROUP_RANK_PROXIMITY`) en un seul post/tweet. **Un lock par overtake individuel** : `song_overtakes/{overtaker}_over_{passed}_posted.lock` (pas un JSON/lock global) — écrit pour **chaque événement** d'un groupe **immédiatement après le post réussi** (pas seulement en fin de script), pour qu'un retry de `finalize_update` (3 tentatives) ne reposte jamais un overtake déjà posté, même si un groupe suivant échoue. `date`, `--limit 8`, `--force`, `--no-post`, `--post-spacing-seconds` |
| `post_stream_milestones.py` | Poste les cartes de milestone (paliers de 100M streams). La ligne « next song expected » est **optionnelle** : si aucun autre morceau n'est prévu pour franchir exactement le même palier, le milestone se poste quand même, juste sans cette phrase (avant le 17/08/2026 : bloquait le post entier — le seul vrai gate restant est un same-day tie sur le même palier, ambigu par construction). Sauvegarde `stream_milestones_posted.json`/lock après chaque post individuel (même raison anti-repost que `post_song_overtakes.py`). `date`, `--limit 10`, `--force`, `--no-post`, `--post-spacing-seconds` |
| `post_gainer_thread.py` | Thread top gainers en %. Le pool est d'abord les tracks non-extra (`load_album_track_ids`) ; si moins de `--limit` qualifient, les slots restants sont comblés par des tracks `chart_extra=true` (jamais l'inverse — un extra ne délogera jamais un non-extra du classement). `date`, `--period`, `--limit`, `--min-baseline`, `--no-post` |
| `post_stream_highlights_thread.py` | Tweets highlights (daily+weekly+best-day). Card gainers masthead « GAINERS », thème light/dark selon le jour. `date`, `--limit`, `--best-limit`, `--min-baseline`, `--min-days`, `--no-post` |
| `post_throwback_thread.py` | Thread throwback. `date`, `--action`, `--event`, `--label`, `--top-n`, `--force`, `--no-post` |
| `post_weekend_streams_twitter.py` | Poste le récap streams « Masthead ». `date`, `--no-post`, `--force-weekday`. Appelé **chaque jour** par `finalize_update._post_daily_recap_card` (qui ajoute `--force-weekday` en semaine) ; lock `weekend_streams_posted.lock`. Le nom « weekend » est historique. |
| `post_weekend_song_gainers.py` | Poste les weekend gainers (samedi/dimanche) en song_card. Qualifie si gain ≥ `--min-pct` (déf. 10%), OU best-day-since record, OU charté au Global Top 200 ce jour-là (jamais mentionné dans le tweet — signal de qualification silencieux). `date`, `--limit 5`, `--min-pct 10.0`, `--min-baseline 1000`, `--force-weekday`, `--force`, `--no-post`, `--post-spacing-seconds` |
| `post_locks.py` | Gestion des locks `*_posted.lock` (anti-double-post) |
| `artist_metadata.py` / `update_artist_metadata.py` | Métadonnées artiste → `db/discography/artist.json`. `monthly_listeners`/`monthly_rank` par scraping du texte de la page artiste ; `followers` capturé depuis la réponse GraphQL `queryArtistOverview` de la page (le DOM ne l'affiche plus) — best-effort, jamais bloquant. `update_artist_metadata` écrit aussi `previous_*` (dont `previous_followers`) pour les deltas et upsert `artist_monthly_listeners_history.csv` (schéma listeners/rank inchangé). |
| `update_release_dates.py` | Rafraîchit les release_date depuis l'API web-player |
| `git_ops.py` | Commit/push du pipeline streams |
| `run_logs.py` / `reporting.py` | Logs de run et reporting |
| `rebuild_site.py`, `generate_history_index.py`, `split_history.py`, `migrate_streams_to_csv.py`, `scenario_new_song_versions.py`, `release_targets.py`, `stream_utils.py`, `page_scraper.py`, `config.py` | Utilitaires ponctuels/support |

### `streams/extras/` — imports & backfills

| Fichier | Rôle / lancement |
|---|---|
| `import_daily_streams.py` | Importe des CSV pivots dans l'history. `csv_files…`, `--dry-run`, `--output` |
| `backfill_from_kworb.py` | Ajoute les tracks présents sur kworb mais absents de songs.json. `--dry-run` |
| `export_for_web.py` | Export web complet (67 Ko — le vrai exporteur). `--new-date`, `--dry-run`. `export_best_day_since()` → `data/best_day_since.json` : en plus de `items`/`albums`, écrit `era_recaps` (groupes par ère ≥ 5 chansons best-day post-éligibles, calqué sur `best_day_since.era_recap_groups`) et `all_items` (tous les records best-day-since du jour, sans gate `min_days`). |
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

Idempotence (fix 17/07/2026, après double post global/us) : les `daily.py` global/fr/us/uk appelés avec une date explicite (dont `--post-only <date>`) respectent désormais `posted.lock` (skip, exit 0) — seul `--force` reposte. En plus, le lock est re-vérifié via `skip_if` juste après l'acquisition du slot de compte X (voir `core/twitter.py`), ce qui ferme la course entre deux process concurrents.

| Dossier | Contenu notable |
|---|---|
| `global/` | `daily.py` / `daily_no_post.py` ; `tools/script/` : `refresh_session.py` (relogin Playwright → `spotify_session.json`, `--output`), `import_cookies.py` (Cookie-Editor JSON → session, `input`, `--output`), `fix_missing.py` (reconstruit les jours manquants sans poster), `rebuild.py`, `rebuild_history_from_logs.py`, `migrate_charts_to_csv.py`, `daily_test.py` (dry-run) |
| `fr/` | `daily.py` / `daily_no_post.py` ; `tools/rebuild.py`, `tools/rebuild_pop_history.py` |
| `us/`, `uk/` | `daily.py` ; `tools/scripts/backfill_charts_history_{us,uk}.py` (backfill massif avec resume/retry : `--start --end --resume --retry-at-end --until-complete --cache --failed-dates --only-failed --min-delay --base-rate-limit-sleep --max-rate-limit-retries --no-retry --verbose --log-http`) |
| `worldwide/` | `daily.py` (105 Ko) : tous les pays en parallèle ; poste desormais aussi une re-entree standalone par pays des sa collecte et sync le CSV `db/charts_history_<region>.csv` de ce pays immediatement (voir SKILL spotify-charts § "Immediate re-entry posting" / "Sync CSV immediat"). `date`, `--dates`, `--dates-file`, `--backfill-from/to`, `--no-post`, `--post-song-updates`, `--post-priority-global-new`, `--post-priority-region R`, `--post-multi-song-regions[-only]`, `--force`, `--force-priority-global-new`, `--regions CODE…` / `--exclude-regions CODE…` (restreint/exclut des pays ; sous filtre, le snapshot daté existant est relu et les régions hors périmètre sont mergées en retour — même sous `--force`, qui ne ré-fetch alors que les régions ciblées : permet de remplir un snapshot région par région). En boucle multi-dates : une région qui 404 `SPOTIFY_WORLDWIDE_DEAD_REGION_STREAK` fois d'affilée (déf. 6) est retirée du fetch pour le reste du run (chart jamais publié sur la période) ; si `global` 404 pour une date, la date est court-circuitée après la Phase 1 ; `tools/scripts/generate_card_images.py` (cards par chanson : `--date`, `--theme`, `--min-countries 3`, `--force`, `--post`), `post_global_new_releases.py` (card NEW prioritaire : `date`, `--post`, `--post-worldwide`, `--force`, `--force-song`), `backfill_total_days.py`, `profile_daily.py` |
| `artists_global/` | Autonome, PAS orchestre par `run_all_charts.py` (task scheduler dediee `TSM - Spotify Artists Global Daily`, 15h). `artist_global_daily.py` : chart artistes global + declenche lui-meme les charts filtres en fin de run (`_run_filtered_artist_charts`, liste `_ACTIVE_FILTERED_ARTIST_CHARTS`). `--period daily\|weekly`, `--date`, `--no-wait`, `--retry-seconds`, `--no-csv`, `--no-upload`, `--no-post`, `--force`, `--force-post`, `--no-warp`. `Artists.csv` (artist_id, artist_name, gender F/M/Group — curation manuelle, exception au `.gitignore` `*.csv`, auto-completee avec les nouveaux artistes rencontres) ; `tools/scripts/generate_artist_chart_image.py` (`date`, `--period`, `--no-post`, `--session`, `--force` ; builders `build_top5_html/build_top10_html/build_solo_html` acceptent un `title=` reutilise par les charts filtres), `generate_filtered_artist_chart.py` (variantes filtrees du chart artiste — filtres locaux `female` (cadence quotidienne type chart principal), `starts_with_t`/`named_taylor` (cadence "poste seulement si le rang de Taylor s'ameliore") filtrent le chart GLOBAL deja collecte sans reseau ; filtres regionaux `us_artist_chart`/`uk_artist_chart` (cadence rank_up) fetchent en LIVE le chart artiste propre au pays (`artist-us-daily`/`artist-gb-daily`, via `_fetch_chart`/`_get_bearer_token` de `artist_global_daily.py`) — classement different du chart global ; `filter_key`, `date`, `--no-post`, `--session`, `--force` ; nouveaux filtres = une entree dans `FILTERS` (ce script) + `_ACTIVE_FILTERED_ARTIST_CHARTS` (`artist_global_daily.py`), synchronisees a la main. `generate_artist_worldwide_card.py` supprime le 2026-08-16 (redondant avec les filtres regionaux, plus utilise). |

### `collectors/spotify/core/` — modules partagés (pas des scripts)

| Fichier | Rôle |
|---|---|
| `twitter.py` (49 Ko) | Post Twitter via Playwright (profil Chrome persistant), espacement entre posts, sessions par compte ; `post_with_image(..., skip_if=cb)` : callback re-évaluée après l'acquisition du slot de compte — si True, post annulé et retour True (anti-double-post inter-process). **Slot de post à priorité** : `post_thread` / `post_with_image` / `post_image_thread` / `schedule_post` acceptent `priority=<int>` (plus bas = plus urgent, défaut 5) ou lisent `TWITTER_POST_PRIORITY` dans l'env. Sous contention (streams finalize + charts run_all visent le même compte), le slot va au process le plus prioritaire puis au plus ancien en attente (fichiers `waiter_<acct>_<pid>.json` dans `%TEMP%/tsm_twitter_posts/`), pas au premier qui tape le verrou. Anti-famine : +1 cran toutes les `TWITTER_WAITER_AGING_SECONDS` (défaut 300). Barème utilisé : 0 = posts « priority early » pendant la collecte (debut releases, best-day-since early) ; 1 = tweets charts (`run_all_charts._build_env`) ; 3 = étapes streams finalize (`finalize_update._subprocess_env`) ; 4 = sweep album quotidien (jours de semaine). |
| `data_paths.py` | **Source de vérité de tous les chemins** (REPO_ROOT, runtime, snapshots, exports web, legacy) |
| `download.py` | Téléchargement CSV Spotify Charts |
| `history.py` | Gestion `ts_history.json` |
| `chart_comment.py` | Commentaire de chart partagé entre régions |
| `notify.py` | Notifications mobiles via ntfy.sh |
| `fmt.py`, `logger.py`, `git_ops.py`, `retention.py`, `album_emoji.py`, `swift_top_gate.py` | Formatage, log, git, rétention, emojis d'albums, gating TayBoard |

---

## 4. `collectors/apple_music/` — charts Apple Music

**Avant tout travail ici : charger le skill `collector-apple-music`** (briefing complet : `docs/apple-music-script-context.md`).

Orchestrateur : `run_apple_music.py` (appelé par `python -m tsm collect apple-music`) — `--no-post` (skip notification), `--no-images`, `--force-images`. `scraped_at` est arrondi par défaut au dernier slot pair Europe/Paris `00/02/04/.../22` (`APPLE_MUSIC_ROUND_SCRAPED_AT`, `APPLE_MUSIC_SNAPSHOT_TZ`, `APPLE_MUSIC_SNAPSHOT_HOURS`) et la même date est passée explicitement aux sous-collecteurs. Depuis 2026-08-30, `build_scraped_at()` écrit un timestamp **timezone-aware** (`2026-08-30T14:00:00+02:00`) au lieu d'une heure murale nue — le frontend le reconvertit dans le fuseau du visiteur (`parseCollectorTimestamp` / `formatCollectorClock` dans `tsm-frontend/frontend/src/utils/format.js`). Les vieilles lignes CSV sans offset sont traitées comme Europe/Paris côté frontend. Tous les consommateurs backend ne font que du `scraped_at[:10]` (slice date), donc le suffixe d'offset est inoffensif ; aucune migration de l'historique. Launcher : `run_apple_music.bat`. Un collecteur en échec = run abandonné (pas d'export/images/upload). Ordre du runner : `global.py` → `ts_page.py` → `ts_page_all.py` → `country_all.py` → `genre_all.py`.

| Fichier | Rôle / lancement |
|---|---|
| `country_all.py` | **(runner)** Songs + albums + vidéos par pays en 1 requête combinée (`types=songs,albums,music-videos`) ; écrit les 3 CSV legacy. `--countries`, `--date`, `--scraped-at`. Fallback per-type sur 400 ; abort si >5% storefronts en échec |
| `genre_all.py` | **(runner)** Songs + albums par (pays, genre) en 1 requête (`types=songs,albums&genre=`) ; écrit les 2 CSV legacy. idem |
| `global.py` | **(runner)** Top 100 global (playlist publique). `--date`, `--scraped-at` |
| `ts_page.py` | **(runner)** Top songs page artiste TS, **un seul storefront** (`DEFAULT_STOREFRONT` = `us`). `storefront`, `--date`, `--scraped-at`. Écrit `apple_music_ts_top_songs.csv` — **seule** entrée lue par TayBoard (`collectors/billboard/swift_top_100.py::_weekly_apple_music_ts_points`), ne pas rebrancher sur autre chose sans mettre à jour ce scoring calibré |
| `ts_page_all.py` | **(runner, 2026-08-17)** Même chart TS top songs mais agrégé sur **tous les storefronts découverts** (`core/storefronts.py::resolve_storefronts`, ~167 pays) en un classement composite (score `500/rank**0.75` par storefront, multiplié par poids marché puis sommé). Écrit `apple_music_ts_top_songs_global.csv`, séparé exprès du fichier single-storefront ci-dessus pour ne pas polluer le scoring TayBoard. Pagination plafonnée à `APPLE_MUSIC_TS_GLOBAL_DEPTH` (défaut 200 = 2 pages/storefront ; le catalogue TS complet fait ~675 titres/storefront). `ts_page_all.py` garde un gate interne une fois/jour (`APPLE_MUSIC_TS_GLOBAL_HOUR`, défaut `02`), mais `run_apple_music.py` lui passe `--force` à chaque run depuis le passage GitHub Actions data-only afin que l'export R2 ait toujours un composite frais. `--date`, `--scraped-at`. C'est ce fichier qu'`export_apple_music.py`/`upload_ap_r2.py` lisent désormais pour l'onglet site "TS Top Songs" |
| `country_charts.py` / `country_albums.py` / `music_video_charts.py` | Legacy per-type (manuels ; remplacés par `country_all.py` dans le runner) |
| `genre_charts.py` / `genre_album_charts.py` | Legacy per-type (manuels ; remplacés par `genre_all.py`) |
| `global_albums.py` / `top_music_videos.py` | Legacy hors runner (`storefront` pour top_music_videos) |
| `generate_country_card_images.py` | Cards PNG par pays. `date`, `--min-countries`, `--limit`, `--force` |
| `generate_snapshot_images.py` | PNG des snapshots. `--date`, `--region us\|fr\|global…`, `--genre` (avec `--region`), `--list-genres`, `--out-dir` |
| `core/` | `token.py` (jeton API + `TokenManager` refresh coordonné pour les pools), `http.py`, `storefronts.py`, `filters.py`, `csv_utils.py` (`previous_rank` = dernier snapshot du **jour précédent**, fenêtre lecture 30 j), `r2.py`, `config.py`, `export.py`, `models.py` |

---

## 4bis. `collectors/deezer/` — charts Deezer (ABANDONNÉ 2026-08-14)

**Avant tout travail ici : charger le skill `collector-deezer`.** Décision
produit : Deezer est abandonné (retiré du scoring TayBoard, remplacé par
YouTube — voir § 5 et `collector-billboard/CONTEXTE.md`). Aucun run
planifié n'existait pour ce collecteur au moment de la décision. Code
conservé, exécutable manuellement, mais mort tant qu'aucune nouvelle
décision ne le relance.

API publique Deezer (`api.deezer.com`), sans auth, 50 req/5s par IP.
Orchestrateur : `run_deezer.py` (appelé par `python -m tsm collect deezer`).
Pas de posting X, aucun commit/push git (seul l'upload R2 distribue la
donnée), comme Apple Music. Ordre du runner : `global.py` → `artist_top.py`
→ `artist_stats.py`, abandon du run si un collecteur échoue.

| Fichier | Rôle / lancement |
|---|---|
| `global.py` | **(runner)** Top 100 chart global Deezer (`/chart/0/tracks`), filtré aux pistes `artist.id == 12246` (Taylor Swift). `--date`, `--scraped-at` |
| `artist_top.py` | **(runner)** Top tracks de l'artiste (`/artist/12246/top`, déjà scopé — pas de filtre nécessaire). `deezer_popularity` = score interne Deezer (`rank` API), distinct du `rank` = position dans la liste |
| `artist_stats.py` | **(runner)** `nb_fan`/`nb_album` (`/artist/12246`), une ligne/jour, append-only (pas de logique de classement) |
| `core/` | `config.py` (BASE_URL, ARTIST_ID, limites), `http.py` (session/retry générique, sans auth — réutilisable tel quel), `filters.py`, `csv_utils.py` (même logique idempotente-par-jour qu'Apple Music) |

CSV dans `db/` (`deezer_global_chart.csv`, `deezer_artist_top_tracks.csv`,
`deezer_artist_stats.csv`), snapshots sous
`snapshots/deezer_charts/AAAA/MM/AAAA-MM-JJ/`. Exports :
`runtime/exports/web/site/data/deezer.json` /
`deezer_history.json` (`scripts/export_deezer.py`), upload R2 via
`scripts/upload_deezer_r2.py` (préfixes `deezer/snapshots`, `deezer/db`,
`deezer/history-by-song` dans `scripts/r2_keys.py`, mirrorés dans
`tsm-frontend/api/data/r2_keys.py`).

**Confirmé 2026-08-09 (Anas)** : `/chart/0/tracks` n'a pas de paramètre pays
explicite — la réponse est géolocalisée par l'IP de la requête, et c'est en
réalité le chart **France**, pas un chart mondial. Décision : renommage
complet plutôt qu'un simple correctif de libellé (`global.py` ->
`france.py`, `deezer_global_chart.csv` -> `deezer_france_chart.csv`,
`DEEZER_GLOBAL_*` -> `DEEZER_FRANCE_*`, libellés UI "Global Chart" ->
"France Chart") — **mis en pause volontairement**, pas encore fait (et sans
objet tant que le collecteur reste abandonné). Tout le code garde le nom
"global" pour l'instant, avec un commentaire TODO dans
`collectors/deezer/global.py` (le renommage correspondant côté
`swift_top_100.py`/`DEEZER_GLOBAL_WEIGHT` a disparu avec le retrait complet
de Deezer du scoring le 2026-08-14). Détail complet :
`collector-deezer/CONTEXTE.md`.

**N'alimente plus le scoring TayBoard depuis le 2026-08-14** (retiré,
remplacé par YouTube — voir § 5 et `collector-billboard/CONTEXTE.md` §
"Deezer retiré du scoring"). N'a jamais été déployé sur le VPS OVH.

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
| `update_youtube.py` | Collecteur principal (appelé par `python -m tsm collect youtube`). `--date` (= **jour d'activité voulu**, sans l'option : `run_date − 1j` — le run à minuit NY mesure la journée écoulée), `--dry-run`, `--debug` (écrit mais pas de git/notif), `--no-post` (pas de ntfy, ni du post/de la planification "first 24h views"), `--no-notify` (coupe UNIQUEMENT la ntfy quotidienne — les cards "first 24h views" restent planifiées/postées ; **c'est ce que `run_youtube.bat` utilise** depuis le 2026-08-30, avant c'était `--no-post` qui tuait aussi le first-day), `--bootstrap` (découverte complète de la chaîne, une seule fois, jamais de planification "first 24h"), `--commit` (git désactivé par défaut !), `--force` (remplace les lignes du jour), `--preview` (aperçu à la demande de la card "first 24h views" avec un fetch live, sans écrire le CSV ni poster), `--post-first-day VIDEO_ID` (interne — exécuté par la tâche Planificateur Windows one-off créée à la découverte d'une vidéo, pas un usage manuel courant). À la découverte d'une vidéo, planifie une tâche Windows Task Scheduler one-off (`TSM_YouTube_FirstDay_<video_id>`) pour `published_at+24h` qui poste alors une card @swiftiescharts "views in its first 24 hours" via `--post-first-day` ; `post_first_day_views()` reste un filet de sécurité quotidien si cette tâche échoue (cf. skill `collector-youtube`) |
| `videos/update_youtube.py` | Point d'entrée canonique (wrapper) |
| `core/` | `api.py` (YouTube Data API v3, stdlib), `channel.py` (catalogue vidéos), `csv_utils.py` (deltas), `title_groups.py` (agrégation par chanson), `git_ops.py`, `config.py` |
| `run_youtube.bat` | Launcher |

`title_groups.py::build_title_rows()` regroupe les vidéos par chanson pour `youtube_title_history.csv` (une vidéo officielle + son lyric video + son audio + ses remixes = une seule ligne titre). Deux sources, la manuelle prime :
1. **Override manuel** — `collectors/youtube/tools/json/video_groups.json` (`video_id → title_key`), édité via `scripts/youtube_grouping_editor/` (voir § 8). Gitignoré comme `video_db.json`/`youtube_history.json` (état local, pas de la donnée de catalogue versionnée). L'éditeur pré-remplit le board avec le regroupement automatique déjà en place (même logique `match_video_title`, override manuel prioritaire s'il existe) plutôt que de partir d'un pool vide — l'usage attendu est de corriger les erreurs, pas de reconstruire les groupes à la main. « Enregistrer » réécrit tout le fichier avec l'état courant du board (sauvegarde intégrale, pas un diff) : ça fige en override manuel tous les groupes affichés, pas seulement ceux modifiés.
2. **Fallback automatique** — matching flou du titre vidéo contre `db/discography/songs.json` (`song_family`/`title_clean`/`base_title`/aliases), sinon la vidéo devient son propre groupe.

Depuis le 2026-08-14, `youtube_title_history.csv` (`daily_views` par titre
groupé) alimente aussi le scoring TayBoard (`units_youtube`, § 5) —
voir `collector-billboard/CONTEXTE.md`.

Depuis le 2026-08-29, les deux CSV portent une colonne `snapshot_at` :
horodatage UTC ISO 8601 exact du run (`datetime.now(timezone.utc)` juste
après le batch-fetch des stats). `daily_views` d'une date D = delta entre le
`snapshot_at` de la ligne précédente et celui de la ligne D ;
`api/routes/youtube.py` (frontend) en dérive `window_start`/`window_end` pour
afficher la vraie fenêtre horaire sur la page YouTube Charts. Colonne ajoutée
en tête des `CSV_FIELDNAMES`/`TITLE_CSV_FIELDNAMES`, migration du header
automatique (`csv_utils._ensure_fieldnames` / `write_title_history`) ; lignes
historiques = `snapshot_at` vide, l'UI masque alors la mention.

**Dating `date` = jour d'activité (décision 2026-09-02).** Le run planifié
tourne à 06:05 Paris ≈ 00:05 New York (`YOUTUBE_COLLECTION_TZ`), soit minuit
NY : le delta `viewCount` couvre la journée NY qui vient de finir → `main()`
date les lignes `run_date − 1` (`activity_date`). `--date D` = jour d'activité
voulu directement. Historique existant réaligné une fois par
`scripts/shift_youtube_dates_back_one_day.py` (§ 8), puis R2 ré-uploadé
(`r2.upload_youtube()`) + `generate_home_highlights.py` re-run.
`ci_data_collector_gate.py::youtube_day_pending` cherche `run_date − 1`.
TayBoard : snapshots passés gelés, semaines futures = dates corrigées.

---

## 7. `collectors/comp/` — composants visuels partagés

Modules importés par les générateurs d'images (pas des scripts, sauf preview) :
`discography.py` (accès catalogue), `song_card.py` (helpers partagés image/palette/logo — son propre `render_song_card()` best_since/default n'est plus appelé par aucun script de prod depuis le 2026-08-26), `song_card_chart_sheet.py` (**la card chanson réellement postée** — `render_chart_sheet_card()`/`write_chart_sheet_card_png()`, 1080×594, cover art blurrée en fond, bar chart 14 jours avec callback historique dimmed pour best_since ; utilisée par `post_best_day_since_twitter.py` et `post_weekend_song_gainers.py` ; détail complet → skill `collector-comp`), `youtube_card.py` (card dédiée titres longs, ex. `collector-youtube` "first 24h views" — réutilise les helpers génériques de `song_card.py`), `tables_image.py`, `fmt.py`, `track_cover_cache.py`, `img_fetch.py`.
`img_fetch.py` = **fetch d'image → data-URI partagé et résilient** pour TOUS les générateurs de PNG. `fetch_data_uri(url)` : cache mémoire → cache disque persistant (`db/discography/.image_cache/`, un petit fichier par URL, gitignored) → download (3 tentatives par URL + fallback sur les autres tailles CDN Spotify `ab67616d0000b273/1e02/4851/1e03`). Une cover téléchargée une fois avec succès n'est **jamais re-téléchargée** → un coup de mou réseau (WARP instable, throttle `i.scdn.co`, timeout 8s) ne peut plus vider une cover qui a déjà marché (incidents : *The Fate of Ophelia* absent du chart global 2026-08-27, *Speak Now (TV)* absent de l'album update 2026-08-26). `tables_image.url_to_data_uri`/`download_as_data_uri` y délèguent ; `generate_album_update_image.py` et `generate_card_images.py` aussi. Les échecs ne sont PAS mis en cache (seulement mémoire pour le run courant).
`preview.py` = galerie de previews de tous les styles : `--date`, `--only FAMILLE`, `--output-dir`, `--limit`, `--min-days`, `--keep-html/--no-keep-html`.

---

## 8. `scripts/` — outils one-shot & maintenance

Avant tout travail ici : charger le skill `scripts-maintenance` (ordre du workflow d'enrichissement discographie, piège dry-run de `r2.py`, scripts destructifs).

| Fichier | Rôle / lancement |
|---|---|
| `r2_keys.py` | **Source de vérité des préfixes/clés R2** (`history/`, `history-by-track/`, `data/`, `db/`, `images/apple-music/`, `apple-music/*`, `chart-history-global-by-track/`, `cache/*`, `hiring`, `report-*`). Importé par `r2.py`, `upload_ap_r2.py`, `chartr2.py`, `download_apple_music_images.py`, `generate_home_highlights.py`, `migrate_app_r2.py`, `fetch_issues.py`, `fetch_hiring.py`, `check_r2_storage.py` — ne pas redéfinir un préfixe localement. Préserve les env-var overrides existants (`R2_STATIC_DATA_PREFIX` etc.). Miroir tenu à la main côté frontend : `tsm-frontend/api/data/r2_keys.py` (à garder synchronisé, même session si une valeur change) |
| `r2.py` (34 Ko) | **Upload R2 principal** (data/history/db/images). `--bucket`, `--new-date D` (une seule date), `--slugs a,b`, `--streams-daily`, `--charts-only`, `--worldwide-snapshot-only` (avec `--new-date`), `--skip-{history,static,db,images}-upload`, `--dry-run` (⚠️ opt-in ici, contrairement au reste de `scripts/` — sans ce flag, upload réel). Liste `json_mappings` = fichiers `data/*.json` poussés (songs/albums/artist/milestones/billboard/swift_top_100/applemusic*/charts_worldwide*/active_catalog_merges/**best_day_since** — ce dernier ajouté 2026-08-30, il manquait, cf. skill `spotify-streams`) |
| `generate_home_highlights.py` | Précalcule le pool de highlights de la Charts Gallery + les dates de dernier snapshot (`version`), à partir des données locales déjà exportées (`WEB_EXPORT_DATA_DIR`/`WEB_EXPORT_HISTORY_DIR`, `db/charts_history_*.csv`) — évite au frontend de refaire ce calcul à chaque requête. Upload `cache/home_highlights.json` et `cache/version.json` sur R2 (mêmes clés lues par `tsm-frontend/api/data/precompute_cache.py`). Utilise directement `charts_history_global.csv` (track_id/movement déjà résolus par le collector) plutôt que le matching flou du frontend — ne pas y porter la logique de `api/routes/charts.py`. Le highlight `best_day_since` réutilise directement les fonctions de `collectors/spotify/streams/best_day_since.py` (mêmes filtres/tri que ce qui serait posté sur Twitter) plutôt que de redupliquer la logique de regroupement par `song_family` ; un 2e highlight `oldest_record` surface séparément le record `kind="since"` au plus grand `days_since` du jour (perdu sinon quand un `best_ever` du même jour gagne le pick principal). `regional_climb` : plus gros bond de rang jour/jour (seuil `_REGIONAL_CLIMB_THRESHOLD = 20`) tous charts confondus (`global`/`fr`/`us`/`uk`, worldwide inclus). `top_album`/`top_era`/`tayboard_1` ne sont ajoutés que si le chart a ≤ 2 jours (`_TAYBOARD_MAX_AGE_DAYS`) par rapport à `latest_date` (évite d'afficher un #1 TayBoard vieux d'une semaine). `--dry-run` (affiche sans uploader), `--quiet` (utilisé quand appelé depuis un autre collector). Appelé en best-effort (jamais bloquant) en fin de `update_streams.py` (via `finalize_update.py`), `run_all_charts.py`, `run_apple_music.py`, `swift_top_100.py` et `swift_top_albums.py` |
| `export_for_web.py` | Wrapper → `streams/extras/export_for_web.py`. `--new-date`, `--dry-run` |
| `check_r2_storage.py` | Alerte ntfy si le stockage R2 dépasse les seuils. `--dry-run`, `--bucket-limits b=size,…`, `--warning-percent`, `--topic`. `--breakdown` : mode séparé, lecture seule — taille/nb d'objets par préfixe R2 (`list_objects_v2` sur les préfixes de `r2_keys.py`) pour objectiver ce qui grossit ; coûte des requêtes List, à lancer manuellement/hebdo, pas sur le cron de l'alerte de seuil |
| `migrate_app_r2.py` | Copie bucket public → bucket app. `--dry-run`, `--overwrite`, `--key`, `--prefix` |
| `upload_ap_r2.py` | Upload Apple Music vers R2 (JSON, snapshots par date, CSV du jour, history-by-song incl. vidéos). `--bucket`, `--prefix`, `--dry-run` |
| `sync_apple_music_snapshots_from_r2.py` | **Sens inverse** de `upload_ap_r2.py` : reconstruit les CSV quotidiens locaux `snapshots/apple_music_charts/YYYY/MM/YYYY-MM-DD/apple_music_{global,country_charts,genre_charts,ts_top_songs}.csv` depuis `apple-music/snapshots/{timestamp}.json` sur R2 (jamais supprimé, historique complet). Nécessaire depuis le passage d'Apple Music au VPS OVH (2026-07-30, voir § 12) : la machine locale qui fait tourner `swift_top_100.py` n'a plus aucune écriture Apple Music locale, donc son historique diverge silencieusement de R2 (incident 2026-08-09, voir piège `collector-billboard/CONTEXTE.md`). Dry-run par défaut (liste ce qui serait écrit), `--apply` pour écrire ; `--start`/`--end` (défaut : lendemain du dernier jour local détecté → aujourd'hui) ; `--force` pour écraser un jour déjà présent localement |
| `prune_apple_music_snapshots.py` | Rétention snapshots Apple Music : garde le dernier snapshot par jour passé, lignes retirées archivées en `.csv.gz` dans `_pruned_archive/` (dry-run par défaut). `--apply`, `--since`, `--no-archive` |
| `prune_apple_music_images.py` | Supprime les objets R2 orphelins de `images/apple-music/` (adressés par `md5(url CDN Apple)`, non référencés par le CSV Apple Music courant — voir skill `scripts-maintenance` § "R2 : données pérennes vs cache"). Dry-run par défaut, `--apply` pour supprimer, manifeste local des clés supprimées sauf `--no-archive`, `--bucket` |
| `ci_data_collector_gate.py` | **CI seulement**, utilisé par le `workflow_dispatch` de `run-data-only-collectors.yml` (escape hatch manuel). Décide `apple_music`/`youtube`/`youtube_commit` (→ `$GITHUB_OUTPUT`) : sur `workflow_dispatch` les inputs `collector`/`commit_youtube` gagnent. Contient aussi une logique de fraîcheur (slot snapshot 2h Europe/Paris manquant sur R2 ; ligne YouTube du jour `America/New_York` absente) datant de la tentative cron 2026-08-28→29, abandonnée |
| `chartr2.py` | Upload R2 des charts |
| `build_spotify_chart_discography.py` | Precalcule le payload "History view" de Spotify Charts (`runtime/exports/web/site/data/charts_discography/{region}.json`, un par region/pays deja vu — global/fr/us/uk + tout pays worldwide), lu par `tsm-frontend/api/routes/charts.py::load_charts_discography`. Agrege `db/charts_history_{global,fr,us,uk}.csv` + tous les snapshots `worldwide/.../ts_worldwide_*.json` par track_id : `last_*`, `peak_rank`, `peak_streams`, `total_days`, et depuis 2026-08-26 `longest_streak`/`longest_streak_active` (plus long streak consecutif jamais atteint sur ce chart, actif si le streak courant l'egale encore). Depuis 2026-08-28 : `days_at_peak` (nombre de jours OBSERVES exactement au `peak_rank` — minorant, 0 = non observe) et `current_streak` (streak courant, 0 si la chanson ne charte plus a la derniere date). Ecrit aussi `{region}_previous.json` (meme agregation en excluant la derniere date chartee de chaque region/pays — baseline stable pour le mouvement de rang du History view) et `peaks_by_track.json` (lookup a plat `"<track_id>|<country>" -> {peak_rank, peak_streams, peak_streams_date, days_at_peak, total_days, current_streak, longest_streak, longest_streak_active}`, consomme par `charts.py` pour enrichir les lignes du chart worldwide "Overall" — `peak_streams`/`days_at_peak` absents du snapshot brut). Ecrit aussi `song_countries.json` (pivot par track_id : `{track_id: {song_name, image_url, latest_date, countries:[{country, country_name, last_date, last_rank, last_streams, peak_rank, peak_streams, peak_streams_date, total_days, longest_streak, longest_streak_active, current_streak, days_at_peak}]}}`, consomme par `charts.py::/api/charts/discography/song{s,/{track_id}}` pour le mode "Per Song" du History view (`/songs` = defaut : toutes les chansons du dernier snapshot Global ; `/song/{id}` = une chanson via le selecteur) — ajoute 2026-08-31). `--regions` fait un rebuild partiel qui MERGE dans le `peaks_by_track.json` ET le `song_countries.json` existants. Appele automatiquement par `run_all_charts.py` (defaut : tout ; `scripts/r2.py` uploade tout `charts_discography/*.json`) |
| `fetch_issues.py` | Récupère les signalements du site depuis R2. `--save`, `--images`, `--delete` |
| `fetch_hiring.py` | Récupère les candidatures (préfixe `hiring/`). `--json`, `--role` |
| `backfill_discography_from_spotify.py` | Backfill de la discographie depuis Spotify. `--apply` (déf. dry-run), `--no-backup`, tracks musicales par défaut, `--include-non-songs` pour ajouter commentary/karaoke/instrumental en DB et les collecter comme `chart_extra`, `--exclude-non-songs` (défaut), `--skip-api`, `--recent-releases N`, `--target-release-date`, `--quiet` |
| `backfill_spotify_track_metadata.py` | Backfill métadonnées tracks. `--apply`, `--track`, `--limit`, `--skip-existing`, `--sleep` |
| `backfill_global_charts.py` | Backfill `charts_history_global/fr.csv`. `--charts`, `--start`, `--end`, `--dl-workers`, `--filter-workers`, `--headless`, `--dry-run` |
| `backfill_track_cover_cache.py` | Pré-chauffe `track_cover_cache.json` |
| `assign_tsm_ids.py` | **IDs internes stables du catalogue.** Attribue/synchronise `tsm_song_id` (par chanson, toutes versions confondues) et `tsm_album_id` (par album) — base36 opaque, **gelés à vie**, écrits dans le registre `db/discography/tsm_id_registry.json` (append-only, source de vérité) ET dans chaque track object de `db/discography/`. Régénère `db/discography/catalog_index.json` (commité) + `catalog_index.csv` (gitignoré) : vue à plat, 1 ligne/entrée track, colonnes de schéma décodées + `catalog_code` lisible + `counts_toward_era`. Renommage de `song_family` détecté via recoupement des `track_id`/`historical_track_ids` → ID conservé. Dry-run par défaut, `--apply`, `--no-backup`, `--quiet`. À relancer après toute édition de `db/discography/` — idempotent (0 écriture si rien n'a changé). Détail → skill `scripts-maintenance` § "IDs internes du catalogue" |
| `enrich_genres.py` | Genres par chanson. `--apply`, `--track`, `--set-genres`, `--sources`, `--refresh-cache`, `--limit`, `--skip-existing` |
| `infer_track_flags.py` | Déduit les `filter_tags` (ancien schéma) de la discographie. `--apply`, `--track`, `--limit`, `--csv`, `--json`. ⚠️ Probablement obsolète depuis `migrate_discography_schema.py` (son rôle — déduire des tags depuis des champs flous — disparaît puisque `role`/`extra_type`/`category` sont désormais explicites) ; pas encore supprimé, à confirmer avant de le faire |
| `migrate_discography_schema.py` | **Phase 1 de la refonte du schéma discographie** (voir § 9 "Schéma de discographie"). Dry-run par défaut (écrit `data/schema_migration_review.csv`, liste des tracks au mapping ambigu à revalider), `--apply` pour écrire (backup `.schema-migration-<horodatage>.bak`), `--no-backup`. Additif uniquement : n'écrit que les nouveaux champs (`on_album`/`role`/`extra_type`/`category`/`release_edition`/`display_album`/`tags`), ne touche/supprime jamais `type`/`edition`/`section`/`filter_tags`/`display_era` — donc zéro impact sur les scripts qui les lisent encore (Phase 2, chantier séparé). Corrige aussi `song_family` pour les extras mal reliés (ex. karaoké/voice memo qui pointaient vers leur propre famille au lieu de la chanson de base), via un index des titres "ancres" (tracks non suffixés) — jamais par vote/majorité entre extras, qui s'est révélé peu fiable sur cette DB (incohérences préexistantes de nommage) |
| `discography_editor/` | **GUI locale d'édition de `db/discography/`** (voir aussi § 9). `python scripts/discography_editor/server.py [--port 8765] [--no-browser]`, ou `run_discography_editor.bat` à la racine. `catalog.py` = logique pure (charge/aplati/déplace/sauvegarde, testable sans serveur) ; `server.py` = `http.server` stdlib (zéro dépendance) qui sert `static/` (SPA vanilla JS) + `GET /api/state` + `POST /api/save` + `POST /api/mark-done`. Édite le **nouveau schéma** (`on_album`, `role`, `extra_type`, `category`, `release_edition`, `display_album`, `tags`) — pas les anciens `type`/`edition`/`filter_tags`, laissés intacts sur disque pour ne rien casser côté Phase 2. Sauvegarde `/api/save` : copie en mémoire → garde-fou (compte total de tracks conservé avant/après, sinon tout est annulé) → re-parse JSON de validation → backup `.discoedit-<horodatage>.bak` → écriture atomique (fichier temp + `os.replace`) → rechargement disque. V1 = édite/déplace des tracks existants uniquement (pas de création/suppression de track, pas de nouveau fichier d'album). Case « Fait » (chanson déjà vérifiée) : bookkeeping pur, appliquée immédiatement via `/api/mark-done`, stockée dans `discography_editor/review_state.json` (identité = track_id) — **hors `db/discography`**, jamais lu par un collector |
| `youtube_grouping_editor/` | **GUI locale pour combiner manuellement des vidéos YouTube** en un seul titre (voir aussi § 6). `python scripts/youtube_grouping_editor/server.py [--port 8766] [--no-browser]`, ou `run_youtube_grouping_editor.bat` à la racine. `catalog.py` = logique pure (charge `video_db.json` + `video_groups.json`, valide, sauvegarde) ; `server.py` = `http.server` stdlib, sert `static/` (tableau/board vanilla JS, une colonne par groupe + une colonne "Non groupées", glisser-déposer ou sélecteur par carte) + `GET /api/state` + `POST /api/save`. Sauvegarde en un seul bloc (pas de diff par ligne comme `discography_editor`) : re-valide (video_id connu, pas assigné à deux colonnes, titre non vide si la colonne contient des vidéos), backup `.ytgroup-<horodatage>.bak`, écriture atomique, rechargement disque. N'écrit que `collectors/youtube/tools/json/video_groups.json` — ne touche jamais `db/discography/songs.json` |
| `header_editor/` | **GUI locale de préparation des headers d'images** (albums de la discographie + targets globales Top Songs/Top Albums/Top Eras), interaction en glisser-déposer plutôt que des menus déroulants. `python scripts/header_editor/server.py [--port 8787] [--no-browser]`, ou `run_header_editor.bat` à la racine. `server.py` = `http.server` stdlib (zéro dépendance) qui sert `static/` (SPA vanilla JS, thème clair/sombre) + `GET /api/albums` (targets + variantes déjà sauvegardées + `coverUrl` Spotify lu depuis `db/discography/covers.json`, clé = nom d'album exact) + `GET /headers/<slug>/<variant>` + `POST /api/save`. Flux : import (input fichier, glisser-déposer sur le canvas, ou coller depuis le presse-papiers) → l'image atterrit dans un **shelf** (colonne de gauche) → édition (zoom molette centré sur le curseur, glisser **à l'intérieur du canvas** pour repositionner le crop, flèches clavier pour un nudge pixel-près avec Shift = 10px, brightness/contrast/saturation, Fit Cover/Center/Reset Filters, "Copy filters to all" pour répliquer brightness/contrast/saturation sur les autres images déjà ouvertes du shelf) → **glisser la barre "⠿ Drag onto a target below to save" sous le canvas** (pas le canvas lui-même, qui ne fait que repositionner le crop en interne) **sur la pochette cible** dans la grille (colonne de droite, une carte par album avec sa cover Spotify + une carte par target globale) pour l'enregistrer ; la sauvegarde retire l'image du shelf. On peut aussi glisser directement une vignette du shelf sur une carte (même comportement, la barre sous le canvas n'est qu'un raccourci plus visible/plus gros pour l'image active). Cliquer une carte cible charge son header existant dans le shelf pour le retoucher (si plusieurs variantes, la carte s'ouvre en accordéon avec une vignette par variante, cliquer une vignette charge celle-là) ; un champ de nom par image du shelf sert de nom de sauvegarde (vide = auto-nommé par le serveur). Pas de bouton Save/Save All explicite — le geste de dépôt (ou le clic sur une carte pour recharger) est la seule voie de sauvegarde, volontairement, pour rester rapide sur un gros lot d'images. Export PNG 2212×608 (aperçu canvas 1106×304, même ratio). Retirer une image du shelf sans la sauvegarder : bouton × ou touche Suppr/Backspace quand la carte a le focus (sans intercepter la frappe dans ses champs) ; "Clear" vide tout le shelf (confirmation, n'efface aucun fichier disque). Sauvegarde serveur : backup `.bak.<horodatage>` si une variante existante est écrasée. Variantes d'album dans `db/discography/headers/<album>/` (fallback legacy : `db/discography/headers/<album>.png`) ; variantes globales dans `collectors/spotify/streams/tools/headers/{top_songs,top_albums,top_eras}/`. Consommé par `generate_album_update_image.py` (piochage multi-variantes par album), `generate_streams_image.py` (`top_songs/`) et `generate_albums_image.py` (`top_eras/`), chacun avec fallback vers l'ancien dossier commun si le nouveau dossier est vide |
| `enrich.py` / `add_display.py` / `fix_songs_json.py` / `fix_song_images.py` / `fill_images.py` / `split_albums.py` / `list_songs.py` | Maintenance discographie/données |
| `fill_streams_from_archive.py` | Backfill `streams_history.csv` depuis les Daily Archive |
| `shift_youtube_dates_back_one_day.py` | **One-off (fait le 2026-09-02).** Décale de −1 jour la colonne `date` des 2 CSV `db/youtube_*_history.csv` — le collector datait chaque ligne avec la date du run (minuit NY) alors que le delta couvre la journée écoulée. Dry-run par défaut, `--apply`, `--expect-latest YYYY-MM-DD` (garde anti-double-run), `.csv.<ts>.bak` (gitignoré). Après : `r2.upload_youtube()` + `generate_home_highlights.py`. Cf. `collector-youtube/CONTEXTE.md` § Regles data |
| `backfill_spotify_charts_history.py` | **Backfill worldwide résumable, no-post.** Découpe la plage en `--workers` chunks contigus (1 process long par chunk via `worldwide/daily.py --dates-file --no-post --backfill-mode`, capé au nb de `spotify_session*.json`), état JSON reprenable par date (`--state`, défaut `worldwide/tools/json/run_all_backfill_done.json`). `--start` (défaut 2017-01-01) / `--end` (défaut hier), `--limit`, `--sleep`, `--refetch-done`, `--no-force`, `--dry-run`. **Débit / anti-hang** : `daily.py` sérialise TOUTES les requêtes via un `RequestPacer` global, donc `--request-interval SECONDS` (défaut 1.0 ; ~1 req/interval/worker, ~68 régions/date) est le vrai levier, pas `--concurrency N` (régions en parallèle par worker, défaut 6, override le split `SPOTIFY_WORLDWIDE_TOTAL_CONCURRENCY`). `--fetch-max-attempts N` (défaut 8, borné : une région coincée en 429/WARP ne gèle plus une date à l'infini — `daily.py` seul est à 0=illimité). `--rate-limit-max N` (défaut 30) cape une pause 429 `GlobalPause` (sinon backoff multiplicatif 20→…→300 s = silence total). Monter `--request-interval` vers 1.5-2.0 au premier `429 - pause globale` ; `--workers 1` si ça 429 en boucle (2 workers depuis la même IP = 2x plus de 429). **Reprise** : thread watcher qui checkpoint le state file toutes les `--progress-interval` s (déf. 30) en scannant les snapshots écrits + `[PROGRESS] N/total (~Yh left)` ; un kill perd ≤30 s, relancer reprend. Sync final = enrichit seulement la plage touchée. **31 régionaux abandonnés par Spotify ~2019-08-24** (`DISCONTINUED_REGIONS`) sont auto-exclus pour les dates postérieures au cutoff (le lot d'un worker est splitté en deux sous-appels) ; `--include-discontinued-regions` garde tout, `--regions` court-circuite. `--regions CODE…` / `--exclude-regions CODE…` : forwardé à `daily.py` (fill ciblé région par région, ex. `--regions fr`). **`--per-region-sweep [CODE…]`** : boucle séquentielle région par région — un run `daily.py --regions <une>` par région, ne fetche que les dates-gap de SON `charts_history_<code>.csv`. **Une seule région par run = une requête par `--request-interval`, pas de cascade 429 parallèle** (bien plus doux que N régions/date). Bare = tous les `charts_history_<cc>.csv` 2 lettres non quasi-vides, historique le plus riche d'abord ; checkpoint (`state['swept_regions']`) après chaque région, reprenable. `uk` fetché comme le chart `gb`. `--gaps-from-csv [REGION…]` : dates pending = dates ABSENTES de `db/charts_history_<region>.csv` (store durable git, pas la présence de snapshot local souvent incomplet) ; flag nu = `global`, plusieurs régions = union des trous — **meilleur signal pour un gros rattrapage historique**. `--no-sync` (saute les 4 étapes de sync finales : `sync_spotify_country_charts_from_worldwide` → `backfill_charts_history_track_ids --rebuild-ts-history` → `enrich_spotify_worldwide_snapshots` → `backfill_total_days`), `--upload-r2` (push R2 des dates touchées, ignoré si `--no-sync`). Entrypoint aussi via `run_all_charts.py --backfill --backfill-from/-to --backfill-workers` |
| `sync_spotify_country_charts_from_worldwide.py` | Resynchronise les charts pays depuis worldwide. `--charts`, `--dry-run` |
| `repair_charts_fr_archive_from_history.py` | Répare l'archive FR. `dates…`, `--archive`, `--history-root`, `--dry-run` |
| `reset_swift_top_100_history.py` | ⚠️ Reset de l'historique Swift Top 100. Dry-run par défaut ; `--yes` pour supprimer, `--remove-bonuses`, `--skip-r2` |
| `refresh_spotify_session.py` | Rafraîchit `spotify_session.json` |
| `download_apple_music_images.py` / `merge_am_csv.py` | Outils Apple Music |
| `export_apple_music.py` | Export CSV → `applemusic.json` (+ section `last_charted` précalculée) et `applemusic_history.json` **fenêtré** (`APPLE_MUSIC_HISTORY_DAYS`, défaut 30 j, jours passés réduits à leur dernier snapshot, JSON compact) |
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
| `db/` | Source de vérité : `discography/artist.json` (catalogue maître), `discography/albums/`, `track_cover_cache.json`, `charts_history_*.csv`, `streams_history.csv`, headers d'albums | Modifier via les scripts, ou via `scripts/discography_editor/` (GUI locale) pour de l'édition manuelle ciblée — jamais à la main dans l'éditeur de texte |
| `data/` | Archives datées (`data/2024/…`) + `_archive/` (migrations) + `_tmp/` (scénarios de test) | Lecture seule en pratique |
| `snapshots/` | Snapshots datés : `spotify_charts/`, `spotify_streams/`, `apple_music_charts/`, `billboard/`, `tayboard/`, `recap/` | Écrits par les collectors |
| `runtime/` | `exports/web/` (payloads générés avant upload R2), `tmp/`, `spotify_streams/` | Non versionné, régénérable |
| `website/` | ⚠️ **Site statique LEGACY** — ne JAMAIS y écrire sauf demande explicite (`website/site/data`, `website/site/history` = anciens exports) | Interdit (règle CLAUDE.md) |

### Schéma de discographie (`db/discography/`) — en cours de refonte

Chaque track a maintenant, EN PLUS des anciens champs (`type`, `edition`, `section`, `filter_tags`, `display_era` — encore lus par pas mal de scripts, cf. `migrate_discography_schema.py` ci-dessus) :

| Champ | Valeurs | Sens |
|---|---|---|
| `on_album` | bool | fait officiellement partie du tracklist d'un album ? |
| `role` | `single` \| `album_track` \| `extra` | si `on_album=true` (⚠️ `single` n'est jamais déduit automatiquement — aucun champ source ne le distingue de `album_track`, c'est à taguer à la main via l'éditeur) |
| `extra_type` | `live` \| `remix` \| `commentary` \| `karaoke` \| `voice_memo` \| `acoustic` \| `demo` \| `instrumental` \| `other` | si `role=extra` ; sert aussi de regroupement d'affichage/génération d'image |
| `category` | `soundtrack` \| `feature` \| `collab` \| `other` | si `on_album=false`. **feature** = Taylor `primary_artist` (autre artiste en featured) ; **collab** = un autre artiste est `primary_artist`, Taylor en featured |
| `release_edition` | `standard` \| `deluxe` \| `platinum` \| `anthology` \| `from_the_vault` \| `til_dawn` \| `3am` \| `taylors_version` \| ... | si `on_album=true` — enum ouvert, pas figé |
| `display_album` | string \| null | ex-`display_era`, renommé, même usage (afficher sous un album sans compter dans son tracklist) |
| `tags` | liste réduite (ex. `christmas`) | ex-`filter_tags` — uniquement les thèmes qui ne rentrent dans aucun champ ci-dessus |
| `song_family` | string | inchangé mais **fiabilisé** par la migration (relie maintenant correctement karaoké/voice memo/etc. à leur chanson de base) |
| `tsm_song_id` | string \| null | **ID interne gelé** de la chanson (toutes versions confondues), base36 opaque. Écrit par `scripts/assign_tsm_ids.py`. Clé de jointure cross-collector + clé d'URL de partage. Source de vérité = `db/discography/tsm_id_registry.json` (le champ ici en est un miroir). |
| `tsm_album_id` | string \| null | idem au niveau album (null pour les entrées `songs.json`/`misc.json`/`features.json`) |

`slug` (lisible) et `catalog_code` (`REDTV/std/05` — segmenté, signifiant) sont **recalculés à chaque run** et vivent uniquement dans `tsm_id_registry.json` + `db/discography/catalog_index.{json,csv}` (jamais dans les fichiers de disco — ils re-churneraient ~20 fichiers à chaque retouche de titre). `catalog_index` = vue à plat, 1 ligne/entrée track, pour « s'y retrouver » dans le catalogue. Détail → skill `scripts-maintenance` § "IDs internes du catalogue". `tsm_song_id` + `tsm_slug` sont portés dans l'export web (`data/songs.json`, `data/albums.json`) par `export_for_web.py::load_tsm_slug_map()`.

Statut : migration Phase 1 appliquée le 2026-07-29 (voir `data/schema_migration_review.csv` pour les ~131 tracks au mapping incertain à revalider via l'éditeur). **Phase 2 (pas encore faite)** : migrer chaque consommateur des anciens champs vers les nouveaux, puis retirer les anciens — gros chantier séparé touchant `best_day_since.py`, `history_store.py`, `generate_album_update_image.py`, `enrich_genres.py`, `export_for_web.py` (doit continuer à produire les mêmes champs de sortie `type`/`edition`/`display_section` pour ne rien casser côté `tsm-frontend`/`api/routes/period_recaps.py`), `swift_top_albums.py`, `swift_top_100.py`, `backfill_discography_from_spotify.py`.

---

## 10. Racine, docs, CI

| Fichier | Rôle |
|---|---|
| `run_daily.bat` | Launcher Task Scheduler : `python -m tsm daily` (python3.13 WindowsApps) |
| `run_all_charts.bat` | Launcher : `python -m tsm collect charts` |
| `docs/runbook.md` | Runbook : layout canonique + commandes principales + règles de sécurité |
| `docs/data-layout-audit.md` | Audit généré par `python -m tsm audit data --write` |
| `docs/apple-music-script-context.md` | Briefing d'audit du pipeline Apple Music (architecture, comportement d'échec, points fragiles) — version condensée dans le skill `collector-apple-music` |
| `README.md` / `README_FULL.md` / `CONTRIBUTING.md` / `AGENTS.md` / `CLAUDE.md` | Docs générales / instructions IA |
| `DEPLOYMENT_AUDIT.md`, `GITHUB_SECRETS_SETUP.md`, `add_github_secrets.py` | Setup GitHub Actions (secrets) |
| `setup.py`, `requirements.txt`, `.python-version` | Packaging/deps Python |
| `.github/workflows/` | Tous `disabled` côté GitHub, `workflow_dispatch` manuel seulement (collecte = Task Scheduler local). `run-data-only-collectors.yml` = escape hatch Apple Music/YouTube quand le PC est éteint (`scripts/ci_data_collector_gate.py` route les inputs) ; `run-apple-music.yml` legacy équivalent ; `run-all-charts.yml`, `update-streams.yml`, `check-r2-storage.yml`, `keepalive.yml` idem |
| `.claude/skills/` | Skills Claude Code : `tsm-map`, `pipeline-ops`, `data-rules`, `image-gen`, `deploy`, `admin-work`, `style-rules`, `collector-apple-music` (série « un skill par collecteur » — à charger avant tout travail sur le collecteur correspondant) |

Les `.bat` du Task Scheduler vivent dans **`tsm-frontend/tasks/`** (`run_spotify_streams.bat`, `run_spotify_charts_global.bat`, `run_spotify_charts_fr.bat`, `watch_logs.bat`) et font `cd` vers ce repo.

## 11. `dev/` — ad-hoc (jetable)

Scripts de vérification/debug ponctuels, non maintenus : `adhoc/checks/` (vérifs CSV/formules), `adhoc/debug/`, `adhoc/verify/`, `maintenance/` (fix de colonnes CSV, régénération d'images d'albums), tests rapides `test_*.py` à la racine. Exceptions utiles :
- `adhoc/period_streams_recap.py` — thread récap de période. `--period`, `--date`, `--current`, `--start/--end`, `--top`, `--no-images`, `--post --yes`
- `adhoc/post_test_tweet.py` / `adhoc/schedule_test_tweet.py` — smoke-tests Twitter (`--text`, `--at`, `--yes` requis pour poster)
- `adhoc/render_album_ranking_cards.py`, `adhoc/update_album_rankings_r2.py` — cards Album Ranking figées + upload R2

## 12. Déploiement VPS OVH (YouTube + Apple Music) — DÉCOMMISSIONNÉ 2026-08-17

**Ce VPS a été supprimé le 2026-08-17** (coût jugé disproportionné, ~74€/mois
pour 2 crons légers — voir `OVH.md` section « Décommissionnement »).
**État actuel (2026-08-29) : `collectors/youtube` et `collectors/apple_music`
tournent en local via le Planificateur de tâches Windows** (`TSM Apple Music
Every 4 Hours` : `run_apple_music_hidden.vbs` → `run_apple_music.bat`, repeat
2h ; `TSM YouTube Videos Daily` : `run_youtube.bat`, 06:05 Europe/Paris).

Bascule GitHub Actions tentée le 2026-08-28 (`run-data-only-collectors.yml`,
cron fréquent + `scripts/ci_data_collector_gate.py`) puis **abandonnée le
2026-08-29** : le `schedule:` natif de GitHub est trop peu fiable pour une
cadence 2h (retardé au top de l'heure, runs droppés sous charge — constaté :
2 runs planifiés sur ~13 attendus, ~1 h de retard). Les workflows
`run-data-only-collectors.yml` et `run-apple-music.yml` sont désormais
`disabled` côté GitHub + `workflow_dispatch` manuel seulement, gardés comme
escape hatch de rattrapage quand le PC est éteint (le gate route les inputs
`collector`/`commit_youtube`). Si un jour manque, lancer une fois le workflow
en manuel ou relancer la tâche locale.

Note : si un futur besoin de rendu PNG apparaît dans `run-data-only-collectors.yml`,
il faut y ajouter `playwright` + `playwright install chromium --with-deps` +
Pillow (le job n'installe qu'un jeu de deps minimal ; `run-apple-music.yml`
fait déjà le setup complet). Sinon `--notify-global-only` suffit avec les deps
minimales depuis que `generate_snapshot_images.py` importe `tables_image` en
lazy.

Section VPS conservée ci-dessous pour l'historique, au cas où l'option
reviendrait (viser un flavor plus petit que `b3-16` cette fois).

Depuis le 2026-07-30 (et jusqu'au 2026-08-17), `collectors/youtube` et
`collectors/apple_music` ont tourné en prod sur une instance OVH Public
Cloud (Ubuntu, `cron`, plus le Planificateur de tâches Windows pour ces
deux-là — désactivé pendant cette période). Contexte et essais complets
dans `OVH.md` à la racine.

- Repo cloné dans `~/tsm-backend` via une **Deploy Key GitHub dédiée**
  (`~/.ssh/id_ed25519_github`, write access, scoping limité à ce repo — pas
  la clé perso d'Anas).
- venv Python (`~/tsm-backend/.venv`) avec seulement les deps nécessaires à
  ces deux collectors (pas tout `requirements.txt` Spotify/Billboard) :
  `requests urllib3 aiohttp boto3 python-dotenv Pillow pandas playwright`
  (+ `playwright install --with-deps chromium`, requis par
  `generate_country_card_images.py`).
- `~/tsm-backend/.env` réduit aux clés utilisées par ces deux collectors :
  `YOUTUBE_API_KEY`, `UPLOAD_TO_R2`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
- Fuseau horaire VPS réglé sur `Europe/Paris` (`timedatectl`) pour que les
  horaires cron matchent ceux du Planificateur de tâches Windows sans calcul
  manuel de décalage UTC/DST.
- Wrappers `~/tsm-backend/run_youtube_vps.sh` et `run_apple_music_vps.sh`
  (`git pull` puis la même commande que le `.bat` Windows d'origine), logs
  dans `~/logs/`.
- Crontab (`Europe/Paris`) :
  ```
  5 6 * * * run_youtube_vps.sh        # identique à l'ancien job Windows
  0 */4 * * * run_apple_music_vps.sh                # equiv. 4h UTC (ancien run-apple-music.yml)
  ```
- Apple Music ne fait **aucun** commit/push git (jamais fait, même en local
  — seul l'upload R2 distribue la donnée) ; YouTube fait `git commit --push`
  réel depuis le VPS (`--commit --no-post`, identique au `.bat`).
- Spotify (streams + charts, via WARP) et Billboard (scrape direct, pas une
  API) **restent en local** — Spotify testé et bloqué (WAF 403) depuis une
  IP OVH même à travers WARP ; Billboard pas testé mais jugé à risque
  similaire (scrape direct de site, pas une API officielle).

---

## Conventions & pièges

- **Dry-run d'abord** : quasiment tous les scripts destructifs ont `--dry-run` ou exigent `--apply`/`--yes` — toujours utiliser le dry-run avant.
- **Locks anti-double-post** : fichiers `*_posted.lock` ; les flags `--force` les ignorent.
- Commits data : `charts run all YYYY-MM-DD`, `youtube views YYYY-MM-DD` (voir `git log`).
- Panne connue : **WARP instable → lecture infinie** pendant la collecte ; tuer le process et relancer. Jour manqué → `reconcile_gap_catchup.py`.
- `collectors/spotify/core/data_paths.py` = source de vérité des chemins ; ne pas hardcoder.
- Buckets R2 : prod site = `taylor-data` ; le `.env` local du frontend pointe `taylor-app`.
