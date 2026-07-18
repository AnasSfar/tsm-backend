---
name: collector-apple-music
description: Contexte obligatoire avant TOUT travail sur collectors/apple_music/ (audit, bugfix, feature, run manuel) — architecture, comportement d'échec du runner, règles data spécifiques Apple Music (NEW vs RE, IDs > titres), points fragiles connus. Premier skill de la série « un skill par collecteur ».
---

# Collecteur Apple Music (`collectors/apple_music/`)

Briefing complet (arbre annoté, endpoints, env, points fragiles détaillés) : `docs/apple-music-script-context.md`. Ce skill est le résumé opérationnel ; le doc est la référence. Règles data générales : → skill `data-rules`. Toucher aux scripts image : → skill `image-gen` d'abord.

## L'essentiel en 30 secondes

- Entrée : `python -m tsm collect apple-music` (log dans `snapshots/run_logs/`) → `run_apple_music.py`. Launcher scheduler : `run_apple_music.bat` (tourne ~6×/jour, **sans log tee'd**). Prod = Task Scheduler local Windows.
- Runner (depuis 2026-07-18) : `global.py` → `ts_page.py` → **`country_all.py`** (songs+albums+vidéos par pays en 1 requête `types=songs,albums,music-videos`) → **`genre_all.py`** (songs+albums par genre en 1 requête). Les scripts per-type (`country_charts.py`, `country_albums.py`, `music_video_charts.py`, `genre_charts.py`, `genre_album_charts.py`) sont des outils manuels legacy — même CSV, mêmes en-têtes.
- Tous les collecteurs reçoivent le même `--scraped-at` (heure locale, sans timezone). **Un collecteur qui échoue = run entier abandonné** (pas d'export/images/upload).
- Les collecteurs combinés tolèrent des échecs par storefront/genre (chart manquant ≠ fausse data) mais **abandonnent si >5 % d'échecs** (`MAX_FAILURE_PCT`) pour ne pas publier une journée partielle.
- 401 en pool : `TokenManager` (core/token.py) fait UN refresh coordonné, les workers passent les headers **par requête**. `400` = chart indisponible ; fallback per-type si l'appel combiné est rejeté.
- `previous_rank` = dernier snapshot du **jour distinct précédent** (jamais un rerun du même jour) — `load_previous_ranks`, fenêtre de lecture 30 j.
- `rewrite_for_snapshot` : idempotent par `scraped_at` ; skip si identique même jour ; log `[info] … may not have refreshed` si identique au jour précédent (signal faible, on écrit quand même).
- Export (`scripts/export_apple_music.py`) : `applemusic.json` (petit, + section `last_charted` précalculée sur TOUT l'historique) ; `applemusic_history.json` **fenêtré** `APPLE_MUSIC_HISTORY_DAYS` (30 j), jours passés réduits à leur dernier snapshot, compact (≈122 Mo vs 3 Go avant). L'historique complet reste dans les CSV + R2 (`apple-music/snapshots/{key}.json`, `history-by-song/`).
- API frontend (tsm-frontend) : `/api/apple-music-last-charted` lit `applemusic.json.last_charted` ; `/api/apple-music/song/{id}/history` lit l'objet R2 `history-by-song/{slug}--{sha}.json` (nommage à répliquer exactement depuis `upload_ap_r2.py`) ; le gros history JSON n'est plus qu'un fallback.
- Rétention : `python scripts/prune_apple_music_snapshots.py` (dry-run ; `--apply` réduit les jours passés à leur dernier snapshot, lignes retirées archivées en gzip dans `_pruned_archive/`). Appliqué 2026-07-18 (1 139 → 415 Mo) ; chaque snapshot horodaté a aussi son JSON immuable dans R2 `apple-music/snapshots/` (648/648 vérifiés).
- `--no-post` est un flag mort : ce pipeline ne poste rien sur X. Pas de `posted.lock` ici.

## Règles data spécifiques Apple Music (non négociables)

- Jamais de fausse data : valeur absente/ambiguë → bloquer/loguer, pas publier.
- Pas d'historique complet depuis l'origine → une chanson déjà sortie qui apparaît n'est **pas** `NEW` ; l'API taggue `NEW` seulement si release_date ≤ 21 j du chart (`_NEW_RELEASE_WINDOW_DAYS`), sinon `RE`.
- `apple_music_id` > titre pour tout matching ; le fallback titre normalisé n'existe que pour les vieilles lignes sans ID. Ne jamais élargir en « même titre = même chanson ».
- Frontend : rang inchangé = cellule vide (pas de « = ») — décision 2026-07-18.

## Pièges connus avant de coder

- Imports `from core...` (pas `collectors.apple_music.core...`) : résolus via le PYTHONPATH injecté par le runner ; lancer un collecteur seul depuis la racine du repo fonctionne, les tests en module non.
- `TSM_DATA_DATE` / `DB_DIR` évalués **à l'import** de `core/config.py`. Pour tester sans polluer les données réelles : `TSM_DATA_DATE=2020-01-01` (dossier sandbox à supprimer après). **Ne jamais lancer un collecteur avec `--countries` subset sur la date du jour réelle** : ça écrit un snapshot partiel que l'export prendrait pour le « current ».
- Un changement de schéma CSV → vérifier `export_apple_music.py`, `upload_ap_r2.py` ET les deux scripts image.
- pytest n'est pas installé dans le Python système ; `collectors/apple_music/tests/test_http.py` existe mais ne tourne pas en l'état.
- Storefronts : découverte dynamique (~170) par défaut ; `COUNTRIES` de config.py n'est qu'un fallback.

## Commandes de travail

```powershell
# Test sandbox (ne touche pas aux données réelles)
$env:TSM_DATA_DATE='2020-01-01'; $env:APPLE_MUSIC_SKIP_EXPORT='1'; $env:PYTHONPATH="$PWD;$PWD\collectors\apple_music"
python collectors/apple_music/country_all.py --countries fr us --scraped-at 2020-01-01T12:00:00
# puis: Remove-Item -Recurse -Force 'snapshots\apple_music_charts\2020'

# Export seul / upload à blanc / rétention
python scripts/export_apple_music.py
python scripts/upload_ap_r2.py --dry-run
python scripts/prune_apple_music_snapshots.py          # dry-run
```

## Maintenance (obligatoire)

Tout changement dans `collectors/apple_music/` (script, option, comportement, panne découverte) → mettre à jour dans la même session : ce skill, `docs/apple-music-script-context.md` et `REPO_CONTEXT.md` (section 4).
