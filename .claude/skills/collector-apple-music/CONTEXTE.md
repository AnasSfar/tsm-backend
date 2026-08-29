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
un controle de publication Twitter. Ne fait jamais de commit/push git (seul
l'upload R2 distribue la donnee).

Scheduler : depuis le 2026-08-29, prod tourne via **GitHub Actions**
(`.github/workflows/run-data-only-collectors.yml`, job `data-only`), pas en
local. Le `schedule:` natif de GitHub etant non fiable (retarde au top de
l'heure, runs droppes sous charge), le workflow fire un cron frequent
(`9,29,49 * * * *`) et `scripts/ci_data_collector_gate.py` reduit a un no-op
(~10s) tout run inutile : Apple Music ne tourne que si le slot snapshot 2h
Europe/Paris courant (meme arrondi que `run_apple_music.build_scraped_at`,
importe pour rester synchro) est encore absent de
`apple-music/db/apple_music_global.csv` sur R2. Un slot manque est
irrecuperable (charts = etat live), d'ou 3 firings/heure.
Le workflow legacy `run-apple-music.yml` reste `disabled` cote GitHub +
`workflow_dispatch` seulement. Historique : Windows Task Scheduler local
(`TSM Apple Music Every 4 Hours`) jusqu'au 2026-08-28 ; VPS OVH du
2026-07-30 au 2026-08-17. Detail : `REPO_CONTEXT.md` section « Deploiement
VPS OVH » et `OVH.md`.

## Entrypoint

Commande principale:

```powershell
python .\collectors\apple_music\run_apple_music.py
```

Le runner lance, avec le meme `--scraped-at`:

1. `global.py`
2. `ts_page.py`
3. `ts_page_all.py`
4. `country_all.py`
5. `genre_all.py`
6. `scripts/export_apple_music.py`
7. `generate_country_card_images.py`
8. `generate_snapshot_images.py`
9. `scripts/upload_ap_r2.py`, sauf `UPLOAD_TO_R2=0`

Options runner:

- `--no-post`: flag legacy sans effet Twitter reel.
- `--no-images`: saute les images.
- `--force-images`: regenere les images.

## Scripts

Scripts combines quotidiens:

- `country_all.py`: songs + albums + music-videos par pays en un appel quand
  possible; fallback per-type si l'appel combine est rejete.
- `genre_all.py`: songs + albums par genre.
- `ts_page_all.py` (2026-08-17): variante composite de `ts_page.py`, agregee
  sur tous les storefronts decouverts (`core/storefronts.resolve_storefronts`,
  ~167 pays) au lieu d'un seul. Score par storefront = `500/rank**0.75` *
  poids marche (meme courbe et meme table `AM_MARKET_WEIGHTS` que le scoring
  Apple Music de TayBoard, `collectors/billboard/swift_top_100.py`, dupliquees
  localement expres pour eviter un import cross-collector — us=1.00,
  gb=0.70, jp=0.55, de/fr/ca=0.50, ... defaut 0.08 pour les marches non
  listes), somme -> classement global. Sans cette ponderation un #1 dans un
  marche ou TS est marginale compterait comme un #1 US ; garder les deux
  tables synchronisees si TayBoard change la sienne. Ecrit un CSV **separe**
  (`apple_music_ts_top_songs_global.csv`) : `ts_page.py` garde son fichier
  single-storefront intact car c'est la seule source lue par le scoring
  TayBoard (`_weekly_apple_music_ts_points`) — brancher ce dernier sur le
  composite fausserait ce score deja calibre. Pagination plafonnee a
  `APPLE_MUSIC_TS_GLOBAL_DEPTH` (defaut 200, soit 2 pages/storefront — le
  catalogue TS complet fait ~675 titres/storefront, la queue au-dela du rang
  200 pese <10 pts sur ~500 pour le rang 1, donc negligeable). Ne tourne pour
  de vrai qu'une fois/jour (`APPLE_MUSIC_TS_GLOBAL_HOUR`, defaut `02`, les
  autres passages du cron 4h se skippent avec exit 0 — `--force` bypasse) car
  le site n'affiche que le dernier snapshot et ~167 storefronts x plusieurs
  runs/jour serait un cout API inutile.

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

- `apple_music_ts_top_songs.csv` (single-storefront `us`, input TayBoard uniquement)
- `apple_music_ts_top_songs_global.csv` (composite tous storefronts, alimente l'onglet site "TS Top Songs" via `export_apple_music.py`/`upload_ap_r2.py`)
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

**Best rank since (2026-08-14)** : `collectors/apple_music/best_rank_since.py`
detecte le meilleur rang Global Top 100 d'une chanson depuis au moins 14 jours
(reutilise `core.rank_since.compute_rank_since`, meme primitif que Spotify
Charts). Lit l'union `db/apple_music_global.csv` + snapshots quotidiens via
`apple_music_daily_csv_paths` (deja utilise ailleurs, ne pas reinventer),
regroupe les scrapes multiples d'un meme jour en gardant le dernier
`scraped_at` (meme regle que `export_apple_music.py::window_rows()`). **Ne
declenche jamais `kind="best_ever"`** (toujours appele avec
`release_date=None, history_start_date=None`) — l'historique local ne remonte
qu'a quelques mois (2026-06-05 sur cette machine, VPS prod depuis
2026-07-30), donc pas assez profond pour revendiquer un record "de tous les
temps" en confiance ; meme principe que la regle NEW ci-dessus. Le highlight
produit (`type="best_rank_since", source="apple_music"`) reste affiche 14
jours apres declenchement — mecanisme dans `generate_home_highlights.py`, pas
ici. Seuils/decisions produit → skill `data-rules` § "Home highlights".

## Pieges

- **`.gitignore` exclut tous les `.csv` du repo, et `db/apple_music_*.csv`
  n'est jamais force-ajoute** (contrairement a YouTube qui fait
  `git add -f` dans `git_ops.py`). Un `git clone` frais (nouvelle machine,
  VPS, CI) n'a donc AUCUN historique CSV/snapshot Apple Music. Consequence
  observee le 2026-07-30 sur le VPS OVH : sans `snapshots/apple_music_charts/`
  ni `db/apple_music_*.csv`, `previous_rank` ne trouve rien a comparer et le
  catalogue entier part en NEW faux, publie sur R2 sans garde-fou (pas de
  blocage automatique comme pour Streams). Avant tout premier run reel sur
  une nouvelle machine : copier `snapshots/apple_music_charts/` (au moins
  les ~35 derniers jours, `PREV_RANK_WINDOW_DAYS=30` dans `core/csv_utils.py`)
  et `db/apple_music_*.csv` depuis une machine qui a l'historique. Detail :
  `OVH.md` a la racine du repo.
