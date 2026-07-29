---
name: scripts-maintenance
description: Backfills et maintenance sous scripts/ (tsm-backend) — enrichissement discographie (backfill_discography_from_spotify, metadata, genres, flags, cover cache), upload R2 (r2.py), réparation d'historique cassé, scripts destructifs à confirmer. À charger avant de lancer ou modifier un script sous scripts/.
---

# Maintenance scripts/ (tsm-backend)

Référence exhaustive de chaque script (options CLI complètes) : `REPO_CONTEXT.md` section 8.
Règles d'intégrité data (jamais de fausse donnée, manual_trusted…) → skill `data-rules`.
Rattrapage streams/charts au jour le jour (pipeline normal) → skill `pipeline-ops`.

## Workflow : nouvelle chanson / entrée catalogue à enrichir

Ordre (chaque étape lit la sortie DB de la précédente) :

```powershell
python scripts/backfill_discography_from_spotify.py --apply          # ajoute l'entrée en DB depuis Spotify
python scripts/backfill_spotify_track_metadata.py --apply             # metadata (release_date, etc.)
python scripts/enrich_genres.py --apply                               # genres
python scripts/infer_track_flags.py --apply                           # filter_tags déduits
python scripts/backfill_track_cover_cache.py                          # pré-chauffe les covers (pas de --apply, écrit direct)
```

Toujours dry-run d'abord (sans `--apply`) pour lire le diff avant d'écrire. Ces scripts créent un `.json.bak` à côté du fichier modifié — normal, pas besoin de le committer séparément.

## Upload R2 (`scripts/r2.py`)

⚠️ **`--dry-run` est ici opt-in** (à l'inverse des scripts de backfill ci-dessus où dry-run est le défaut) : lancer `r2.py` sans flag **uploade réellement** sur le bucket prod (`taylor-data` ou `$R2_BUCKET`). Toujours passer `--dry-run` en premier pour prévisualiser.

Flags utiles pour limiter la portée d'un upload :
- `--new-date YYYY-MM-DD` : une seule date d'history au lieu de tout l'historique
- `--slugs a,b` : charts précis seulement (ex. `swift_top_albums,swift_top_eras`)
- `--streams-daily` : sous-ensemble statique affecté par l'export streams quotidien
- `--charts-only` : sous-ensemble statique charts affecté par `run_all_charts`
- `--worldwide-snapshot-only` (avec `--new-date`) : uniquement `history/charts_worldwide/YYYY-MM-DD.json`
- `--skip-{history,static,db,images}-upload` : désactive une section

## Réparation d'historique cassé

- `fill_streams_from_archive.py` : reconstruit `streams_history.csv` depuis les Daily Archive (title-only ; attention aux lignes album/total après le séparateur, déjà filtrées par le script).
- `repair_charts_fr_archive_from_history.py dates… --dry-run` : répare l'archive FR à partir de l'history.
- `sync_spotify_country_charts_from_worldwide.py --charts --dry-run` : resynchronise les charts pays depuis worldwide.

## Refonte du schéma de discographie (`migrate_discography_schema.py` + éditeur)

Les anciens champs (`type`, `edition`, `section` tapé à la main, `filter_tags`) sont en cours de remplacement par un schéma explicite : `on_album`, `role` (single/album_track/extra), `extra_type` (live/remix/commentary/karaoke/voice_memo/acoustic/demo/instrumental/other), `category` (soundtrack/feature/collab/other), `release_edition`, `display_album` (ex-`display_era`), `tags` (vocabulaire réduit, ex. `christmas`). Détail complet du schéma → `REPO_CONTEXT.md` section 9 "Schéma de discographie".

- **Phase 1 (faite le 2026-07-29)** : `python scripts/migrate_discography_schema.py --apply` a ajouté ces nouveaux champs à toute la DB (793 tracks), de façon **additive** — `type`/`edition`/`section`/`filter_tags`/`display_era` restent intacts sur disque, donc aucun script existant n'est cassé. A aussi corrigé `song_family` pour ~70 extras mal reliés à leur chanson de base. Dry-run par défaut (écrit `data/schema_migration_review.csv`), `--apply` pour écrire, `--no-backup`.
- **~131 tracks** ont un mapping incertain (listés dans `data/schema_migration_review.csv`) — à relire/corriger via l'éditeur (colonnes `on_album`/`role`/`extra_type`/`category`) plutôt qu'à la main dans le JSON.
- **Phase 2 (pas encore faite)** : migrer chaque script qui lit encore `type`/`edition`/`section`/`filter_tags` (liste précise dans REPO_CONTEXT.md § 9), puis seulement retirer les anciens champs. Ne pas supprimer `type`/`edition`/`filter_tags` avant que cette phase soit confirmée terminée.
- `role="single"` n'est **jamais déduit automatiquement** (aucun champ source ne distingue un single d'un album_track) — c'est à taguer à la main via l'éditeur au fur et à mesure.

