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
- Deezer CSV/snapshots (`db/deezer_global_chart.csv`,
  `db/deezer_artist_top_tracks.csv` — voir skill `collector-deezer`)
- discographie DB

### Deezer dans le scoring (ajoute 2026-08-09)

Meme mecanique qu'Apple Music (loi de puissance `500/rank^0.75`, best rank
par jour, ajoute a `total_units`) mais 2 sources seulement, pas de
country/genre :

- `DEEZER_GLOBAL_WEIGHT` (defaut `0.05`, env `TAYBOARD_DEEZER_GLOBAL_WEIGHT`)
  — chart "global" Deezer, discounte (moitie du poids d'Apple Music) car
  **confirme 2026-08-09 : c'est en realite le chart France**, pas mondial
  (geolocalise par IP). Renommage `DEEZER_GLOBAL_*` -> `DEEZER_FRANCE_*`
  decide mais **mis en pause** — voir `collector-deezer/CONTEXTE.md` et le
  TODO dans `collectors/deezer/global.py`.
- `DEEZER_ARTIST_FLOOR_RANK` (defaut `50`, env
  `TAYBOARD_DEEZER_ARTIST_FLOOR_RANK`) — le chart "top tracks" de Taylor sur
  Deezer est Taylor-only comme le chart TS d'Apple Music, donc ajoute a poids
  plein (pas de discount), avec un rang plancher si le morceau n'apparait
  pas dans le snapshot du jour.
- `total_units = units_spotify + units_am + units_deezer`. Aucune donnee
  Deezer n'existe avant le lancement du collecteur -> `units_deezer` vaut 0
  pour toutes les semaines passees, donc l'ajout ne modifie aucun
  `total_units` deja publie (verifie par diff `--dry-run` le 2026-08-09).
- Colonnes ajoutees a `swift_top_100_history.csv` /
  `swift_top_songs_history.csv` / `swift_top_albums_history.csv` :
  `units_deezer`, `deezer_pct`, `deezer_artist_score`, `deezer_global_score`.

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

## Highlights Charts Gallery

Depuis 2026-07-28, `swift_top_100.py` et `swift_top_albums.py` appellent en
best-effort (jamais bloquant) `scripts/generate_home_highlights.py --quiet`
a la fin de leur propre `_maybe_upload_to_r2` (donc sautee si `--skip-r2`).
Regenere `cache/home_highlights.json` et `cache/version.json` sur R2 (lus par
`tsm-frontend/api`).

## Pieges

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