- Les imports `from core...` dependent du `PYTHONPATH` injecte.
- `TSM_DATA_DATE` et chemins de config peuvent etre evalues a l'import.
- Un subset de pays sur la vraie date peut produire un snapshot partiel.
- Un changement de schema CSV doit etre reporte dans export, upload R2, images
  et frontend si necessaire.
- **Bug corrige (2026-08-02) : notif ntfy global marquait NEW au lieu de RE**
  (`generate_snapshot_images.py`). La fonction cherchait le titre dans
  `runtime/exports/web/site/data/songs.json` / `website/site/data/songs.json`
  (exports runtime gitignores, jamais alimentes sur le VPS Apple Music car ils
  viennent de l'export Spotify qui n'y tourne pas), avec un fallback vers
  `db/discography/songs.json` qui est quasi vide (le vrai catalogue vit dans
  `db/discography/albums/*.json`). Resultat : catalogue "connu" vide sur le
  VPS -> toute reentree (`previous_rank` absent) etiquetee NEW dans la notif
  seulement (CSV/JSON restaient corrects via `previous_rank`). Corrige en
  lisant `release_date` depuis `db/discography/albums/*.json` +
  `songs.json`/`features.json`/`misc.json` (committes, toujours presents apres
  clone) et en appliquant la meme fenetre de 21 jours que
  `tsm-frontend/api/routes/apple_music.py` (`_is_recent_release`) au lieu
  d'un simple test d'appartenance au catalogue.
- **Bug corrige (2026-08-29) : `--notify-global-only` echouait en CI sur
  `ModuleNotFoundError: playwright`, et l'echec bloquait l'upload R2**
  (`run_apple_music.notify_global_update` -> `sys.exit(1)`). Cause : commit
  "Enable Apple Music global notification" (retrait de `--no-post`) alors que
  `run-data-only-collectors.yml` n'installe qu'un jeu de deps minimal, et
  `generate_snapshot_images.py` importait `collectors.comp.tables_image`
  (playwright + Pillow) au niveau module meme pour le chemin notif-only qui
  n'envoie qu'un texte ntfy. Corrige en rendant l'import `tables_image` lazy
  (dans `_rows_html` et `generate()` seulement). Si un futur besoin de rendu
  PNG apparait dans le workflow data-only, ajouter `playwright` +
  `playwright install chromium --with-deps` + Pillow aux deps du job (cf.
  `run-apple-music.yml` qui fait deja ce setup complet).
