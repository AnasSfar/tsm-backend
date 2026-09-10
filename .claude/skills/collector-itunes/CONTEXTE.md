# Contexte Collector iTunes

Créé le 2026-09-09. Demande : suivre les charts **d'achats iTunes Store**
(Top Songs + Top Albums) par pays, en plus des charts streaming Apple Music
déjà collectés. Data-only + onglet frontend dédié, pas de post X.

## Rôle

`collectors/itunes` collecte les charts **d'achats** iTunes Store pour Taylor
Swift :

- Top Songs par storefront (`rss/topsongs`) ;
- Top Albums par storefront (`rss/topalbums`) ;
- filtre Taylor Swift (artist id `159260351` ou nom) ;
- export JSON puis upload R2.

C'est un signal **distinct** de `collectors/apple_music` (streaming /
most-played). Le pipeline ne poste jamais sur X et ne commit jamais git —
seul l'upload R2 distribue la donnée (même modèle qu'Apple Music / Deezer).
N'alimente pas TayBoard ni les home highlights.

## Source de données

Flux RSS legacy, sans auth :

```
https://itunes.apple.com/{storefront}/rss/topsongs/limit=100/json
https://itunes.apple.com/{storefront}/rss/topalbums/limit=100/json
```

- Forme JSON : `{"feed": {"entry": [...]}}`. `entry` absent = chart vide,
  dict seul = 1 entrée, liste sinon.
- Pas de rang explicite → position 1-based dans le flux (filtrée Taylor
  ensuite, la position réelle est conservée).
- Champs utiles : `im:name` (titre), `id.attributes.im:id` (= `apple_music_id`),
  `im:artist.label` + `im:artist.attributes.href` (→ artist id par regex
  `/artist/[^/]+/(\d+)`), `category.attributes.term`/`im:id` (genre),
  `im:image` (artwork, upscalé en remplaçant `NNxNNbb.png`),
  `im:collection.im:name` (album, pour les songs), `im:releaseDate.label`
  (ISO 8601 — **on garde seulement la date**, la version localisée dans
  `attributes.label` est ignorée). Le flux Top Songs n'a **pas** de
  releaseDate ; seul Top Albums en a un.
- Cap à 100 entrées — suffisant : en semaine de sortie Taylor tient les
  premières places.

## Entrypoint

```powershell
python .\collectors\itunes\run_itunes.py          # ou : python -m tsm collect itunes
```

Ordre du runner (`run_itunes.py`, calqué sur `run_deezer.py` +
`build_scraped_at` de `run_apple_music.py`) :

1. `charts.py` (Top Songs + Top Albums, tous storefronts)
2. `scripts/export_itunes.py`
3. `scripts/upload_itunes_r2.py` (sauf `UPLOAD_TO_R2=0`)

Un échec de collecteur = run abandonné (pas d'export / upload).
`--no-post` : flag legacy sans effet.

## Storefronts

`core/storefronts.resolve_storefronts()` :

1. override `ITUNES_COUNTRIES="us,gb,jp,..."` si présent ;
2. sinon découverte live Apple Music
   (`apple_music.core.storefronts.resolve_storefronts`, ~167 pays) — **nécessite
   le jeton MusicKit d'Apple Music** (cache partagé
   `collectors/apple_music/tools/json/apple_music_token.json`) ;
3. fallback : liste statique `apple_music.core.config.COUNTRIES`.

### Throttling — important

Le flux RSS legacy `itunes.apple.com/rss` **throttle agressivement en
HTTP 403** (pas 429) sous rafale de requêtes — bien moins tolérant que l'API
AMP d'Apple Music. Mitigations dans le code :

- `ITUNES_WORKERS` déf. **3** (pas 12), + jitter par requête
  (`ITUNES_REQUEST_JITTER_MAX`).
- `_fetch` retente 403/429/503 avec backoff exponentiel
  (`ITUNES_THROTTLE_RETRIES` déf. 4, `ITUNES_THROTTLE_BASE_SLEEP` déf. 2 s) ;
  la `Retry` urllib3 ne gère que connexion + 5xx durs pour ne pas compounder.
