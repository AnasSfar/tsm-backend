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
- `song_card_chart_sheet.py`: **la card chanson réellement postée en prod**
  depuis le 2026-08-26 (`render_chart_sheet_card()` +
  `write_chart_sheet_card_png()`) — remplace l'ancien style best_since de
  `song_card.py`/`song_card_best_since.py` pour les deux vrais appelants,
  `post_best_day_since_twitter.py` et `post_weekend_song_gainers.py`. Détail
  produit complet dans la section « Chart Sheet » plus bas.
- `song_card.py`: garde les helpers partagés (image/palette/logo/slugify/
  write PNG) réutilisés par `song_card_chart_sheet.py`, `chart_card.py` et
  `youtube_card.py`. Son propre `render_song_card()` (styles `default` et
  `best_since`) et les fichiers `song_card_default.py`/
  `song_card_best_since.py` (CSS de ces deux styles) **ne sont plus appelés
  par aucun script de prod** depuis le passage à Chart Sheet — laissés en
  place comme référence/legacy, pas supprimés sans demande explicite. Ne pas
  les faire évoluer pour un nouveau besoin ; construire plutôt sur
  `song_card_chart_sheet.py` ou un nouveau fichier dédié.
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

## Chart Sheet song card (`song_card_chart_sheet.py`, 2026-08-26)

