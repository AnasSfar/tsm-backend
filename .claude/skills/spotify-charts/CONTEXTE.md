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
- `music_videos_global/`: Music Video Charts Global (top 50 videos quotidien).
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

**Retry sur crash de la collecte worldwide (depuis 2026-08-05) :** si le
runner `worldwide` (PHASE1) plante (code de retour non-zero — process crash,
exception non geree, `TokenExpired`), `run_all_charts.py` retente desormais
le subprocess `worldwide/daily.py` seul jusqu'a
`SPOTIFY_WORLDWIDE_COLLECT_MAX_ATTEMPTS` fois (defaut `3`, pause
`SPOTIFY_WORLDWIDE_COLLECT_RETRY_SECONDS` = `60s`) avant de marquer l'echec
comme definitif pour la date. Avant ce fix, un crash isole (WARP/session qui
lache en cours de fetch) rendait tout de suite `worldwide` fatal pour le run
(pas de post cards/global/us ce jour-la), sans aucune nouvelle tentative. Ne
couvre pas un blocage infini sans crash (ex: 429 illimite en live, voir
"Pieges confirmes" plus haut) — seulement un process qui se termine en echec.
`run_all_charts.py` collecte par defaut:

- `artists_global/artist_global_daily.py`
- `music_videos_global/daily.py --no-post --no-wait --allow-unavailable`
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