- Après la passe threadée, **passe de rattrapage séquentielle** : chaque
  storefront encore en échec est refait un par un, session neuve, 5 s
  d'espacement. C'est ce qui récupère `us`/`gb` quand ils se font throttle.
- Un 404 = chart vide (normal, jamais compté).

Gate d'abandon (après la passe de rattrapage) :

- **storefront critique** (`ITUNES_CRITICAL_STOREFRONTS`, déf. `us,gb`) encore
  en échec → abort ;
- sinon taux d'échec > `ITUNES_MAX_FAILURE_PCT` (déf. **25%**) → abort ;
- sinon on publie avec ce qu'on a — un snapshot iТunes 2-horaire qui rate le
  Danemark reste un snapshot valide, le suivant backfille et `previous_rank`
  tolère le trou (contrairement au pipeline streams Spotify où un jour
  partiel est une faute de données).

⚠️ En dev, ne pas enchaîner 5-10 runs complets en quelques minutes : l'IP se
fait throttle globalement et **tous** les storefronts renvoient 403 pendant un
moment, même en séquentiel. Tester avec `ITUNES_COUNTRIES="us,gb,ca"` et
espacer les runs.

## Données et sorties

CSV (idempotents par `scraped_at`, chemin réel
`snapshots/itunes_charts/AAAA/MM/AAAA-MM-JJ/`) :

- `itunes_top_songs.csv` — colonnes : `date, scraped_at, country, chart_type
  (itunes_country), song_name, apple_music_id, rank, previous_rank,
  image_url, url, artist_name, album_name, genre_names, release_date`
- `itunes_top_albums.csv` — idem sans `album_name`, `chart_type =
  itunes_country_albums`

`previous_rank` : dernier snapshot d'un **jour antérieur** (jamais un rerun
du même jour), via `core/csv_utils.load_previous_ranks` (copie de la version
Apple Music pointée sur `itunes_daily_csv*`). Env
`ITUNES_REQUIRE_PREVIOUS_RANKS=1` pour refuser un run sans historique.

Helpers chemins : `collectors/spotify/core/data_paths.py` →
`itunes_charts_dir`, `itunes_daily_csv`, `itunes_daily_csv_paths`.

Exports (`scripts/export_itunes.py`, gate `ITUNES_SKIP_EXPORT`) :

- `runtime/exports/web/site/data/itunes.json` — `scraped_at`, `dates`,
  `last_charted` (`{songs, albums}`, meilleur dernier jour charté par pays,
  calculé sur tout l'historique CSV), `top_songs` / `top_albums`
  (`{date, countries:{cc:[entries]}}`, dernier snapshot).
- `itunes_history.json` — fenêtré `ITUNES_HISTORY_DAYS` (déf. 30 j), jours
  passés réduits à leur dernier snapshot, compact. `{dates, country,
  country_albums}`.
- `itunes_history_dates/*.json` + `index.json` — splits par date.

Upload R2 (`scripts/upload_itunes_r2.py`, préfixes dans `scripts/r2_keys.py`,
mirrorés `tsm-frontend/api/data/r2_keys.py`) :

- `data/itunes.json`, `data/itunes_history.json`
- `itunes/snapshots/{date}.json` (`ITUNES_SNAPSHOTS_PREFIX`)
- `itunes/history-by-date/*.json` (`ITUNES_HISTORY_BY_DATE_PREFIX`)
- `itunes/db/*.csv` (`ITUNES_DB_PREFIX`) — dernier CSV quotidien
- Pas d'objet per-song en v1 (contrairement à Apple Music).

## Variables d'env

`ITUNES_COUNTRIES`, `ITUNES_CHART_LIMIT` (déf. 100), `ITUNES_WORKERS`
(déf. **3**), `ITUNES_TIMEOUT`, `ITUNES_RETRY_TOTAL`, `ITUNES_RETRY_BACKOFF`,
`ITUNES_THROTTLE_RETRIES` (déf. 4), `ITUNES_THROTTLE_BASE_SLEEP` (déf. 2.0),
`ITUNES_REQUEST_JITTER_MAX` (déf. 0.4), `ITUNES_MAX_FAILURE_PCT` (déf. **25**),
`ITUNES_CRITICAL_STOREFRONTS` (déf. `us,gb`), `ITUNES_SNAPSHOT_HOURS` /
`ITUNES_SNAPSHOT_TZ` / `ITUNES_ROUND_SCRAPED_AT` (arrondi `scraped_at`),
`ITUNES_SKIP_EXPORT`, `ITUNES_HISTORY_DAYS`, `ITUNES_REQUIRE_PREVIOUS_RANKS`,
`UPLOAD_TO_R2=0`.

## Scheduler

**Pas de tâche dédiée.** `collectors/apple_music/run_apple_music.bat` (tâche
`TSM Apple Music Every 4 Hours`, repeat 2 h) lance `run_itunes.py` **juste
après** `run_apple_music.py`, dans le même `.bat` :

- séquentiel après Apple Music → le jeton MusicKit du cache est déjà frais
  quand `resolve_storefronts` le lit ;
- tourne **quel que soit** le code de sortie d'Apple Music (pas de
  `goto :error` entre les deux lignes) ;
