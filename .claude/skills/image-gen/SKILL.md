---
name: image-gen
description: Conventions de génération d'images backend TSM (cards Twitter, tableaux de charts) — composants partagés collectors/comp/, workflow de vérification par previews, pièges de layout récurrents, politique de covers. À charger avant de toucher song_card.py, tables_image.py ou tout générateur de PNG des collectors.
---

# Génération d'images backend (collectors)

## Architecture : tout passe par `collectors/comp/`

- Composants partagés : `song_card.py` (cards individuelles), `tables_image.py` (images à tableaux : gainers, top eras, màj albums, récaps…), `fmt.py`, `discography.py`, `track_cover_cache.py`.
- **Ne jamais dupliquer du style dans un script régional** — c'est une refonte volontaire (les régions passent colonnes/en-têtes/contenu en paramètres). Si deux générateurs partagent un style, il va dans `comp/`.

## Workflow obligatoire après toute modif visuelle

```powershell
python collectors/comp/preview.py [--only FAMILLE] [--date D] [--keep-html]
```
génère les previews de **tous les cas possibles** de song_card + tables_image → **regarder les PNG générés** (`collectors/comp/previews/`) avant de conclure. Le propriétaire vérifie visuellement ; « ça compile » ne suffit pas. Si les previews ne changent pas alors que le code a changé, c'est un cache/mauvais fichier — investiguer.

## Pièges de layout corrigés plusieurs fois (ne pas régresser)

- **Titres longs** : la taille de police doit s'adapter au nombre de caractères — un titre ne déborde JAMAIS du cadre (ni le @handle en bas).
- **Footer** (logo, handle, date) : doit avoir son propre espace — ne jamais le laisser chevaucher la section au-dessus ; hauteur de card suffisante, pas de rendu « condensé ».
- Le background d'une card doit s'accorder aux couleurs de la cover de l'album.
- **song_card** : `.hdr-row` (logo Spotify + eyebrow + badge date) est hors du bloc centré verticalement — elle est épinglée en haut de `.info-col` (top:22px, un peu sous le top de `.cover-col` qui est à 10px, pas alignée pile dessus). Le reste (titre, extra/album, sous-titre, stats) vit dans `.body-col` (flex:1, `justify-content:center`, gap fixe 10px) en dessous. Ne pas remettre `hdr-row` dans le flex de `.body-col`.
- **Piège corrigé** : ne pas utiliser `justify-content:space-evenly` (ou tout ce qui étire les gaps selon l'espace restant) dans `.body-col` — pour un contenu court (titre très court type "22", pas de sous-titre), ça pousse le bloc stats presque jusqu'au footer et peut le chevaucher. `justify-content:center` avec un `gap` calculé est borné et sûr quelle que soit la longueur du contenu.
- Le `gap` de `.body-col` est dynamique via `_body_gap(title, has_extra, has_subtitle)` (song_card.py) : plus le titre est long / plus il y a de lignes (extra=album, subtitle=badge best-since), plus le gap se resserre pour garder un bloc compact et équilibré, sans jamais grandir assez pour chevaucher le footer.
- **song_card, cards album best-since** : le badge date en haut à droite doit dire `"Album - {date}"`, jamais `"{nom de l'album} - {date}"` — le titre de la card EST déjà le nom de l'album, répéter le nom dans le badge est confus ; l'utilisateur veut que le badge signale clairement « ceci concerne un album, pas une chanson ». Actuellement seul `collectors/comp/preview.py` (case `best_since_album`) construit ce badge — aucun poster de prod n'utilise encore render_song_card pour les albums (les mises à jour d'album en prod passent par `generate_album_update_image.py`, un card style tableau différent). Si un vrai poster song_card pour albums est ajouté un jour, réutiliser ce même `"Album - {date}"`.

## Cadre d'export (`export_frame.py`, partagé song_card + tables_image)