## Éditeur graphique de discographie (`scripts/discography_editor/`)

Pour des corrections manuelles ponctuelles (album/rôle/artistes/tags faux ou manquants sur des chansons existantes) plutôt qu'un ré-enrichissement en masse : `run_discography_editor.bat` (racine) ou `python scripts/discography_editor/server.py [--port 8765] [--no-browser]` — lance un serveur local (stdlib, zéro dépendance) et ouvre un tableau GUI dans le navigateur, groupé par album, avec sélection parmi les valeurs existantes plutôt que de la saisie libre pour les champs structurants. Édite le **nouveau schéma** ci-dessus, pas les anciens champs.

- Périmètre V1 : édite et **déplace** des tracks existants (changer Album/Section déplace le track entre fichiers/sections) ; ne crée/supprime pas de tracks, ne crée pas de nouveau fichier d'album.
- Sauvegarde : garde-fou de conservation du nombre total de tracks (annule tout si ça diffère), backup timestampé `*.discoedit-<horodatage>.bak` avant chaque écriture, écriture atomique (temp + rename), rechargement depuis le disque après un save réussi.
- `chart_extra` est toujours écrit **au niveau track** (jamais au niveau section) — cohérent avec la façon dont `swift_top_100.py`/`swift_top_albums.py` le lisent déjà (le override track prime sur la section).
- Ne PAS lancer `POST /api/save` (bouton "Enregistrer" de la GUI) en même temps qu'un collector tourne (`update_streams.py`, `run_all_charts.py`…) — pas de verrou partagé avec le pipeline, usage local mono-utilisateur.
- Case à cocher "Fait" (chanson déjà vérifiée) : appliquée immédiatement (pas besoin du bouton Enregistrer), stockée dans `discography_editor/review_state.json` — pur bookkeeping de session de relecture, hors `db/discography`, jamais lu par les collectors.
- Détail complet (architecture, colonnes, algorithme de sauvegarde) : `REPO_CONTEXT.md` section 8.

## ⚠️ Scripts destructifs — confirmer avec l'utilisateur avant `--apply`/`--yes`

- `reset_swift_top_100_history.py` : dry-run par défaut, `--yes` supprime l'historique Swift Top 100 (`--remove-bonuses`, `--skip-r2`).
- `migrate_app_r2.py --overwrite` : écrase des objets du bucket app.
- `migrate_daily_data_layout.py --apply --move` : migration de layout, déplace des fichiers.

## Convention

- Quasiment tout `scripts/` est dry-run par défaut ou exige `--apply`/`--yes` — **sauf `r2.py`** (voir ci-dessus, seul cas où dry-run est opt-in).
- `check_r2_storage.py --dry-run` : teste l'alerte de seuil de stockage sans envoyer de notif ntfy.

## Maintenance (obligatoire)

Nouveau script sous `scripts/`, option changée, ou nouveau workflow de rattrapage → mets à jour cette skill ET `REPO_CONTEXT.md` section 8 dans la même session.
