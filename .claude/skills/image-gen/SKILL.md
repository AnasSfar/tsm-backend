---
name: image-gen
description: Conventions de génération d'images backend TSM (cards Twitter, tableaux de charts) — composants partagés collectors/comp/, workflow de vérification par previews, pièges de layout récurrents, politique de covers. À charger avant de toucher song_card.py, tables_image.py ou tout générateur de PNG des collectors.
---

# Génération d'images backend (collectors)

## Architecture : tout passe par `collectors/comp/`

- Composants partagés : `song_card.py` (helpers partagés image/palette/logo — son propre style `render_song_card()` n'est plus posté nulle part depuis le 2026-08-26, legacy), `song_card_chart_sheet.py` (**la card chanson réellement postée** — voir section dédiée plus bas), `tables_image.py` (images à tableaux : gainers, top eras, màj albums, récaps…), `fmt.py`, `discography.py`, `track_cover_cache.py`.
- **Ne jamais dupliquer du style dans un script régional** — c'est une refonte volontaire (les régions passent colonnes/en-têtes/contenu en paramètres). Si deux générateurs partagent un style, il va dans `comp/`.

## Workflow obligatoire après toute modif visuelle

```powershell
python collectors/comp/preview.py [--only FAMILLE] [--date D] [--keep-html]
```
génère les previews de **tous les cas possibles** de song_card + tables_image → **regarder les PNG générés** (`collectors/comp/previews/`) avant de conclure. Le propriétaire vérifie visuellement ; « ça compile » ne suffit pas. Si les previews ne changent pas alors que le code a changé, c'est un cache/mauvais fichier — investiguer.

## Pièges de layout corrigés plusieurs fois (ne pas régresser)

- **Titres longs** : la taille de police doit s'adapter au nombre de caractères — un titre ne déborde JAMAIS du cadre (ni le @handle en bas). Ne jamais s'appuyer sur `white-space:nowrap;overflow:hidden` seul sans filet de sécurité — incident réel (2026-08-26, Chart Sheet) : un titre de 74 caractères a été tronqué en plein mot, sans ellipse ni indication, repéré uniquement en générant une vraie card (pas dans le mockup). Toujours combiner bucket de taille de police + `-webkit-line-clamp` (2 lignes) en filet de sécurité.
- **Footer** (logo, handle, date) : doit avoir son propre espace — ne jamais le laisser chevaucher la section au-dessus ; hauteur de card suffisante, pas de rendu « condensé ».
- Le background d'une card doit s'accorder aux couleurs de la cover de l'album (ou, pour Chart Sheet, en être directement une version floutée — voir plus bas).

### Legacy — ancien style `render_song_card()` (`song_card.py`, plus posté en prod)

Ces notes documentent le style best_since/default retiré le 2026-08-26 (remplacé par Chart Sheet pour les posts Spotify) — gardées pour référence si ce gabarit est un jour réutilisé (ex. par un nouveau collector), pas pour du travail courant :

- `.hdr-row` (logo Spotify + eyebrow + badge date) est hors du bloc centré verticalement — elle est épinglée en haut de `.info-col` (top:22px, un peu sous le top de `.cover-col` qui est à 10px, pas alignée pile dessus). Le reste (titre, extra/album, sous-titre, stats) vit dans `.body-col` (flex:1, `justify-content:center`, gap fixe 10px) en dessous. Ne pas remettre `hdr-row` dans le flex de `.body-col`.
- Ne pas utiliser `justify-content:space-evenly` (ou tout ce qui étire les gaps selon l'espace restant) dans `.body-col` — pour un contenu court (titre très court type "22", pas de sous-titre), ça pousse le bloc stats presque jusqu'au footer et peut le chevaucher. `justify-content:center` avec un `gap` calculé est borné et sûr quelle que soit la longueur du contenu.
- Le `gap` de `.body-col` est dynamique via `_body_gap(title, has_extra, has_subtitle)` (song_card.py) : plus le titre est long / plus il y a de lignes (extra=album, subtitle=badge best-since), plus le gap se resserre pour garder un bloc compact et équilibré, sans jamais grandir assez pour chevaucher le footer.
- Cards album best-since : le badge date en haut à droite doit dire `"Album - {date}"`, jamais `"{nom de l'album} - {date}"`. Personne n'a jamais posté d'album via ce style (`generate_album_update_image.py` fait ça différemment) ; règle gardée si ce cas revient un jour.

## Chart Sheet (`song_card_chart_sheet.py`) — la card chanson réellement postée

Design produit complet (background, bar chart, callback historique, couleur
accent, titre) → skill `collector-comp`, section « Chart Sheet song card ».
Pièges qui ont mordu une fois, à ne pas régresser :

- **Titre** : voir l'incident de troncature ci-dessus — toujours bucket de
  police + `-webkit-line-clamp:2`, jamais `overflow:hidden` seul.
- **Barre de callback historique** (best_since uniquement) : sa hauteur peut
  dépasser celle du jour courant — `previous_higher_or_equal_daily >=
  daily_streams` par construction de `best_day_since.compute_best_day_since`.
  Ne pas supposer que la barre "aujourd'hui" est toujours la plus haute du
  graphe.
- **Accent gold, pas vert** : ce card dévie volontairement de la convention
  site-wide vert=hausse/rouge=baisse pour les valeurs positives (gold partout,
  rouge conservé pour les baisses) — décision propriétaire explicite, ne pas
  "corriger" vers le vert.
- **Pas de police externe** : contrairement au masthead `tables_image.py`
  (Big Shoulders Display), Chart Sheet n'ajoute aucune dépendance Google
  Fonts — reste 100% hors-ligne-safe pour le pipeline planifié. Ne pas en
  ajouter une sans peser le compromis fiabilité.

## Spotlight (`collectors/spotify/streams/spotlight.py`) — carte "Total Streams"

- **Piège corrigé (2026-08-15)** : quand un jalon (milestone 100M/200M/…) est atteint ET que la carte "Total Streams" est aussi la carte `highlight` (bordure accent), les classes combinées sont `stat-card highlight stat-card-gold`. En CSS pur, `.stat-card.highlight` (2 classes) a une spécificité **supérieure** à `.stat-card-gold` (1 classe) — donc le fond doré et la bordure de `.stat-card-gold` étaient écrasés par le fond blanc/gris clair de `.stat-card.highlight`, alors que le texte restait forcé en blanc par `.stat-card-gold .stat-val`/`.stat-label` (2 classes, qui elles gagnaient) → texte blanc invisible sur fond quasi blanc. Idem pour `.stat-sub` ("800M MILESTONE") écrasé en couleur accent par `.stat-card.highlight .stat-sub` (3 classes) au lieu de rester blanc.
- Fix : ajouter des règles dédiées à spécificité égale mais placées **après** dans la feuille de style (`.stat-card-gold.highlight{background:...;border:none;...}` et `.stat-card-gold.highlight .stat-sub{color:rgba(255,255,255,.75)}`) — à spécificité égale, l'ordre de déclaration dans le CSS tranche. Réflexe pour toute nouvelle variante de carte combinée à `.highlight` : vérifier que le nombre de classes dans le sélecteur correspond, pas seulement l'ordre dans le fichier.
- Vérifier visuellement en forçant `milestone=` dans un appel direct à `spotlight._build_html(...)` (pas de scénario milestone dans `preview.py`) puis screenshot Playwright — c'est ainsi que le bug a été repéré (carte "Total Streams" blanche/illisible sur `the_1__2026-08-13.png`, `Wih_Lit__2026-08-12.png`, `Is_It_Over_Now...__2026-08-12.png`).

## Cadre d'export (`export_frame.py`)

**Pas utilisé par les song cards** (ni l'ancien `render_song_card`/`write_song_card_png`,
ni `song_card_chart_sheet.py`/`write_chart_sheet_card_png`) — seuls `chart_card.py`
(Spotify Charts) et le générateur worldwide charts appellent `add_export_frame`.
Vérifié 2026-08-26 par grep (une doc précédente affirmait à tort que song_card
le partageait). `add_export_frame` lit les dimensions réelles du PNG passé en
argument (`Image.open(path)`), donc dimension-agnostic — pas besoin d'y toucher
si la taille d'une card change.

- La marge autour de la card n'est plus un gris plat : `add_export_frame` échantillonne les bords de l'image rendue et teinte légèrement (`EXPORT_TINT_STRENGTH`) la couleur de fond (`EXPORT_BACKGROUND`) avec cette couleur — le cadre doit rester clairement neutre, juste « teinté » par l'accent de la card.
- La card elle-même est découpée avec des coins arrondis (`EXPORT_CORNER_RADIUS_CSS_PX`, actuellement 18px CSS) avant d'être collée dans le cadre — ne pas dupliquer ce radius dans le CSS interne des cards, il s'applique au niveau du screenshot final.
- **Si la marge blanche autour de la card paraît trop grande, ne pas réduire `EXPORT_MARGIN_CSS_PX`** (le propriétaire préfère la garder) — **augmenter la taille du contenaire (la card elle-même)** à la place, pour que la même marge fixe pèse proportionnellement moins.

## youtube_card.py : card dédiée pour les vidéos YouTube

- Les titres de vidéos YouTube (`collector-youtube`) sont de vraies phrases
  longues, contrairement aux titres de chansons courts que `song_card.py`
  était calibré pour. Plutôt que de détourner son style "default" — jamais
  posté en prod, même avant le passage à Chart Sheet — cette card vit dans
  son propre fichier `collectors/comp/youtube_card.py` (`render_youtube_card`),
  avec ses propres paliers de police (jusqu'à 3 lignes,
  `-webkit-line-clamp:3`) et une seule case stat (pas de doublon "First 24h" /
  "Total" quasi identiques sur une vidéo qui vient d'être publiée).
- Réutilise les helpers génériques de `song_card.py` (`image_data_uri`,
  `cover_palette`, `slugify`, `write_song_card_png`, `_tsm_logo_data_uri`)
  plutôt que de les dupliquer — seul le gabarit HTML/CSS est spécifique.
  Ces mêmes helpers sont aussi réutilisés par `song_card_chart_sheet.py`.
- `song_card.py` lui-même n'a plus été modifié pour ce cas d'usage.

## Albums au branding noir et blanc (folklore, reputation)

`generate_album_update_image.py` extrait normalement l'accent (couleur de la barre "Total" de section et du handle @) depuis l'image header/cover, mais ses helpers (`_header_accent_color`, `_section_palette_colors`) forcent un plancher de saturation et **excluent volontairement les tons gris** pour rester "vifs" — sur un header quasi monochrome (folklore, reputation), ça fait remonter une couleur chair/tache chaude résiduelle (rose/beige) au lieu du gris attendu (fix 24/07/2026). `MONOCHROME_ALBUM_ACCENTS` dans ce fichier force un accent gris neutre (`#6b6b6b`) pour ces albums, cohérent avec le gris déjà codé en dur côté frontend (`tsm-frontend/frontend/src/utils/anniversaries.js` + `themes.css`, thèmes `theme-folklore`/`theme-reputation`). Si un autre album au cover très désaturé fait remonter une teinte parasite, l'ajouter à ce dict plutôt que de retoucher l'algo d'extraction (qui doit rester vif pour les covers colorées).

## Covers des chansons

Politique décidée : **API Spotify en principal, images Apple Music en fallback** (elles ont les bonnes versions). Attention aux multi-versions : prendre la cover de la version principale de la chanson. Cache : `db/discography/track_cover_cache.json`.

## Daily négatif (`--admin`, `estimated_reason=admin_override`)

`generate_album_update_image.py` (ajout 2026-08-19) : un daily forcé négatif
(via `update_streams.py --admin`, cf. skill `spotify-streams`) doit s'afficher
avec un vrai signe moins et en rouge, jamais `+-{nombre}` (bug corrigé —
l'ancien code préfixait `"+"` sans vérifier le signe). Helper partagé
`fmt_signed(n) -> (texte, css_class)` dans ce fichier, utilisé pour la valeur
DAILY par piste, le total de section et le total d'ère. Piège de spécificité
CSS (même famille que l'incident spotlight `.stat-card.highlight` ci-dessous) :
`.col-num.daily-val`, `.sec-num`, `.era-num` fixent chacun une couleur par
défaut à spécificité égale ou supérieure à `.neg` seul — il faut une règle
dédiée par contexte (`.col-num.daily-val.neg`, `.sec-num.neg`, `.era-num.neg`)
placée après la règle de base, pas compter sur `.neg` seul.

## Deltas de rang

- **RE en bleu** ; NEW réservé aux vraies nouveautés. Apple Music : jamais de NEW rétroactif (→ skill `data-rules`).
- Gold = #1, vert hausse / rouge baisse (mêmes conventions que le site).
- **Piège corrigé (2026-07-21)** : `charts_history_global/fr/us/uk.csv` contient des vieilles lignes migrées (avant l'ajout de la colonne `movement`) où **le tout premier jour de chart d'une chanson est marqué `movement=RE`** au lieu de `NEW` (ex. les titres de folklore le 24/07/2020, jour de sortie surprise — `total_days=1`, `peak_rank` vide, mais `movement=RE` en dur). Le calcul du chg pour Spotify Charts (tab Image Studio du tsm-frontend, `api/routes/charts.py::_is_re_entry_chart_row`) faisait confiance à ce `movement` archivé en priorité, donc affichait RE-ENTRY sur des debuts réels. Fix : si `total_days<=1` (et `peak_rank` absent ou = rang courant), c'est forcément NEW, peu importe ce que dit le `movement` archivé — ce check passe maintenant AVANT la lecture du `movement`. `tables_image.py::rank_change` (Python, utilisé par les générateurs PNG des collectors) n'avait pas ce bug — il ne lit jamais de champ `movement`, seulement `previous_rank`/`total_days`/`peak_rank`.

## Thème masthead : light en semaine, dark le week-end (2026-08-26)

Décision propriétaire : les cards à en-tête **masthead** / corps **ledger** du
pipeline streams rendent le thème **light (blanc `#ffffff`, plus le beige)** sur
les posts de semaine (lun-ven) et **dark** le week-end (sam/dim). Helper unique
`comp.tables_image.masthead_theme_for_date(target_date)` — basé sur la date des
données, jamais `now()`. Concerne : Top Songs, Top Eras, Spotlight Gainers,
recap quotidien (`generate_weekend_streams_image.py`, CLI `--light`/`--dark`),
recap Best Day Since. Override : recap Best Day Since « Holiday Collection » =
light toute l'année (le code applique la règle du jour puis force). Détail
produit → skill `spotify-streams` (« Thème masthead selon le jour »). Ne pas
réimplémenter la règle jour-de-semaine ailleurs ; toute nouvelle card masthead
doit passer `masthead_theme=masthead_theme_for_date(...)`.

## Sortie & posting

- PNG écrits dans `snapshots/<source>/YYYY/MM/YYYY-MM-DD/…`.
- Posting via `collectors/spotify/core/twitter.py` (Playwright, sessions par compte) — tout sur @swiftiescharts sauf FR ; locks et règles de complétude → skill `data-rules`.
- Logo Apple Music dispo : `collectors/apple_music/Apple_Music_icon.svg.webp`.

## Maintenance (obligatoire)
Nouveau composant dans `comp/`, nouvelle famille de previews, changement de politique covers/deltas → mets cette skill à jour dans la même session.
