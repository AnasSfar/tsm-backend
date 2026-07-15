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
| Reposter UNE étape (ex. top eras raté) | `python update_streams.py [D] --post-only top-eras` | idem ; étapes : top-eras (hors week-end), all-albums (thread groupé, lundi/vendredi seulement, lock `all_albums_thread_posted.lock`), top45, recap, best-day-since, debut, gainers, album-updates (jours de semaine uniquement, aucune card album le week-end) ; locks/guards respectés, `--no-post` = images seulement |

CLI définie dans `tsm/cli.py`. Outils streams : `collectors/spotify/streams/tools/scripts/` (`history_store.py`, `spotify_api.py`, `generate_albums_image.py`, `post_albums_twitter.py` hors week-end).
Sources actives streams : `db/discography/albums/*.json`, `songs.json`, `features.json`, `misc.json` quand une entrée a une URL Spotify ; seuls les `historical_track_ids` explicites et les entrées `exclude_from_stream_collection=true` sont retirés du périmètre actif. Les entrées `music_track=false` restent collectables mais sont `chart_extra` / hors stats publiques.

## Pannes connues
- **Run manuel sans tee = AUCUN log** : lancer `update_streams.py` à la main dans une console ne laisse aucune trace (le `run_update_streams.log` n'est alimenté que via `run_spotify_streams.bat` ; `python -m tsm` écrit dans `snapshots/run_logs/`). Diagnostic 14/07 : reconstruit uniquement depuis les artefacts snapshot. Toujours passer par le .bat ou `python -m tsm`.
- **WARP instable → lecture infinie** : le script reste bloqué en requête. Tuer le process (fenêtre PowerShell du .bat) et relancer ; vérifier WARP avant.
- **Silence après le header `Run — stats_date`** : depuis le fix du 2026-07-08 (`seen_before_ids` calculé via `HistoryIndex` en mémoire au lieu de 533 relectures du CSV), la progression démarre en quelques secondes. Un silence prolongé à cet endroit = vrai blocage réseau (WARP), pas une phase normale.
- Un run bloqué peut laisser un jour manquant → rattrapage ci-dessous.
- **X change son DOM → « X image upload non confirmee: scope=0/1 » sur TOUS les posts image** (incident 15/07/2026 : l'upload marchait, seule la vérification échouait). Diagnostic : ouvrir le HTML de debug (`%TEMP%\tsm_twitter_posts\x_upload_debug_*.html`) et mesurer les profondeurs d'ancêtres/aria-labels réels. Points fragiles dans `core/twitter.py` : `TWITTER_COMPOSER_SCOPE_MAX_DEPTH` (30 ; le conteneur éditeur↔toolbar était passé à ancestor::div[17]), `MEDIA_BUTTON_SELECTOR` (aria-label devenu « Add media »), et le sélecteur strict `div[role='textbox'][data-testid^='tweetTextarea_']` (le préfixe seul matche aussi `_label`/`RichTextInputContainer` → scope trop petit, comptage image toujours 0). Après fix : relancer `python -m tsm collect charts` — idempotent, ne reposte que ce qui n'a pas de `posted.lock`.

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
