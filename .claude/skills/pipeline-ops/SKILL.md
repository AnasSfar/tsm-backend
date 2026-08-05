---
name: pipeline-ops
description: Opérations du pipeline de données TSM (tsm-backend) — streams Spotify, charts, YouTube ; scripts, Task Scheduler, pannes connues (WARP), rattrapage d'un jour manqué, conventions de commit data. À utiliser pour tout ce qui touche à la collecte/l'update des données.
---

# Ops pipeline TSM (tsm-backend)

Référence exhaustive (chaque script + ses options) : `REPO_CONTEXT.md` à la racine de tsm-backend.
Règles d'intégrité data/posting (jamais de fausse data, manual_trusted, locks, comptes) : → skill `data-rules`.

Tout tourne **en local via le Planificateur de tâches Windows** (pas GitHub Actions). Les launchers `.bat` du scheduler sont dans `tsm-frontend/tasks/` (`run_spotify_streams.bat`, `run_spotify_charts_global.bat`, `run_spotify_charts_fr.bat`, `watch_logs.bat`) et font `cd` vers tsm-backend.

## Commandes
| Quoi | Commande | Où |
|---|---|---|
| Collecte quotidienne complète | `python -m tsm daily` | racine tsm-backend (`run_daily.bat`) |
| Tous les charts | `python -m tsm collect charts` | racine (`run_all_charts.bat`) |
| Streams Spotify | `python update_streams.py` | `collectors/spotify/streams/` (log : `run_update_streams.log` à côté) |
| Reposter UNE étape (ex. top eras raté) | `python update_streams.py [D] --post-only top-eras` | idem ; étapes : top-eras (hors week-end), all-albums (chaque album posté indépendamment — plus un thread depuis le 2026-08-05 —, tri par gain décroissant, lundi/vendredi seulement, toujours en dernier dans le run normal), top45, recap, best-day-since, debut, gainers, album-updates (ALBUM_UPDATE_TARGETS + gainers, jours de semaine uniquement, aucune card album le week-end, toujours en premier dans le run normal) ; locks/guards respectés, `--no-post` = images seulement |

CLI définie dans `tsm/cli.py`. Outils streams : `collectors/spotify/streams/tools/scripts/` (`history_store.py`, `spotify_api.py`, `generate_albums_image.py`, `post_albums_twitter.py` hors week-end).
Sources actives streams : `db/discography/albums/*.json`, `songs.json`, `features.json`, `misc.json` quand une entrée a une URL Spotify ; seuls les `historical_track_ids` explicites et les entrées `exclude_from_stream_collection=true` sont retirés du périmètre actif. Les entrées `music_track=false` restent collectables mais sont `chart_extra` / hors stats publiques.

