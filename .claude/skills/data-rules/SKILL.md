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
4. **Jamais écrire `daily=0` pour un track pending épuisé** — on n'écrit pas la ligne (affichage « -- »), un 0 inventé pollue les charts et les posts. Un `same_total` reste pending pour tous les tracks, y compris les `chart_extra=true`. Seul un vrai total Spotify à 0 peut produire `same_total_zero`. Si tous les `chart_extra=false` sont complets, les extras encore pending ne bloquent pas le post/export final : ils continuent en retry parallèle.
5. **Le script ne s'arrête jamais tout seul** : toujours retry (rounds espacés), skipper le track bloqué plutôt que bloquer le pipeline, notifier via ntfy les tracks qui persistent. Ne jamais réintroduire un « max retries → stop » ou un « skip retries au premier run ». Idem en finalisation (depuis 2026-07-08) : les étapes de post sont indépendantes — un post qui échoue après ses retries est collecté et signalé en fin de run, il n'avorte plus les posts suivants ni le commit git (`run_final_update_tasks`).
6. **Invariant comptable : `total(J) = total(J-1) + daily(J)`** pour chaque track. Toute correction (fix_one, injection) doit préserver cet invariant en recalculant les daily voisins.
7. **Données injectées manuellement** (source de confiance, ex. @spotifyswiftie) = raison `manual_trusted` : **ne jamais les écraser ni les recalculer** ; elles servent de canari pour dater des totaux ambigus.
8. **Retard Spotify multi-jours** (ça arrive régulièrement) : ne jamais deviner à quelle date appartiennent les nouveaux totaux. Utiliser `reconcile_gap_catchup.py` (classe `single_day` / `fully_caught_up` / `partial_catchup` / `uncertain` — `uncertain` n'est JAMAIS auto-appliqué) et la logique canari. Deux updates ne doivent pas tourner en même temps sans savoir lequel date de quel jour.
9. **Pas de scraping HTML de fallback** — retiré volontairement, ne pas le réintroduire.

## Posting Twitter

- **Jamais poster si un album/tableau a un track manquant ou à 0** (albums, top 45, biggest daily/weekly gainers…). Complétude d'abord.
- **Locks `*_posted.lock`** pour tout ce qui poste (anti-double-post) ; `--force` les ignore consciemment.
- **Espacement entre posts : 60 s** (pas 180).
- **Tout est posté sur @swiftiescharts, sauf le chart FR.** L'ancien compte tsmusem13 n'est plus utilisé pour l'automatique.
- Charts régionaux : ne pas poster la même région deux jours de suite (système de score probabiliste) ; une **RE vaut beaucoup de points +, une OUT beaucoup de −**.
- **Apple Music : jamais de NEW pour une chanson déjà sortie** (on n'a pas l'historique complet AM) → tout est RE (affiché **en bleu**) ; NEW est réservé aux chansons qui sortiront après le début du scraping.
- Les seuils précis (best-day-since : jours mini/% mini pendant la collecte vs récap) **vivent dans le code** (`post_best_day_since_twitter.py`, `best_day_since.py`) — les lire, ne pas les supposer, ils ont changé plusieurs fois.

## Réflexes

- Modif du pipeline → demander : « est-ce que ça peut écrire/poster un chiffre faux ou incomplet ? » Si oui, bloquer sur la complétude, pas sur un timeout.
- Ops quotidiennes, commandes, rattrapage → skill `pipeline-ops` ; détail des scripts → `REPO_CONTEXT.md`.

## Maintenance (obligatoire)
Ces règles sont des décisions du propriétaire : si l'une d'elles évolue (nouveau seuil, nouvelle règle de posting, nouveau compte), mets cette skill à jour dans la même session — c'est la source de vérité des décisions produit.
