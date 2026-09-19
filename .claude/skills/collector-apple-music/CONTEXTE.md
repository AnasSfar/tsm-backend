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

Scheduler : prod tourne **en local via le Planificateur de taches Windows**
(`TSM Apple Music Every 4 Hours`, action = `run_apple_music_hidden.vbs` ->
`run_apple_music.bat`, repeat toutes les 2h). Ni GitHub Actions ni VPS.
**Depuis le 2026-09-09, `run_apple_music.bat` lance aussi `collectors/itunes/run_itunes.py`
juste apres** (charts d'achats iTunes Store — collecteur separe, skill
`collector-itunes`, meme cadence, log `collectors/itunes/run_itunes.log`,
tourne quel que soit le code de sortie d'Apple Music). Ne pas casser la 2e
ligne du `.bat` en modifiant la 1re.
Tente sur GitHub Actions le 2026-08-28 (`run-data-only-collectors.yml` +
`scripts/ci_data_collector_gate.py`), re-bascule en local le 2026-08-29 :
le `schedule:` natif de GitHub est trop peu fiable pour une cadence 2h
(retarde au top de l'heure, runs droppes sous charge). Les workflows
`run-apple-music.yml` ET `run-data-only-collectors.yml` restent
`workflow_dispatch` manuel + `disabled` cote GitHub, comme escape hatch de
rattrapage quand le PC est eteint (utilise `ci_data_collector_gate.py` pour
router les inputs). Historique VPS OVH (2026-07-30 -> 2026-08-17) :
`REPO_CONTEXT.md` section « Deploiement VPS OVH » et `OVH.md`.

## Entrypoint

Commande principale:

```powershell
python .\collectors\apple_music\run_apple_music.py
```

Le runner lance, avec le meme `--scraped-at`:

1. `global.py`
2. `ts_page.py`
3. `ts_page_all.py` (collecte brute, chaque cycle -> `*_raw.csv`)
4. `finalize_ts_top_songs_daily.py` (agrege la veille en 1 ligne/chanson si pas deja fait -> CSV canonique)
5. `country_all.py`
6. `genre_all.py`
7. `scripts/export_apple_music.py`
8. `generate_country_card_images.py`
9. `generate_snapshot_images.py`
10. `scripts/upload_ap_r2.py`, sauf `UPLOAD_TO_R2=0`

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
  tables synchronisees si TayBoard change la sienne. Depuis le 2026-09-19,
  TS Top Songs applique en plus `APPLE_MUSIC_TS_IMPORTANT_STOREFRONT_BOOST`
  (defaut x3) aux 11 storefronts affiches sur les fiches chanson
  (`us,gb,fr,ca,au,de,jp,br,mx,it,es`) : le rang global reste tous pays, mais
  doit etre beaucoup plus ancre dans les marches que l'utilisateur peut
  inspecter, au lieu d'etre propulse par la longue traine de petits stores.
  Ecrit un CSV **separe**
  (`apple_music_ts_top_songs_global.csv`) : `ts_page.py` garde son fichier
  single-storefront intact car c'est la seule source lue par le scoring
  TayBoard (`_weekly_apple_music_ts_points`) — brancher ce dernier sur le
  composite fausserait ce score deja calibre.
  **Agregation par ISRC** (fallback `apple_music_id` si ISRC vide, depuis
  2026-09-10) : Apple sert un `apple_music_id` different par catalogue regional
  pour le meme master 2014-era (Wildest Dreams / Blank Space / Style / Shake It
  Off... ont un id US, un international, un Japon-deluxe, tous meme ISRC).
  Keyer sur l'id eclatait la chanson en 2-3 entrees concurrentes, chacune ne
  portant que ses storefronts et une fraction du score marche (Wildest Dreams
  sortait #10 en portant gb/au/de mais US/CA/JP vides car sur un autre id).
  L'ISRC identifie le master independamment de la sortie -> fusionne les
  repackagings deluxe/platinum/EP, garde Taylor's Versions / remixes / lives
  separes (ISRC propre). ~13 % du catalogue etait fragmente. L'`apple_music_id`
  ecrit au CSV = celui du fragment le mieux classe (peut changer d'un jour a
  l'autre ; le fallback previous_rank par nom couvre ce cas). Verifie : 1 seul
  merge discutable (Teardrops on My Guitar absorbe son "Radio Single Remix" car
  Apple leur met le meme ISRC — accepte).
  Pagination plafonnee a
  `APPLE_MUSIC_TS_GLOBAL_DEPTH` (**defaut 400 = 4 pages/storefront depuis le
  2026-09-10**, avant 200 — le catalogue TS complet fait ~675 titres/storefront,
  mais au-dela du rang ~400 le score composite se joue a des fractions de
  point = bruit, pas du signal ; rang 400 ~= 6 pts sur ~500 pour le rang 1).
  Le classement global est l'**union** des tops de tous les storefronts : une
  chanson est classee des qu'elle apparait dans les N premiers d'au moins un
  storefront ; une colonne storefront a `–` = pas dans le top N de ce pays ce
  jour-la (0 point la, jamais une estimation).
  **Architecture collecte/publication (refonte 2026-09-19)** : `ts_page_all.py`
  n'a plus de gate horaire — il collecte a **chaque** cycle Apple Music (00h,
  02h, ..., 22h, comme le reste d'Apple Music), mais ecrit dans un fichier
  **brut** distinct (`apple_music_ts_top_songs_global_raw.csv`, jamais lu par
  l'export/le site). Un nouveau script, `finalize_ts_top_songs_daily.py`,
  tourne juste apres dans `run_apple_music.py` (meme liste `SCRIPTS`) et
  cible **la veille** par rapport a la date du run — des qu'un jour est
  termine (son dernier cycle 22h est passe), le prochain run (celui de 00h du
  lendemain) agrege TOUS les cycles bruts de ce jour en **une seule ligne
  finale par chanson** dans le CSV canonique
  (`apple_music_ts_top_songs_global.csv`, celui que l'export lit) — logique
  d'agregation dans `core/ts_top_songs_daily.py::compute_final_rows` (score
  jour = somme de `500/rank**0.75` sur tous les cycles ou la chanson est
  apparue ce jour-la, fusion par ISRC/apple_music_id comme avant). Idempotent :
  si le CSV canonique du jour a deja des lignes, `finalize_ts_top_songs_daily.py`
  ne fait rien (sauf `--force`) — l'appeler a chaque cycle est donc sans
  risque, il ne travaille reellement qu'une fois, au premier cycle du
  lendemain. `previous_rank` chaine toujours uniquement sur le jour
  calendaire precedent (jamais intra-jour), donc finaliser un jour ne
  recalcule que l'annotation `previous_rank` du jour suivant, jamais son
  classement.
  **Consequence** : le jour en cours (aujourd'hui) n'a **aucune** ligne dans
  le CSV canonique tant qu'il n'est pas termine — le site continue d'afficher
  le dernier jour COMPLET jusqu'a ce que le run de minuit du lendemain
  finalise. Ancien design (gate `APPLE_MUSIC_TS_GLOBAL_HOUR`, une seule
  collecte/jour publiee immediatement) abandonne le meme jour : il ne
  refletait que le cycle tombant sur l'heure du gate, pas la journee entiere.
  **Backfill historique (2026-09-19, one-off)** :
  `backfill_ts_top_songs_daily_final.py` a applique retroactivement la meme
  agregation sur tout l'historique (2026-07-30 -> 2026-09-18, ou chaque jour
  avait 4 a 45 cycles concurrents faute de la separation brut/final
  ci-dessus) ; garde un `.bak` par fichier modifie. Ne pas rejouer sauf bug
  trouve dans l'agregation — le pipeline live n'en a plus besoin.
  **Piege frontend (corrige le 2026-09-18, toujours vrai)** :
  `scripts/export_apple_music.py` construit `applemusic.json["dates"]` comme
  union de TOUTES les sources (global chart, TS composite, country/genre
  charts) ; ces dernieres tournent bien toutes les 2h en continu, et
  `_mirror_flat_current_to_latest` recopie le composite TS (inchange) sur
  chaque heure de cette union pour que l'API `/api/apple-music?date=` ne
  renvoie jamais un bucket vide a une heure sans vrai run — ce mirroring
  reste necessaire cote API, donc `dates` continuera de compter chaque cycle
  de 2h meme si le composite TS lui-meme ne change qu'une fois/jour.
  `applemusic.json["ts_top_songs_dates"]` (dates reelles, non mirrorees, du
  CSV composite canonique — 3e valeur de retour de `build_top_songs`,
  capturee avant tout mirroring) reste la source de verite pour l'affichage :
  `AppleMusic.jsx` scope son selecteur d'heure (`apple_snapshot_time`) dessus
  quand `tab === "ts_top_songs"`, donc l'onglet TS Top Songs Global n'affiche
  jamais plus d'une heure/jour, y compris pour aujourd'hui si le jour n'est
  pas encore finalise (il n'a alors aucune entree du tout, cf. ci-dessus).

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
- `core/ts_top_songs_daily.py` (2026-09-19): agregation brut-cycles -> classement final du jour pour TS Top Songs Global, partagee par `finalize_ts_top_songs_daily.py` et `backfill_ts_top_songs_daily_final.py`.

## Donnees et sorties

CSV principaux dans `db/` (ou le dossier snapshot du jour, `DB_DIR` est date-dependant):

- `apple_music_ts_top_songs.csv` (single-storefront `us`, input TayBoard uniquement)
- `apple_music_ts_top_songs_global_raw.csv` (2026-09-19, ecrit par `ts_page_all.py` a chaque cycle 2h ; jamais lu par l'export — donnees internes seulement)
- `apple_music_ts_top_songs_global.csv` (composite final, **une ligne par chanson par jour**, ecrit uniquement par `finalize_ts_top_songs_daily.py` une fois le jour termine ; alimente l'onglet site "TS Top Songs" via `export_apple_music.py`/`upload_ap_r2.py` — aucune ligne pour le jour en cours tant qu'il n'est pas finalise)
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
- `scraped_at` (depuis 2026-08-30) est timezone-aware : `2026-08-30T14:00:00+02:00`
  (`build_scraped_at()` dans `run_apple_music.py`). L'offset vient de
  `APPLE_MUSIC_SNAPSHOT_TZ` (Europe/Paris). Ne pas revenir à une heure murale
  nue : le frontend s'en sert pour convertir le snapshot dans le fuseau du
  visiteur. Comparaisons internes = `scraped_at[:10]` uniquement, garder ça.
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
- **Bug corrige (2026-08-29) : `--notify-global-only` echouait sur
  `ModuleNotFoundError: playwright` quand playwright/Pillow ne sont pas
  installes, et l'echec bloquait l'upload R2**
  (`run_apple_music.notify_global_update` -> `sys.exit(1)`). Vu sur le
  workflow `run-data-only-collectors.yml` (deps minimales) apres le retrait
  de `--no-post` : `generate_snapshot_images.py` importait
  `collectors.comp.tables_image` (playwright + Pillow) au niveau module meme
  pour le chemin notif-only qui n'envoie qu'un texte ntfy. Corrige en rendant
  l'import `tables_image` lazy (dans `_rows_html` et `generate()`). En local
  le probleme ne se voit pas (venv complet), mais garder l'import lazy.
