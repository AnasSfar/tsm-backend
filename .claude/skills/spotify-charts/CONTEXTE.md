# Contexte Spotify Charts TSM

Ce document cartographie `collectors/spotify/charts` et les scripts externes
qui pilotent ce dossier. Il sert de contexte local pour travailler vite sans
perdre les garanties de data exacte.

## Regles d'integrite TSM

Les stats Spotify Charts sont des donnees exactes. Ne jamais inventer,
approximer, lisser, simuler ou publier une valeur si elle n'est pas verifiee.

Si une valeur est absente, ambigue, dupliquee, stale ou incoherente, laisser le
run bloque/pending et loguer la raison exacte. Un fallback est acceptable
uniquement s'il lit une source exacte deja presente dans le repo: snapshot
verifie, ligne history de la meme date, ou mapping explicite.

Pour les doublons et alias, utiliser des mappings explicites
(`historical_track_ids`, metadata ciblee, correction documentee). Ne pas ajouter
de regles generales du type "same_total => zero streams" ou "same song_family =>
duplicate".

Avant posts, exports finaux ou rebuilds publics, verifier que les tracks actifs
concernes ont une donnee complete et exacte pour la date cible et la date de
comparaison requise.

## Racine du dossier

- `run_all_charts.py`: orchestrateur quotidien principal. Il gere collecte,
  disponibilite Spotify, WARP, posts, cards, R2, locks, catchup, backfill
  historique et git commit/push final.
- `spotify-charts/`: skill Codex local pour ce pipeline.
- `contexte.md`: ce fichier.
- `artists_global/`: chart artiste global Taylor Swift.
- `worldwide/`: collecteur central des charts par pays et snapshot mondial.
- `global/`, `fr/`, `us/`, `uk/`: pipelines regionaux historiques, tweets,
  filtres et images.

Les dossiers `history/` contiennent des donnees generees/locks. Ne pas les
traiter comme du code source ordinaire.

## Entrypoints

### Run quotidien

Commande principale:

```powershell
python .\collectors\spotify\charts\run_all_charts.py
```

Options utiles:

```powershell
python .\collectors\spotify\charts\run_all_charts.py --help
python .\collectors\spotify\charts\run_all_charts.py --no-post
python .\collectors\spotify\charts\run_all_charts.py --force
python .\collectors\spotify\charts\run_all_charts.py --post global fr cards
python .\collectors\spotify\charts\run_all_charts.py --watch-release
```

`run_all_charts.py` collecte par defaut:

- `artists_global/artist_global_daily.py`
- `worldwide/daily.py --force`

Les posts regionaux global/fr/us et les cards sont orchestres autour du
snapshot worldwide pour garder l'ordre de publication.

### Backfill historique

Commande simple via l'orchestrateur:

```powershell
python .\collectors\spotify\charts\run_all_charts.py --backfill --backfill-from 2020-07-01 --backfill-to 2020-07-31 --backfill-workers 2
```

`run_all_charts.py --backfill` delegue immediatement a:

```text
scripts/backfill_spotify_charts_history.py
```

Le bloc de backfill plus bas dans `run_all_charts.py`, apres le `return
subprocess.run(...)`, est inatteignable dans l'etat actuel.

Commande avancee:

```powershell
python .\scripts\backfill_spotify_charts_history.py --start 2020-07-01 --end 2020-07-31 --workers 2
```

Options avancees:

- `--workers`: nombre de process worker en parallele, limite par le nombre de
  `spotify_session*.json` disponibles. Chaque worker recoit un lot (chunk)
  contigu de dates et les traite dans un seul process long via
  `daily.py --dates-file ... --backfill-mode` (au lieu d'un process par date).
  Ca evite de repayer le cout fixe (imports, Playwright, bearer token,
  decouverte des regions) a chaque date.
- `--no-sync`: collecter les snapshots sans lancer les rebuild/sync finaux.
- `--limit`: limiter le nombre de dates traitees dans un run (applique avant
  le decoupage en chunks).
- `--sleep`: pause entre le lancement de chaque chunk worker (utile pour
  etaler les refresh Playwright/WARP simultanes au demarrage).
- `--refetch-done`: refetch meme les dates marquees done.
- `--no-force`: ne pas passer `--force` au collecteur.
- `--state`: chemin du JSON de reprise.

Apres chaque worker, chaque date du chunk est verifiee individuellement via le
snapshot sur disque (succes/echec par date, meme si `daily.py` a plante en
cours de lot) — la reprise reste fine malgre le regroupement en chunks.
`daily.py` en mode multi-dates (`--dates`/`--dates-file`/`--backfill-from/-to`)
continue desormais sur les dates suivantes meme si une date echoue, au lieu
d'abandonner tout le lot a la premiere erreur.

**Piege confirme en prod (2026-07-22):** `_fetch_region` bouclait a l'infini
sur les 429 (le check `FETCH_MAX_ATTEMPTS` n'existait que pour les timeouts et
erreurs generiques, jamais pour la branche 429). Une seule region bloquee
(observe sur `kr`, session/WARP instable) pouvait geler tout le process
indefiniment — c'est la panne "lecture infinie" deja notee ailleurs. Avec le
decoupage en chunks, ca gele en plus TOUTES les dates suivantes du meme
worker, pas juste une seule date comme avant. Corrige: la branche 429 respecte
maintenant `FETCH_MAX_ATTEMPTS`, et le wrapper de backfill fixe
`SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS=8` par defaut (le run quotidien live
garde `0` = illimite, coherent avec la regle "jamais sauter de la vraie
donnee"). Une date qui echoue ainsi en backfill reste marquee failed dans le
state JSON et peut etre retentee plus tard.

Etat par defaut:

```text
collectors/spotify/charts/worldwide/tools/json/run_all_backfill_done.json
```

Pipeline final du script de backfill, sauf `--no-sync`:

1. `scripts/sync_spotify_country_charts_from_worldwide.py`
2. `collectors/spotify/charts/worldwide/backfill_charts_history_track_ids.py --rebuild-ts-history`
3. `scripts/enrich_spotify_worldwide_snapshots.py --start ... --end ...`
4. `collectors/spotify/charts/worldwide/backfill_total_days.py`

## Optimisation sans perte de data

Le backfill est couteux parce que chaque date doit interroger les regions
Spotify exactes. `scripts/backfill_spotify_charts_history.py` regroupe deja
les dates en chunks (un process worker par chunk, pas par date — voir
"Backfill historique" plus haut). Les gains surs restants:

- Monter prudemment `SPOTIFY_WORLDWIDE_SEMAPHORE` pour fetch plus de regions en
  parallele au sein d'une meme date.
- Utiliser `--no-sync` sur plusieurs chunks, puis lancer une seule passe finale
  sans `--no-sync`.
- Ajouter de vraies sessions Spotify supplementaires si on veut plus de
  workers en parallele (`--workers` est plafonne par
  `len(spotify_session*.json)`). Copier le meme fichier de session ne compte
  pas comme une vraie session independante.

Exemple:

```powershell
$env:SPOTIFY_WORLDWIDE_SEMAPHORE="16"
python .\scripts\backfill_spotify_charts_history.py --start 2020-07-01 --end 2020-07-31 --workers 2 --no-sync
```

Si les logs affichent `429 - pause globale`, la concurrence est trop haute.
Redescendre vers `12` ou `16`.

## worldwide/

Fichier principal:

```text
collectors/spotify/charts/worldwide/daily.py
```

Role:

- decouvrir les regions Spotify via l'API overview et/ou Playwright;
- recuperer les charts regionaux `regional-<region>-daily/<date>`;
- filtrer les entrees Taylor Swift;
- resoudre les track IDs via la discographie, les exports web et le mapping
  manuel;
- calculer rank changes, stream changes, total_days, previous/week changes;
- ecrire le snapshot worldwide par date;
- ecrire les JSON regionaux `global/fr/us` utiles aux posts;
- generer certains posts regionaux/scored si demande;
- ecrire latest, locks, R2 et git uniquement hors `--backfill-mode`.

Commandes:

```powershell
python .\collectors\spotify\charts\worldwide\daily.py 2026-07-21 --no-post
python .\collectors\spotify\charts\worldwide\daily.py --dates 2020-07-01 2020-07-02 --no-post --backfill-mode
python .\collectors\spotify\charts\worldwide\daily.py --dates-file dates.txt --no-post --backfill-mode
```

Options importantes:

- date positionnelle ou `--date`
- `--dates`, `--dates-file`, `--backfill-from`, `--backfill-to`
- `--no-post`
- `--force`
- `--backfill-mode`
- `--post-priority-region global|fr|us`
- `--post-priority-global-new`
- `--post-multi-song-regions`
- `--post-multi-song-regions-only`

Variables d'environnement:

- `SPOTIFY_WORLDWIDE_SEMAPHORE`: concurrence regions, defaut `10`, backfill
  via run_all met `12`.
- `SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS`: tentatives max par region, `0` =
  infini.
- `SPOTIFY_SKIP_LATEST_FALLBACK_ON_404`: en backfill, evite de lire `latest`
  quand une date est absente.
- `SPOTIFY_CHARTS_SESSION_FILE`: session Spotify a utiliser.
- `SPOTIFY_CHARTS_SINGLE_SESSION`: force l'utilisation d'une seule session dans
  le process.
- `SPOTIFY_CHARTS_BEARER_CACHE_FILE`: cache bearer associe a une session.

## Snapshots worldwide

Le chemin canonique vient de `core.data_paths.spotify_chart_dir`:

```text
snapshots/spotify_charts/YYYY/MM/YYYY-MM-DD/worldwide/ts_worldwide_YYYY-MM-DD.json
```

Forme principale:

```json
{
  "date": "YYYY-MM-DD",
  "by_track": {
    "spotify_track_id": [
      {
        "country": "global",
        "country_name": "Global",
        "rank": 1,
        "previous_rank": null,
        "rank_change": null,
        "streams": 1234567,
        "peak_rank": 1,
        "total_days": 1,
        "is_new": true,
        "is_re_entry": false,
        "movement": "NEW",
        "stream_change": null,
        "stream_change_pct": null
      }
    ]
  }
}
```

Ne pas publier un snapshot si les donnees requises sont incompletes. Les
comparaisons journaliere/hebdo lisent les snapshots precedents quand ils
existent.

## global/, fr/, us/, uk/

Ces dossiers contiennent les pipelines regionaux historiques.

Fichiers principaux:

- `global/daily.py`
- `fr/daily.py`
- `us/daily.py`
- `uk/daily.py`

Role:

- lire ou produire les donnees regionales;
- lancer `tools/.../filter.py` quand la region doit scraper/filtrer elle-meme;
- generer `tweet.txt` puis `twitter_post.txt`;
- generer `chart_image.png`;
- poster sur Twitter si `--no-post` n'est pas actif;
- creer `posted.lock` apres succes;
- pour global/fr/us, creer aussi `updated.lock`.

Modes:

- Date positionnelle pour cibler une date.
- `--force` supprime/ignore certains locks et relance.
- `--no-post` execute sans publication.
- `--post-only` saute le filtre et utilise le JSON regional ecrit par
  worldwide.

Les chemins regionaux modernes passent par `spotify_chart_dir(region, date)`.
Certains scripts UK ou anciens chemins peuvent encore utiliser `history/`.

## artists_global/

Fichier principal:

```text
artists_global/artist_global_daily.py
```

Role:

- recuperer le chart artiste Spotify;
- trouver Taylor Swift;
- ecrire snapshots JSON/CSV;
- eventuellement generer image et poster.

Options:

- `--date YYYY-MM-DD|latest`
- `--no-wait`
- `--retry-seconds`
- `--no-csv`
- `--no-upload`
- `--no-post`
- `--force`
- `--force-post`
- `--no-warp`

Scripts image/cards:

- `artists_global/tools/scripts/generate_artist_chart_image.py`
- `artists_global/tools/scripts/generate_artist_worldwide_card.py`

La card artiste worldwide lit/ecrit:

- `artist_global_worldwide.json`
- `runtime/exports/web/site/data/charts_artists_global_worldwide.json`
- `artist_worldwide_card.png`
- `artist_worldwide_card_posted.lock`

## Cards et images

Worldwide cards:

```text
worldwide/tools/scripts/generate_card_images.py
```

Commande:

```powershell
python .\collectors\spotify\charts\worldwide\tools\scripts\generate_card_images.py 2026-07-21 --min-countries 1
```

Options:

- date positionnelle ou `--date`
- `--theme`
- `--min-countries`
- `--force`
- `--post`

Sorties:

```text
snapshots/spotify_charts/YYYY/MM/YYYY-MM-DD/worldwide/cards/
```

Fichiers typiques:

- PNG de summary/card;
- `cards_index.json`;
- `posted_cards.json`.

Priority Global NEW/RE cards:

```text
worldwide/tools/scripts/post_global_new_releases.py
```

Utilise le JSON global `ts_chart_YYYY-MM-DD.json`, la discographie et
`db/charts_history_global.csv` pour determiner les entrees prioritaires.

## Sessions, tokens, WARP

Sessions Spotify principales:

```text
global/tools/json/spotify_session.json
global/tools/json/spotify_session_2.json
```

Les backfills paralleles assignent une session par worker. Le nombre de workers
est donc plafonne par le nombre de fichiers `spotify_session*.json` trouves.

Bearer caches:

- `global/tools/json/bearer_cache.json`
- caches par session comme `bearer_cache_spotify_session_2.json`

`run_all_charts.py` peut connecter Cloudflare WARP quand necessaire, sauf
`--no-warp`. Ne pas masquer un probleme de token/session en ajoutant un fallback
silencieux.

## Locks

Locks courants:

- `posted.lock`: post Twitter effectue ou considere fait.
- `updated.lock`: donnees collectees/preparees pour une date.
- `r2_exported.lock`: export/upload R2 charts-only effectue par run_all.
- `exported_done.lock`: export R2 local du worldwide hors backfill.
- `artist_worldwide_card_posted.lock`: card artiste worldwide postee.
- `regional_posts/posted_<region>.lock`: post regional scored/priority.

Verifier les locks avant de relancer avec `--force`. Ne pas supprimer un lock
sans comprendre quelle sortie exacte il protege.

## Exports et DB

Exports web modernes:

```text
runtime/exports/web/site/data/
runtime/exports/web/site/history/
```

Legacy encore lu en fallback:

```text
website/site/data/
website/site/history/
```

DB/history liees:

- `db/charts_history_global.csv`
- `db/charts_history_fr.csv`
- `db/charts_history_us.csv`
- `db/charts_history_uk.csv`
- `db/streams_history.csv`
- `data/_archive/original/db/streams_history.csv`
- `db/discography/songs.json`
- `db/discography/albums/`
- `scripts/chart_title_to_track_id.json`

`best-day-since` lit `streams_history.csv`; si la date cible est absente, le
post est skippe plutot que devine.

## Rebuild/sync

Scripts importants hors dossier:

- `scripts/backfill_spotify_charts_history.py`
- `scripts/sync_spotify_country_charts_from_worldwide.py`
- `scripts/enrich_spotify_worldwide_snapshots.py`
- `scripts/r2.py`
- `scripts/export_for_web.py`

Scripts importants dans `worldwide/`:

- `backfill_charts_history_track_ids.py`
- `backfill_total_days.py`

Apres backfill massif, preferer:

1. collecter les snapshots avec `--no-sync`;
2. lancer une seule passe finale sans `--no-sync`;
3. verifier les CSV modifiees et les snapshots enrichis avant publication.

## Verification rapide

Checks non destructifs:

```powershell
python .\collectors\spotify\charts\run_all_charts.py --help
python .\scripts\backfill_spotify_charts_history.py --help
python .\collectors\spotify\charts\worldwide\daily.py --help
```

Pour une modification de logique:

- inspecter les chemins exacts via `core.data_paths`;
- tester le parse/CLI sans poster;
- utiliser `--dry-run` quand disponible;
- comparer les sorties modifiees contre snapshots/CSV source;
- ne pas lancer Twitter/R2/git push sans intention claire.

## Pieges connus

- `--backfill-workers` est limite par les sessions Spotify disponibles.
- `run_all_charts.py --backfill` ne lance pas le vieux bloc backfill situe sous
  le `return subprocess.run(...)`.
- `worldwide/daily.py --backfill-mode` coupe latest/R2/git/total_days write,
  mais recupere quand meme toutes les regions necessaires.
- `--force` peut refetch ou reposter selon le script; lire le script cible avant
  de l'utiliser.
- Les anciens dossiers `history/` et les nouveaux `snapshots/spotify_charts/`
  peuvent coexister; utiliser `core.data_paths` et les fallbacks existants.
- Les cards peuvent lire les snapshots existants sans recollecter.
- Les scripts de posting utilisent des locks pour eviter les doublons Twitter.