- La marge autour de la card n'est plus un gris plat : `add_export_frame` échantillonne les bords de l'image rendue et teinte légèrement (`EXPORT_TINT_STRENGTH`) la couleur de fond (`EXPORT_BACKGROUND`) avec cette couleur — le cadre doit rester clairement neutre, juste « teinté » par l'accent de la card.
- La card elle-même est découpée avec des coins arrondis (`EXPORT_CORNER_RADIUS_CSS_PX`, actuellement 18px CSS) avant d'être collée dans le cadre — ne pas dupliquer ce radius dans le CSS interne des cards, il s'applique au niveau du screenshot final.
- **Si la marge blanche autour de la card paraît trop grande, ne pas réduire `EXPORT_MARGIN_CSS_PX`** (le propriétaire préfère la garder) — **augmenter la taille du contenaire (la card elle-même)** à la place, pour que la même marge fixe pèse proportionnellement moins. `song_card.py` fait 920×344px CSS (cover 321×321, offsets/paddings/font-sizes ~×1.15 par rapport à la base historique 800×299) suite à ce changement — si on retouche encore la taille de la card, garder ce même réflexe (grossir le contenaire, pas la marge) et repasser toutes les valeurs pixel (`.cover-col`, `.info-col`, tailles de police, `_best_since_title_font_size`, viewport Playwright dans `write_song_card_png`) au même facteur d'échelle pour ne rien casser.

## Albums au branding noir et blanc (folklore, reputation)

`generate_album_update_image.py` extrait normalement l'accent (couleur de la barre "Total" de section et du handle @) depuis l'image header/cover, mais ses helpers (`_header_accent_color`, `_section_palette_colors`) forcent un plancher de saturation et **excluent volontairement les tons gris** pour rester "vifs" — sur un header quasi monochrome (folklore, reputation), ça fait remonter une couleur chair/tache chaude résiduelle (rose/beige) au lieu du gris attendu (fix 24/07/2026). `MONOCHROME_ALBUM_ACCENTS` dans ce fichier force un accent gris neutre (`#6b6b6b`) pour ces albums, cohérent avec le gris déjà codé en dur côté frontend (`tsm-frontend/frontend/src/utils/anniversaries.js` + `themes.css`, thèmes `theme-folklore`/`theme-reputation`). Si un autre album au cover très désaturé fait remonter une teinte parasite, l'ajouter à ce dict plutôt que de retoucher l'algo d'extraction (qui doit rester vif pour les covers colorées).

## Covers des chansons

Politique décidée : **API Spotify en principal, images Apple Music en fallback** (elles ont les bonnes versions). Attention aux multi-versions : prendre la cover de la version principale de la chanson. Cache : `db/discography/track_cover_cache.json`.

## Deltas de rang

- **RE en bleu** ; NEW réservé aux vraies nouveautés. Apple Music : jamais de NEW rétroactif (→ skill `data-rules`).
- Gold = #1, vert hausse / rouge baisse (mêmes conventions que le site).
- **Piège corrigé (2026-07-21)** : `charts_history_global/fr/us/uk.csv` contient des vieilles lignes migrées (avant l'ajout de la colonne `movement`) où **le tout premier jour de chart d'une chanson est marqué `movement=RE`** au lieu de `NEW` (ex. les titres de folklore le 24/07/2020, jour de sortie surprise — `total_days=1`, `peak_rank` vide, mais `movement=RE` en dur). Le calcul du chg pour Spotify Charts (tab Image Studio du tsm-frontend, `api/routes/charts.py::_is_re_entry_chart_row`) faisait confiance à ce `movement` archivé en priorité, donc affichait RE-ENTRY sur des debuts réels. Fix : si `total_days<=1` (et `peak_rank` absent ou = rang courant), c'est forcément NEW, peu importe ce que dit le `movement` archivé — ce check passe maintenant AVANT la lecture du `movement`. `tables_image.py::rank_change` (Python, utilisé par les générateurs PNG des collectors) n'avait pas ce bug — il ne lit jamais de champ `movement`, seulement `previous_rank`/`total_days`/`peak_rank`.

## Sortie & posting

- PNG écrits dans `snapshots/<source>/YYYY/MM/YYYY-MM-DD/…`.
- Posting via `collectors/spotify/core/twitter.py` (Playwright, sessions par compte) — tout sur @swiftiescharts sauf FR ; locks et règles de complétude → skill `data-rules`.
- Logo Apple Music dispo : `collectors/apple_music/Apple_Music_icon.svg.webp`.

## Maintenance (obligatoire)
Nouveau composant dans `comp/`, nouvelle famille de previews, changement de politique covers/deltas → mets cette skill à jour dans la même session.
