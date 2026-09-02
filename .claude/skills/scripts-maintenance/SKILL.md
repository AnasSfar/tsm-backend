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
python scripts/assign_tsm_ids.py --apply                              # attribue tsm_song_id / tsm_album_id + régénère catalog_index
```

Toujours dry-run d'abord (sans `--apply`) pour lire le diff avant d'écrire. Ces scripts créent un `.json.bak` à côté du fichier modifié — normal, pas besoin de le committer séparément.

## IDs internes du catalogue (`tsm_song_id` / `tsm_album_id`)

`scripts/assign_tsm_ids.py` — clé de jointure interne stable pour le catalogue, à relancer **après toute modification de `db/discography/`** (nouvelle chanson, save de l'éditeur GUI, édition manuelle). Idempotent, dry-run par défaut.

**Deux identifiants, rôles opposés :**

| | `tsm_song_id` / `tsm_album_id` | `slug` + `catalog_code` |
|---|---|---|
| Forme | base36 opaque court (`4ty3` / `lhq`) | `slug` lisible (`all-too-well`) ; `catalog_code` segmenté (`REDTV/std/05`) |
| Stabilité | **gelé à vie** | recalculé à chaque run depuis le titre / les champs de schéma |
| Stocké | dans les track objects de `db/discography/` **et** le registre | registre + `catalog_index` uniquement (jamais dans les fichiers de disco) |
| Rôle | jointure cross-collector + clé d'URL de partage | lecture humaine, tri, filtrage, debug |
| Cardinalité | 1 par chanson (toutes versions) / 1 par album | `catalog_code` : 1 par (chanson × placement) |

- **Source de vérité = `db/discography/tsm_id_registry.json`** (append-only). Une chanson supprimée/fusionnée n'est jamais effacée du registre → `merged_into` pointe le survivant. Éditable à la main si besoin (fusion manuelle).
- Détecte les renommages de `song_family` via le recoupement des `track_id` (courants + `historical_track_ids`) → l'ID est conservé, seul le label bouge.
- `db/discography/catalog_index.json` (commité) + `catalog_index.csv` (gitignoré, régénéré, à ouvrir dans un tableur) : une ligne par entrée track, toutes les colonnes de schéma décodées + `catalog_code` + `counts_toward_era` — **c'est la vue à plat pour « s'y retrouver »** dans les ~720 entrées éclatées sur 20 fichiers.
- **Warnings à traiter** (le script ne corrige jamais tout seul) : un `track_id` Spotify sur 2+ entrées (vrai doublon DB), une entrée sans `song_family` ni titre, deux identités qui résolvent vers le même `tsm_song_id` (fusion à valider). Au 2026-08-29 : 3 warnings connus (1 track sans id Spotify + les doublons `babe` / `ready_for_it` BloodPop remix).
- Consommé par l'export web : `collectors/spotify/streams/extras/export_for_web.py::load_tsm_slug_map()` porte `tsm_song_id` + `tsm_slug` dans `data/songs.json` / `data/albums.json` (jamais bloquant — un `[tsm-ids] WARNING` signale les tracks sans id, le fix est de relancer le script).

## Upload R2 (`scripts/r2.py`)

⚠️ **`--dry-run` est ici opt-in** (à l'inverse des scripts de backfill ci-dessus où dry-run est le défaut) : lancer `r2.py` sans flag **uploade réellement** sur le bucket prod (`taylor-data` ou `$R2_BUCKET`). Toujours passer `--dry-run` en premier pour prévisualiser.

Flags utiles pour limiter la portée d'un upload :
- `--new-date YYYY-MM-DD` : une seule date d'history au lieu de tout l'historique
- `--slugs a,b` : charts précis seulement (ex. `swift_top_albums,swift_top_eras`)
- `--streams-daily` : sous-ensemble statique affecté par l'export streams quotidien
- `--charts-only` : sous-ensemble statique charts affecté par `run_all_charts`
- `--worldwide-snapshot-only` (avec `--new-date`) : uniquement `history/charts_worldwide/YYYY-MM-DD.json`
- `--skip-{history,static,db,images}-upload` : désactive une section

Tous les préfixes/clés R2 (`history/`, `history-by-track/`, `data/`, `db/`, `images/apple-music/`, `apple-music/*`, `cache/*`, `hiring`, `report-*`...) sont centralisés dans `scripts/r2_keys.py` — importer ces constantes plutôt que redéfinir un `os.getenv("R2_..._PREFIX", "...")` local. Miroir côté frontend (valeurs tenues à la main, pas d'import cross-repo) : `tsm-frontend/api/data/r2_keys.py` — si une valeur change, mettre à jour les deux fichiers dans la même session.

## R2 : données pérennes vs cache

Avant de songer à "nettoyer" quoi que ce soit sur R2, vérifier cette liste — supprimer une vraie donnée historique est une régression au même titre qu'une donnée fausse (règle n°1 de la skill `data-rules`).

**Jamais supprimer** (vraie donnée historique, servie aux utilisateurs) :
- `history/`, `history-by-track/` — historique streams.
- `apple-music/snapshots/`, `apple-music/history-by-song/` — lus par `tsm-frontend/api/data/loader.py` pour des dates/chansons arbitraires.
- `data/{slug}_{date}.json` (TayBoard/Swift Top 100/albums/eras) — alimente le sélecteur de date de `frontend/src/pages/TayBoard.jsx`.
- `chart-history-global-by-track/` — équivalent chart de `history-by-track/`. ⚠️ Nommage incohérent entre les deux (connu, non corrigé — renommer nécessiterait une migration/copie de clé prod, hors scope ; voir docstring de `r2_keys.py`).

**Cache réellement supprimable** :
- `images/apple-music/*.jpg` — adressé par `md5(url CDN Apple)`, dédupliqué contre le CSV Apple Music **courant**. Aucun objet historique ne référence jamais notre propre clé R2 (ils stockent l'URL `mzstatic.com` d'origine) → un objet non référencé par le scan courant est un vrai orphelin. Nettoyage : `python scripts/prune_apple_music_images.py` (dry-run par défaut, `--apply` pour supprimer, manifeste local des clés supprimées sauf `--no-archive`). **Premier `--apply` en prod : vérifier manuellement un échantillon d'orphelins détectés avant de lancer.**
- `cache/*.json` (`home_highlights.json`, `version.json`) — 2 clés fixes, toujours écrasées, ne grossissent pas.
- `site-settings-backups/` (frontend) — déjà auto-pruné aux 20 derniers par `api/routes/site_settings.py`.

**Déjà gérable manuellement** : `hiring/`/`report-*`/`report-img-*` via `fetch_issues.py --save --delete` ; `db/` est un miroir écrasé en place, ne grossit pas.

**Visibilité avant d'agir** : `python scripts/check_r2_storage.py --breakdown` liste la taille/nb d'objets par préfixe (via `list_objects_v2`, coûte des requêtes List — usage manuel/hebdo, pas sur le cron de l'alerte de seuil). Constat au 2026-08 : `apple-music/snapshots/` est de loin le plus gros poste (~2,3 Go sur ~3,4 Go comptabilisés sur `taylor-data`) — et c'est justement de la donnée jamais supprimable ; `images/apple-music/` est négligeable (<1 Mo).

**Compression gzip** : option future non implémentée. Puisque la donnée historique ne peut pas être supprimée, la compression serait le seul autre levier pour limiter la taille stockée — mais nécessiterait de faire décompresser le chemin de lecture chaud du frontend (`tsm-frontend/api/data/loader.py::_read_r2_bytes`) en plus des writers backend (`r2.py`, `upload_ap_r2.py`, `chartr2.py`). Chantier dédié séparé si la croissance devient un problème malgré ce qui précède.

## Réparation d'historique cassé

- `shift_youtube_dates_back_one_day.py` : **one-off, fait le 2026-09-02.** Décale de −1 j la colonne `date` de `db/youtube_views_history.csv` + `db/youtube_title_history.csv` (le collecteur datait les lignes avec la date du run à minuit NY au lieu de la journée d'activité écoulée). Dry-run par défaut, `--apply --expect-latest YYYY-MM-DD` (le garde-fou anti-double-run — refuse si la date max ≠ celle attendue), `.csv.<horodatage>.bak` (gitignoré via `*.csv.*.bak`). Seule la colonne `date` change (réécriture du préfixe 11 car. de chaque ligne, diff minimal par ligne). Après : `python -c "from scripts import r2; r2.upload_youtube()"` puis `python scripts/generate_home_highlights.py`. Détail de la décision → skill `data-rules` § Data (YouTube).
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

## Éditeur graphique de combinaison YouTube (`scripts/youtube_grouping_editor/`)

Pour combiner manuellement plusieurs vidéos (officielle, lyric video, audio, remix…) sous un seul titre dans `youtube_title_history.csv`, quand le matching automatique de `title_groups.py` contre `db/discography/songs.json` échoue ou groupe mal : `run_youtube_grouping_editor.bat` (racine) ou `python scripts/youtube_grouping_editor/server.py [--port 8766] [--no-browser]` — serveur local stdlib, tableau/board dans le navigateur avec une colonne par groupe (glisser-déposer les vidéos, ou sélecteur par carte) + une colonne "Non groupées".

- Écrit uniquement `collectors/youtube/tools/json/video_groups.json` (`video_id → title_key`, gitignoré comme `video_db.json`) — ne touche jamais `db/discography/songs.json`. Cet override est prioritaire sur le matching automatique dans `build_title_rows()`, vidéo par vidéo.
- Sauvegarde en un seul bloc (tout le board envoyé à `/api/save`, pas un diff par ligne) : rejette si un `video_id` est assigné à deux colonnes ou si une colonne non vide n'a pas de titre ; backup `.ytgroup-<horodatage>.bak`, écriture atomique.
- Ne PAS lancer en même temps qu'une collecte YouTube (`update_youtube.py`) — pas de verrou partagé, usage local mono-utilisateur.

## ⚠️ Scripts destructifs — confirmer avec l'utilisateur avant `--apply`/`--yes`

- `reset_swift_top_100_history.py` : dry-run par défaut, `--yes` supprime l'historique Swift Top 100 (`--remove-bonuses`, `--skip-r2`).
- `migrate_app_r2.py --overwrite` : écrase des objets du bucket app.
- `migrate_daily_data_layout.py --apply --move` : migration de layout, déplace des fichiers.
- `prune_apple_music_images.py --apply` : supprime des objets R2 (images orphelines uniquement, voir section "R2 : données pérennes vs cache" ci-dessous) — dry-run par défaut, vérifier un échantillon avant le premier `--apply` en prod.

## Convention

- Quasiment tout `scripts/` est dry-run par défaut ou exige `--apply`/`--yes` — **sauf `r2.py`** (voir ci-dessus, seul cas où dry-run est opt-in).
- `check_r2_storage.py --dry-run` : teste l'alerte de seuil de stockage sans envoyer de notif ntfy. `check_r2_storage.py --breakdown` : mode séparé, visibilité taille/nb d'objets par préfixe (lecture seule, voir ci-dessous).

## Maintenance (obligatoire)

Nouveau script sous `scripts/`, option changée, ou nouveau workflow de rattrapage → mets à jour cette skill ET `REPO_CONTEXT.md` section 8 dans la même session.