## Pannes connues
- **Run manuel sans tee = AUCUN log** : lancer `update_streams.py` à la main dans une console ne laisse aucune trace (le `run_update_streams.log` n'est alimenté que via `run_spotify_streams.bat` ; `python -m tsm` écrit dans `snapshots/run_logs/`). Diagnostic 14/07 : reconstruit uniquement depuis les artefacts snapshot. Toujours passer par le .bat ou `python -m tsm`.
- **WARP instable → lecture infinie** : le script reste bloqué en requête. Tuer le process (fenêtre PowerShell du .bat) et relancer ; vérifier WARP avant.
- **Tâche planifiée tuée après son timeout (2h) → jour manquant sans aucune trace** (incident 2026-08-04) : la tâche « TSM Update Streams » a un "Arrêter la tâche après" de 2h. Si le run se bloque tout ce temps (typiquement WARP), le Planificateur la tue purement et simplement ; aucun dossier `snapshots/spotify_streams/AAAA/MM/AAAA-MM-JJ/` n'est même créé (le blocage survient avant toute écriture), et `db/streams_history.csv` n'a aucune ligne pour ce jour. Diagnostic : `schtasks /query /fo LIST /v` sur la tâche → `Dernier résultat: 267014` (= `SCHED_S_TASK_TERMINATED`) confirme un kill par timeout, pas un crash normal. Vérifier WARP puis relancer manuellement (`run_spotify_streams.bat` ou `python -m tsm daily`) dès que possible pour rattraper le jour.
- **Post album ciblé (`album-updates`) bloqué en boucle silencieuse par une section catalogue mal taguée** (incident 2026-08-05, The Life of a Showgirl) : `history_store.album_tracks_done_for` exige que TOUS les tracks non-`chart_extra` de l'album aient un daily réel pour la date — si une section du fichier `db/discography/albums/<slug>.json` a `"chart_extra": false` par erreur (au lieu de `true`, comme pour les sections `kworb_extras` des autres albums), ses track_id entrent dans le set obligatoire. Si ces track_id sont en fait des `historical_track_ids` retirés (donc plus jamais collectés), la condition n'est jamais remplie et le post ciblé est skip silencieusement chaque jour, sans erreur ni lock créé. Diagnostic : comparer les `chart_extra` de chaque section à un album similaire (ex. Speak Now) et vérifier en Python `history_store.album_tracks_done_for(album, date)` + les track_id manquants contre le CSV (dernière ligne avec `daily_streams` rempli).
- **Silence après le header `Run — stats_date`** : depuis le fix du 2026-07-08 (`seen_before_ids` calculé via `HistoryIndex` en mémoire au lieu de 533 relectures du CSV), la progression démarre en quelques secondes. Un silence prolongé à cet endroit = vrai blocage réseau (WARP), pas une phase normale.
- Un run bloqué peut laisser un jour manquant → rattrapage ci-dessous.
- **X change son DOM → « X image upload non confirmee: scope=0/1 » sur TOUS les posts image** (incident 15/07/2026 : l'upload marchait, seule la vérification échouait). Diagnostic : ouvrir le HTML de debug (`%TEMP%\tsm_twitter_posts\x_upload_debug_*.html`) et mesurer les profondeurs d'ancêtres/aria-labels réels. Points fragiles dans `core/twitter.py` : `TWITTER_COMPOSER_SCOPE_MAX_DEPTH` (30 ; le conteneur éditeur↔toolbar était passé à ancestor::div[17]), `MEDIA_BUTTON_SELECTOR` (aria-label devenu « Add media »), et le sélecteur strict `div[role='textbox'][data-testid^='tweetTextarea_']` (le préfixe seul matche aussi `_label`/`RichTextInputContainer` → scope trop petit, comptage image toujours 0). Après fix : relancer `python -m tsm collect charts` — idempotent, ne reposte que ce qui n'a pas de `posted.lock`.
- **Deux run_all_charts.py concurrents → charts global/us postés deux fois** (incident nuit 16→17/07/2026) : la tâche planifiée « TSM Spotify Charts Watch Release » (15h00, `run_all_charts.py --watch-release`, **aucun log** — sortie non tee'd) tournait EN MÊME TEMPS qu'un `python -m tsm collect charts` lancé à 16h07. Les deux ont détecté la publication Spotify (~23h57), collecté worldwide chacun, et chacun a lancé `{region}/daily.py --post-only <date>` — qui à l'époque ignorait `posted.lock` quand une date explicite était passée. Le 2e process attendait le slot de compte X pendant que le 1er postait, puis postait à son tour (~1 min après). FR n'a pas doublé uniquement parce que sa session était expirée. Fix 17/07 : les dailies avec date explicite respectent `posted.lock` (skip sauf `--force`) + re-check `skip_if` après acquisition du slot dans `post_with_image`. Réflexe : ne pas lancer un run charts manuel pendant que la tâche watch-release est active (vérifier le Planificateur / fenêtres PowerShell) — c'est désormais sans double post, mais ça double la collecte et les uploads.
- **Session X expirée → « X compose editor introuvable » avec URL de login** (fr-post 15/07/2026) : la redirection SPA vers le login arrive après le check `_looks_logged_out` du haut de boucle ; `_open_compose_and_wait_editor` re-vérifie maintenant l'état de session quand l'éditeur est introuvable et tente l'auto-relogin (credentials dans le `twitter_session.json` de la région). Si l'auto-relogin échoue (2FA/vérification), reconnexion manuelle : `setup_session` de `core/twitter.py` sur le fichier de session concerné (FR : `collectors/spotify/charts/fr/tools/json/twitter_session.json`).

## Rattraper un jour manqué (streams)
Script : `collectors/spotify/streams/tools/scripts/reconcile_gap_catchup.py` — reclasse les totaux réellement observés sur les bons jours (raison `manual_trusted`), n'invente jamais de valeur.
```powershell
python reconcile_gap_catchup.py 2026-07-03 --all-pending          # dry-run, classification
python reconcile_gap_catchup.py 2026-07-03 --all-pending --apply  # écrit les lignes sûres
# ciblé : --track-ids id1,id2   ou   --album Lover
```
Classifications : `single_day` / `fully_caught_up` / `partial_catchup` appliquées ; `uncertain` = jamais auto-appliqué, revue manuelle. Cross-check optionnel via `RAPIDAPI_KEY`.

## Données & commits
- Catalogue maître : `db/discography/artist.json` ; cache covers : `db/discography/track_cover_cache.json`.
- Backfill catalogue Spotify : `scripts/backfill_discography_from_spotify.py` inclut par défaut toutes les tracks musicales exposées par Spotify (albums, features, remixes, live, acoustic, mixes, versions régionales quand elles sont distinctes). Les commentary/karaoke/instrumental sont ignorés par défaut ; utiliser `--include-non-songs` pour les ajouter en DB, collectés comme `chart_extra` mais exclus des stats publiques.
- Conventions de commit data (voir historique) : `charts run all YYYY-MM-DD`, `youtube views YYYY-MM-DD`. Ne committer que sur demande.
- Les données finissent dans R2 (`taylor-data`) d'où l'API du frontend les lit — pas de deploy nécessaire côté backend pour que le site voie les nouvelles données.

## Maintenance (obligatoire)
Nouveau script, option changée, nouvelle panne connue ou nouveau workflow de rattrapage → mets à jour cette skill ET `REPO_CONTEXT.md` dans la même session.