- log séparé : `collectors/itunes/run_itunes.log`.

`collectors/itunes/run_itunes.bat` + `run_itunes_hidden.vbs` existent pour un
lancement manuel / rattrapage, **pas** branchés à une tâche.

## Pièges

- **`.gitignore` exclut tous les `.csv`**, aucun n'est force-ajouté (comme
  Apple Music, contrairement à YouTube). Un clone frais / une nouvelle
  machine n'a **aucun** historique local → `previous_rank` vide, tout le
  catalogue en NEW faux si on publiait sans garde-fou. Avant un premier run
  réel : copier `snapshots/itunes_charts/` (≥ 35 j, `PREV_RANK_WINDOW_DAYS`
  = 30) depuis une machine à jour, ou re-télécharger depuis R2
  (`itunes/snapshots/`). Pas de script de resync R2→local pour l'instant
  (Apple Music en a un — `sync_apple_music_snapshots_from_r2.py` — parce
  qu'une 2e machine consomme sa donnée pour TayBoard ; iTunes n'a pas ce
  besoin).
- Les imports `from core...` dépendent du `PYTHONPATH` injecté par le runner
  (`REPO_ROOT` + `collectors/itunes`).
- `resolve_storefronts` importe `collectors.apple_music.core.*` → si le jeton
  MusicKit ne peut pas être récupéré (401, hors-ligne), fallback silencieux
  sur la liste statique. Pour un run déterministe / offline : forcer
  `ITUNES_COUNTRIES`.
- `release_date` n'existe que pour les albums ; vide pour les songs — ne pas
  s'en servir pour un badge NEW, utiliser `db/discography` comme Apple Music.
- Changement de schéma CSV → répercuter dans `export_itunes.py`,
  `upload_itunes_r2.py`, et le futur `api/routes/itunes.py` + page React.

## Frontend (câblé le 2026-09-09)

