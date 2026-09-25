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
  image_url, url, artist_name, album_name, genre_names, release_date,
  explicitness`
- `itunes_top_albums.csv` — idem sans `album_name`/`explicitness`, `chart_type
  = itunes_country_albums`

### Doublons explicit/clean (2026-09-24)

Un même titre peut apparaître deux fois dans un même pays avec le **même**
`album_name` mais un `apple_music_id` différent : Apple vend l'album en
édition "explicit" et "cleaned" séparément (deux fiches catalogue distinctes),
et le flux RSS ne porte **aucun** flag explicit/clean (`im:explicit` absent du
feed topsongs — vérifié en direct). Ce n'est pas un bug de collecte, c'est
fidèle au chart réel.

`charts.py::main()` détecte ces doublons après collecte (même
`(country, song_name normalisé, album_name)`, id différent) et résout
`collectionExplicitness` en un **seul appel groupé** (`core/lookup.py::
resolve_explicitness`, iTunes Lookup API `/lookup?id=...`) — jamais un appel
par entrée : coûteux en throttle sinon, et l'ambiguïté est rare. Résultat
écrit dans la colonne `explicitness` (`explicit` / `clean` / vide si non
ambigu ou non résolu). Le frontend (`ITunes.jsx`) groupe les "éditions" par
`(album_name, explicitness)` au lieu de `album_name` seul, pour ne pas fusionner
à tort ces deux entrées, et affiche un badge "E" sur l'édition explicite.

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

**Garde-fous des 2 collecteurs (2026-09-25, recap)** :
- verrou mono-instance + heure sautee (sortie 75) + alertes ntfy sur tout echec : `collectors/spotify/core/run_guard.py` ;
- sortie **3** = echec deja alerte par Python ; tout AUTRE code non nul fait alerter le `.bat` lui-meme via
  `%SystemRoot%\System32\curl.exe` -> ntfy (crash avant que Python puisse alerter : import, interpreteur) ;
  teste : 0/3/75 -> rien, 1/9009 -> alerte ;
- iTunes : feed US/UK (`ITUNES_CRITICAL_STOREFRONTS`) de moins de `ITUNES_MIN_CRITICAL_FEED_ENTRIES` (50)
  entrees = echec (reessai puis abandon + alerte), jamais publie comme « Taylor absente du chart » ;
- **Toujours reessayer (proprio 2026-09-25 : « si quelque chose echoue on reessaie toujours »)** :
  `run_guard.retry_step` (3 essais, `RUN_RETRY_ATTEMPTS`) sur collecte iTunes / export / upload des 2
  collecteurs ; Apple Music relance chaque collecteur bloquant en echec (et relance les posts si c'etait
  global.py/country_all.py) ; posts X via `post_with_retries` (3 essais, 30 s, `APPLE_MUSIC_DEBUT_POST_ATTEMPTS`
  / `_RETRY_WAIT`) ; guet : fetch express 5 essais, collecte complete 3 essais. L'alerte ne part qu'apres le
  dernier echec. **Exception** : post « non confirme apres clic » = peut-etre en ligne -> JAMAIS repost
  (doublon), compte comme poste + alerte haute « verifie le compte ».
- posts contenant un DEBUT : priorite 0 et attente du slot X jusqu'a 15 min (`APPLE_MUSIC_DEBUT_FIRST_POST_*`) ;
- controle pre-sortie (`release_watch.preflight`, a l'ouverture de la fenetre de guet) : collectes < 90 min,
  feed iTunes US + API lookup joignables, > 2 Go libres, profil Chrome X present -> UNE notif « armed » (basse)
  ou la liste des problemes (haute). La connexion X elle-meme n'est pas verifiable sans navigateur : un post
  rate reste alerte + retente.

**Attente de sortie (2026-09-25)** : pas de process ni de tâche séparés (proprio). `run_itunes.py`
appelle `release_watch.run_if_release_due()` en fin de run, après avoir libéré son verrou : simple
comparaison de date (~0,1 s), sauf si un titre candidat non sorti a sa sortie dans les ~70 min (sortie =
00:00 America/New_York à la date `release_date` du catalogue, fenêtre −10 min → +2 h ; Encore : c'est le run
de 05:00 qui attend 05:50 puis guette jusqu'à 08:00). Pourquoi : le run horaire lit les feeds une fois à
HH:00:01 ; si Apple publie 30 s après → 1 h de retard. Dans la fenêtre : 1 feed clé/seconde (US une seconde
sur deux ; 66 s testés sans 403 le 25/09), lookup toutes les 15 s ; dès qu'un titre apparaît : post express
(proprio 2026-09-25 : « 1 minute plus tard » mais toutes les régions → Top Songs des ~168 storefronts, 168/168
en 53 s mesuré ; albums laissés au run complet qui suit, dont le début d'album part juste après) + collecte complète en parallèle (réessai tant que le verrou
horaire renvoie 75) puis post normal. Les runs horaires suivants continuent normalement pendant l'attente.
403/429 → pause 15→120 s (même hôte que le collecteur). Verrou mono-instance `tools/locks/release_watch.lock`.
Simulation : `previews_and_sims/apple-music-debut-progression/express.py`.

**Pas de tâche dédiée.** `collectors/apple_music/run_apple_music.bat` (tâche
`TSM Apple Music Every 4 Hours`, repeat **1 h depuis le 2026-09-24**, avant ça
2 h) démarre **en premier et EN PARALLÈLE** (depuis le 2026-09-24 au soir, demande
proprio « whoever is ready first is posted first ») `collectors/itunes/run_itunes.bat` =
`run_itunes.py` puis `post_new_release_progression.py --platform itunes` (posts iTunes
~HH:03, log `collectors/apple_music/post_new_release_progression_itunes.log`, état
`tools/json/new_release_progression_state_itunes.json`). Lancé via
`start "" /b cmd /d /c "<chemin absolu>"` — **un chemin relatif n'était pas trouvé par le
cmd enfant (testé)**. La chaîne continue si le `.bat` parent finit avant.

**`ITUNES_SNAPSHOT_HOURS` défaut code = heures paires seulement** — comme pour
Apple Music, le passage à une cadence horaire du Planificateur ne suffisait
pas seul (les runs impairs auraient juste ré-arrondi au créneau pair
précédent). Fix 2026-09-24 : `.env` fixe
`ITUNES_SNAPSHOT_HOURS=0,1,...,23`.

- en parallèle d'Apple Music : le cache du jeton MusicKit est écrit atomiquement
  (`core/token.py`) et `resolve_storefronts` a un fallback fixe → pas de risque de
  course ; `export_itunes.py` écrit `itunes.json` en tmp + `os.replace` (lu en même
  temps par `generate_home_highlights.py` côté Apple Music) ;
- tourne **quel que soit** le code de sortie d'Apple Music (pas de
  dépendance entre les deux chaînes) ;
- log séparé : `collectors/itunes/run_itunes.log`.

`collectors/itunes/run_itunes.bat` + `run_itunes_hidden.vbs` servent aussi seuls pour un
rattrapage manuel (pas de tâche dédiée).

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
- **Timeouts (2026-09-24)** : `run_itunes.py::run_child()` plafonne chaque
  sous-script à 900s et force `PYTHONUNBUFFERED=1` ; `upload_itunes_r2.py` a
  un client boto3 avec timeouts + `Body=io.BytesIO(...)` (ne jamais repasser
  un body `bytes` brut : le timeout couvrirait tout l'envoi). Pire cas 3 × 900 s < 1 h.
  **Le Planificateur n'empêche PAS les chevauchements** (le `.vbs` n'attend pas le `.bat`,
  IgnoreNew ne joue jamais) → **verrou mono-instance (2026-09-25)** :
  `collectors/itunes/tools/locks/run_itunes.lock` (PID + heure). Un run qui trouve le PID
  précédent encore vivant (vérif Windows via `OpenProcess`/`GetExitCodeProcess`, jamais
  `os.kill(pid, 0)` qui TUE le process sous Windows ; l'âge seul ne vole jamais le verrou,
  un run endormi par la veille reste propriétaire) saute son heure, alerte, et sort en **75** ;
  `run_itunes.bat` saute alors aussi l'étape de post. Verrou d'un PID mort = repris.
- **Alertes ntfy (2026-09-25)** : `run_itunes.py::alert()` → topic `NTFY_TOPIC_ITUNES` (défaut
  `NTFY_TOPIC_APPLE_MUSIC`) sur échec collecte (dont timeout), export, upload R2, crash, heure
  sautée. Testé le 2026-09-25 (envoi réel OK).
  Détail : skill `collector-apple-music`, piège « Upload R2 bloqué à l'infini ».

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


### Phase follow après la sortie (2026-09-25)
Constat le jour J : iTunes se met à jour en continu (Patient Zero absent du Top US à 06:36, #1 à 06:41), le run horaire ne suffit pas.
`release_watch.follow()` : après le post express, puis à chaque run horaire pendant `ITUNES_FOLLOW_HOURS` (24 h), poll des 5 feeds pays clés
(1 toutes les `ITUNES_FOLLOW_INTERVAL`=2 s) jusqu'à HH:58:30 ; déclenche fetch express 168 storefronts + post si un pays clé montre
un debut / nouveau peak / montée après le délai de 3 h, comparé au dernier état **posté** (`_worth_express`). Baisses ignorées.
Piège CDN : l'origine du RSS legacy sert en alternance des versions différentes du même feed (Patient Zero #1 puis absent puis #1,
requêtes à quelques secondes). Comparer à l'état posté (pas au poll précédent) évite les faux posts ; une « disparition » n'est jamais postée.
Mise à jour 07:00 : un rang ne compte qu'une fois **confirmé par deux lectures consécutives** du même feed (anti-clignotement CDN) ; le follower lit aussi un Top Albums pays clé une fois sur 4 (card album de l'Encore, fetch express albums seulement si c'est l'album qui bouge) ; il applique exactement les règles de volume de `post_due` (debut / #1 immédiats, autres peaks regroupés 15 min, montées 3 h). En mode `--auto`, il lance d'abord le post horaire normal (sinon celui du .bat attendrait la fin du follow ~HH:58).

### Cache Akamai du RSS legacy (incident 2026-09-25, corrigé)
L'URL RSS normale est servie depuis le cache Akamai (`X-Cache: TCP_MEM_HIT`, `feed.updated` figé ≥ 1 h) : les runs de 08:00 et 10:00 du jour J ont reçu le chart de l'heure d'avant octet pour octet (`[skip] snapshot identical`, cycles absents du CSV), le follower lisait le même cache, et 3 alertes « out for 2h but on no key chart » étaient fausses. Correctif : `core/rss.feed_url()` ajoute `?cb=<time_ns>` à **chaque** requête (collecteur, follower, express, preflight) → lecture origine (`TCP_MISS`). Ne jamais revenir à l'URL nue.

### Card album par pays (2026-09-25)
`post_new_release_progression._post_itunes_album_card` (chaîne iTunes, run horaire) : toutes les chansons de l'album (standard + nouvelle édition, meilleure édition explicit/clean) dans le Top Songs d'un pays (`ITUNES_ALBUM_CARD_REGIONS`, déf. `us`), +/- vs la dernière card postée (1re : vs snapshot précédent), peak depuis notre historique iTunes (2026-09-09 → « since Sep 9 » pour les titres sortis avant, NEW PEAK / RE-PEAK pour les nouveaux). Postée quand le classement de l'album change, au plus 1×/`ITUNES_ALBUM_CARD_GAP_MINUTES` (60), seulement quand un nouveau titre y est. Tweet « songs hold the top N on iTunes in the US right now » si l'album tient #1..#N (N ≥ 3). État `album_snapshot|<album>|itunes_<cc>`.
