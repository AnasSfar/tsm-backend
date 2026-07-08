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

## Covers des chansons

Politique décidée : **API Spotify en principal, images Apple Music en fallback** (elles ont les bonnes versions). Attention aux multi-versions : prendre la cover de la version principale de la chanson. Cache : `db/discography/track_cover_cache.json`.

## Deltas de rang

- **RE en bleu** ; NEW réservé aux vraies nouveautés. Apple Music : jamais de NEW rétroactif (→ skill `data-rules`).
- Gold = #1, vert hausse / rouge baisse (mêmes conventions que le site).

## Sortie & posting

- PNG écrits dans `snapshots/<source>/YYYY/MM/YYYY-MM-DD/…`.
- Posting via `collectors/spotify/core/twitter.py` (Playwright, sessions par compte) — tout sur @swiftiescharts sauf FR ; locks et règles de complétude → skill `data-rules`.
- Logo Apple Music dispo : `collectors/apple_music/Apple_Music_icon.svg.webp`.

## Maintenance (obligatoire)
Nouveau composant dans `comp/`, nouvelle famille de previews, changement de politique covers/deltas → mets cette skill à jour dans la même session.
