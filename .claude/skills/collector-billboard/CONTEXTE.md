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
