---
name: data-rules
description: Règles d'intégrité des données streams/charts TSM et règles de posting Twitter — les décisions produit non négociables prises au fil des sessions (jamais de fausse data, gestion des retards Spotify, manual_trusted, locks, comptes). À charger avant TOUTE modification du pipeline streams, de l'export web ou du posting.
---

# Règles d'intégrité de la data & du posting (décisions du propriétaire)

Ces règles ont été édictées puis re-corrigées plusieurs fois — les violer = régression garantie.

## Data (streams)

1. **Une donnée fausse est PIRE qu'une donnée manquante.** On ne publie jamais un chiffre douteux.
2. **`chart_extra=false` = catalogue officiel : doit être 100 % complet et exact** avant export web et avant tout post. Jamais estimé, jamais partiel. (Les extras peuvent attendre/être estimés, pas eux.)
3. **Estimations : UNIQUEMENT pour les tracks `chart_extra=true`**, marquées `estimated` avec raison, affichées comme estimation sur le site, recalculables quand de vraies données arrivent. Ne jamais estimer un non-extra.
4. **Jamais écrire `daily=0` pour un track pending épuisé sans preuve explicite** — un `same_total` reste pending par défaut. Seul un vrai total Spotify à 0 produit `same_total_zero`. Exception ciblée : un `chart_extra=true` déjà présent dans le `same_total.json` de la veille, encore inchangé après plusieurs retries, peut être écrit avec `daily=0` et raison `persistent_same_total_extra_zero`. Si tous les `chart_extra=false` sont complets, les autres extras encore pending ne bloquent pas le post/export final : ils continuent en retry parallèle.
5. **Le script ne s'arrête jamais tout seul** : toujours retry (rounds espacés), skipper le track bloqué plutôt que bloquer le pipeline, notifier via ntfy les tracks qui persistent. Ne jamais réintroduire un « max retries → stop » ou un « skip retries au premier run ». Idem en finalisation (depuis 2026-07-08) : les étapes de post sont indépendantes — un post qui échoue après ses retries est collecté et signalé en fin de run, il n'avorte plus les posts suivants ni le commit git (`run_final_update_tasks`).
6. **Invariant comptable : `total(J) = total(J-1) + daily(J)`** pour chaque track. Toute correction (fix_one, injection) doit préserver cet invariant en recalculant les daily voisins.
7. **Données injectées manuellement** (source de confiance, ex. @spotifyswiftie) = raison `manual_trusted` : **ne jamais les écraser ni les recalculer** ; elles servent de canari pour dater des totaux ambigus.
8. **Retard Spotify multi-jours** (ça arrive régulièrement) : ne jamais deviner à quelle date appartiennent les nouveaux totaux. Utiliser `reconcile_gap_catchup.py` (classe `single_day` / `fully_caught_up` / `partial_catchup` / `uncertain` — `uncertain` n'est JAMAIS auto-appliqué) et la logique canari. Deux updates ne doivent pas tourner en même temps sans savoir lequel date de quel jour.
9. **Pas de scraping HTML de fallback** — retiré volontairement, ne pas le réintroduire.
10. **Gap > 4 jours ⇒ daily VIDE, jamais le delta** (incident 12/07/2026 : 160 tracks backfillées ont enregistré leur total lifetime comme daily, +40M sur le jour). Quand un track n'a pas de ligne J-1 et que sa dernière ligne date de plus de `MAX_ESTIMATED_STREAM_GAP_DAYS` (4) jours : à la collecte, `try_apply_track_update` écrit le total en baseline avec daily vide (raison `baseline_after_long_gap`) ; à l'export, `normalize_daily_streams_from_totals` blanke tout daily couvrant un gap > 4 jours (sauf `manual_trusted`). Le vrai daily reprend le lendemain via l'invariant des totaux.
11. **Album update / throwback : jamais une section avant sa release_date, et NEW basé sur le catalogue, jamais sur l'historique CSV** (fix 24/07/2026, `generate_album_update_image.py`). Une édition/section (deluxe, extras...) ne doit jamais s'afficher avant que son `release_date` catalogue soit atteint — filtrer avec `load_album_sections(album, target_date)`. Le badge `NEW` (bleu) doit être déclenché uniquement quand `release_date == target_date` (`_is_release_day`), jamais en scannant si le track_id apparaît dans `streams_history.csv` avant la date : le CSV contient des lignes de baseline synthétiques (jour J-1 avec le même total, `daily=0`) autour d'une vraie release, et des tracks ajoutés au périmètre de collecte bien après leur vraie date de sortie (ex. remix "Bad Blood" collecté seulement depuis 2024 alors que sorti en 2015) — utiliser le CSV pour "NEW" ferait soit rater un vrai NEW, soit en afficher un faux sur un track juste incomplet (ce qui viole la règle n°1).

## Posting Twitter

- **Jamais poster si un album/tableau a un track manquant ou à 0** (albums, top 45, biggest daily/weekly gainers…). Complétude d'abord.
- **Locks `*_posted.lock`** pour tout ce qui poste (anti-double-post) ; `--force` les ignore consciemment. Depuis le 17/07/2026 : un `daily.py` régional appelé avec une date explicite (dont `--post-only`) respecte le lock (skip), et `post_with_image` re-vérifie le lock via `skip_if` APRÈS l'acquisition du slot de compte — tout nouveau chemin de post doit passer ce `skip_if`, sinon deux process concurrents peuvent poster en double (incident global/us 17/07/2026).
- **Espacement entre posts : 60 s** (pas 180).
- **Commentaires Spotify Charts** : quand un tweet chart ajoute un commentaire sous le header, laisser une ligne vide entre le header et le commentaire, finir le commentaire par une ponctuation, et inclure les streams exacts pour les NEW/RE quand le champ chart les fournit.
- **Spotify Charts Global** : si une priority chart_card Global existe (NEW/RE, ex. re-entry Global), elle doit partir avant le tweet chart Global principal.
- **Spotify Charts single-region** : une chanson qui entre/re-entre dans un seul pays doit utiliser/poster la chart_card régionale dédiée, avec un lock distinct du slug de card worldwide générale.
- **Caption top eras « over the last N days »** (fix 17/07/2026) : N = plus grand écart depuis la dernière donnée, calculé UNIQUEMENT sur les tracks ayant un daily non vide à la date cible (`_max_days_covered_fast`). Les tracks morts/délistés et les baselines post-gap (daily vide) sont exclus — sinon un seul track mort gonfle N (incident « last 39 days » du 16/07/2026).
- **Règles albums (décision 15/07/2026)** : thread groupé all-albums à chaque collecte du **lundi et vendredi** (lock `all_albums_thread_posted.lock`, distinct du top eras) ; cards album update (targets, +10 % de gain, gainers) **tous les jours de semaine** y compris lundi/vendredi ; **aucune card album le week-end** (early poster désactivé aussi) ; la photo top eras se poste toujours (hors week-end où elle est dans la recap combinée).
- **Un post avec image ne doit JAMAIS partir sans son image** : la vérification d'upload se fait dans le scope strict du composer (`core/twitter.py`) et re-check juste avant le clic — un attach non confirmé fait échouer le post (retry), il ne dégrade pas en texte seul (incident gainers du 13/07/2026).
- **Tout est posté sur @swiftiescharts, sauf le chart FR.** L'ancien compte tsmusem13 n'est plus utilisé pour l'automatique.
- Charts régionaux : ne pas poster la même région deux jours de suite (système de score probabiliste) ; une **RE vaut beaucoup de points +, une OUT beaucoup de −**.
- **Apple Music : jamais de NEW pour une chanson déjà sortie** (on n'a pas l'historique complet AM) → tout est RE (affiché **en bleu**) ; NEW est réservé aux chansons qui sortiront après le début du scraping.
- Best-day-since songs: if `days_since` is strictly greater than 60 days (more than two months), post it even if it is below the normal stream or day-over-day change gate; try these as priority early posts during collection.
- Les seuils précis (best-day-since : jours mini/% mini pendant la collecte vs récap) **vivent dans le code** (`post_best_day_since_twitter.py`, `best_day_since.py`) — les lire, ne pas les supposer, ils ont changé plusieurs fois.

## Réflexes

- Modif du pipeline → demander : « est-ce que ça peut écrire/poster un chiffre faux ou incomplet ? » Si oui, bloquer sur la complétude, pas sur un timeout.
- Ops quotidiennes, commandes, rattrapage → skill `pipeline-ops` ; détail des scripts → `REPO_CONTEXT.md`.

## Maintenance (obligatoire)
Ces règles sont des décisions du propriétaire : si l'une d'elles évolue (nouveau seuil, nouvelle règle de posting, nouveau compte), mets cette skill à jour dans la même session — c'est la source de vérité des décisions produit.