Designed with the owner as a Claude Artifact ("Chart Sheet Bloom") before being
built for real — see that history if the design rationale is ever unclear.
Real callers: `post_best_day_since_twitter.py` (kicker "Best day since
{date}" / "Best day ever" / "Biggest day of the year...") and
`post_weekend_song_gainers.py` (kicker "Weekend Gainer"). Both always show a
kicker row — there is no shorter "no kicker" variant in production, so the
card is a fixed `CARD_WIDTH=1080 × CARD_HEIGHT=594` CSS px always.

- **Background**: the track's own cover art, scaled past the frame
  (`inset:-90px`) and blurred with a plain CSS `filter:blur(52px)` — no
  Pillow-side blur pass, the same cover data URI already used for the
  thumbnail just gets reused with a blur filter inside the same Playwright
  render (like Spotify's own Now Playing screen). No frosted panel — text
  floats directly on the photo behind a dark scrim (`.sc-scrim`).
- **Bar chart**: `best_day_since.build_chart_sheet_bars(points, target_date,
  historical_date=..., historical_daily=...)` builds the 14-day column list.
  Each bar shows its own value (K/M-abbreviated, e.g. "842K") and date label,
  horizontal (not rotated — an earlier draft rotated them, changed after
  owner feedback). `today` gets the gold accent color; other bars are muted.
  Heights are proportional to the tallest value **in the window, including
  the historical one when present** — a "best day since X" row means day X's
  own total was *at or above* today's, not below it (`last_at_or_above.daily
  >= current.daily` in `compute_best_day_since`), so the dimmed callback bar
  can end up taller than today's. Don't assume today is always the visual
  max.
- **Historical callback bar**: only for `post_best_day_since_twitter.py`,
  only when `row["kind"] == "since"` (never for `"best_ever"` — nothing to
  reference) — pass `historical_date=date.fromisoformat(row["previous_higher_or_equal_date"])`
  and `historical_daily=row["previous_higher_or_equal_daily"]`. Rendered at
  50% opacity (`.sc-bar-col.historical`) with a "···" gap-marker column
  (`{"type": "gap"}`) right after it, signaling the time skip before the
  recent 14-day run. `post_weekend_song_gainers.py` never passes these — no
  specific record being referenced, so no callback bar.
- **Change field**: "Daily Streams" (signed count) + a combined "Change
  Daily / Weekly" field (`song_card_chart_sheet.format_change_html(daily_pct,
  daily_class, weekly_pct, weekly_class)`) + "Total Streams". Weekly is the
  point 7 days before `target_date` in the same Points list used for the bar
  chart — `None`/omitted gracefully if that day's data is missing, never a
  guessed number.
- **Accent color**: gold (`#F0B36A`) for positive values throughout
  (kicker, today's bar, "up" deltas) — **not** the site-wide green-up/red-down
  convention (`data-rules` skill) used elsewhere (chart ranks, etc.). This
  was an explicit owner call for this card specifically; `down` deltas still
  render red (`#fca5a5`), only `up` moved off green. Don't "fix" this back to
  green without asking — it's a deliberate deviation, not an oversight.
- **Title**: bucketed font size (`_title_font_size`, 44px down to 22px) plus
  a permanent `-webkit-line-clamp:2` safety net (max-width 620px) — a title
  can wrap to 2 lines but is never hard-clipped mid-word. Fixed real-data bug
  (2026-08-26): an early version used `white-space:nowrap` + `overflow:hidden`
  with no ellipsis, silently truncating long titles like "Safe & Sound (feat.
  Joy Williams and John Paul White) (Taylor's Version)" — caught by generating
  a real card, not from the mockup. The header row's height is fixed at the
  104px cover-thumb height regardless of 1 vs 2 title lines (2-line title
  block stays well under 104px even at the largest bucket), so this never
  needs the card's overall height to change.
- **Footer right**: `"Released {DD/MM/YYYY}"` from the track's catalog
  `release_date` when known, else falls back to the card's own date badge
  text — never "Since release" (the old song_card.py copy).
- **No `write_song_card_png` reuse**: dimensions differ from the legacy
  920×480 song card, so this has its own `write_chart_sheet_card_png`
  (viewport `CARD_WIDTH`×`CARD_HEIGHT`, screenshots `.sheet-card` specifically
  rather than `body`). `export_frame.py` is untouched/irrelevant here — it
  reads the image's own dimensions and was never wired into song cards
  anyway (only `chart_card.py` and the worldwide charts generator use it).
- **Fonts**: deliberately no Google Fonts dependency (unlike
  `tables_image.py`'s masthead) — system font stack only, so this stays fully
  offline-safe for the scheduled posting pipeline. Don't add IBM Plex Mono/
  Big Shoulders Display here without discussing the offline-reliability
  tradeoff first.
- `preview.py`'s song-card gallery (`--only song-card`) now generates
  `weekend_gainer_{short,long}` and `best_since_{solo,combined}_{short,long}`
  cases through this same renderer — the old `default_not_best_*` and
  `best_since_album` cases were dropped (album best-day-since never used
  `render_song_card`, see `generate_album_update_image.py` instead; plain
  no-kicker "default" style isn't posted anywhere).

## `build_table_html(masthead_word=...)` (2026-08-25)

`tables_image.py::build_table_html` supporte un style de header alternatif
opt-in : `masthead_word="SONGS"` / `"ERAS"` / `"STREAMS"` (utilise par
`generate_streams_image.py`, `generate_albums_image.py` et
`post_song_overtakes.py` — voir plus bas) remplace le header
classique logo+titre par un bandeau plus haut avec un gros wordmark fantome
en overlay sur la photo. Par defaut (`masthead_word=None`), les appelants
restants (`generate_snapshot_images.py` Apple Music) gardent le header
classique inchange — ne jamais rendre `masthead_word` obligatoire ni changer
son comportement par defaut sans verifier cet appelant. Charge une police
Google Fonts ("Big Shoulders Display") via un
`<link>` ajoute uniquement quand `masthead_word` est fourni — seul point du
pipeline `comp/` qui depend d'une police externe ; degrade sans casser si le
rendu tourne hors-ligne.

Quand `masthead_word` est actif, `build_table_html` bascule aussi les lignes/
colonnes/footer vers un style "ledger" (`.ledger-*`, dark ou light selon
`masthead_theme`) au lieu du tableau classique — voir le detail cote produit
dans la skill `spotify-streams` ("Table Ledger"). Cote composant partage,
retenir :

- **`post_song_overtakes.py` est passe au masthead/ledger le 2026-09-06**
  (`masthead_word="STREAMS"`, `masthead_theme=masthead_theme_for_date(date)`,
  titre `"Taylor Swift · All-Time Streams"`). Colonnes ledger
  `Rank | +/- | Track | Total | Daily | Daily Chg | Weekly Chg` (Total en
  premier + gras via `extra_css`, Daily attenue ; Weekly Chg = delta vs J-7).
  Les deux chansons de
  l'overtake sont mises en avant via `extra_css` local :
  `.ledger-row.overtaker` (rail + tint vert) / `.ledger-row.passed` (rail +
  tint rouge). Reutilise les helpers `era_accent_color` /
  `dominant_color_from_data_uri` / `ledger_name_with_best_day` /
  `masthead_theme_for_date` comme Top Songs. Seul `generate_snapshot_images.py`
  (Apple Music) garde encore le header classique.

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
