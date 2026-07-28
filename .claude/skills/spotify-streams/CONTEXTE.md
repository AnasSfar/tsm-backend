# Contexte Spotify Streams

## Role

`collectors/spotify/streams` collecte les totals Spotify des tracks Taylor
Swift, calcule les daily streams exacts, maintient `db/streams_history.csv`,
exporte les donnees web/R2 et orchestre les posts streams.

Ce pipeline est plus sensible que les charts: une daily fausse contamine les
records, albums, best-day-since, gains et exports frontend.

## Entrypoint

Commande directe:

```powershell
python .\collectors\spotify\streams\update_streams.py
```

Commandes via CLI/ops:

```powershell
python -m tsm daily
python -m tsm collect streams
```

Le runner local Windows utilise aussi des `.bat` dans le frontend/tasks selon
`pipeline-ops`.

## Modes utiles

`update_streams.py` contient plusieurs modes; lire le code avant usage si la
commande ecrit ou poste.

Modes visibles dans les logs/code:

- `--dry-run`: scraping uniquement, aucune modification.
- `--debug-daily`: retry de tracks inacheves, ecrit l'history, pas de
  Twitter/git/forecast/images/notify.
- `--debug-total YYYY-MM-DD`: remplace des totals sur une date existante.
- `--post-only <steps...>`: poste des etapes depuis l'history existant, sans
  scraping/export/git.
- `--latest-history-date`: utilise la derniere date presente en history.
- modes locaux/test: pas de writes/R2/Twitter/git.

Avant de conseiller une option non listee ici, verifier `update_streams.py`.

## Donnees

Source principale:

```text
db/streams_history.csv
```

Archive/fallback:

```text
data/_archive/original/db/streams_history.csv
```

Exports:

```text
runtime/exports/web/site/data/
runtime/exports/web/site/history/
```

Objets R2 importants:

- `history/{YYYY-MM-DD}.json`
- `history-by-track/{track_id}.json`

Catalogue actif:

- `db/discography/songs.json`
- `db/discography/albums/*.json`
- `features.json`, `misc.json` quand les entrees ont une URL Spotify.

Seuls les `historical_track_ids` explicites et
`exclude_from_stream_collection=true` retirent vraiment des IDs du perimetre
actif. `music_track=false` reste collectable mais hors stats publiques selon
les regles data.

## Locks et etats

Les locks quotidiens vivent dans les chemins `spotify_chart_dir`/snapshots
selon `core.data_paths` ou dans `collectors/spotify/streams/history`.

Locks typiques:

- scraping complete;
- update complete;
- post locks (`posted.lock`, `*_posted.lock`, `*.streams_posted`);
- locks par etape Twitter dans les scripts `tools/scripts/post_*.py`.

Ne pas supprimer un lock sans verifier quelle etape il protege.

## Scripts principaux

Racine:

- `update_streams.py`: collecteur principal.
- `fix_one.py`: correction ciblee d'une chanson/date, avec recalcul voisins,
  export web et upload R2 cible.
- `fix_streams.py`: correction/scrape de masse via navigateur.
- `best_day_since.py`: calcule les notes best-day-since depuis history.
- `spotlight.py`: logique de mise en avant.

`tools/scripts/`:

- `history_store.py`: lecture/ecriture/dedupe history.
- `spotify_api.py`: acces API Spotify/ChartSnapshot selon config.
- `reconcile_gap_catchup.py`: rattrapage jours manques, applique seulement les
  classifications sures.
- `finalize_update.py`: finalisation export/post.
- `post_*`: etapes Twitter.
- `generate_*`: images albums/streams/weekend.
- `gap_estimate.py`, `forecast_milestones.py`: estimation/forecast encadree.
- `post_locks.py`: gestion locks posting.

`extras/`:

- backfills/exports/imports historiques, images, merges et nettoyage. Lire le
  script cible avant usage.

## Regles exact-data

- `total(J) = total(J-1) + daily(J)` doit rester vrai.
- Un gap de plus de 4 jours ne doit pas devenir un daily geant.
- `daily=0` n'est valide que sur preuve explicite, pas par `same_total` seul.
- `manual_trusted` ne doit pas etre ecrase.
- Les non-extra actifs doivent etre complets avant export/post final.
- Les extras peuvent etre pending/estimated seulement selon les regles du code
  et de `data-rules`.
- Pour toute correction historique, comparer DB, snapshots et exports affectes.

## Commandes de travail

Dry-run:

```powershell
python .\collectors\spotify\streams\update_streams.py --dry-run
```

Correction ciblee:

```powershell
python .\collectors\spotify\streams\fix_one.py "Song Title" 2026-03-10 980000 --dry-run
python .\collectors\spotify\streams\fix_one.py "Song Title" 2026-03-10 2500000000 --total --track-id SPOTIFY_ID
```

Best-day-since:

```powershell
python .\collectors\spotify\streams\best_day_since.py 2026-05-07 --limit 25
python .\collectors\spotify\streams\best_day_since.py 2026-05-07 --include-extras --no-write
```

Gap catchup:

```powershell
python .\collectors\spotify\streams\tools\scripts\reconcile_gap_catchup.py 2026-07-03 --all-pending
python .\collectors\spotify\streams\tools\scripts\reconcile_gap_catchup.py 2026-07-03 --all-pending --apply
```

## Highlights Charts Gallery

Depuis 2026-07-28, `finalize_update.py::run_final_update_tasks` appelle en
best-effort (jamais bloquant) `scripts/generate_home_highlights.py --quiet`
juste apres le web export, sauf en `--local-test`/`--test`. Regenere
`cache/home_highlights.json` et `cache/version.json` sur R2 (lus par
`tsm-frontend/api`). Un echec ne doit jamais faire echouer la finalisation.

Ce script reutilise directement `best_day_since.py` (load_tracks,
load_history, compute_best_day_since_combined, passes_filters, sort_key) pour
produire le highlight `best_day_since` — meme logique de regroupement par
`song_family` et memes filtres que ce qui serait poste sur Twitter, pas de
duplication.

## Pieges

- Un run manuel sans le `.bat` peut ne pas laisser le meme log scheduler.
- WARP ou Spotify peuvent bloquer longtemps; ne pas masquer par timeouts qui
  publient partiel.
- Deux runs concurrents peuvent doubler collecte ou posts si les locks/skip_if
  ne sont pas respectes.
- Toute nouvelle etape de post avec image doit echouer si l'image n'est pas
  attachee, pas degrader en texte seul.
- `generate_album_update_image.py` (throwback/album update) filtre les
  sections par `release_date` catalogue et base le badge NEW sur
  `release_date == target_date`, jamais sur `streams_history.csv` (voir
  `data-rules` regle 11). Certains tracks anciens ont des trous de collecte
  reels dans le CSV (ex. remix "Bad Blood" collecte seulement depuis 2024,
  quelques tracks reputation/Lover sans ligne a des dates anciennes) : ca
  s'affiche en tirets `-`, pas en NEW ni en donnee inventee — c'est attendu
  tant que l'historique n'est pas backfille pour ces track_id.
