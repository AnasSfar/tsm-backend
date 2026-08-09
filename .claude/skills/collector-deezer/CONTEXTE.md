# Contexte Collector Deezer

## Role

`collectors/deezer` collecte, via l'API publique Deezer (`api.deezer.com`,
sans auth, 50 req/5s par IP) :

- le chart global Deezer (`/chart/0/tracks`), filtre aux entrees Taylor
  Swift (`artist.id == 12246`) ;
- le classement "top tracks" propre a Taylor Swift (`/artist/12246/top`) ;
- le nombre de fans/albums Deezer de l'artiste (`/artist/12246`).

Le pipeline ne poste pas sur X et ne fait aucun commit/push git (seul
l'upload R2 distribue la donnee), comme Apple Music.

Ajoute le 2026-08-09 (voir aussi `collector-billboard/CONTEXTE.md` pour son
role dans le scoring TayBoard).

## Entrypoint

```powershell
python .\collectors\deezer\run_deezer.py
```

Le runner lance, avec le meme `--scraped-at` : `global.py` -> `artist_top.py`
-> `artist_stats.py`. **Un collecteur en echec = run abandonne** (pas
d'export/upload). Sur succes : `scripts/export_deezer.py` puis
`scripts/upload_deezer_r2.py` (skip via `UPLOAD_TO_R2=0`).

## Scripts

- `global.py` : top 100 chart Deezer, garde uniquement les pistes de
  l'artiste `ARTIST_ID` (`core/config.py`). Ecrit
  `db/deezer_global_chart.csv`.
- `artist_top.py` : top tracks de l'artiste (deja filtre par construction,
  pas besoin de filtre supplementaire). Ecrit
  `db/deezer_artist_top_tracks.csv`. Le champ `deezer_popularity` est le
  score interne Deezer (`rank` de l'API), distinct de notre propre `rank`
  (position dans la liste).
- `artist_stats.py` : `nb_fan` / `nb_album`, une ligne/jour, append-only
  (pas de logique de classement, n'utilise pas `core/csv_utils.py`). Ecrit
  `db/deezer_artist_stats.csv`.
- `core/` : `config.py` (BASE_URL, ARTIST_ID, limites), `http.py`
  (session/retry generique, sans auth), `filters.py` (`clean_text`,
  `rank_key`), `csv_utils.py` (`load_previous_ranks`/`rewrite_for_snapshot`,
  meme logique idempotente-par-jour qu'Apple Music), `export.py`
  (`DEEZER_SKIP_EXPORT`).

## Donnees et sorties

CSV dans `db/` (snapshots quotidiens sous
`snapshots/deezer_charts/AAAA/MM/AAAA-MM-JJ/`, meme structure
qu'`apple_music_charts/`) :

- `deezer_global_chart.csv`
- `deezer_artist_top_tracks.csv`
- `deezer_artist_stats.csv`

Exports :

- `runtime/exports/web/site/data/deezer.json` (snapshot courant :
  `global_chart`, `ts_top_tracks`, `fan_stats`)
- `runtime/exports/web/site/data/deezer_history.json` (fenetre
  `DEEZER_HISTORY_DAYS`, defaut 30j, plus `fan_stats_series` = serie
  temporelle brute non fenetree)
- objets R2 `deezer/snapshots/`, `deezer/db/`, `deezer/history-by-song/`

## Variables

- `DEEZER_TIMEOUT`, `DEEZER_RETRY_TOTAL`, `DEEZER_RETRY_BACKOFF`
- `DEEZER_CHART_LIMIT` (defaut 100), `DEEZER_ARTIST_TOP_LIMIT` (defaut 50)
- `DEEZER_SKIP_EXPORT` : mis a `1` par le runner pendant les sous-scripts
- `UPLOAD_TO_R2=0` : skip upload R2
- `DEEZER_HISTORY_DAYS` (export, defaut 30)

## Regles data

- Donnee absente : `previous_rank` reste vide, jamais de NEW invente (pas de
  `release_date` disponible sur les items de chart Deezer, contrairement a
  Apple Music — voir la legon `apple-music/CONTEXTE.md` sur les faux NEW).
- `previous_rank` vient du dernier snapshot du jour precedent uniquement
  (jamais d'un rerun du meme jour) — meme regle qu'Apple Music.
- **CONFIRME 2026-08-09 (Anas) : le "chart global" Deezer est en realite le
  chart France**, pas un chart mondial (`/chart/0/tracks` n'a pas de
  parametre pays explicite ; geolocalise par l'IP source de la requete).
  Decision : renommage complet (fichier `global.py` -> `france.py`, CSV
  `deezer_global_chart.csv` -> `deezer_france_chart.csv`, constantes
  `DEEZER_GLOBAL_*` -> `DEEZER_FRANCE_*`, cle JSON `global_chart` ->
  `france_chart`, libelles UI "Global Chart" -> "France Chart") plutot
  qu'un simple correctif de libelle — **mis en pause volontairement**,
  pas encore fait. Tout le code garde le nom "global" pour l'instant (avec
  commentaires TODO a chaque emplacement cle :
  `collectors/deezer/global.py`, `swift_top_100.py` pres de
  `DEEZER_GLOBAL_WEIGHT`). A reprendre avant d'accumuler trop d'historique
  sur le schema actuel (un seul jour de donnees existe au moment de cette
  note, pas encore deploye sur le VPS).

## Commandes utiles

Run complet :

```powershell
python .\collectors\deezer\run_deezer.py
```

Export/upload :

```powershell
python .\scripts\export_deezer.py
python .\scripts\upload_deezer_r2.py --dry-run
```

## Pieges

- Comme Apple Music, `.gitignore` exclut les `.csv` de `db/` — un clone frais
  (VPS, nouvelle machine) n'a aucun historique tant qu'on n'a pas copie
  `snapshots/deezer_charts/` (fenetre lue : `PREV_RANK_WINDOW_DAYS = 30` dans
  `core/csv_utils.py`).
- `artist_top.py` ne filtre pas par artiste (l'endpoint `/artist/{id}/top`
  est deja scope a Taylor) ; `global.py` doit imperativement filtrer par
  `artist.id`, sinon on collecte tout le top 100 Deezer.
- Deployment VPS : pas encore fait au moment de la creation de ce skill —
  voir `REPO_CONTEXT.md` section "Deploiement VPS OVH" pour le wrapper/
  crontab a ajouter manuellement (pas de commande depuis ce repo, acces SSH
  requis).
