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
