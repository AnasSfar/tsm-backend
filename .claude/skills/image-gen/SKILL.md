---
name: image-gen
description: Conventions de génération d'images backend TSM (cards Twitter, tableaux de charts) — composants partagés collectors/comp/, workflow de vérification par previews, pièges de layout récurrents, politique de covers. À charger avant de toucher song_card.py, tables_image.py ou tout générateur de PNG des collectors.
---

# Génération d'images backend (collectors)

## Hors périmètre : images de partage Open Graph

Les `og:image` du site (aperçu quand on partage un lien sur Twitter/Discord/…)
**ne sont PAS des cards `comp/`**. Ce sont de vraies captures Playwright des
pages du site live, produites par `scripts/generate_og_screenshots.py` (2×/j,
tâche `TSM OG Screenshots`) et servies depuis R2 `og/<slug>.png` par
`tsm-frontend/api/routes/og_images.py`. Ne pas les reconstruire en card. Détail
→ skills `pipeline-ops` et `tsm-map`.

## Architecture : tout passe par `collectors/comp/`

- Composants partagés : `song_card.py` (helpers partagés image/palette/logo — son propre style `render_song_card()` n'est plus posté nulle part depuis le 2026-08-26, legacy), `song_card_chart_sheet.py` (**la card chanson réellement postée** — voir section dédiée plus bas), `tables_image.py` (images à tableaux : gainers, top eras, màj albums, récaps…), `fmt.py`, `discography.py`, `track_cover_cache.py`.
- **Ne jamais dupliquer du style dans un script régional** — c'est une refonte volontaire (les régions passent colonnes/en-têtes/contenu en paramètres). Si deux générateurs partagent un style, il va dans `comp/`.

## Workflow obligatoire après toute modif visuelle

```powershell
python collectors/comp/preview.py [--only FAMILLE] [--date D] [--keep-html]
```
génère les previews de **tous les cas possibles** de song_card + tables_image → **regarder les PNG générés** (`collectors/comp/previews/`) avant de conclure. Le propriétaire vérifie visuellement ; « ça compile » ne suffit pas. Si les previews ne changent pas alors que le code a changé, c'est un cache/mauvais fichier — investiguer.

Pour tester un **nouveau** générateur/pipeline avec des données fictives (feature pas encore live, simulation d'une sortie future) plutôt qu'une retouche visuelle sur un générateur existant → skill `previews-and-sims` (dossier `previews_and_sims/<slug>/`), pas `collectors/comp/previews/` (réservé aux cas déjà couverts par `preview.py`).

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

## Top Eras — ère « Non-Album » (generate_albums_image.py)

`generate_albums_image.py` ajoute une ère `Non-Album` (constante
`NON_ALBUM_ERA`, source `db/discography/misc.json`, `force_album_name=True`)
qui regroupe les chansons hors album. `_is_unranked_era()` la reconnaît et
`build_album_rows` la trie **toujours en dernier** (`key=(_is_unranked_era, ...)`),
`rank=None` — elle ne prend jamais de place dans le classement. En dessous, la
ligne `Total` (`load_public_total_row`, classe `.ledger-row-total`) : ses
`daily`/`total` viennent de `site_history.json` (export web) et doivent
coïncider avec la somme des Daily Streams.

Sa cover est un **collage 2x2 de 4 covers hors-album tirées au hasard**
(`pick_non_album_collage` + `_attach_non_album_collage`, `import random`) :
priorité aux titres misc ayant des streams à la date courante, fallback sur
tout misc.json ; sélection différente à chaque génération. Rendu via
`.ledger-art-collage` (grid 2x2, `extra_css` local) dans `build_rows_html`
quand `row["collage_covers"]` est présent. `prefetch_covers` télécharge aussi
ces 4 URLs.

## Section "annoncée" (édition pas encore sortie) dans l'image album update

`generate_album_update_image.py` supporte une section catalogue marquée `"announced": true` (+ `"announced_text"` optionnel, sinon "COMING SOON") dans `db/discography/albums/<slug>.json` — cas d'usage : annoncer les titres d'une deluxe/édition pas encore sortie sur la card quotidienne, sans jamais afficher de streams inventés (règle n°1 `data-rules`). Ajouté 2026-09-23 pour "The Encore" (`the_life_of_a_showgirl.json`, section `the_encore`, 4 tracks, `release_date: 2026-09-25`, `announced_text: "OUT THIS FRIDAY, SEPTEMBER 25TH 2026"`).

- **Bypass volontaire du gate `release_date`** : `load_album_sections` exclut normalement toute section dont `release_date > target_date` (règle n°11 data-rules — jamais une section avant sa sortie). Une section `announced: true` est une exception explicite et assumée : ses tracks s'affichent quand même, mais **jamais avec un chiffre** — `build_table_dark_html` remplace les 4 colonnes numériques par une bannière texte (`.td.announced-banner{grid-column:span 4}`) tant que `track["announced"]` est vrai.
- **`announced` est maintenant auto-gaté par date, pas juste par le flag DB brut (fix 2026-09-23)** : `load_album_sections` calcule `track["announced"]` comme `(flag DB section/track) AND target_date < track["release_date"]` — dès que `target_date` atteint la vraie `release_date` du track, la bannière disparaît et le rendu normal (hist réel + `★`/`NEW`) prend le relais **automatiquement**, même si `"announced": true` traîne encore dans le JSON. Avant ce fix, le flag DB brut était utilisé tel quel (`bool(t.get("announced")) or bool(sec.get("announced"))`) — il fallait le retirer manuellement du JSON le jour de sortie sous peine de masquer les vraies données indéfiniment. Le nettoyage manuel du flag reste une bonne pratique (clarté du JSON) mais n'est plus une dépendance bloquante.
- **`announced_text` bascule automatiquement sur "OUT NOW" la veille de la sortie** (même fix) : `load_album_sections` compare `target_date` à la `release_date` de la section (min des `release_date` de ses tracks) — dès que `target_date >= release_date - 1 jour`, le texte stocké en DB (ex. `"OUT THIS FRIDAY, SEPTEMBER 25TH 2026"`) est remplacé par `"OUT NOW"` au rendu, sans toucher au JSON. Ça couvre exactement le cas "on poste le snapshot du jeudi le vendredi matin, l'ancien texte annonçant vendredi est déjà périmé". Le texte DB reste affiché tel quel pour tous les jours avant la veille.
- **Pas de sous-total pour une section annoncée** (`_table_dark_section_row` skip si `section.get("announced")`) — un total à 0 serait trompeur.
- Les tracks annoncés comptent quand même dans `_counts_in_album_total`/`_display_total_tracks` (via `on_album`/`chart_extra` normaux) mais contribuent 0 puisque `hist` n'a aucune ligne pour eux — le TOTAL global reste donc exact, basé uniquement sur les tracks réellement sortis.
- Ajouté 2026-09-23 pour "The Encore" (`the_life_of_a_showgirl.json`, section `the_encore`, 4 tracks, `release_date: 2026-09-25`). Voir mémoire projet `showgirl-encore-deluxe-release` pour le suivi de ce cas précis.

## Cover d'une card debut release (`post_debut_releases.py`) : fallback en 3 niveaux (2026-09-23)

Un titre tout juste ajouté au catalogue peut ne pas encore avoir de `image_url` en DB (le backfill catalogue peut être en retard sur la première collecte réussie — cas "The Encore"). `_resolve_image_url(ids, meta, cover_cache)` dans `post_debut_releases.py` essaie, dans l'ordre :
1. `image_url` du/des track_id(s) directement en DB (`db/discography/albums/*.json`).
2. `db/discography/track_cover_cache.json` (`track_cover_cache.get_cached_cover`) — **rempli automatiquement par `update_streams.py`** à chaque scrape réussi (le call `fetch_playcount_api` renvoie déjà `cover_url` dans ses metrics, `merge_track_cover_cache` l'écrit après chaque run, indépendamment du backfill catalogue). Un track dont le total du jour a été scrapé avec succès (condition déjà requise pour qu'un post debut se déclenche, `tid in day_rows`) a donc quasi toujours une entrée ici, même sans `image_url` catalogue.
3. La cover de l'**album** du track (`spotlight.load_covers()`, `db/discography/covers.json`), en dernier recours.
- Choix délibéré de **ne pas** croiser avec Apple Music/Spotify Charts par titre : matching fragile (mismatch possible), alors que les 2 fallbacks ci-dessus restent la même source (Spotify) déjà exacte et déjà câblée dans le pipeline.
- Si les 3 échouent (track jamais scrapé + pas de cover d'album connue), `image_url=None` → `_build_debut_html` retombe sur le placeholder gris `.debut-cover-ph`, comme avant.

## Renommage d'affichage d'un album sans toucher aux clés de matching

`comp/discography.py::ALBUM_DISPLAY_TITLE_OVERRIDES` / `display_title_for_album()` (ajouté 2026-09-23 dans `generate_album_update_image.py`, cas "The Life of a Showgirl" → "The Life of a Showgirl: The Encore" à l'annonce du deluxe, avant même le backfill catalogue du 2026-09-25 ; **déplacé dans `comp/discography.py` le 2026-09-23 même jour** pour être partagé entre générateurs) : override **texte affiché uniquement**, appliqué juste avant le rendu, jamais sur la clé de matching/tri/lookup :
- `generate_album_update_image.py` : titre de la card `hdr-title`/`album-title` + première ligne du tweet (`display_name = display_title_for_album(album_name)` passé à `build_html`/`build_table_dark_html`, substitué à `canonical_name` dans les f-strings de `_build_album_post_text`).
- `generate_albums_image.py` (Top Eras / Top Albums, `build_rows_html`) : le nom réel (`row["album"]`) sert à `era_accent_color`, au lookup `best_day_labels` et au tri/rank ; seule la variable locale passée à `ledger_name_with_best_day` (texte de la ligne) est overridée — ne jamais appliquer `display_title_for_album` avant ces lookups, seulement juste avant l'injection HTML.
- `generate_weekend_streams_image.py` (card combinée « Streams Recap » postée **chaque jour**, pas seulement le week-end) : **a ses propres renderers de ligne**, `_row_html` (réutilisé par `post_throwback_thread.py`) et `_mh_rows_html` (le masthead recap lui-même) — ni l'un ni l'autre ne passe par `generate_albums_image.build_rows_html`, donc oublié au premier passage du rename "The Encore" (repéré par le propriétaire sur un vrai post généré). Même pattern : `title = display_title_for_album(row.get("album") or "")` juste avant l'injection HTML, dans les deux fonctions.
- `post_albums_twitter.py::build_tweet_with_best_day` (phrase « biggest gainer » du tweet Top Eras) : `row["album"]` sert à la fois au lookup emoji (`album_emoji`, match par sous-chaîne donc insensible au override) et au texte affiché (`_short_album`) — séparé en `album` (réel, lookups) / `display_album` (texte imprimé).
- **`comp/song_card_chart_sheet.py::render_chart_sheet_card`** (la card chanson réellement postée — best-day-since single, weekend gainer) a un `.sc-subtitle` qui affiche le nom d'album passé par l'appelant. Overridé **au centre**, dans le composant partagé (`display_title_for_album(album)` au seul point de rendu), pas chez chacun de ses 2 appelants — `album` n'a aucun autre usage (lookup/matching) dans ce fichier.
- `post_best_day_since_twitter.py::_recap_row_html` (subtitle par chanson du tableau recap best-day-since, global + par-ère) et le titre/tweet de la card recap par-ère (`f"{era_display} - Best Day Recap"`, `best_day_since_era_recap_tweet(era=...)`) : override appliqué seulement à la valeur imprimée, `era_display` reste intact pour `header_images_for_album`, `_album_key`, le check `"holiday collection" in era_display.casefold()`.
- `post_stream_highlights_thread.py` (tableau Spotlight/GAINERS) : subtitle `album` par ligne overridé, `era_accent_color(...)` juste après continue de lire le nom réel (non overridé).
- **Réflexe pour tout nouveau renommage d'affichage similaire** : ce genre de rename touche au moins 3 chemins de rendu de ligne indépendants pour "Top Eras" (`generate_albums_image.build_rows_html`, `generate_weekend_streams_image._row_html`, `._mh_rows_html`) + la card chanson partagée (`song_card_chart_sheet.py`) + des caption/subtitle builders (`post_albums_twitter.py`, `post_best_day_since_twitter.py`, `post_stream_highlights_thread.py`) qui lisent tous `row["album"]`/`row.get("album")`/`track.get("album")` directement — grep `\.get\("album"\)|row\["album"\]|track\.get\("album"\)|\["album"\]` sur `collectors/spotify/streams/tools/scripts/*.py` **et** `collectors/comp/*.py` avant de considérer un rename comme terminé, ne pas supposer qu'une seule fonction partagée couvre toutes les surfaces.

Le nom catalogue réel (`album` dans `db/discography/albums/*.json`) reste inchangé partout ailleurs — dossier headers, `load_cover_url`, `ERA_MAP`/`ERA_COVER_PRIORITY`, index best-day-since, `album_update_slug`/locks, `_era_best_day_row`/`_album_best_day_row` (lookups) — donc aucun risque de casser un matching en changeant juste le nom public. Pour un renommage similaire (ex. suffixe "(Taylor's Version)", ère qui prend un sous-titre) : ajouter une entrée à ce dict dans `comp/discography.py` plutôt que de renommer le champ `album` dans le JSON (blast radius bien plus large : ~40+ occurrences par album, dossier headers, TayBoard, catalog_index...), et appliquer `display_title_for_album()` au tout dernier moment dans chaque nouveau générateur qui affiche un nom d'album/ère.

**Cover d'album mise à jour dans `db/discography/covers.json` (2026-09-23)** : `cover_url` de `"The Life of a Showgirl"` remplacé par le vrai artwork de l'édition Encore (`https://i.scdn.co/image/ab67616d0000b2733c9ea57c5fce6677a860a6e2`, résolu via l'oEmbed Spotify public de l'album deluxe `4hF2gTGuPYlykYuphDxi8J` — distinct du cover standard `...d7812467811a7da6e6a44902`). `covers.json` est la source unique lue par `comp.discography.build_cover_map`/`get_album_cover` et par `load_cover_url` (album update) — ce seul changement propage la nouvelle cover à la card album update, à Top Eras/Top Albums, et à tout fallback cover de track de cet album, sans toucher aux covers par-track (`image_url` catalogue, prioritaires).

Header épinglé pour cet album : `db/discography/headers/preferences.json` → `the life of a showgirl/the_encore_header_v2.png` (remplace l'ancien pin, pas d'ajout à la rotation aléatoire).

Thème `_table_dark_theme()` clé `"the life of a showgirl"` refait le 2026-09-23 pour coller au header "Encore" :
- **`hero-filter: none`, `hero-opacity: 1`** — le header est déjà saturé/vif avec seulement quelques zones sombres ; tout filtre CSS dessus l'assourdissait à tort. Overlay réduit à un simple fondu bas → `page-bg` (plus de vignette colorée sur les côtés).
- **Palette fixée en dur sur 3 hex fournis par le propriétaire** (pas dérivée de `header_accent`/dominante auto-échantillonnée — une 1ère tentative de dérivation automatique donnait un ton trop terne) : `bright #df024f` (accent, chiffres, TOTAL), `mid #960133` (haut du dégradé card-bg, hero-bg), `deep #760125` (bas du dégradé, base des cell-bg/head-bg/grid-line via `_mix_hex(deep, "#000000", …)`). Si le header change à nouveau, redemander les 3 tons au propriétaire plutôt que de ré-essayer une extraction automatique pour cette clé précise — ça a déjà été tenté et rejeté deux fois sur ce header.
- Section "The Encore" (voir plus bas) : ligne de sous-total repositionnée après le dernier track de chaque édition (pas avant), et rendue avec les mêmes classes `.td` que le tableau principal (`section-name{grid-column:1/3}`) plutôt qu'un grid CSS séparé recalculé à la main — un ancien essai avec des largeurs de colonnes indépendantes causait un léger décalage horizontal avec le tableau, corrigé en réutilisant le grid parent (même pattern que `.total-label{grid-column:1/3}`).

## Albums au branding noir et blanc (folklore, reputation)

`generate_album_update_image.py` extrait normalement l'accent (couleur de la barre "Total" de section et du handle @) depuis l'image header/cover, mais ses helpers (`_header_accent_color`, `_section_palette_colors`) forcent un plancher de saturation et **excluent volontairement les tons gris** pour rester "vifs" — sur un header quasi monochrome (folklore, reputation), ça fait remonter une couleur chair/tache chaude résiduelle (rose/beige) au lieu du gris attendu (fix 24/07/2026). `MONOCHROME_ALBUM_ACCENTS` dans ce fichier force un accent gris neutre (`#6b6b6b`) pour ces albums, cohérent avec le gris déjà codé en dur côté frontend (`tsm-frontend/frontend/src/utils/anniversaries.js` + `themes.css`, thèmes `theme-folklore`/`theme-reputation`). Si un autre album au cover très désaturé fait remonter une teinte parasite, l'ajouter à ce dict plutôt que de retoucher l'algo d'extraction (qui doit rester vif pour les covers colorées).

## Album update — variante overtake même-album (2026-09-06)

`generate_album_update_image.generate(album, date, flat_rank_by_total=True, highlight_track_ids={track_id: "overtaker"|"passed"})` : quand deux chansons **du même album** se dépassent au total streams, `post_song_overtakes.py` ne poste pas la card ledger STREAMS mais cette variante — suffixe fichier `_update*_overtake.png`, style `default` forcé (jamais `table-*`).

- **Liste plate 1..N triée par total streams** : `generate()` remplace `sections` par une seule section synthétique (`no_section_total=True`) contenant `_display_total_tracks(sections)` triés par `hist[tid]["streams"]` desc. La ligne grand-total (`Total` / `Total Era`) reste ; les section-totals par édition disparaissent.
- **Marqueur `▲n/▼n` par ligne** : `_flat_total_movement(pool, hist)` = position par total du jour vs position par total de la veille (`streams - daily`). **delta 0 ou pas de daily → `_movement_span` renvoie `""`** (la majorité des lignes d'un album ne bougent pas d'un jour à l'autre — un marqueur partout = bruit). Rendu inline dans `.col-rank` (`{rang}<span class="rank-move up|down">…`), **pas** de colonne de grille en plus (le grid `default` est fragile, on n'y touche pas).
- **Lignes des 2 chansons** : `highlight_track_ids` → classes `.song-row.ov-up` (overtaker, rail vert + tint) / `.song-row.ov-down` (passed, rail rouge). Un même album peut avoir plusieurs overtakers (ex. 3 chansons passent la même) → toutes en vert.
- Params ajoutés : `build_html(movement_by_track=, highlight_track_ids=)`, `build_song_row_html(movement_html=, row_cls_extra=)`. `movement_by_track=None` → comportement d'origine strictement inchangé pour la card daily.
- Piège track_id : l'event overtake vient de `history_store` (pipeline streams), l'image de `load_album_sections` (discographie) — si une chanson a des IDs Spotify différents entre les deux sources, son highlight/marqueur peut manquer. Rare (multi-éditions), pas bloquant.

## Covers des chansons

Politique décidée : **API Spotify en principal, images Apple Music en fallback** (elles ont les bonnes versions). Attention aux multi-versions : prendre la cover de la version principale de la chanson. Cache d'URL : `db/discography/track_cover_cache.json` (résolution track_id → URL, `comp/discography.get_album_cover`).

### Fetch d'image résilient — `comp/img_fetch.py` (obligatoire, ne pas dupliquer)

Tous les générateurs embarquent la cover en la téléchargeant au moment du rendu et en l'inline en data-URI. **Toujours passer par `comp.img_fetch.fetch_data_uri(url)`** (ou `tables_image.url_to_data_uri` / `download_as_data_uri` qui y délèguent). Ne JAMAIS recopier une petite fonction `_url_to_data_uri` locale avec juste un cache mémoire — c'est ce qui a causé les covers manquantes (un seul timeout réseau → `except` → `""` → placeholder vide, alors que les autres covers du même run passaient : *The Fate of Ophelia* 2026-08-27 charts, *Speak Now (TV)* 2026-08-26 album update).

`fetch_data_uri` : cache mémoire → **cache disque persistant** (`db/discography/.image_cache/`, gitignored, 1 fichier/URL) → download **3 tentatives** + fallback sur les autres tailles CDN Spotify. Une cover fetchée une fois avec succès n'est jamais re-téléchargée → un coup de mou réseau ultérieur ne peut plus la vider. Les échecs ne sont pas mis en cache disque.

Générateurs déjà migrés : `comp/tables_image.py` (→ global charts, apple music, spotlight, streams/albums image, best_day_since, overtakes, debut…), `generate_album_update_image.py`, `generate_card_images.py`. Copies privées restantes (à migrer si elles montrent le même bug de cover vide) : `generate_country_card_images.py` (apple music), `generate_artist_chart_image.py`, `billboard/swift_top_100_image.py` (celui-ci a une sémantique de placeholder SVG propre).

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

### Top Eras : un daily négatif ne doit plus poisonner ni bloquer l'agrégat (2026-09-23)

Incident réel (Red, 2026-09-22) : un run `--admin` a rescrapé tout le
catalogue les 09-21/09-22, et plusieurs tracks (Red: "The Last Time" ;
Fearless: "The Best Day" ; Midnights: "Karma (feat. Ice Spice)" ; Speak Now:
"Ours"...) ont reçu un `daily` négatif (`admin_override`) le 09-21. Deux bugs
en cascade :

1. **Faux badge "NEW"** — `generate_albums_image.build_album_rows`,
   `_usable_daily` rejetait tout daily négatif ; comme le track concerné est
   non-extra et déjà sorti (`required=True`), `_add_comparison_daily` mettait
   **tout le `yest_daily` de l'album à `None`**, propagé à l'ère combinée. La
   card affichait donc `Δ Day = "-"` **et** un badge `+/- = "NEW"` (faux —
   Red existe depuis 2012).
2. **Post bloqué entièrement** — `history_store.validate_released_active_history_complete`
   (le gate de complétude appelé avant tout artefact publiable) traite tout
   daily négatif hors 1er du mois comme une donnée invalide et lève
   `IncompleteHistoryError` → "albums post blocked... top eras image failed".
   Observé le 2026-09-23 avec 4 tracks (Karma feat. Ice Spice, Ours, The Best
   Day, The Last Time) le 09-21, bloquant le post du jour suivant.

**Décision propriétaire (2026-09-23) : ne pas masquer/bloquer, afficher le
vrai chiffre même négatif** — un daily `admin_override` négatif est déjà un
choix opérateur délibéré d'accepter le total brut Spotify tel quel (cf. skill
`spotify-streams`, "daily negatif admin_override" — "on ne fabrique jamais un
chiffre different de ce que Spotify a renvoye"). Le cacher derrière un "-" ou
bloquer le post entier contredisait cette règle déjà actée.

Fix :
- `generate_albums_image._usable_daily` **n'exclut plus les valeurs
  négatives** — elles sont sommées telles quelles dans `yest_daily`/`week_daily`,
  donc `Δ Day`/`Δ Week` et le badge de rang (`prev_rank`) reflètent le vrai
  mouvement (éventuellement réduit par la valeur négative), sans jamais
  retomber sur `None`/"NEW" à cause de ça.
- `history_store.validate_released_active_history_complete` **exempte les
  lignes `estimated_reason` commençant par `admin_override`** du check "daily
  négatif hors 1er du mois" — un négatif issu d'un scrape normal (pas
  `--admin`) bloque toujours (vraie corruption de scraping, pas une décision
  opérateur).
- Garde-fou restant (inchangé) : `generate_albums_image.build_rows_html` /
  `generate_weekend_streams_image._mh_move` affichent toujours un badge
  neutre `"–"` (pas "NEW") si `yest_daily` est explicitement `None` — ce cas
  peut encore arriver pour une raison différente (track vraiment manquant ce
  jour-là, pas juste négatif). Le `rank_change` **partagé**
  (`comp/tables_image.py`, aussi utilisé par Spotify Charts pour NEW/RE) n'a
  pas été touché.

## Deltas de rang

- **RE en bleu** ; NEW réservé aux vraies nouveautés. Apple Music : jamais de NEW rétroactif (→ skill `data-rules`).
- Gold = #1, vert hausse / rouge baisse (mêmes conventions que le site).
- **Piège corrigé (2026-07-21)** : `charts_history_global/fr/us/uk.csv` contient des vieilles lignes migrées (avant l'ajout de la colonne `movement`) où **le tout premier jour de chart d'une chanson est marqué `movement=RE`** au lieu de `NEW` (ex. les titres de folklore le 24/07/2020, jour de sortie surprise — `total_days=1`, `peak_rank` vide, mais `movement=RE` en dur). Le calcul du chg pour Spotify Charts (tab Image Studio du tsm-frontend, `api/routes/charts.py::_is_re_entry_chart_row`) faisait confiance à ce `movement` archivé en priorité, donc affichait RE-ENTRY sur des debuts réels. Fix : si `total_days<=1` (et `peak_rank` absent ou = rang courant), c'est forcément NEW, peu importe ce que dit le `movement` archivé — ce check passe maintenant AVANT la lecture du `movement`. `tables_image.py::rank_change` (Python, utilisé par les générateurs PNG des collectors) n'avait pas ce bug — il ne lit jamais de champ `movement`, seulement `previous_rank`/`total_days`/`peak_rank`.
- **Piège corrigé (2026-09-05) — Top Songs streams, `+/-` faux à partir du ~rang 10** : `generate_streams_image.build_top_n` appliquait `_drop_active_catalog_merge_duplicates` (retrait des track_id que Spotify est en train de fusionner dans le total d'un autre) **uniquement au jour courant**, pas aux fenêtres veille / semaine-dernière servant à calculer `prev_rank`. Un merge loser au **titre distinct** (ex. `Shake It Off (Best Work Edition)`, `Love Story - Pop Mix`) survit alors au dédup-par-titre dans le classement de la veille seulement → une entrée fantôme s'y intercale → toutes les chansons sous son rang héritent d'un `prev_rank` gonflé de 1 → faux `▲ 1` uniforme sur toute la moitié basse du tableau (observé 2026-09-04). Fix : `build_top_n` applique désormais le drop aux trois listes (`today_rows`, `yesterday_rows`, `last_week_rows`). Concerne aussi `generate_weekend_streams_image.py` et `post_throwback_thread.py` qui réutilisent `build_top_n`.

## Thème masthead : light en semaine, dark le week-end (2026-08-26)

Décision propriétaire : les cards à en-tête **masthead** / corps **ledger** du
pipeline streams rendent le thème **light (blanc `#ffffff`, plus le beige)** sur
les posts de semaine (lun-ven) et **dark** le week-end (sam/dim). Helper unique
`comp.tables_image.masthead_theme_for_date(target_date)` — basé sur la date des
données, jamais `now()`. Concerne : Top Songs, Top Eras, Spotlight Gainers,
recap quotidien (`generate_weekend_streams_image.py`, CLI `--light`/`--dark`),
recap Best Day Since **global** (header fixe « all eras » depuis le 03/09/2026,
plus de theming par album — voir `spotify-streams` § « Recap best-day-since :
header fixe »). **Exception** : la **card recap best-day-since PAR ÈRE**
(`_generate_recap_image(era_display=…)`, `--only-era-recap`) utilise le pool de
headers de l'ère + titre `{Ère} - Best Day Recap` (théming volontaire). Détail
produit → skill `spotify-streams` (« Thème masthead selon le jour », « Card
recap best-day-since PAR ÈRE »). Ne pas réimplémenter la règle jour-de-semaine
ailleurs ; toute nouvelle card masthead doit passer
`masthead_theme=masthead_theme_for_date(...)`.

### Marqueur ★ best-day-since sur les cards ledger (2026-09-03)

Top Songs (`generate_streams_image.py`), Top Eras (`generate_albums_image.py`) et
GAINERS (`post_stream_highlights_thread.py`) affichent `★ Titre · since <date>`
(ou `· of the year` / `· of the month`) dans la colonne Track/Album, comme les
images d'album update. Helper partagé `comp.tables_image.ledger_name_with_best_day(name_html, marker_label)`
(+ CSS `.ledger-name .best-day-star` / `.best-day-note` dans `STREAMS_TABLE_CSS`) ;
labels via `best_day_since.best_day_marker_text` / `best_day_marker_labels`
(track-level, records combined exclus) ou `compute_album_best_day_since` par
`era_key` (Top Eras). Toujours en try/except : un échec de lookup ne bloque
jamais la card.

### Taille du corps de masthead (2026-08-27)

Le header `.hdr.masthead` fait 168px de haut, mais le bloc gauche (badge
Spotify + titre + sous-titre) était calibré petit et paraissait perdu dans le
header. Valeurs partagées dans `tables_image.py` (touchent Top Songs, Top Eras,
Gainers ensemble) : `.hdr.masthead .hdr-title` 34px/900, `.hdr-sub` 18px/650,
`.mast-logo-badge` 56px (logo interne 28px). Les overrides `.hdr-title` /
`.hdr-sub` des scripts (`generate_streams_image.py` compact, `GAINER_LEDGER_CSS`)
sont des sélecteurs 1-classe : le sélecteur masthead 2-classes gagne toujours,
inutile de les neutraliser. Le récap quotidien
(`generate_weekend_streams_image.py`) a son **propre** masthead (`.mh-head`,
`.mh-head-in h1`), non concerné par ce réglage.

## `chart_card.py::render_chart_card` — bloc Streams optionnel (2026-09-24)

Le bloc `.metric-row`/`.streams-label`/`.change-row` (nombre de streams + delta
+ %) ne s'affiche plus que si `stats` contient une entree `{"label": "Streams",
...}`. Avant ce fix, un appelant qui ne fournit qu'un `Rank` (cas
`post_new_release_progression.py`, cards Apple Music/iTunes qui n'ont pas de
metrique streams) affichait quand meme le bloc avec `streams_value="-"` — un
gros tiret gras dans la couleur accent, illisible/confus (repere en generant
une vraie card via simulation, pas en relisant le code). Callers existants
(rank-record Spotify Charts) fournissent toujours un stat `Streams` -> `has_streams=True`
-> rendu strictement inchange. Nouveau caller sans metrique streams -> carte
juste plus courte (le contenu ne remplit plus tout le bas de la card), pas de
bloc vide.

## Sortie & posting

- PNG écrits dans `snapshots/<source>/YYYY/MM/YYYY-MM-DD/…`.
- Posting via `collectors/spotify/core/twitter.py` (Playwright, sessions par compte) — tout sur @swiftiescharts sauf FR ; locks et règles de complétude → skill `data-rules`.
- Logo Apple Music dispo : `collectors/apple_music/Apple_Music_icon.svg.webp` ; logo iTunes officiel (Wikimedia) : `collectors/apple_music/itunes_logo.svg`.

## Maintenance (obligatoire)
Nouveau composant dans `comp/`, nouvelle famille de previews, changement de politique covers/deltas → mets cette skill à jour dans la même session.
