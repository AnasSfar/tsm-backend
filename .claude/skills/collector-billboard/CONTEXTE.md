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
- `db/youtube_title_history.csv` (vues exactes par titre groupe — voir skill
  `collector-youtube`)
- discographie DB

### YouTube dans le scoring (ajoute 2026-08-14, remplace Deezer)

Contrairement a Apple Music (loi de puissance sur un rang de chart limite),
`db/youtube_title_history.csv` donne un volume exact (`daily_views`,
delta exact entre deux snapshots calendaires, voir skill `collector-youtube`)
par titre groupe (le grouping officiel/lyric/audio/visualizer et TV/original
est deja fait cote collecteur, via `core/title_groups.py`). Donc YouTube est
score comme Spotify (volume direct x poids), pas comme Apple Music
(power-law de rang) :

- `_weekly_youtube_views()` (`swift_top_100.py`) somme `daily_views` par
  titre normalise (`_chart_lookup_key`) sur les jours de la semaine ; lignes
  a `daily_views` vide sautees (pas traitees comme 0 — meme regle que la
  source, cf. `data-rules`).
- `units_youtube = weekly_youtube_views * YOUTUBE_WEIGHT` (`YOUTUBE_WEIGHT`,
  defaut `0.3`, env `TAYBOARD_YOUTUBE_WEIGHT`). Poids calibre le 2026-08-14
  (decision Anas) en comparant les volumes bruts reels sur une semaine :
  les vues YouTube tournent a ~25-40% du volume de streams Spotify pour les
  gros titres (nouveau single comme catalogue ancien, ratio stable). Ce
  `YOUTUBE_WEIGHT` joue le meme role que `SPOTIFY_WEIGHT`/`AM_WEIGHT`
  ci-dessous (poids plateforme top-level) — voir "Poids plateforme".
- `total_units = units_spotify + units_am + units_youtube`. Aucune donnee
  YouTube n'existe dans le scoring avant l'ajout du 2026-08-14 ->
  `units_youtube` vaut 0 pour toutes les semaines passees, donc l'ajout ne
  modifie aucun `total_units` deja publie.
- Colonnes ajoutees a `swift_top_100_history.csv` /
  `swift_top_songs_history.csv` : `units_youtube`, `youtube_pct`,
  `weekly_youtube_views`.
