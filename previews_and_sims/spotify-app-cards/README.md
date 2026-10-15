# Spotify app cards (2026-10-13) — EN PROD

Validées puis branchées le 2026-10-13 : le rendu vit dans `collectors/spotify/streams/tools/scripts/spotify_app_cards.py` (appelé par les générateurs, repli auto sur l'ancien rendu, `SPOTIFY_APP_CARDS=0` pour revenir) ; `simulate.py` ici n'est qu'un appel de ce module sur vraies données, PNG dans `cards/`, aucun post. Les autres images streams quotidiennes refaites dans la même famille que la
card album (page album Spotify), mais chaque image imite une page différente de l'app :

| Fichier | Image actuelle | Page Spotify imitée |
|---|---|---|
| `1_recap_artist_page_<date>.png` | `weekend_streams_image.png` (récap quotidien) | page artiste en paysage : colonne gauche = image du thème du jour (`day_theme.py` : anniversaire d'un événement de la frise → portrait de son ère + légende, sinon la sortie la plus récente : pochette album/single en priorité, sinon affiche/photo Eras Tour), nom, date du jour toujours en grand, légende d'événement en grand (jaune), monthly listeners / rang monde / followers avec leurs variations, boutons ; colonne droite = bandeau horizontal « Daily streams » en haut (total du jour + all-time, puis variations vs hier et vs même jour semaine dernière côte à côte), puis « Popular today » (top 5) et « Top eras today » (5 tuiles). La colonne la plus courte est centrée verticalement par rapport à l'autre. Fond = même dégradé que la card album, couleur tirée du portrait |
| `2_top_songs_playlist_<date>.png` | `streams_image*.png` (top 20) | playlist : mosaïque de 4 covers, « Daily Top Songs », 2 colonnes de 10 |
| `3_top_eras_discography_<date>.png` | `albums_image.png` (Top Eras) | page discographie en paysage (refaite le 2026-10-13, le mur de 15 tuiles identiques n'avait aucune hiérarchie) : en-tête titre + date en grand à gauche, bandeau « Daily streams » à droite (total, variation, all-time) ; **podium** = top 3 en grandes cartes (cover + nom + daily + pastilles + plays) ; puis ères 4-13 + Non-Album (sans rang, en dernier) en lignes compactes sur 2 colonnes (Plays / Daily / +/-). Noms longs sur 2 lignes plus petites, jamais coupés |
| `4_gainers_{daily,weekly}_charts_<date>.png` | gainers jour / semaine | page Charts : cover de chart générée, le % en pastille verte en vedette |
| `5_billion_watch_<date>.png` | nouveau post `post_milestone_watch.py` (palier franchi ; 900M/1B/2B/3B aussi quand la liste change) — `watch=900M` / `watch=2B` pour les autres paliers | page Charts « Billion Watch » (jamais « Road to 1B », nom pris par un autre compte) : les 10 prochaines chansons sous 1B classées par date attendue ; colonne Plays = total + barre de progression vers 1B + « N to go » en jaune (fusionnées pour agrandir le contenu), puis Daily, Expected (date + « in N days »). Prévision = même modèle que `forecast_milestones.py` (site, expected milestones) avec cible fixée à 1B et facteur de tendance plafonné à 1.0 (pas de croissance composée sur des années ; sinon une version karaoké à 1,7k/jour arrive à 1B en 2029). Horizon max 5 ans |

Date toujours en grand : dans la cover pour les pages chart/playlist (coin haut-droit de la cover Gainers / Billion Watch, bandeau en bas de la mosaïque Top Songs), en ligne dédiée sous le titre pour Eras et la page artiste.

Vraies données lues via les loaders des générateurs actuels (lecture seule), PNG dans `cards/`, aucun post.
Samedi/dimanche : les colonnes de variation passent en « vs last wk » (même règle que la card album).

    python previews_and_sims/spotify-app-cards/simulate.py [DATE] [recap|songs|eras|gainers|billion|watch=900M ...]

Thème du jour (frise `taylor_swift_timeline_en_2006_2026.json`, module `collectors/spotify/streams/tools/scripts/day_theme.py`) :
un jour d'événement, la couleur de l'ère s'applique aux 4 images. Pour prévisualiser un thème sur des stats déjà finalisées :

    python previews_and_sims/spotify-app-cards/simulate.py 2026-10-11 --theme=2026-10-21   # stats du 11/10, thème du jour « Red »
