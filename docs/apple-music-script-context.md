# Contexte pour auditer et améliorer le pipeline Apple Music TSM

Briefing pour une autre IA ou un dev qui doit améliorer le pipeline Apple Music sans casser les données TSM. Version condensée / opérationnelle : skill `.claude/skills/collector-apple-music/` (à charger avant tout travail sur ce collecteur).

Contexte d'exécution : la prod TSM tourne **en local sous Windows via le Planificateur de tâches** — pas via GitHub Actions (`.github/workflows/run-apple-music.yml` existe mais n'est pas la prod). Les commandes ci-dessous sont données en PowerShell quand une variable d'env est nécessaire.

## Objectif du pipeline

Collecter les apparitions de Taylor Swift dans plusieurs classements Apple Music, sauvegarder des snapshots CSV horodatés, exporter des JSON pour le frontend/API, générer des images de cards/snapshots, puis uploader vers Cloudflare R2.

## Points d'entrée

```powershell
python -m tsm collect apple-music          # entrée officielle (log dans snapshots/run_logs/)
python collectors/apple_music/run_apple_music.py   # équivalent direct
```

Launcher planificateur : `run_apple_music.bat`. La CLI est définie dans `tsm/cli.py` (`collect_apple_music`, passthrough des options).

Options :

```powershell
python -m tsm collect apple-music --no-images     # saute cards + snapshots images
python -m tsm collect apple-music --force-images  # régénère les cards
$env:UPLOAD_TO_R2='0'; python -m tsm collect apple-music            # sans upload R2
$env:APPLE_MUSIC_COUNTRIES='fr,us'; python -m tsm collect apple-music --no-images  # test rapide
```

