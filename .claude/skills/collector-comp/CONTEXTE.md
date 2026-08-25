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
- `song_card.py`: cards chanson.
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