**Suite (2026-07-22):** le cap ci-dessus faisait echouer TOUTE la date des
qu'UNE region atteignait `FETCH_MAX_ATTEMPTS` (observe sur `co`/Colombie,
persistant sur plusieurs dates alors que les ~74 autres regions reussissaient).
Decision produit: quand une region atteint le cap (429, timeout, erreur, ou
4xx non-429/401/404), elle est desormais **omise** du snapshot au lieu de
faire echouer toute la date — pas de streams inventes, mais les autres
regions reelles sont gardees. La liste des regions omises est loguee
(`[WARN] N region(s) skipped...`) et persistee dans le JSON de sortie sous la
cle `skipped_regions` (absente si rien n'a ete saute), pour pouvoir cibler un
retry plus tard. Seul `TokenExpired` (401) reste fatal pour toute la date
(token casse globalement, pas un probleme d'une seule region).

Correlativement, le wrapper de backfill evalue maintenant chaque date de son
chunk sur l'existence reelle de son snapshot, pas sur le code de retour
agrege du chunk — avant ce fix, UNE date en echec dans un chunk faisait
marquer TOUTES les dates du chunk comme failed dans le state JSON, meme
celles reellement ecrites avec succes.

**Regression du fix 2026-07-22 (trouvee et re-corrigee le 2026-08-05):** la
branche 429 de `_fetch_region` avait perdu son check `FETCH_MAX_ATTEMPTS`
(seules les branches timeout/erreur generique le respectaient encore) —
probablement perdu lors d'un refactor ulterieur. En run quotidien live
(`SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS=0` = illimite par design), un 429
persistant sur `global` — desormais toujours fetche en priorite et bloquant
avant la Phase 2 (voir plus bas) — pouvait donc geler tout le run
indefiniment sans jamais fetcher/poster le reste. Recorrige: la branche 429
verifie de nouveau le cap avant de boucler. Le comportement par defaut du run
quotidien live reste inchange (cap `0` = illimite, donc un vrai blocage 429
prolonge peut toujours geler le run par design — c'est la regle "jamais
sauter de la vraie donnee"); seul un cap explicite (backfill, ou
`SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS` positionne a la main) beneficie
reellement de ce fix.

**Snapshots backfill sans metadata (song_name/image_url absents) — cause et
fix:** les snapshots `ts_worldwide_*.json` collectes ne contiennent que
rank/streams/movement ; c'est `scripts/enrich_spotify_worldwide_snapshots.py`
qui y ajoute `song_name`/`image_url`/`artist_name`/`album_name`/`spotify_url`
(via `historical_track_ids` de `songs.json` notamment). Si un backfill est
interrompu avant la passe de sync finale (429/hang, Ctrl+C), les dates deja
collectees restent avec des entrees "nues" — le frontend (`SongBlock.jsx`)
affiche alors une cover cassee et un titre vide pour ces dates-la. Reparer
apres coup: `python scripts\enrich_spotify_worldwide_snapshots.py --start
<D1> --end <D2>` sur la plage concernee (idempotent, `--dry-run` dispo).

En local/dev, le frontend lit en fallback directement le snapshot backend
(`_load_first_existing_worldwide_snapshot` dans `tsm-frontend/api/data/loader.py`)
quand la cle R2 datee est absente — donc reparer le fichier local suffit pour
voir le fix en dev (redemarrer le serveur API : `load_charts_worldwide` est
`@lru_cache`, une date deja consultee reste en cache memoire jusqu'au
restart). En prod, il faut en plus pousser vers R2 (bucket **`taylor-data`**,
prod — cf skill `tsm-map`) via `scripts/r2.py --charts-only
--skip-history-upload --skip-db-upload --skip-images-upload` (uploade
`charts_worldwide.json`, les snapshots worldwide par date vers
`history/charts_worldwide/{date}.json`, et les CSV `charts_history_*`; hash-check,
donc idempotent).

**Bug confirme et corrige (2026-08-05) : `--worldwide-snapshot-only` rendait
les rattrapages invisibles sur le site prod.** Ce flag zeroait aussi
`csv_mappings` dans `upload_static_data` (`scripts/r2.py`), donc
`charts_history_<region>.csv` n'etait jamais reuploade lors d'un rattrapage
explicite (single-date via `run_all_charts.py <date>`, ou backfill avec
`--backfill-upload-r2`). Or en prod (Vercel serverless, pas de checkout
backend local), `/api/charts?region=global|fr|us|uk` lit **exclusivement**
`data/charts_history_<region>.csv` sur R2 pour `available_dates` et les
`rows` — le fallback "snapshot worldwide local" (`load_spotify_chart_snapshot`)
ne fonctionne qu'en dev. Resultat: une date rattrapee restait invisible sur le
site indefiniment, meme apres collecte locale reussie et malgre
`r2_exported.lock` cree (observe le 2026-08-05: le 3 aout collecte a 11h37
restait absent de `thetsmuseum.app` jusqu'a correction). Corrige: les CSV
`charts_history_*` sont desormais toujours uploades, meme en
`--worldwide-snapshot-only` — seuls `charts_worldwide.json` et
`charts_worldwide_total_days.json` restent proteges (ce sont de vrais
pointeurs "latest" qu'un rattrapage sur une vieille date pourrait faire
regresser ; les CSV, eux, sont un historique append-only sans ce risque).

Pour ne plus avoir a faire ce enrich+upload a la main apres un backfill:
`scripts/backfill_spotify_charts_history.py --upload-r2` (ignore si
`--no-sync`) lance ce `r2.py --charts-only` juste apres les etapes de sync
existantes. Passthrough depuis l'entrypoint principal:
`run_all_charts.py --backfill ... --backfill-upload-r2`. Reste **off par
defaut** dans les deux cas — c'est une ecriture reseau/prod, opt-in
volontaire.

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

**Ordre de fetch — `global` toujours en premier, bloquant (depuis 2026-08-01) :**
la region `global` est desormais systematiquement dans le lot "Phase 1" (fetch
prioritaire), meme en `--no-post` (ex: appel `worldwide-regional-data` de
`run_all_charts.py` pour regenerer les JSON regionaux avant les cards). La
Phase 1 est `await`ee entierement (donc `global` est retente selon
`FETCH_MAX_ATTEMPTS`/`SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS` jusqu'a
confirmation ou skip apres cap) avant que la Phase 2 (fetch des ~74 autres
regions worldwide) ne demarre. Avant ce fix, en `--no-post`, `global` etait
mélangé dans le meme lot asyncio que toutes les autres regions — un `global`
lent/rate-limite ne bloquait rien et pouvait finir apres (ou etre skip en
meme temps que) les autres regions, sans garantie d'ordre. Regle produit:
ne jamais lancer le fetch des autres regions worldwide avant que `global`
soit confirme (ou explicitement skip apres le cap de tentatives).

**Retry sur chart pas encore propage (corrige le 2026-08-06) :** le cas 404
sur l'URL datee + `/latest` 200 mais pointant encore sur la veille (ex.
`global` 404 pour `2026-08-05`, `/latest` renvoie `2026-08-04`) ne retentait
jamais malgre le commentaire existant qui le laissait penser — seules les
branches 429/timeout/erreur generique du meme `_fetch_region` respectaient un
cap de tentatives. Un seul essai concluait donc a tort "pas de chart pour
cette date" en cas de simple decalage de propagation CDN Spotify, meme
observe en prod alors qu'une autre region avait deja bascule sur la nouvelle
date au meme moment (preuve que le chart existait deja cote Spotify). Corrige:
cette branche precise retente desormais jusqu'a
`SPOTIFY_WORLDWIDE_NOT_FOUND_RETRY_ATTEMPTS` fois (defaut `3`, pause
`SPOTIFY_WORLDWIDE_NOT_FOUND_RETRY_SECONDS` = `20s`) avant d'abandonner. Cap
volontairement independant de `FETCH_MAX_ATTEMPTS` (illimite par defaut en run
live) pour ne pas risquer un hang si la region ne publie vraiment pas ce
jour-la.

Variables d'environnement:

- `SPOTIFY_WORLDWIDE_SEMAPHORE`: concurrence regions, defaut `10`, backfill
  via run_all met `12`.
- `SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS`: tentatives max par region, `0` =
  infini.
- `SPOTIFY_SKIP_LATEST_FALLBACK_ON_404`: en backfill, evite de lire `latest`
  quand une date est absente. Defaut `daily.py` (run quotidien): off — sur un
  404 sur l'URL datee, on retente via `/latest` et on garde la donnee si
  `/latest` pointe deja sur `chart_date` (rattrape le decalage de propagation
  CDN cote Spotify entre `/latest` et l'URL datee explicite, observe le
  2026-07-30 sur `global`). Le wrapper de backfill force `1` explicitement
  (`run_all_charts.py`), donc le comportement backfill ne change pas meme si
  le defaut interne de `daily.py` change.
- `SPOTIFY_WORLDWIDE_NOT_FOUND_RETRY_ATTEMPTS` / `SPOTIFY_WORLDWIDE_NOT_FOUND_RETRY_SECONDS`:
  retry specifique au cas "URL datee 404, `/latest` 200 mais pointe encore sur
  la veille" (voir "Retry sur chart pas encore propage" plus bas), defaut `3`
  tentatives / `20s`. Toujours borne, meme en run quotidien live — contrairement
  a `FETCH_MAX_ATTEMPTS` (defaut `0` = illimite en live), pour eviter un hang si
  la region ne publie vraiment pas ce jour-la.
- `SPOTIFY_CHARTS_SESSION_FILE`: session Spotify a utiliser.
- `SPOTIFY_CHARTS_SINGLE_SESSION`: force l'utilisation d'une seule session dans
  le process.
- `SPOTIFY_CHARTS_BEARER_CACHE_FILE`: cache bearer associe a une session.
- `SPOTIFY_PRIORITY_CARD_POST_MAX_ATTEMPTS` / `SPOTIFY_PRIORITY_CARD_POST_RETRY_SECONDS`:
  retry de la card standalone RE/NEW (`_post_priority_global_new_card`),
  defaut `3` tentatives / `30s`.
- `SPOTIFY_WORLDWIDE_COLLECT_MAX_ATTEMPTS` / `SPOTIFY_WORLDWIDE_COLLECT_RETRY_SECONDS`
  (lus par `run_all_charts.py`, pas `daily.py`): retry du subprocess
  `worldwide/daily.py` complet quand il plante (code retour non-zero),
  defaut `3` tentatives / `60s`.
- `SPOTIFY_FIRST_SINGLE_REGION_POST_MAX_ATTEMPTS` / `SPOTIFY_FIRST_SINGLE_REGION_POST_RETRY_SECONDS`
  (lus par `generate_card_images.py`): retry du post standalone "first single
  region entry", defaut `3` tentatives / `30s`.
- `SPOTIFY_REGIONAL_POST_MAX_ATTEMPTS` / `SPOTIFY_REGIONAL_POST_RETRY_SECONDS`
  (lus par `run_all_charts.py`): retry de `global-post`/`fr-post`/`us-post`
  (`_verify_regional_posts`), defaut `3` tentatives / `30s`.

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

## music_videos_global/

Fichier principal:

```text
music_videos_global/daily.py
```

Role:

- recuperer Music Video Charts Global via l'API Spotify Charts;
- ecrire le top complet quand Spotify le fournit + une sous-liste
  `taylor_videos`;
- ecrire snapshots JSON/CSV sous `snapshots/spotify_charts/...`;
- ecrire le latest web `charts_music_videos_global.json`;
- creer `updated.lock`.

Options:

- date positionnelle ou `--date YYYY-MM-DD|latest`
- `--no-wait`
- `--retry-seconds`
- `--no-csv`
- `--no-post` (accepte pour run_all; aucun tweet automatique cable pour
  l'instant)
- `--allow-unavailable` (run_all uniquement: ecrit `pending.json` et laisse les
  autres charts continuer si Spotify ne publie pas encore cette chart via API)

Le slug interne Spotify etant nouveau, le script essaie explicitement plusieurs
candidats (`SPOTIFY_MUSIC_VIDEO_CHART_IDS` permet de les remplacer). Si aucun
ne marche, la collecte stricte reste en erreur; via run_all, elle reste
`pending` sans inventer de rows ni bloquer songs/artists.

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
- `first_single_region_posted.json` (voir "First single region entry" ci-dessous).

**First single region entry — postee standalone en priorite, hors thread
(depuis 2026-08-05) :** quand une chanson charte pour la toute premiere fois
(aucun pays avant, `has_prev_snapshot` requis) dans exactement UN pays
(`_is_first_single_region_entry` dans `generate_card_images.py`), une
chart_card dediee est generee (`{slug}_chart_card.png`, meme composant
`comp/chart_card.py` que la card Global RE/NEW). Avant ce fix, cette image
etait simplement inseree dans le gros thread multi-images `cards` (postee via
`post_image_thread`, ordre selon `_card_priority` parmi ~80 autres cards) —
donc noyee, pas vraiment "en priorite". Desormais elle est **retiree du
thread** et postee individuellement en tweet standalone
(`_post_first_single_region_standalone`, retry jusqu'a
`SPOTIFY_FIRST_SINGLE_REGION_POST_MAX_ATTEMPTS` fois, defaut `3`, pause
`SPOTIFY_FIRST_SINGLE_REGION_POST_RETRY_SECONDS` = `30s`), suivie par son
propre lock `first_single_region_posted.json` (independant de
`posted_cards.json`, cle `{slug}_chart_card`). L'image est generee que `--post`
soit passe ou non (comme les autres cards), seule la publication est
conditionnelle.

**Badge NEW vs RE (corrige le 2026-08-05) :** `_render_single_region_chart_card_html`
affichait toujours le badge `"RE"`, meme pour un vrai premier debut, car
`_is_first_single_region_entry` ne distingue pas NEW vs RE (elle sait juste
que la chanson n'etait dans aucun pays la veille). Corrige : nouveau helper
`_single_region_badge(entry)` qui lit `movement`/`is_re_entry` de l'entree
Spotify de ce pays (meme signal deja utilise par `_build_tweet` pour choisir
entre "re-entered" et "charted on Spotify") — badge `"RE"` seulement si
`movement == "RE"` ou `is_re_entry` est vrai, `"NEW"` sinon.

**Retry de la publication (depuis 2026-07-30) :** aucun chemin de post X
(single ou thread, `post_with_image`/`post_image_thread` dans
`collectors/spotify/core/twitter.py`) ne retente en interne — une erreur X
("something went wrong") fait echouer l'appel une seule fois. Pour l'etape
`cards` specifiquement, `run_all_charts.py` retente desormais l'appel
complet a `generate_card_images.py` jusqu'a `SPOTIFY_CARDS_POST_MAX_ATTEMPTS`
fois (defaut `3`, pause `SPOTIFY_CARDS_POST_RETRY_SECONDS` = `30s` entre
tentatives), et passe `--force` sur la derniere tentative (regenere les PNG
avant de reposter).

**Retry `global-post`/`fr-post`/`us-post` (depuis 2026-08-05) :** meme
`_verify_regional_posts` (le sous-appel `daily.py --post-only` par region)
n'avait aucun retry — confirme en prod (echec X, puis succes seulement apres
un rerun manuel/Task Scheduler). Safe a retenter tel quel: `posted.lock` n'est
ecrit qu'apres succes cote `global/fr/us daily.py` (jamais sur echec), donc
relancer `--post-only` sans `--force` ne peut pas double-poster. Retente
maintenant jusqu'a `SPOTIFY_REGIONAL_POST_MAX_ATTEMPTS` fois (defaut `3`,
pause `SPOTIFY_REGIONAL_POST_RETRY_SECONDS` = `30s`), memes args a chaque
tentative (pas de `--force` ajoute automatiquement — `--force` ici supprime le
lock et relance le pipeline complet, pas juste "regenere l'image"). Seul
`priority-global-highlights-worldwide` n'a toujours pas de retry — un echec y
reste fatal pour la date.

**Retry de la card standalone RE/NEW (depuis 2026-08-05) :** le thread Python
de fond `_post_priority_global_new_card` dans `worldwide/daily.py` (qui
appelle `post_global_new_releases.py <date> --post`, PAS `--post-worldwide`)
n'avait aucun retry — une erreur X transitoire faisait echouer l'appel une
seule fois, silencieusement (juste un `[WARN]` dans les logs). Comme
`generate_card_images.py` (etape `cards`, fil de tweets separe et sans
rapport, deja retry par ailleurs) rend lui aussi un chart_card highlight
via `comp/chart_card.py` pour toute "first single region entry" et le poste
dans son propre thread multi-images (voir `_is_first_single_region_entry` /
`to_post` dans `generate_card_images.py`), la chanson restait visible quelque
part sur le compte — donnant l'impression que "c'est dans le thread" — alors
que le VRAI tweet dedie standalone (`post_global_new_releases.py --post`,
cense passer en priorite, seul, avant le reste) ne partait jamais. Corrige :
`_post_priority_global_new_card` retente maintenant jusqu'a
`SPOTIFY_PRIORITY_CARD_POST_MAX_ATTEMPTS` fois (defaut `3`, pause
`SPOTIFY_PRIORITY_CARD_POST_RETRY_SECONDS` = `30s`), `--force` sur la
derniere tentative.

Priority Global NEW/RE cards:

```text
worldwide/tools/scripts/post_global_new_releases.py
```

Utilise le JSON global `ts_chart_YYYY-MM-DD.json`, la discographie et
`db/charts_history_global.csv` pour determiner les entrees prioritaires.

**Piege confirme (2026-07-30) :** la vraie card highlight NEW/RE du chart
Global (`post_global_new_releases.py <date> --post`, badge "RE" via
`comp/chart_card.py`) n'est declenchee automatiquement par `worldwide/daily.py`
qu'en thread de fond quand `--post-priority-global-new` est passe ET que la
region `global` vient d'etre fetchee. Ce flag n'etait jamais positionne dans
un run quotidien normal (`COLLECT_RUNNERS` dans `run_all_charts.py` ne
passait que `--force`), et le filet de secours ("catchup") plus bas dans
`run_all_charts.py` ne se declenche que si `not ran_collect` — jamais vrai
un jour normal ou la collecte tourne. Resultat : une reelle re-entree du
chart Global (ex. Blank Space le 2026-07-29) ne generait/postait jamais sa
card RE. Fix : `run_all_charts.py` ajoute maintenant `--post-priority-global-new`
aux args forwardees vers `worldwide/daily.py` des que `"cards" in post_parts`
(donc pas en `--no-post`, coherent avec le reste du posting `cards`). Ne pas
confondre avec la "priority
worldwide card" (`--post-worldwide`, fichier `worldwide_new_card_*.png`,
etape `priority-global-highlights-worldwide`) qui parle de nombre de pays et
tourne dans tous les cas — c'est une feature separee et independante.

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
2. lancer une seule passe finale sans `--no-sync` (et `--upload-r2` si la prod
   doit refleter ce backfill, sinon les dates restent invisibles hors
   fallback local dev tant que personne ne pousse R2 a la main);
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

## Highlights Charts Gallery

Depuis 2026-07-28, `run_all_charts.py` appelle en best-effort (jamais
bloquant) `scripts/generate_home_highlights.py --quiet` a la toute fin du run
(apres les commits git), seulement si `not args.dry_run and ran_collect`.
Regenere `cache/home_highlights.json` et `cache/version.json` sur R2 (lus par
`tsm-frontend/api`) — lit `db/charts_history_global.csv` directement (pas de
matching flou a dupliquer, track_id/movement deja resolus par le collector).

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
- Bug fixe le 2026-08-09 : `worldwide/daily.py::_iter_disco_tracks()`
  traitait `db/discography/songs.json` comme une liste de tracks plate
  (`yield from (x for x in data if isinstance(x, dict))`) alors que c'est une
  liste de sections avec `tracks: [...]` — donc ses entrees ne matchaient
  jamais `_get_track_id_from_item`/`_title_fields` et ne contribuaient rien a
  `build_track_lookup()`/`build_historical_track_id_lookup()`. `features.json`
  et `misc.json` n'etaient en plus jamais lus. Fix : boucle sur les 3 fichiers
  extras (`songs.json`, `features.json`, `misc.json`) en depliant chaque
  section comme le bloc albums le fait deja. Meme trou trouve et corrige le
  meme jour dans `scripts/r2.py::_iter_discography_tracks()` et
  `scripts/chartr2.py::iter_discography_tracks()` (utilises par le
  canonicalization R2 des snapshots worldwide et l'export per-track Global) —
  voir skill `spotify-streams` pour le detail complet de l'audit (14 fichiers
  au total avec le meme pattern de bug).
- Les cards peuvent lire les snapshots existants sans recollecter.
- Les scripts de posting utilisent des locks pour eviter les doublons Twitter.