- **Limite connue** : `youtube_title_history.csv` groupe deja original et
  Taylor's Version sous un seul titre (pas de vues separees). Sur le
  variant `not-combined` (qui doit distinguer les deux), les deux entrees
  matchent donc la meme cle `_chart_lookup_key` et recoivent chacune la
  totalite des vues YouTube du titre groupe (pas de double comptage sur
  `total_units` globaux car ce sont deux track_id/lignes distincts, mais la
  vraie repartition originale/TV des vues n'est pas connue) — limite de la
  source, pas un bug du moteur de scoring.
- **Bug fixe 2026-08-15 : matching titre YouTube trop strict, plusieurs
  chansons a 0 vues alors que la video existe** (repere par Anas : "End
  Game", "Who's Afraid of Little Old Me?" a 0 ; ME! sous-compte). Deux
  causes distinctes :
  1. Apostrophes incoherentes entre sources — le nettoyage de titre du
     collecteur YouTube (`core/title_groups.py`) supprime l'apostrophe sans
     la remplacer ("Who's" -> "Whos"), alors que `_normalize_title` la
     transforme en espace-separateur ("who's" -> "who s", ou le
     `song_family` catalogue deja pre-slugifie "who_s_afraid..." donne le
     meme resultat). Fix : `_TRAILING_S_RE` fusionne un token "s" isole
     avec le mot precedent apres normalisation ("who s" -> "whos") dans
     `_normalize_title`/`_normalize_full_title` — converge les deux
     conventions, sans regression (transforme identiquement des deux cotes,
     donc tout ce qui matchait avant matche encore).
  2. Suffixe featuring redondant dans certains titres groupes YouTube — le
     vrai titre video contient parfois deux fois l'artiste feature (ex.
     `"ME! (feat. Brendon Urie of Panic! At The Disco) ft. Brendon Urie"`),
     ce qui a fait scinder ME! en DEUX groupes distincts cote YouTube ("Me",
     3 videos live seulement, ~87M vues lifetime — celui qui matchait) et
     ("Me Ft Brendon Urie", la vraie video officielle + son lyric video,
     ~484M vues lifetime — jamais matche). Meme motif pour "End Game" /
     "Everything Has Changed" (suffixe `Ft Ed Sheeran...` hors parentheses,
     0 vues avant le fix car le titre catalogue n'a pas ce suffixe). Fix :
     `_YOUTUBE_FEAT_SUFFIX_RE` (dans `_weekly_youtube_views` uniquement, pas
     touche aux autres sources) strip un suffixe `ft./feat./featuring X`
     final avant de batir la cle — fusionne les deux groupes YouTube sous
     la meme cle catalogue au lieu d'ignorer l'un des deux.
  Verifie apres fix (semaine 2026-08-07..13, comptage brut avant poids) :
  End Game 0 -> 121k vues, Who's Afraid 0 -> 52k, Everything Has Changed
  0 -> 330k, ME! 13.5k -> 250k unites (poids 0.3 deja applique sur ce
  dernier chiffre). Reflexe si un titre `chart_extra=false` semble a 0 cote
  YouTube malgre une vraie video : grep `db/youtube_title_history.csv` pour
  verifier si le titre est scinde en plusieurs `title_key` avant de
  soupconner le calcul de poids.

Le code prefere les snapshots worldwide Spotify quand ils existent, car ils
contiennent toutes les apparitions pays; les CSV regionaux servent de fallback.

### Poids plateforme (ajoute 2026-08-14/15)

Trois constantes top-level multiplient chaque contribution plateforme
**apres** son calcul interne habituel (pas de changement a la logique de
calcul elle-meme, juste un facteur d'echelle final) — decision Anas
2026-08-15 pour rendre le volume brut Spotify moins dominant face a
Apple Music/YouTube :

- `SPOTIFY_WEIGHT` (defaut `0.6`, env `TAYBOARD_SPOTIFY_WEIGHT`) :
  `units_spotify = round((units_charts + units_surplus * 0.7) * SPOTIFY_WEIGHT)`.
- `AM_WEIGHT` (defaut `0.3`, env `TAYBOARD_AM_WEIGHT`) :
  `units_am = round((am_ts_raw + am_overall_raw) * 1000 * AM_WEIGHT)`.
- `YOUTUBE_WEIGHT` (defaut `0.3`) joue deja exactement ce role pour YouTube
  (applique directement sur les vues brutes) — pas de constante separee.
- Les champs d'affichage (`am_ts_units_display`, `am_global_units_display`,
  `units_charts_display`, `units_surplus_display` dans `snapshot_entries`,
  utilises par la colonne tableau `swift_top_100_image.py`) sont scales par
  le meme poids que leur plateforme pour que la somme visuelle des colonnes
  reste coherente avec le total pondere. Les scores diagnostiques bruts
  (`am_ts_score`, `am_global_score`, `am_country_score`, `am_overall_score`)
  restent **non ponderes** (loi de puissance brute, pas des unites).
- `total_units` (donc `points` = `total_units/100_000`) baisse nettement
  partout par rapport a avant ce changement — attendu, pas une regression.
  Les trois semaines deja publiees au moment de l'introduction du poids ont
  ete regenerees (`--date` explicite par semaine) pour rester coherentes.

### Sync Apple Music R2 automatique (ajoute 2026-08-15)

`_sync_apple_music_from_r2_best_effort()` appelle
`scripts/sync_apple_music_snapshots_from_r2.py --apply` en sous-processus,
une seule fois par run (garde par le flag module `_APPLE_MUSIC_R2_SYNC_DONE`
puisque `--variant all` traverse `main_from_args` 4 fois), au tout debut de
`main_from_args` (skip si `--dry-run`). Best-effort comme
`_regenerate_home_highlights_cache` : jamais bloquant, une erreur (pas de
creds R2, pas de reseau) est loggee (`am_sync : failed — ...`) et le run
continue avec les snapshots locaux existants. Corrige a la source le piege
documente plus haut ("Apple Music Overall a 0") — plus besoin de lancer le
script de sync a la main avant un run/backfill.

### Deezer retire du scoring (2026-08-14)

Deezer a ete integre au scoring le 2026-08-09 puis **retire completement le
2026-08-14** (decision produit d'abandonner Deezer — voir aussi
`collector-deezer/CONTEXTE.md`). `units_deezer`, `deezer_pct`,
`deezer_artist_score`, `deezer_global_score`, `DEEZER_GLOBAL_WEIGHT`,
`DEEZER_ARTIST_FLOOR_RANK` et les fonctions `_weekly_deezer_*`/
`_deezer_artist_floor_score`/`_active_deezer_csvs` ont ete supprimes de
`swift_top_100.py`, `swift_top_albums.py`, `swift_top_100_image.py` (colonne
"Deezer" -> "YouTube" dans le tableau) et `tayboard_explainer_images.py`
(cards methodo publiques). Les colonnes Deezer disparaissent des CSV
d'historique a la prochaine reecriture complete (`_atomic_write_csv` avec
`extrasaction="ignore"`) ; les `total_units` deja publies ne changent pas
retroactivement (ils avaient deja leur contribution Deezer figee au moment
du calcul). Aucun run planifie (Task Scheduler local, cron VPS) n'existait
pour `collectors/deezer` au moment du retrait — rien a desactiver cote
ordonnancement ; le collecteur reste appelable manuellement
(`python -m tsm collect deezer` / `run_deezer.bat`) mais n'est plus utilise.

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

### Per-song breakdown dans swift_top_albums.py (ajoute 2026-08-15)

Chaque entree de `swift_top_albums.json`/`swift_top_eras.json` (les deux
partagent le meme moteur, `swift_top_era.py` appelant `swift_top_albums.py`
avec `--variant eras`) porte maintenant un champ `"songs"` : la liste des
chansons de cet album/era pour la semaine du snapshot, avec leur unit
breakdown complet (`units_am_ts`, `units_am_overall`, `units_youtube`,
`units_charts`, `units_surplus`, `total_units`, `points`, `rank`, `change`,
`rank_change`, `percentage_change`, `weeks_on_chart`, `peak_position`,
`times_at_peak`, `image_url`, `spotify_url`), trie par `total_units`
decroissant. Construit dans `_build_album_week()` a partir des lignes
`db/swift_top_100_not_combined_songs_history.csv` de la semaine courante
(donc "not combined" : les versions/TV distinctes restent separees), enrichi
avec `swift_top_100._iter_discography_tracks()` (import direct du module
voisin, comme fait deja `swift_top_era.py`) pour `title`/`image_url`/
`spotify_url`. **N'existe que pour la semaine courante** (`track_meta_by_id`
n'est passe qu'au premier appel de `_build_album_week`, pas a celui de la
semaine precedente) — pas la peine cote semaine precedente, elle ne sert
qu'au diff de %change. N'apparait pas dans `swift_top_albums_history.csv`
(champ additif au JSON de snapshot uniquement, comme `points_display`/
`units_charts_display` etc. — le CSV garde son schema figé). Utilise par la
page detail album/era de tsm-frontend (`/tayboard/album/:albumId`,
`/tayboard/era/:albumId`).

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