Attention : `--no-post` est **un flag mort** — parsé dans `run_apple_music.py` mais jamais lu (il n'existe que pour compat avec la CLI `tsm`). Le pipeline Apple Music ne poste rien sur X.

## Règles importantes de données

Les données TSM doivent rester exactes. Ne pas inventer, estimer ou lisser des positions. Si une valeur est absente ou ambiguë, bloquer/loguer clairement plutôt que publier une donnée fausse. (Règles complètes : skill `data-rules`.)

Règle Apple Music spécifique : comme l'historique Apple Music complet n'existe pas depuis le début, ne pas traiter automatiquement une chanson déjà sortie comme `NEW`. Les vraies nouveautés sorties après le début du scraping peuvent être `NEW` ; les anciens retours doivent plutôt être des reentries (`RE`) côté affichage/export.

Ne pas ajouter de fallback large du type « si c'est pareil alors zéro changement » ou « même titre = même chanson ». Les collisions de versions sont réelles : les `apple_music_id` doivent être préférés aux titres quand ils existent (c'est déjà le cas : `load_previous_ranks` par ID d'abord, fallback nom normalisé via `rank_key` pour les vieilles lignes sans ID).

## Architecture actuelle

```text
collectors/apple_music/
  run_apple_music.py              orchestrateur
  global.py                       top 100 global (playlist publique)
  ts_page.py                      top songs page artiste (storefront positionnel)
  country_all.py                  RUNNER: songs+albums+vidéos par pays en 1 requête combinée
  genre_all.py                    RUNNER: songs+albums par (pays, genre) en 1 requête combinée
  country_charts.py               legacy per-type (manuel)
  country_albums.py               legacy per-type (manuel)
  genre_charts.py                 legacy per-type (manuel)
  genre_album_charts.py           legacy per-type (manuel)
  music_video_charts.py           legacy per-type (manuel)
  top_music_videos.py             top vidéos page artiste — PAS dans le runner
  global_albums.py                legacy, PAS dans le runner (pas de vrai global albums via ce endpoint)
  generate_country_card_images.py cards PNG par pays
  generate_snapshot_images.py     PNG snapshots (Global, US, US Pop/Country/Alternative)
  core/
    config.py       env, ARTIST_ID="159260351" (string), DEFAULT_STOREFRONT="fr", GENRES (8), COUNTRIES fallback (~90)
    csv_utils.py    read_csv_rows (fenêtre optionnelle), rewrite_for_snapshot, load_previous_ranks (vs jour précédent)
    export.py       maybe_run_export (respecte APPLE_MUSIC_SKIP_EXPORT)
    filters.py      is_taylor_swift_song, clean_text, rank_key, build_artwork_url
    http.py         session requests avec timeout + retry
    models.py       dataclass SongEntry (peu utilisé)
    r2.py           client R2 + upload si hash SHA-256 changé
    storefronts.py  découverte dynamique des storefronts
    token.py        extraction/cache du token MusicKit + TokenManager (refresh coordonné)
  tests/
    test_http.py    seul test existant (pytest non installé dans le Python système)
  tools/json/
    apple_music_token.json        cache du token (gitignoré ou régénérable)
```

`run_apple_music.py` exécute ces scripts dans cet ordre, chacun en sous-process avec `--scraped-at` commun :

1. `global.py`
2. `ts_page.py`
3. `country_all.py` — `types=songs,albums,music-videos` par storefront (÷3 requêtes vs legacy) ; écrit les 3 CSV pays ; fallback per-type si un storefront rejette l'appel combiné (400) ; abort si >5 % de storefronts en échec (`MAX_FAILURE_PCT`)
4. `genre_all.py` — `types=songs,albums&genre={id}` par (storefront, genre) (÷2 requêtes) ; écrit les 2 CSV genres ; mêmes règles

Les scripts per-type restent utilisables à la main et produisent les mêmes CSV/en-têtes (validé le 2026-07-18).

Puis, **seulement si les 7 ont réussi** :

1. `scripts/export_apple_music.py`
2. `generate_country_card_images.py` (`chart_date --min-countries 1`, `--force` si `--force-images`) — sauf `--no-images`
3. `generate_snapshot_images.py` (`--date chart_date`) — sauf `--no-images`
4. `scripts/upload_ap_r2.py` — sauf `UPLOAD_TO_R2=0|false|no`

### Comportement d'échec (important pour l'audit)

- Un seul collecteur en non-zéro → le runner liste les échecs et `sys.exit(1)` : **pas d'export, pas d'images, pas d'upload**.
- Export échoué → images et upload sautés. Images échouées → upload sauté.
- Conséquence : un seul pays/genre qui casse un collecteur bloque toute la publication du jour. C'est le point central à garder en tête pour la gestion d'erreurs.

Pendant le run complet, `APPLE_MUSIC_SKIP_EXPORT=1` est forcé pour empêcher chaque sous-script de relancer l'export individuellement (`core/export.py::maybe_run_export`).

### `scraped_at`

Généré une fois par le runner : `datetime.now().strftime("%Y-%m-%dT%H:%M:%S")` — **heure locale, sans timezone** — et passé à tous les collecteurs pour unifier le timestamp du run. L'idempotence de `rewrite_for_snapshot` et la sélection du « snapshot précédent » de `load_previous_ranks` sont clés par ce champ (comparaison lexicographique). Lancé à la main sans `--scraped-at`, chaque collecteur fabrique le sien (`{date}T{HH:MM:SS}`).

### `previous_rank` (depuis 2026-07-18)

`load_previous_ranks` prend le dernier snapshot du **jour distinct précédent** (jamais un rerun du même jour) : les flèches ▲/▼ signifient toujours « vs hier », quel que soit le nombre de runs du scheduler dans la journée (~6/jour). Fenêtre de lecture : 30 derniers jours de CSV quotidiens (`PREV_RANK_WINDOW_DAYS`). `rewrite_for_snapshot` logue `[info] … may not have refreshed` quand un snapshot est identique au dernier du jour précédent (signal faible — les sous-ensembles filtrés TS peuvent se répéter légitimement — donc on écrit quand même).

## Chemins de données

Le chemin du jour vient de `collectors.spotify.core.data_paths.apple_music_charts_dir`. `RUN_DATE`/`DB_DIR` sont évalués **à l'import** de `core/config.py` (`TSM_DATA_DATE` sinon date du jour) — piège classique pour les tests/monkeypatching.

Les nouveaux CSV sont écrits dans :

```text
snapshots/apple_music_charts/YYYY/MM/YYYY-MM-DD/*.csv
```

Le code lit aussi les anciennes sources pour conserver l'historique :

```text
db/*.csv
data/_archive/original/db/*.csv          (ARCHIVE_DB_DIR)
snapshots/apple_music_charts/YYYY/MM/YYYY-MM-DD/*.csv   (via apple_music_daily_csv_paths)
```

Attention : l'export/upload gardent des constantes legacy `DB_DIR = ROOT / "db"`, mais leurs fonctions de lecture ajoutent ensuite les snapshots quotidiens.

Sorties images (non uploadées par `upload_ap_r2.py`) :

```text
snapshots/apple_music_charts/.../country_cards/*.png + cards_index.json
snapshots/apple_music_charts/.../snapshot_images/*.png
```

## CSV produits

```text
apple_music_global.csv
apple_music_ts_top_songs.csv
apple_music_country_charts.csv
apple_music_country_albums.csv
apple_music_genre_charts.csv
apple_music_genre_album_charts.csv
apple_music_music_video_charts.csv
apple_music_ts_top_videos.csv        (top_music_videos.py — pas produit par le runner actuel)
```

Schémas fréquents :

- champs communs : `date`, `scraped_at`, `rank`, `previous_rank`, `apple_music_id`, `image_url`, `url`, `artist_name`
- chansons : `song_name`, parfois `album_name`, `duration_ms`, `release_date`, `isrc`, `content_rating`, `genre_names`
- albums : `album_name`, `release_date`, `genre_names`
- pays : `country`, `chart_type`
- genres : `country`, `genre_id`, `genre_name`
- page artiste : `storefront`

`rewrite_for_snapshot` (csv_utils.py) : idempotent par `scraped_at` ; compare le nouveau snapshot au précédent en ignorant `scraped_at`/`date`/`previous_rank` et **saute l'écriture uniquement si identique ET même jour** ; sinon écrit dans le CSV quotidien du jour (lignes du jour seulement). L'export peut ensuite « miroiter » le current vers le dernier timestamp pour éviter une API partiellement vide.

## API Apple Music utilisée

Endpoints publics MusicKit, base catalog :

```text
https://amp-api-edge.music.apple.com/v1/catalog/{storefront}/...
```

- charts pays chansons : `/charts?types=songs&limit={CHART_LIMIT}`
- charts pays albums : `/charts?types=albums&limit={CHART_LIMIT}`
- charts genres chansons : `/charts?types=songs&genre={genre_id}&limit={CHART_LIMIT}`
- charts genres albums : `/charts?types=albums&genre={genre_id}&limit={CHART_LIMIT}`
- music videos : `/charts?types=music-videos&limit={CHART_LIMIT}`
- top songs artiste : `/artists/{ARTIST_ID}/view/top-songs?limit=100&offset={offset}` (paginé)
- top music videos artiste : `/artists/{ARTIST_ID}/view/top-music-videos?limit={CHART_LIMIT}`
- global : `/playlists/{playlist_id}/tracks?limit=100` (playlist `pl.d25f5d1181894928af76c85c967f8f31`, essai storefront `fr` puis `us`, résultat accepté si ≥ 10 items)

Découverte des storefronts (hors base catalog) : `https://amp-api-edge.music.apple.com/v1/storefronts?limit=200`. Par défaut les collecteurs pays interrogent **tous** les storefronts découverts (~170+) ; la liste `COUNTRIES` de `config.py` (~90 pays) n'est utilisée que si `APPLE_MUSIC_COUNTRIES` est défini ou si la découverte échoue.

Gestion des statuts dans les collecteurs concurrents : `400` → chart considéré indisponible (liste vide, non bloquant) ; `401` → `RuntimeError` (voir points fragiles) ; autres → `raise_for_status` (avec retry HTTP en amont).

### Token MusicKit

Extrait par `core/token.py` en deux temps : regex sur le HTML de `https://music.apple.com/fr/new`, puis fallback sur l'asset `/assets/index-*.js` référencé par la page. Si Apple change son bundling, c'est le premier point de rupture. Cache :

```text
collectors/apple_music/tools/json/apple_music_token.json
```

`core/http.py` : session `requests` avec timeout par défaut et retry (GET) sur `429, 500, 502, 503, 504`.

## Configuration env

```text
TSM_DATA_DATE              date des snapshots, sinon date du jour (évalué à l'import de config.py)
APPLE_MUSIC_COUNTRIES      override pays, ex: fr,us,gb (sinon: tous les storefronts découverts)
APPLE_MUSIC_CHART_LIMIT    profondeur charts, défaut 200
APPLE_MUSIC_WORKERS        concurrence ThreadPoolExecutor, défaut 12 (1 = mode séquentiel)
APPLE_MUSIC_TIMEOUT        timeout HTTP, défaut 20
APPLE_MUSIC_RETRY_TOTAL    retries HTTP, défaut 3
APPLE_MUSIC_RETRY_BACKOFF  backoff, défaut 1.0
APPLE_MUSIC_SKIP_EXPORT    forcé à 1 par le runner (sinon chaque collecteur relance l'export)
APPLE_MUSIC_HISTORY_DAYS   fenêtre de l'history JSON exporté, défaut 30
UPLOAD_TO_R2               0/false/no pour skipper l'upload
R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
R2_BUCKET                  défaut taylor-data
```

Genres suivis (`config.GENRES`) : Pop (14), Country (6), Hip-Hop/Rap (18), Rock (21), Singer/Songwriter (10), Alternative (20), R&B/Soul (15), Dance (17). Volume d'appels typique d'un run : ~170 storefronts × 5 types de charts pays/vidéos + 8 genres × 2 types × ~170 pays — la config workers/retry/timeout n'est pas décorative.

## Export JSON

`scripts/export_apple_music.py` lit les CSV et écrit :

```text
runtime/exports/web/site/data/applemusic.json          (~6 Mo, indent 2)
runtime/exports/web/site/data/applemusic_history.json  (~122 Mo, compact)
```

Compat : lit aussi `website/site/data/applemusic.json` comme source précédente pour backfiller `previous_rank` (utile en CI ou sur checkout frais).

Sections `applemusic.json` : `dates` (TOUTES les dates, y compris hors fenêtre — sert à la résolution des snapshots R2), `last_charted` (précalculé sur tout l'historique : dernier jour où chaque pays a chargé), `global_chart`, `ts_top_songs`, `ts_top_videos`, `country_charts`, `country_album_charts`, `genre_album_charts`, `music_video_charts`, `genre_charts`.

`applemusic_history.json` (depuis 2026-07-18) est **fenêtré et compacté** — il faisait 3 Go :
- fenêtre `APPLE_MUSIC_HISTORY_DAYS` (défaut 30 j) ;
- les jours passés sont réduits à leur **dernier** snapshot (le scheduler scrape ~6×/jour, le jour publié = dernier snapshot) ; aujourd'hui garde tous ses reruns ;
- JSON compact (`separators`), `dates` = clés réellement présentes.

L'historique complet reste servi par : les CSV locaux, les snapshots par date R2 (`apple-music/snapshots/{key}.json`, immuables) et les objets `history-by-song/` (par chanson, incluent désormais les vidéos). Côté API (tsm-frontend), `/api/apple-music-last-charted` lit `applemusic.json.last_charted` et `/api/apple-music/song/{id}/history` lit l'objet `history-by-song/{slug}--{sha1[:10]}.json` (nommage identique à `upload_ap_r2.py`) — le gros history JSON n'est plus qu'un fallback.

## Upload R2

`scripts/upload_ap_r2.py` (`--bucket`, `--prefix`, `--dry-run`) upload :

- `data/applemusic.json`, `data/applemusic_history.json` (fenêtré, ~122 Mo)
- snapshots par date/timestamp sous `apple-music/snapshots/{snapshot_key}.json` (seules les clés de la fenêtre sont re-poussées ; les anciennes restent en place, immuables)
- CSV quotidiens les plus récents sous `apple-music/db/{filename}`
- historiques par chanson sous `apple-music/history-by-song/{slug}--{sha1[:10]}.json` — sources : country, genre, global, ts_top_songs et (depuis 2026-07-18) music_video_charts

Compare un hash SHA-256 stocké en metadata R2 pour éviter les uploads inutiles (`core/r2.py`). Les PNG (cards, snapshots) ne sont **pas** uploadés par ce script.

### Rétention locale

`scripts/prune_apple_music_snapshots.py` (dry-run par défaut, `--apply`, `--since`, `--no-archive`) : pour chaque jour passé, ne garde que le dernier snapshot de chaque CSV quotidien ; les lignes retirées sont archivées en gzip dans `snapshots/apple_music_charts/_pruned_archive/` (sauf `--no-archive`). Appliqué le 2026-07-18 : 1 139 → 415 Mo. Note : le premier prune (version pré-archivage) n'a pas d'archive locale, mais les 648 snapshots horodatés ont été vérifiés présents dans R2 (`apple-music/snapshots/`) — zéro manquant.

## Tests existants

```powershell
python -m pytest collectors/apple_music/tests/
```

Couverture actuelle : uniquement `test_http.py` (timeout, headers, retry). Manquent notamment : extraction/cache token, `rewrite_for_snapshot`, `load_previous_ranks`, `is_taylor_swift_song` (artiste/relations), export JSON + backfill `previous_rank`, gestion 401 en concurrence, snapshots identiques.

## Points fragiles / pistes d'amélioration

État au 2026-07-18 (✔ = traité ce jour-là) :

1. **Imports fragiles** : les collecteurs font `from core...` au lieu de `from collectors.apple_music.core...`. Marche via le `PYTHONPATH` injecté par le runner (`child_env()`), mais complique tests et exécution en module. (Lié : `TSM_DATA_DATE` évalué à l'import.)
2. ✔ **Duplication** : le runner passe par `country_all.py`/`genre_all.py` (requêtes combinées). Les 5 scripts per-type restent en legacy manuel — une correction de parsing doit être portée des deux côtés tant qu'ils existent.
3. ✔ **Refresh token en pool** : `TokenManager` (core/token.py) fait un refresh coordonné ; les workers passent les headers par requête et réessaient une fois après refresh.
4. ✔ **Erreurs réseau** : les collecteurs combinés loguent et skippent le storefront/genre en échec (`RequestException`), mais abandonnent au-delà de 5 % d'échecs (`MAX_FAILURE_PCT`) pour ne pas publier une journée partielle.
5. **`global.py` fragile par design** : playlist id unique essayé sur `fr` puis `us`, seuil arbitraire ≥ 10 items. Vérifier si c'est encore la meilleure source.
6. **`top_music_videos.py` orphelin** : pas lancé par le runner, alors que l'export lit `apple_music_ts_top_videos.csv` (section `ts_top_videos` alimentée par le legacy uniquement). À trancher : l'ajouter au runner ou le supprimer.
7. **`--no-post` mort** dans `run_apple_music.py` (jamais lu).
8. **`previous_rank`** : ID d'abord, fallback titre normalisé — bon, mais les exports ont encore des backfills par nom ; attention aux collisions de versions.
9. ✔ **Performances de lecture** : `load_previous_ranks` fenêtré à 30 j ; export fenêtré/collapsé. Reste : l'export relit toujours tous les CSV pour `dates`/`last_charted` (~4 M lignes) — lancer `prune_apple_music_snapshots.py --apply` réduira ça d'environ ×5.
10. **Logs non structurés** : un résumé final par collecteur (pays/genres en erreur) ou une sortie JSONL aiderait le diagnostic. Les combinés loguent déjà les storefronts/genres skippés + un seuil d'échec.
11. **Schémas CSV non centralisés** : chaque collecteur définit son `FIELDNAMES` ; ajouter un champ = toucher plusieurs fichiers + l'export.
12. **`is_taylor_swift_song` pour les albums** : nom trompeur mais logique correcte (artistId, artistName, relationships).
13. **`scraped_at` heure locale sans timezone** : clés d'idempotence et tri lexicographique sensibles au DST / changement de machine.
14. **Scripts image couplés aux schémas** : tout changement de schéma CSV/JSON doit être testé contre `generate_country_card_images.py` et `generate_snapshot_images.py` (charger le skill `image-gen` avant de les toucher).
15. **Snapshot partiel = piège** : lancer un collecteur avec `--countries` subset sur la date réelle écrit un snapshot partiel que l'export prendrait pour le « current ». Toujours tester avec `TSM_DATA_DATE` sandbox (voir commandes).

## Commandes utiles pour auditer

```powershell
# Test SANDBOX (obligatoire pour un subset de pays : ne pollue pas les données réelles)
$env:TSM_DATA_DATE='2020-01-01'; $env:APPLE_MUSIC_SKIP_EXPORT='1'; $env:PYTHONPATH="$PWD;$PWD\collectors\apple_music"
python collectors/apple_music/country_all.py --countries fr us --scraped-at 2020-01-01T12:00:00
python collectors/apple_music/genre_all.py --countries fr --scraped-at 2020-01-01T12:00:00
# nettoyage : Remove-Item -Recurse -Force 'snapshots\apple_music_charts\2020'

# Export seul / upload à blanc / rétention (dry-run)
python scripts/export_apple_music.py
python scripts/upload_ap_r2.py --dry-run
python scripts/prune_apple_music_snapshots.py
```

pytest n'est pas installé dans le Python système ; valider par `python -m py_compile` + run sandbox.

NB : lancer un collecteur seul hors runner nécessite que les imports `from core...` résolvent — lancer depuis la racine du repo fonctionne car `config.py` insère le repo root dans `sys.path`, et le dossier du script est dans le path par défaut de Python.

## Ce qu'il faut demander à Claude

> Charge d'abord les skills `collector-apple-music` et `data-rules`. Audite le pipeline `collectors/apple_music` et propose un plan d'amélioration pragmatique, puis implémente les changements à faible risque en gardant l'exactitude des données. Ne change pas la sémantique des classements sans preuve. Ajoute ou adapte des tests pour les parties touchées. À la fin, mets à jour `REPO_CONTEXT.md`, le skill `collector-apple-music` et ce document (règle « living documentation » du CLAUDE.md).

Priorités suggérées :

1. Stabiliser les imports et tests.
2. Centraliser la logique commune des collecteurs sans gros rewrite.
3. Améliorer la gestion token/401 en concurrence (aujourd'hui : un 401 en pool = run entier perdu).
4. Ajouter des tests pour CSV snapshots, previous ranks, token extraction et export minimal.
5. Trancher le sort de `top_music_videos.py` (runner ou manuel documenté).
6. Améliorer les logs/résumés sans masquer les erreurs bloquantes.

Avant toute modification, vérifier le worktree : il peut déjà contenir des changements utilisateur.
