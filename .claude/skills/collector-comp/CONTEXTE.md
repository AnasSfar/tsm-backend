# Contexte collectors/comp

## Role

`collectors/comp` n'est pas un collector autonome. C'est une bibliotheque
partagee pour generer des visuels et normaliser des donnees utilisees par les
collectors.

Modifier ce dossier peut affecter:

- Spotify Charts cards;
- Spotify Streams images;
- Apple Music cards/snapshots;
- Billboard/TayBoard images;
- previews locales.

## Fichiers

- `chart_card.py`: rendu HTML/PNG de cards chart.
- `song_card.py`: cards chanson — **orchestrateur uniquement** (2026-08-25).
  Contient les helpers partages (image/palette/logo/slugify/write PNG) et
  `render_song_card()`, qui prepare toutes les donnees communes (stats,
  titre, badge, footer, body_gap) puis delegue la generation du CSS a l'un
  des deux fichiers de style ci-dessous selon `best_since`. Ne plus ajouter
  de CSS directement dans `song_card.py` — ca va dans le fichier de style
  concerne.
- `song_card_default.py`: CSS du style "default" (`build_css(gradient,
  title_font_size, body_gap)`) — utilise par
  `post_weekend_song_gainers.py`.
- `song_card_best_since.py`: CSS du style "best-since" (`build_css(gradient,
  title_font_size, body_gap, badge_bg, badge_fg)`) — utilise par
  `post_best_day_since_twitter.py`. Seul ce style a le badge/sous-titre
  "Best day since...".
- `tables_image.py`: tableaux/images.
- `export_frame.py`: frame d'export autour des PNG.
- `discography.py`: helpers metadata discographie.
- `track_cover_cache.py`: cache covers.
- `fmt.py`: formatting.
- `preview.py`: previews.
- `previews/`: sorties/fixtures de preview.

## Regles

- Ne pas casser les dimensions attendues par les scripts appelants.
- Garder les composants deterministes: meme input => meme image.
- Eviter de changer globalement une palette/layout sans verifier les collectors
  qui consomment le composant.
- Les images generees ne doivent pas tronquer texte, nombres, ranks ou dates.
- Les assets/covers manquants doivent etre visibles comme probleme, pas caches
  par une fausse metadata.

## Verification

Choisir une commande de generation du collector impacte:

- Spotify Charts: `worldwide/tools/scripts/generate_card_images.py`
- Apple Music: `generate_country_card_images.py` ou `generate_snapshot_images.py`
- Billboard: `swift_top_100_image.py` ou wrappers Swift Top
- Streams: scripts `generate_*` dans `collectors/spotify/streams/tools/scripts`

Inspecter le PNG rendu. Pour gros changements, comparer avant/apres.

## Pieges

- `chart_card.py` est importe depuis plusieurs chemins (`collectors.comp` ou
  `comp`) selon le PYTHONPATH.
- Les scripts Playwright screenshot attendent souvent un element `#card` ou
  `.card`; ne pas renommer sans adapter les appelants.
- Les changements de CSS peuvent passer les tests CLI mais casser la lisibilite.
- `song_card_default.py`/`song_card_best_since.py` ne doivent JAMAIS importer
  depuis `song_card.py` (ca creerait un cycle d'import, puisque `song_card.py`
  les importe deja). Ce sont des fonctions pures `build_css(...)` qui ne
  prennent que des valeurs deja calculees (gradient, title_font_size,
  body_gap, badge_bg/fg) — pas de titre brut ni d'appel a un helper de
  `song_card.py`. Si un futur collector (Apple Music, Billboard) veut son
  propre style de song card, suivre le meme schema qu'un nouveau fichier
  `song_card_<style>.py` avec un seul `build_css(...)`, plutot que de
  rajouter une branche dans `render_song_card()`.

## `build_table_html(masthead_word=...)` (2026-08-25)

`tables_image.py::build_table_html` supporte un style de header alternatif
opt-in : `masthead_word="SONGS"` / `"ERAS"` (utilise par
`generate_streams_image.py` et `generate_albums_image.py`) remplace le header
classique logo+titre par un bandeau plus haut avec un gros wordmark fantome
en overlay sur la photo. Par defaut (`masthead_word=None`), tous les autres
appelants (`generate_snapshot_images.py` Apple Music, `post_song_overtakes.py`)
gardent le header classique inchange — ne jamais rendre `masthead_word`
obligatoire ni changer son comportement par defaut sans verifier ces deux
appelants. Charge une police Google Fonts ("Big Shoulders Display") via un
`<link>` ajoute uniquement quand `masthead_word` est fourni — seul point du
pipeline `comp/` qui depend d'une police externe ; degrade sans casser si le
rendu tourne hors-ligne.

Quand `masthead_word` est actif, `build_table_html` bascule aussi les lignes/
colonnes/footer vers un style "ledger" (`.ledger-*`, dark ou light selon
`masthead_theme`) au lieu du tableau classique — voir le detail cote produit
dans la skill `spotify-streams` ("Table Ledger"). Cote composant partage,
retenir :

- **`masthead_theme_for_date(date)` (2026-08-26)** : helper unique qui encode
  la regle proprietaire "thème selon le jour" — `"light"` sur les posts de
  semaine (lun-ven), `"dark"` le week-end (sam/dim). Accepte `date`/`datetime`/
  `"YYYY-MM-DD"`/`None` ; `None` ou inparsable -> `"dark"` (defaut sur). Tout
  appelant masthead du pipeline streams passe `masthead_theme=
  masthead_theme_for_date(target_date)` ; les overrides d'ere (ex. recap Best
  Day Since "Holiday Collection" = light toute l'annee) sont appliques par
  l'appelant *apres* cet appel. Ne pas dupliquer la logique jour-de-semaine
  ailleurs.
- `_LEDGER_THEME_TOKENS["light"]` est desormais un **blanc propre** (`#ffffff`,
  panneaux `#f4f6f8`, texte `#1a1d24`, vert/rouge vifs `#067647`/`#b42318`) —
  plus le beige `#f6f1ea` d'avant (2026-08-26, demande proprietaire : "blanc
  light theme"). Impacte toutes les cards masthead en semaine + le recap Best
  Day Since Holiday Collection.

- `ERA_ACCENT_COLORS` / `era_accent_color(album)` : palette figee par ere
  (calquee sur `tsm-frontend/frontend/src/utils/anniversaries.js`), pas
  couplee au rang du jour. A tenir a jour si le frontend ajoute/renomme une
  ere (pas d'entree pour "The Life of a Showgirl" cote frontend au moment de
  l'ecriture — couleur choisie a la main depuis `showgirl-museum.css`).
- `dominant_color_from_data_uri(data_uri)` : meme extraction que
  `get_dominant_color` mais depuis des bytes deja en memoire (une data URI
  d'un `image_cache` deja rempli) au lieu d'un chemin fichier — pas d'appel
  reseau supplementaire. Les deux partagent maintenant `_dominant_color_from_image`;
  toujours faire evoluer les deux ensemble si l'algo d'extraction change.
- N'importer `era_accent_color`/`dominant_color_from_data_uri` que dans un
  appelant qui utilise deja `masthead_word` — le style classique n'a pas de
  case couleur par ligne pour le rang.