Onglet **iTunes Charts** à `/amcharts/itunes` (réutilise le préfixe
`APPLE_MUSIC_CHARTS`, page distincte d'Apple Music). Calqué sur Deezer
(simple), pas sur la page Apple Music (complexe).

- `tsm-frontend/api/routes/itunes.py` : `GET /api/itunes` (no-date = dernier
  payload, `?date=` = snapshot via history/R2), `GET /api/itunes-last-charted`.
  Le snapshot R2 (`country_charts`/`country_album_charts`) est **normalisé**
  vers la forme `top_songs.countries` / `top_albums.countries` du payload
  courant — un seul shape côté front.
- `api/data/loader.py` : `load_itunes` / `load_itunes_history` /
  `load_itunes_snapshot` (mêmes patterns local↔R2 que Deezer).
- `api/index.py` : router enregistré + `_ROUTE_META` + `_ROUTE_PRELOAD` pour
  `/amcharts/itunes`.
- `frontend/src/pages/ITunes.jsx` + `styles/ITunes.css` (préfixe `itunes-`,
  grid 3 colonnes calquée sur `Deezer.css`). Tabs Top Songs / Top Albums,
  `CountryPicker` (pays présents dans le snapshot, priorité us/gb/fr…),
  `CalendarPicker`, fallback "last charted" quand un pays est vide.
- `client.js` : `getITunes` / `getITunesLastCharted` + TTL `ITUNES_*`.
- **Vue "Overall"** (comme Apple Music) : option `overall` en tête du
  `CountryPicker` (défaut quand ≥2 pays), agrège chaque chanson/album sur tous
  les pays en un bloc (`.overall-song-block` + `.overall-country-table`
  partagés de `Charts.css`), trié par nombre de pays décroissant puis meilleur
  rang. Groupe par `apple_music_id` (deux éditions d'un même titre = deux
  blocs, comme Apple Music).
- **NEW vs RE (garde 21 j)** : `api/routes/itunes.py::_tag_new_releases` ne
  pose `is_new_release=true` que si la `release_date` (ISO des albums, sinon
  lookup catalogue via `charts._build_song_enrichment_indexes`) est à ≤ 21 j
  de la date du snapshot. Le front affiche **RE par défaut** quand il n'y a
  pas de `previous_rank`, **NEW seulement si `is_new_release`** — comme
  `AppleMusic.jsx`. Sans ça, un trou d'historique (storefront throttlé) ou
  une réentrée d'un vieux titre s'affichait à tort "NEW" (règle `data-rules` :
  jamais inférer NEW d'un historique incomplet). Au 1er jour de collecte, tout
  s'affiche RE (honnête : pas de baseline).
- Nav : entrée dans la section "charts" de `Nav.jsx` (icône Apple Music
  partagée) ; `getSiteContext` / `isPrimaryLinkActive` gèrent la collision de
  préfixe `/amcharts/itunes` vs `/amcharts/apple-music` (checks explicites,
  iTunes AVANT le check générique apple-music).
- `App.jsx` : route + pageClass `page-itunes`.
- SEO : `SEO.jsx`, `sitemap.xml`, `ChartsGallery.jsx` (carte), `en.json`
  (clés `itunes_*` / `nav_itunes` — autres langues via `i18n:translate`).
- `useDataVersion.js` : `pcClearPrefix("itunes:")` piggyback sur le signal
  Apple Music (même cadence, `run_apple_music.bat` lance les deux).
- **`latest_itunes_date` / `latest_itunes_updated_at`** ajoutés à
  `api/routes/version.py` (chemins caché ET calculé) + `scripts/generate_home_highlights.py`
  (2026-09-09) → la tuile ChartsGallery iTunes a maintenant sa date "Last
  snapshot" et se trie par fraîcheur comme les autres.
- **Highlight `itunes_1`** (Charts Gallery, 2026-09-09) : Taylor #1 sur un
  chart d'achats iTunes d'un pays → highlight du plus gros marché. Backend
  `generate_home_highlights.py::compute_itunes_highlight` + fallback
  `api/routes/home_highlights.py` (les deux ont la table `_ITUNES_MARKET_PRIORITY`,
  à garder en phase) + `ChartsHighlights.jsx` case `itunes_1` + clé
  `hl_itunes_1` + accent CSS `.hl-card-itunes_1`. Détail → `data-rules`
  § Home highlights.
- `api/data/r2_keys.py` : `ITUNES_SNAPSHOTS_PREFIX` / `ITUNES_HISTORY_BY_DATE_PREFIX`.

Pas encore fait : capture OG (`_OG_SCREENSHOT_PATHS` + backend
`generate_og_screenshots.py::MAIN_PAGES` — miroirs à garder synchro, la page
prend l'image générique en attendant). Déploiement Vercel via le skill
`deploy` (build `frontend` OK au 2026-09-09).