## Highlights Charts Gallery

Depuis 2026-07-28, `swift_top_100.py` et `swift_top_albums.py` appellent en
best-effort (jamais bloquant) `scripts/generate_home_highlights.py --quiet`
a la fin de leur propre `_maybe_upload_to_r2` (donc sautee si `--skip-r2`).
Regenere `cache/home_highlights.json` et `cache/version.json` sur R2 (lus par
`tsm-frontend/api`).

## Live projection (ajoute 2026-09-22)

`swift_top_100_live.py` genere un aperçu best-effort du classement final de
la semaine Fri->Thu **en cours**, en projetant les jours restants a partir
des jours deja reels. **Jamais le classement officiel** — n'ecrit jamais dans
`db/swift_top_100_history.csv`/`swift_top_100.json` ni aucun artefact
officiel, fichiers separes uniquement (voir "Outputs" plus bas).

**Declenchement (change 2026-09-23) : plus de schedule fixe.** Auparavant
pense pour tourner une fois par jour sur un horaire dedie ; decision produit
Anas 2026-09-23 : il doit tourner **juste apres chaque collecteur individuel**
(Apple Music, Spotify streams, YouTube) qui alimente son scoring, avec les
donnees fraiches que ce collecteur vient de produire plus ce que les deux
autres ont deja — sans attendre que les trois soient synchronises. Le script
gere deja le partiel/perime par plateforme via `data_as_of`, donc c'est
purement une question de QUAND il est invoque, aucun changement interne.
Nouveau module partage `collectors/billboard/live_trigger.py` :
`trigger_live_projection(*, log=print)` lance `swift_top_100_live.py` en
sous-processus (run reel, pas de `--dry-run`/`--skip-r2`), best-effort
(try/except, jamais bloquant, log une ligne succes/echec via le callable
`log` passe par l'appelant). Appele en fin de run reussi par
`collectors/apple_music/run_apple_music.py::main()` (apres
`regenerate_home_highlights_cache()`), `collectors/spotify/streams/update_streams.py::main()`
(dans le meme bloc que le `notify()` final, donc jamais en
`--local-test`/`--throwback`/`--debug-daily`), et
`collectors/youtube/update_youtube.py::main()` (juste avant le print final,
apres `maybe_upload_youtube_to_r2` ; deja hors d'atteinte en `--dry-run`/
`--preview` car ces modes retournent plus tot). Pas de changement aux .bat
Task Scheduler — le declenchement vient du code Python des collecteurs, pas
de l'ordonnancement. `collectors/itunes` n'appelle pas ce trigger (pas une
source du scoring live).

Commandes :

```powershell
python .\collectors\billboard\swift_top_100_live.py
python .\collectors\billboard\swift_top_100_live.py --date 2026-09-22
python .\collectors\billboard\swift_top_100_live.py --dry-run
python .\collectors\billboard\swift_top_100_live.py --skip-r2
```

Options : `--date YYYY-MM-DD` (as-of, defaut aujourd'hui), `--dry-run`,
`--skip-r2`.

### Refactor `swift_top_100.py` (pre-requis, 2026-09-22)

Pour partager exactement le meme code de scoring entre le run officiel du
jeudi et ce script live, deux changements retro-compatibles ont ete faits
dans `swift_top_100.py` :

- `return_daily: bool = False` ajoute a `_weekly_apple_music_global_points`,
  `_weekly_apple_music_country_points`, `_weekly_apple_music_genre_points`,
  `_weekly_apple_music_ts_points`, `_weekly_youtube_views`,
  `_weekly_charts_streams_by_title`. A `False` (defaut), comportement et
  valeur de retour strictement identiques a avant — aucun appelant existant
  (le `run()` officiel) n'a ete touche. A `True`, retourne un tuple
  `(weekly_dict, daily_dict)` ou `daily_dict[key][date] = valeur` — utilise
  par le script live pour restreindre les sommes aux jours deja reels de la
  semaine en cours, et pour recuperer l'historique journalier servant a la
  saisonnalite/momentum. Verifie par test randomise (20k tirages) que
  `compute_track_units` (voir ci-dessous) egale bit-a-bit l'ancienne formule
  inline.
- `compute_track_units(*, weekly_streams, raw_units_charts, am_ts_raw,
  am_overall_raw, weekly_youtube_views) -> dict` extrait la formule
  `units_spotify`/`units_am`/`units_youtube`/`units_charts`/`units_surplus`/
  `total_units` qui etait inline dans `run()` (~ligne 2096-2121 avant
  refactor) — meme calcul, meme ordre d'arrondis, aucune valeur
  historique ne change. `run()` appelle maintenant cette fonction au lieu de
  refaire le calcul en dur ; une seule implementation partagee par le run
  officiel et `swift_top_100_live.py`.

### Methode de projection — `live_projection.py`

Nouveau module (stdlib pur, pas de numpy/pandas), adapte de deux patterns
deja presents dans le repo plutot qu'invente de zero :

- `weekday_seasonal_factor(daily_series, target_weekday, *, fallback_series=None)`
  — facteur multiplicatif par jour de semaine a partir des ~8 dernieres
  semaines de l'historique propre du morceau (meme jour de semaine
  uniquement) ; si moins de 3 occurrences reelles de ce jour de semaine
  existent dans l'historique du morceau, retombe sur `fallback_series`
  (serie agregee/catalogue) puis sur neutre `1.0` — meme logique de repli
  que `gap_estimate.py::_weekday_seasonal_factors`.
- `trend_momentum_factor(daily_series)` — facteur de tendance recente base
  EWMA (meme recurrence que `forecast_milestones.py::ewma`), clamp
  `[0.55, 1.75]` — **meme borne** que la ratio-correction deja utilisee dans
  `swift_top_100.py::_estimate_missing_stream_days` (~ligne 819).
- `project_remaining_days(actual_daily, remaining_dates, history_daily)` —
  deseasonalise une baseline recente (moyenne des jours deja reels de la
  semaine, ou a defaut le dernier jour reel de l'historique), applique le
  momentum, reseasonalise avec le facteur du jour de semaine cible pour
  chaque jour restant. Generalisation du pattern deseasonalize/apply-growth/
  reseasonalize deja utilise par `gap_estimate.py::estimate_gap` (qui, lui,
  ne comble que jusqu'a 2 jours passes bornes par deux vraies valeurs — le
  script live projette N jours **futurs** sans borne de fin connue).

### Ce que fait le script

Pour la date as-of (defaut aujourd'hui), determine la semaine Fri->Thu en
cours via la meme logique que `_week_dates` (reutilisee, pas reimplementee),
separe `days_actual` (Ven..as-of) et `days_remaining` (as-of+1..Jeu). Pour
chaque plateforme (Spotify via `_load_stream_daily_cache`/
`_aggregate_weekly_streams` **restreint a `days_actual` uniquement, jamais
via l'estimateur de jours manquants** — cet estimateur est plafonne a 2 jours
et pense pour de petits trous retroactifs sur une semaine deja terminee, pas
pour projeter 6 jours a l'avance ; AM/YouTube/Charts via les fonctions
`return_daily=True` ci-dessus) : calcule le total reel jusqu'a present, puis
projette les jours restants avec `live_projection.project_remaining_days`, et
appelle `compute_track_units` deux fois (reel seul, puis reel+projete) pour
produire `total_units_actual_so_far` et `total_units_projected_final`.
Classement final sur `total_units_projected_final`.

Variante **combined uniquement** (not-combined retire du produit live le
2026-09-23, decision Anas — le chart officiel `swift_top_100.py` garde ses
deux variantes, ce script non). Applique aussi le merge des
`historical_track_ids` et le dedup `_currently_merged_track_ids` (best-effort,
jamais bloquant) comme le run officiel, pour eviter les doublons Shake It
Off/Love Story deja documentes plus haut.

### Comparaison vs le dernier chart officiel complet (ajoute 2026-09-23)

`rank_change`, `percentage_change`, `peak_position`, `times_at_peak`,
`weeks_on_chart` et `change` (NEW/RE) sont calcules **contre le dernier
chart officiel publie** (le jeudi precedent dans `db/swift_top_100_history.csv`),
pas contre la projection de la veille — decision Anas 2026-09-23 (l'ancien
`rank_change_vs_yesterday_projection` jour-sur-jour, base sur
`_load_prev_rank_by_tid` lisant l'historique live lui-meme, a ete
completement retire). Reutilise directement les memes fonctions/pattern que
`swift_top_100.py::run()` (~ligne 2097-2380), juste ancre sur `week_end` (le
jeudi a venir) au lieu d'un `chart_date` deja passe — puisque la semaine live
en cours est toujours celle qui suit immediatement le dernier chart officiel,
c'est exactement le meme "prev_week" que le run officiel utiliserait lui-meme
ce jeudi-la :
- `top100._load_existing_history_before_date(format(week_end), logger)` +
  `top100._history_stats(existing_rows)` -> `weeks_on_chart_by_track`,
  `peak_by_track`, `times_at_peak_by_track`.
- `prev_week_end = week_end - 7j` ; lignes de `existing_rows` a cette date ->
  `prev_ranks`, `prev_total_units_by_track`, plus le meme repli par titre
  normalise (`_normalize_title`) que l'officiel pour gerer les changements de
  `track_id` entre versions (`prev_tid_by_title`/`hist_tid_by_title`).
- Par entree : `pr` (prev_rank, avec repli par titre), `change` (`NEW`/`RE`
  si `pr is None`, selon `weeks_on_chart_by_track`), `weeks_on_chart`,
  `percentage_change` (vs `prev_total_units_by_track`) calcules dans la
  boucle principale ; `rank_change`/`peak_position`/`times_at_peak` calcules
  dans une **deuxieme passe apres le tri final** (meme decoupage en deux
  passes que l'officiel, ligne 2340-2380 — le rang final n'est connu qu'apres
  le tri par `total_units_projected_final`).
- `points_projected_final = round(total_units_projected_final / 100_000, 1)`
  — pas de bonus manuel applique (les bonus `swift_top_100_bonuses.json` sont
  une decision produit par semaine deja publiee, pas geree pour une semaine
  encore en cours).
- Colonnes AM TS/Overall et Spotify Charts/Streams (`units_am_ts`,
  `units_am_overall`, `units_spotify_charts`, `units_spotify_streams`)
  ajoutees le meme jour avec la meme formule d'affichage ponderee que
  l'officiel (`am_ts_units_display`/`am_global_units_display`/
  `units_charts_display`/`units_surplus_display`, swift_top_100.py:2301-2307)
  — le tableau `/tayboard/live` cote frontend a maintenant les memes colonnes
  que le tableau officiel (Rank/Delta/Song/Points/%/Peak/WOC/AM/YouTube/
  Spotify/Total), le prefixe "Est." vit dans les en-tetes de colonne (une
  fois), pas repete sur chaque cellule.
- Chaque colonne unit a aussi son propre `%` vs le dernier chart officiel
  (`units_am_ts_pct`, `units_am_overall_pct`, `units_spotify_charts_pct`,
  `units_spotify_streams_pct`, `units_youtube_pct`) — meme principe que
  `percentage_change` mais par plateforme, calcule directement contre les
  champs bruts (non ponderes) deja stockes dans
  `db/swift_top_100_history.csv` (`am_ts_score`, `am_overall_score`,
  `units_charts`, `units_surplus`, `units_youtube`) — le `%` est invariant
  d'echelle donc comparer brut-vs-brut ou pondere-vs-pondere donne le meme
  resultat, pas besoin de reponderer l'historique. `None` si le morceau
  n'etait pas sur le dernier chart officiel (`pr is None`) ou si le champ
  precedent est absent/zero (`_pct_change`).

### Bugs corriges le 2026-09-23 (memes symptomes : chiffres qui semblaient bons mais faux)

- **Tous les `percentage_change` ressortaient negatifs alors que la semaine
  etait objectivement tres forte.** Cause racine : `days_actual`/
  `days_remaining` (calendaire, base sur `as_of`) etaient utilises pour
  DECIDER quels jours sont "reels" pour chaque plateforme — mais un
  collecteur peut avoir 1-2 jours de retard sur `as_of` (ex. Spotify
  streams/YouTube a `J-2`, alors qu'Apple Music est a jour). Les jours en
  retard tombaient dans `days_actual` (car <= `as_of`) mais n'avaient aucune
  ligne reelle dans les CSV -> silencieusement ignores dans le total actuel
  ET jamais projetes non plus (seuls les jours strictement apres `as_of`
  etaient dans la liste a projeter) -> volume reel manquant sur 2-3 jours,
  jamais compense, pour TOUS les morceaux. Fix : chaque plateforme scanne
  desormais toute la semaine (`week_set_full`, jamais `days_actual_set`) et
  calcule son propre `data_as_of`/jours manquants
  (`spotify_missing_dates`/`_missing_dates(apple_music_data_as_of)`/etc.) au
  lieu d'un decoupage unique base sur `as_of` — les jours reellement futurs
  ne matchent simplement aucune ligne CSV, donc scanner toute la semaine est
  sans risque. `days_actual`/`days_remaining` calendaires restent utilises
  tels quels uniquement pour l'affichage ("X/7 jours") et `confidence`, pas
  pour la logique de scoring.
- **Meme cause racine, symptome different : Shake It Off (Best Work
  Edition), Love Story (Pop Mix) et Karma feat. Ice Spice reapparaissaient
  en doublon** malgre le dedup deja en place
  (`top100._currently_merged_track_ids`). La fonction verifie si deux
  track_id partagent le meme total ce jour-la en lisant
  `STREAMS_HISTORY_CSV` pour la date exacte `chart_date` — appelee avec
  `chart_date=as_of` (aujourd'hui), qui n'a souvent AUCUNE ligne (Spotify pas
  encore collecte pour aujourd'hui), donc `totals_by_track_id` vide -> aucun
  doublon detecte -> les deux track_id fusionnes ressortent comme deux
  entrees separees avec un total distinct. Fix : appeler avec
  `chart_date=spotify_data_as_of` (la vraie derniere date avec des donnees
  Spotify reelles), jamais `as_of` litteral.
  Reflexe si un futur symptome ressemble a "un morceau connu pour etre
  fusionne reapparait en double sur le live" : verifier d'abord que la date
  passee au dedup a bien une ligne dans `streams_history.csv`, avant de
  soupconner `pick_active_catalog_merge_losers` lui-meme.

### Albums/Eras live (ajoute 2026-09-23, suite du songs-only)

Nouveau script `collectors/billboard/swift_top_albums_live.py`, meme
architecture/conventions que `swift_top_100_live.py` (CLI `--date`/
`--dry-run`/`--skip-r2`, aucun commit git, aucune image PNG). Pipeline :

1. Appelle `swift_top_100_live._build_live_variant(variant="not-combined",
   include_album_agg=True, ...)` — reutilise **exactement** la meme machinerie
   de projection actual/projected jour par jour que le variant combined (rien
   de duplique), juste sans le cap Top 100 (voir piege ci-dessous) et avec un
   nouveau flag `include_album_agg` qui attache un champ interne
   `entry["_album_agg"]` (weekly_streams/am_ts_score/am_overall_score bruts
   non pondérés/units_charts/units_surplus/base_title/song_family — les
   champs qu'un `swift_top_100_not_combined_songs_history.csv` a mais qu'une
   entree live combinee normale n'a pas) a chaque entree. `include_album_agg`
   vaut `False` par defaut donc le variant combined public de
   `swift_top_100_live.py` n'est jamais affecte (`_album_agg` n'apparait
   jamais dans `swift_top_100_live.json`). Ce resultat not-combined n'est
   **jamais ecrit dans aucun fichier** — intermediaire memoire pur, ne
   ressuscite pas l'ancien JSON/CSV not-combined public retire le 2026-09-23.
2. Reshape chaque entree en dict shape `swift_top_100_not_combined_songs_history.csv`-row
   avec **tous les champs numeriques stringifies** (`_build_album_week` fait
   `(row.get(champ) or "").strip()` en interne via ses helpers
   `_to_int`/`_to_float`/`_score_to_units` — attend un `csv.DictReader`-shaped
   dict, pas des nombres Python natifs ; verifie directement en lancant le
   script, pas suppose).
3. Appelle `swift_top_albums._build_album_week(chart_date=<date as-of comme
   tag>, song_rows=..., track_to_album=..., albums_by_id=..., logger=...,
   track_meta_by_id=...)` — **la meme fonction que le chart officiel**, pour
   chacun des variants "albums" et "eras" (`albums_engine._configure_variant`
   avant chaque appel, comme fait deja `swift_top_era.py`).
4. `rank_change`/`percentage_change`/`peak_position`/`times_at_peak`/
   `weeks_on_chart`/`change` (NEW/RE) calcules contre le **dernier chart
   officiel publie** (`swift_top_albums_history.csv`/`swift_top_eras_history.csv`),
   pas contre `_build_album_week`'s propre mecanisme prev-week (qui attend
   des lignes de la semaine precedente dans le meme `song_rows`, absentes en
   live) — meme principe que la comparaison songs vs dernier chart officiel,
   une octave au-dessus, cle par `album_id` (pas de fallback par titre
   necessaire : contrairement a `track_id`, `album_id` est un slug stable du
   titre album/ere, cf. `_normalize_album_id`).
5. Confidence par entree : `swift_top_100_live._confidence_for(days_actual)`
   reutilise telle quelle.
6. Chaque entree porte son breakdown par chanson (`songs`, deja calcule
   gratuitement par `_build_album_week` quand `track_meta_by_id` est passe —
   meme champ que le chart officiel, section "Per-song breakdown" plus haut),
   avec les propres stats vs-dernier-chart-officiel-not-combined de chaque
   chanson (calculees par l'appel `variant="not-combined"` a l'etape 1).

**Piege trouve et corrige en verifiant (2026-09-23) : le cap Top 100 de
`_build_live_variant` (`entries = entries[:100]`, applique uniquement au
variant combined public) aurait tronque le pool not-combined a 100 chansons
si reutilise tel quel** — hors l'officiel alimente `_build_album_week` depuis
`full_song_rows` (TOUTES les chansons scorees, non cape ; seul
`swift_top_100_history.csv`/`.json` sont capes a 100 dans
`swift_top_100.py::run()`, pas `swift_top_100_not_combined_songs_history.csv`).
Sans fix, chaque album/ere aurait sous-compte ses chansons hors top 100
combined. Fix : le cap est saute quand `include_album_agg=True`. Verifie
avant/apres : 100 → 697 chansons alimentant l'agregation, 16 → 17 albums et
12 → 13 eras classes (le catalogue complet).

Outputs (jamais les fichiers officiels swift_top_albums*/swift_top_eras*) :

- `db/swift_top_albums_live_history.csv`, `db/swift_top_eras_live_history.csv`
  — une ligne par `(as_of_date, album_id)`.
- `runtime/exports/web/site/data/swift_top_albums_live.json` (+ copie datee),
  `runtime/exports/web/site/data/swift_top_eras_live.json` (+ copie datee).
- R2 : `scripts/r2.py::upload_slugs(["swift_top_albums_live", "swift_top_eras_live"])`,
  best-effort, saute si `--skip-r2`/`--dry-run`. Pas de regen highlights.
  **Aucun commit git.**

`collectors/billboard/live_trigger.py::trigger_live_projection()` lance
maintenant les DEUX scripts (`swift_top_100_live.py` puis
`swift_top_albums_live.py`), chacun avec son propre try/except best-effort
independant (un echec de l'un ne bloque pas l'autre) — aucun changement cote
`run_apple_music.py`/`update_streams.py`/`update_youtube.py`, qui appellent
deja seulement `trigger_live_projection()`.

Commandes :

```powershell
python .\collectors\billboard\swift_top_albums_live.py
python .\collectors\billboard\swift_top_albums_live.py --date 2026-09-22
python .\collectors\billboard\swift_top_albums_live.py --dry-run
python .\collectors\billboard\swift_top_albums_live.py --skip-r2
```

Confidence par entree : `days_actual <= 2` -> `early_estimate`, `3-5` ->
`firming_up`, `6` (et `7`, semaine quasi/complete) -> `nearly_final`.

### Outputs (jamais les fichiers officiels)

- `db/swift_top_100_live_history.csv` — une ligne par `(as_of_date,
  track_id)`, append/replace par `as_of_date`.
- `runtime/exports/web/site/data/swift_top_100_live.json` (derniere
  projection) + copie datee `swift_top_100_live_YYYY-MM-DD.json` (necessaire
  car `scripts/r2.py` `_collect_slug_tasks` fait un glob
  `{slug}_????-??-??.json`).
- R2 : `scripts/r2.py::upload_slugs(["swift_top_100_live"])`, best-effort
  (try/except, jamais bloquant), saute si `--skip-r2`/`--dry-run`. Pas de
  regen highlights (pas une source des Charts Gallery highlights). **Aucun
  commit git.**

## Pieges

- **Corrige 2026-08-15** : le sync R2 decrit ci-dessous dans "Sync Apple Music
  R2 automatique" tourne maintenant automatiquement au debut de chaque run —
  l'incident suivant ne devrait plus se reproduire silencieusement, mais le
  reflexe diagnostic (compter les fichiers `apple_ts`/`apple_country` dans les
  logs) reste valable si le sync echoue (creds/reseau).
- **Incident 2026-08-09 : Apple Music "Overall" a 0 et % de variation absent sur le
  tayboard, deux semaines d'affilee.** Cause racine : depuis le passage d'Apple
  Music au VPS OVH le 2026-07-30 (voir `REPO_CONTEXT.md` § 12, `OVH.md`), la
  machine locale (celle qui fait tourner `swift_top_100.py`) n'ecrit plus jamais
  `db/apple_music_*.csv` ni `snapshots/apple_music_charts/YYYY/MM/YYYY-MM-DD/`
  -- le VPS accumule son propre historique mais ne le repousse nulle part (tout
  gitignore, seul l'upload R2 distribue la donnee). `_active_apple_music_csvs()`
  lit donc des fichiers locaux figes au 2026-07-30 pour toute semaine calculee
  apres cette date : `am_global_score`/`am_country_score`/`am_genre_score`
  retombent silencieusement a 0 (pas de plancher pour ces trois-la, contrairement
  a `am_ts_raw` qui a un fallback `am_ts_floor_raw` -- d'ou le symptome trompeur
  "TS" affiche un nombre non-nul en forte baisse pendant que "Overall" affiche
  franchement 0). Consequence secondaire : la semaine se terminant le
  2026-07-30 n'a jamais ete generee du tout (gate `check_swift_top_gate` reste
  "waiting" -- seul le cote "charts" a signale ce jeudi-la, jamais "streams" --
  voir `collectors/spotify/core/swift_top_gate.py`), ce qui a aussi coupe le
  lien `prev_week` de la semaine suivante (2026-08-06) et fait disparaitre la
  colonne `%`/`percentage_change` sur le tayboard (pas de ligne d'historique
  J-7 a comparer).
  Fix : nouveau script `scripts/sync_apple_music_snapshots_from_r2.py`
  reconstruit les CSV quotidiens locaux depuis `apple-music/snapshots/` sur R2
  (jamais supprime, contient l'historique VPS complet par date/heure de run) ;
  puis regenerer la semaine manquante (`--date 2026-07-30 --variant all`) et
  reforcer la semaine impactee (`--date 2026-08-06 --variant all`, un `--date`
  explicite ecrase toujours, `--force` n'a d'effet qu'avec `--backfill`).
  A refaire a chaque fois que le local accuse un retard sur le VPS (pas de
  synchro automatique -- voir aussi le piege equivalent deja documente pour le
  premier run VPS dans `OVH.md` § "Incident -- Apple Music a publie des NEW
  faux"). Reflexe : si `apple_country`/`apple_global`/`apple_genre` loggent
  `missing` ou un nombre de fichiers anormalement bas dans la sortie de
  `swift_top_100.py`, verifier d'abord la date du plus recent
  `snapshots/apple_music_charts/*/*/*/` local avant de soupconner le scoring.
  Effet de bord attendu en re-generant : le simple fait de restaurer la vraie
  donnee AM (plus le fix `misc.json` du meme jour, voir piege suivant) peut
  reclasser fortement le top -- ex. "I Knew It, I Knew You" (Toy Story 5) est
  passe #1 grace a une presence tres large sur les genre charts AM (~165 pays,
  Pop + Country), verifie ligne par ligne contre les CSV bruts avant publication,
  pas une regression du sync.
- Bug fixe le 2026-08-09 : `swift_top_100.py::_iter_discography_tracks()`
  ne lisait jamais `db/discography/misc.json`. La constante `MISC_JSON`
  pointait en fait vers `songs.json` (mauvais nom, meme piege que celui
  trouve le meme jour cote `generate_streams_image.py` / skill
  `spotify-streams`) — le vrai fichier `misc.json` (sections "Standalone &
  Extras": soundtracks, vault, remixes, streaming_extras...) n'avait aucune
  constante ni aucun bloc de lecture dedie. Consequence : tout track vivant
  uniquement dans `misc.json` etait invisible du classement TayBoard quel
  que soit son volume de streams (observe : "I Knew It, I Knew You", section
  `soundtracks` avec `chart_extra=false` explicite donc cense compter comme
  un titre normal — absent malgre ~1.3M streams/jour). Fix : ajout d'une
  constante `MISC_JSON = DISCOGRAPHY_DIR / "misc.json"` distincte (l'ancienne
  `MISC_JSON` renommee `SONGS_JSON`, toujours `songs.json`) et d'un bloc de
  lecture qui respecte le `chart_extra` de section/track comme le bloc
  albums/songs.json (ne force pas `True` contrairement au bloc
  `features.json`, car les sections de `misc.json` peuvent etre des titres
  non-extra). Reflexe a garder : `db/discography` a 4 sources de tracks
  (`albums/*.json`, `songs.json`, `misc.json`, `features.json`) — tout
  chargeur de catalogue ecrit a la main (ici ou ailleurs) doit couvrir les 4,
  sinon un titre reel avec de vrais streams peut disparaitre silencieusement
  d'un classement sans aucune erreur.
- Meme audit du 2026-08-09 : `swift_top_albums.py::_augment_era_albums_with_matched_extras()`
  (chart Eras uniquement) ne lisait que `songs.json` pour rattacher les
  extras/standalone a leur album/ere -- un track present seulement dans
  `misc.json`/`features.json` ne se voyait jamais attribuer d'ere, donc
  jamais compte dans le total de l'ere correspondante. Fix : boucle sur
  `SONGS_JSON`/`MISC_JSON`/`FEATURES_JSON` (nouvelles constantes ajoutees).
  `collectors/comp/discography.py::build_track_album_map()` /
  `build_track_image_map()` (composant partage utilise par
  `generate_streams_image.py`, `generate_chart_image.py` du chart Global, et
  `post_song_overtakes.py`) avait le meme trou et a ete corrige en meme
  temps -- impact transverse a plusieurs collecteurs, pas seulement
  Billboard. Voir skill `spotify-streams` pour la liste complete des 14
  fichiers touches par cet audit.
- `swift_top_seperate.py` garde une faute dans le nom de fichier; ne pas le
  renommer sans traiter les references.
- `scrape_billboard.py` est network/Playwright et peut etre fragile au DOM.
- Les variants Swift Top partagent le moteur principal via import/module; verifier
  les arguments transmis avant de modifier un wrapper.
- **Incident 2026-08-20/25 : la semaine du 2026-08-20 n'a jamais ete generee
  (gate reste `waiting` indefiniment) a cause d'un `NameError` dans
  `swift_top_100.py`** — `_TRAILING_S_RE` (introduit par le fix titre du
  2026-08-15) referencait une constante jamais definie ; le vrai nom etait
  `_TRAILING_CONTRACTION_RE`. Comme `_normalize_title`/`_normalize_full_title`
  sont appelees des le debut du scoring, TOUTE invocation de `swift_top_100.py`
  plantait (donc uniquement visible le jeudi, seul jour ou le moteur tourne) —
  `finalize_update.py` (source="streams") crashait avant d'appeler
  `check_swift_top_gate`, laissant `swift_you.lock` seul (source="charts") sans
  jamais poser `swift_top_done.lock`. Deuxieme bug trouve en verifiant le fix :
  `_TRAILING_CONTRACTION_RE.sub(r"\1s", s)` remplacait TOUJOURS par un `s`
  litteral quel que soit le groupe matche (`s|t|d|m|ll|re|ve`) — correct pour
  "who's"->"whos" mais cassait "don't"->"don t"->"dons" au lieu de "dont" (et
  pareil pour tout titre avec 't/'d/'m/'ll/'re/'ve). Fixe en `r"\1\2"`. Reflexe
  si un futur jeudi reste bloque en `waiting` : lancer
  `swift_top_100.py --date <jeudi> --variant all --dry-run` a la main pour voir
  le vrai traceback avant de soupconner un probleme de donnees/gate.
- **Piege distinct (meme audit) : `song_family` peut contenir du texte
  descriptif que les sources externes (YouTube) ne portent pas dans leur titre
  groupe, cassant le matching en mode combined sans jamais planter.**
  `_chart_lookup_key(combined=True)` priorise `song_family` ; si ce slug
  encode un suffixe genre `(Fifty Shades Darker)`, `(feat. X)` ou un `&` non
  converti en `and` (contrairement a `_clean_title_text` qui le fait), la cle
  ne matche plus jamais la cle YouTube correspondante -> 0 vues silencieux,
  sans lien avec les deux bugs regex ci-dessus. 4 cas trouves et corriges le
  2026-08-25 (renommage direct du `song_family` dans le JSON discographie,
  jamais de nouvelle regex globale — trop risque de sur-fusionner des vrais
  remixes/versions distincts, ex. "Lover (Remix) [feat. Shawn Mendes]" doit
  RESTER separe de "Lover") :
  - reputation.json "I Don't Wanna Live Forever (Fifty Shades Darker)" :
    `i_don_t_wanna_live_forever_fifty_shades_darker` -> `i_don_t_wanna_live_forever`
  - the_life_of_a_showgirl.json "The Life of a Showgirl (feat. Sabrina
    Carpenter)" (edition "extras", meme track_id que l'edition standard) :
    `the_life_of_a_showgirl_feat_sabrina_carpenter` -> `the_life_of_a_showgirl`
    (aligne sur les 3 autres entrees du meme titre qui utilisaient deja la
    bonne cle — incoherence entre editions, pas un cas isole)
  - lover.json "Miss Americana & The Heartbreak Prince" :
    `miss_americana_the_heartbreak_prince` -> `miss_americana_and_the_heartbreak_prince`
  - red.json "Safe & Sound - from The Hunger Games Soundtrack" ET
    red_taylor_s_version.json "Safe & Sound (feat. Joy Williams and John Paul
    White) (Taylor's Version)" : `safe_sound_from_the_hunger_games_soundtrack`
    / `safe_sound` -> `safe_and_sound` (les deux alignes pour fusionner
    correctement en mode combined, comme prevu par le design du chart)
  Reflexe pour detecter d'autres cas : comparer, pour chaque track, la cle
  utilisee (`_chart_lookup_key` avec `song_family`) a une cle de repli
  (`_normalize_title(base_title or title)`) ; si differentes ET que la cle de
  repli a un volume YouTube reel alors que la cle utilisee a 0, investiguer —
  mais NE PAS fixer en masse, la plupart des ecarts sont des versions/remixes
  legitimement separes (instrumentaux, commentary, mixes alternatifs) dont la
  separation est voulue.
- **Troisieme piege (meme audit 2026-08-25) : `swift_top_100.py` n'appliquait
  jamais le dedup des fusions catalogue Spotify actives**, contrairement a
  `generate_albums_image.py`/`export_for_web.py`/`post_gainer_thread.py`/
  `generate_weekend_streams_image.py` qui appellent tous deja
  `history_store.pick_active_catalog_merge_losers()`. Symptome : "Shake It
  Off" et "Love Story - Pop Mix" sont apparus en `NEW` a des rangs bas
  (#30/#61) sur le TayBoard du 2026-08-20 avec un total duplique de leur
  version principale — les track_id "Best Work Edition"/"Pop Mix" sont dans
  la liste de fusion active connue (memoire `spotify-streams-0817-corruption`,
  meme paires que Karma/Karma feat. Ice Spice a l'epoque). Fix : nouvelle
  fonction `_currently_merged_track_ids()` dans `swift_top_100.py` (lit
  `STREAMS_HISTORY_CSV` pour la date du chart, importe `history_store` en
  ajoutant `collectors/spotify/streams/tools/scripts` a `sys.path` — meme
  pattern que l'import dynamique de `r2` dans `_maybe_upload_to_r2`), appelee
  au debut de `_build_week_chart()` pour retirer ces track_id de
  `weekly_streams`/`daily_streams` avant tout scoring. Confirme avec le rerun
  du 08-20 : les deux entrees dupliquees ont disparu, le total du track
  gagnant ne change pas (Spotify traite deja les deux track_id comme un seul
  pool cote total, donc rien a additionner). Reflexe : toute nouvelle logique
  de classement/top-N dans ce repo doit appeler ce dedup — il n'est PAS
  partage automatiquement entre generateurs (meme lecon que l'incident
  Shake It Off/Love Story du 2026-08-22 documente dans la memoire
  `spotify-streams-0817-corruption`, juste jamais applique a `swift_top_100.py`
  jusqu'ici).
