# Spotify app cards — propositions (2026-10-13)

Maquettes, rien en prod. Les autres images streams quotidiennes refaites dans la même famille que la
card album (page album Spotify), mais chaque image imite une page différente de l'app :

| Fichier | Image actuelle | Page Spotify imitée |
|---|---|---|
| `1_recap_artist_page_<date>.png` | `weekend_streams_image.png` (récap quotidien) | page artiste en paysage : colonne gauche = image du thème du jour (`day_theme.py` : anniversaire d'un événement de la frise → portrait de son ère + légende, sinon un des 3 modes au hasard), nom, monthly listeners / rang monde / followers avec leurs variations, total du jour + vs hier + vs semaine dernière + all-time, boutons ; colonne droite = « Popular today » (top 5) et « Top eras today » (5 tuiles). Fond = même dégradé que la card album, couleur tirée du portrait |
| `2_top_songs_playlist_<date>.png` | `streams_image*.png` (top 20) | playlist : mosaïque de 4 covers, « Daily Top Songs », 2 colonnes de 10 |
| `3_top_eras_discography_<date>.png` | `albums_image.png` (Top Eras) | discographie en tuiles : 13 ères + Non-Album + tuile Total |
| `4_gainers_{daily,weekly}_charts_<date>.png` | gainers jour / semaine | page Charts : cover de chart générée, le % en pastille verte en vedette |

Vraies données lues via les loaders des générateurs actuels (lecture seule), PNG dans `cards/`, aucun post.
Samedi/dimanche : les colonnes de variation passent en « vs last wk » (même règle que la card album).

    python previews_and_sims/spotify-app-cards/simulate.py [DATE] [recap|songs|eras|gainers ...]

Thème du jour (frise `taylor_swift_timeline_en_2006_2026.json`, module `collectors/spotify/streams/tools/scripts/day_theme.py`) :
un jour d'événement, la couleur de l'ère s'applique aux 4 images. Pour prévisualiser un thème sur des stats déjà finalisées :

    python previews_and_sims/spotify-app-cards/simulate.py 2026-10-11 --theme=2026-10-21   # stats du 11/10, thème du jour « Red »
