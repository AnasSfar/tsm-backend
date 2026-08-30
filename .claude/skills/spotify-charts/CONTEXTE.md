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

- `music_videos_global/daily.py --no-post --no-wait --allow-unavailable`
- `worldwide/daily.py --force`

Les posts regionaux global/fr/us et les cards sont orchestres autour du
snapshot worldwide pour garder l'ordre de publication.

**US poste des la Phase 1 (depuis 2026-08-17), sans attendre la fin de la
collecte worldwide.** `worldwide/daily.py` gere deja en interne
`--post-priority-region {global,fr,us}` ("poste cette region des que sa
collecte prioritaire est ecrite") mais `run_all_charts.py` ne le forwardait
jamais avant ce fix — seul `--post-priority-global-new` etait ajoute, donc
seul `global` beneficiait d'un post anticipe ; `fr`/`us` attendaient
systematiquement la fin complete de la collecte des ~75 regions (Phase 2)
avant que `_verify_regional_posts` ne les poste, en toute fin de run.
Corrige : `run_all_charts.py` ajoute maintenant `--post-priority-region us`
aux args forwardees vers `worldwide/daily.py` des que `"us" in post_parts`
(defaut : actif, `fr` est en pause via `_PAUSED_POST_PARTS` donc pas concerne
pour l'instant). Consequence : `us` rejoint desormais `global` dans la Phase 1
bloquante (fetch prioritaire avant les ~74 autres regions), et son post part
en tache de fond des que sa donnee est ecrite — plus besoin d'attendre la
Phase 2 entiere. `_verify_regional_posts` reste le filet de secours en fin de
run (retry si le post anticipe a echoue) ; protege par le meme `posted.lock`
que le post anticipe, donc jamais de double-post.

**`artists_global` n'est PAS orchestre par `run_all_charts.py` (depuis
2026-08-16).** Task Scheduler local a deux taches distinctes qui declenchent
toutes les deux a **15h** :

- `TSM Spotify Charts Watch Release` -> `run_all_charts.py --watch-release`
  (worldwide/global/fr/us/cards/music_videos, PAS artists_global).
- `TSM - Spotify Artists Global Daily` -> `artist_global_daily.py` seul, sans
  argument (`--date` par defaut `latest`, posting actif) — entierement
  autonome : collecte le chart artiste global, poste le chart de base
  (top5/top10/solo Taylor, voir plus bas), PUIS poste les charts artiste
  filtres (`female`/`starts_with_t`/`named_taylor`/`us_artist_chart`/
  `uk_artist_chart`, voir `artists_global/` plus bas).

**Historique de ce choix :** ces deux taches tournaient deja en parallele
avant meme que `artists_global` soit ajoute a `run_all_charts.py`
(`COLLECT_RUNNERS`), ce qui creait un vrai risque de double-fetch/double-post
sur le meme chart (protege seulement par le `posted.lock`, donc une race
TOCTOU restait possible). Corrige le 2026-08-16 en retirant `artists_global`
de `run_all_charts.py` entierement (plus dans `COLLECT_RUNNERS`,
`CHART_AVAILABILITY`, `_runner_done`, `_region_data_exists`, `--post` choices)
plutot que l'inverse — decision produit : la tache dediee reste la source
unique de verite pour tout ce qui est artiste. Consequence assumee : si cette
tache dediee echoue un jour (WARP, session), il n'y a plus de filet de
rattrapage cote `run_all_charts.py` pour `artists_global` — le rattrapage
doit se faire manuellement (`python artist_global_daily.py --date
<date-manquee> --force`).

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
- `--gaps-from-csv [REGION ...]`: calcule les dates pending depuis les dates
  ABSENTES de `db/charts_history_<region>.csv` (le store durable suivi par git,
  et ce que le site lit) au lieu de la presence d'un snapshot local — qui sur
  une machine donnee est souvent tres incomplet (les snapshots ne sont PAS
  commit, purge/desync frequent). Flag nu = `global` (Taylor charte tous les
  jours en global, donc "date absente de `charts_history_global.csv`" = "jour
  jamais collecte worldwide") ; plusieurs codes = union des trous
  (`--gaps-from-csv global us uk`). **A utiliser pour tout gros rattrapage
  historique** : sans lui, le wrapper re-collecte des centaines de dates deja
  presentes dans les CSV/R2. Une date encore dans `done_dates` du state mais
  toujours absente du CSV (collecte OK, sync qui a echoue) est quand meme
  skippee — passer `--refetch-done` pour la forcer.
- `--regions CODE ...` / `--exclude-regions CODE ...`: forwarde a
  `daily.py`. Fill cible region par region (ex. `--regions fr` sur ~2400 dates
  = 1 region x N dates au lieu de 68 x N). Sous filtre, `daily.py` relit le
  snapshot date existant et merge en retour les regions hors perimetre — donc
  jamais de perte, meme avec le `--force` que le wrapper passe par defaut (il
  ne ré-fetch alors QUE les regions ciblees). NE PAS lancer deux workers avec
  `--regions` sur des plages de dates qui se recouvrent : ils ecriraient le
  meme fichier snapshot date en concurrence (read-modify-write race). Le
  decoupage par chunks de dates du wrapper garantit deja des fichiers disjoints
  par worker tant qu'on ne force pas un recouvrement.
- **`--per-region-sweep [CODE ...]`** : boucle sequentielle region par region.
  Pour chaque region, UN run `daily.py --regions <une>` qui ne fetche que les
  dates absentes de SON `charts_history_<code>.csv` dans `[--start,--end]`. Une
  seule region par run => une requete par `--request-interval`, **jamais deux
  requetes concurrentes qui 429 en meme temps et declenchent GlobalPause** (le
  probleme quand on fait `--regions us gb` = 2 en parallele). Bare = tous les
  `charts_history_<cc>.csv` 2 lettres avec >= 60 lignes, historique le plus
  riche d'abord (une region riche en 2017-2019 a quasi surement encore un chart
  live). Checkpoint `state['swept_regions']` apres chaque region, reprenable.
  `uk` -> chart `gb`. `--include-discontinued-regions` pour forcer les 31
  gelees. C'est LA methode pour combler le trou worldwide par-pays 2020-2025.
- `--request-interval` (defaut 1.0) / `--concurrency` (defaut 6) /
  `--fetch-max-attempts` (defaut 8) / `--rate-limit-max` (defaut 30) : reglage
  du debit et de la robustesse. Detail + pieges -> section "Optimisation sans
  perte de data" plus bas. En bref : `--request-interval` est le vrai levier de
  vitesse (pacer global) ; `--concurrency` bas evite les 429 paralleles ;
  `--fetch-max-attempts` + `--rate-limit-max` bornent le hang / les pauses.
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

**Bug confirme et corrige (2026-08-16) : le rattrapage (`_collect_data_only_dates`,
appele quand Spotify a deja publie une date plus recente que la cible au
moment du run) ne collectait jamais `artists_global` pour les dates
sautees — seulement `worldwide`.** Observe concretement le 2026-08-12 :
`run_all_charts.py` a tourne en retard (execute le 13/08 pour la date du 12),
`worldwide`/`global`/`us` ont bien ete rattrapes en data-only, mais aucun
dossier `snapshots/spotify_charts/2026/08/2026-08-12/artists_global/` n'a
jamais existe. Or c'est precisement ce jour-la que le rang artiste Spotify de
Taylor Swift est passe de #4 a #3 (confirme en comparant les snapshots locaux
du 08-11 et du 08-13). Consequence : ce changement de rang n'a jamais eu de
donnee locale a comparer, ni pour le post "Top Artists"
(`generate_artist_chart_image.py`, garde-fou base sur `previous_rank`), ni
pour la card worldwide par pays — pas un bug du garde-fou lui-meme (verifie
sur tout l'historique local de juin a aout : chaque transition de rang
calendaire consecutive a ete correctement detectee via le champ `previousRank`
de Spotify), juste une chance ratee de collecter la donnee source. Racine :
`_collect_data_only_dates` ne construisait ses `runners` qu'a partir de
`COLLECT_RUNNERS` filtre sur `name == "worldwide"`, jamais `artists_global`.
Corrige : pour chaque date de rattrapage, un runner `artists_global --no-post
--date <date>` est desormais ajoute en parallele du runner `worldwide`
(meme `_run_parallel`/`ThreadPoolExecutor`, donc toujours collecte en
parallele comme le reste). Reste `--no-post` par design (le rattrapage ne
tweete jamais de vieilles actus) — seule la donnee historique/`days_at_pos`
est desormais preservee pour ces dates.

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
"Backfill historique" plus haut).

**Le vrai plafond de debit = le `RequestPacer` global de `daily.py`.** Chaque
process worker serialise TOUTES ses requetes a une toutes les
`SPOTIFY_WORLDWIDE_REQUEST_INTERVAL_SECONDS` (peu importe le semaphore, qui ne
regle que le nombre "en vol"). Avec le defaut historique `2.0s` et ~68 regions
par date : ~2,5 min/date/worker minimum -> un backfill de 650 dates a 2 workers
= 12h+. Pieges observes ("ca n'affiche plus rien", "pas fluide") :

- semaphore effectif `1` : l'ancien wrapper posait
  `SPOTIFY_WORLDWIDE_SEMAPHORE = SPOTIFY_WORLDWIDE_TOTAL_CONCURRENCY // workers`,
  avec `TOTAL_CONCURRENCY` defaut `1` -> `1//2 = 1`. Regions fetchees une par
  une. **Corrige** : `--concurrency` (defaut 8).
- `FETCH_MAX_ATTEMPTS = 0` (illimite) : le wrapper direct ne le fixait pas ->
  une region coincee en 429/WARP retente indefiniment (pause `RATE_LIMIT_MIN`
  = 20s entre essais), zero progres, quasi zero log = "lecture infinie".
  **Corrige** : le wrapper fixe `--fetch-max-attempts` (defaut 8) ; au-dela la
  region est omise du snapshot (pas de stream invente), loguee, retentable.
- `SPOTIFY_WORLDWIDE_SEMAPHORE` positionne a la main dans le shell **ne sert a
  rien** via ce wrapper : `_run_chunk` l'ecrase. Passer par `--concurrency`.

- `429 - pause globale` qui gonfle (`x1 x2 x3...`, 20s -> 40s -> ... -> 300s
  dans `daily.py`) = pendant la pause, **zero ligne de log**, indistinguable
  d'un hang. **Corrige** : le wrapper cape a `--rate-limit-max` (defaut 30s) et
  baisse `RATE_LIMIT_MIN_SECONDS` a 12. Une pause ne depasse plus 30s -> il y a
  toujours une ligne au moins toutes les 30s.
- 2 workers depuis la meme IP/WARP = 2x plus de 429 pour rien quand on est deja
  rate-limite. Si ca 429 en boucle : `--workers 1`.
- **Detection de chart mort (2026-08-30)** : dans une boucle multi-dates
  (`--dates-file`), une region qui renvoie 404 `BACKFILL_DEAD_REGION_STREAK`
  fois d'affilee (defaut 6, env `SPOTIFY_WORLDWIDE_DEAD_REGION_STREAK`) est
  **retiree du fetch pour le reste du run** (chart jamais publie sur cette
  periode ; beaucoup de regionaux lances seulement en 2020-2021). Un 200
  reset le compteur. `global` n'est jamais retire. **Si `global` lui-meme
  404 pour une date** (date pas publiee par Spotify) : la date est court-circuitee
  apres la Phase 1, les ~40 autres regions ne sont pas tentees, la date reste
  non ecrite donc retentable. Ordre newest-first du wrapper => sur pour le
  sens (un chart absent en 2021 l'est aussi en 2020).

Leviers surs :

- `--request-interval` (defaut 1.0) : c'est LE levier de vitesse (pacer
  global). Baisser vers `0.7` si zero 429 ; **remonter vers `1.5`-`2.0` des le
  premier `429 - pause globale`**.
- `--concurrency` (defaut 6) : plus bas = moins de 429 simultanes = moins de
  pauses. Monter seulement si aucun 429.
- `--rate-limit-max` (defaut 30) : plafond d'une pause 429. Ne pas monter.
- `--end` a ~aujourd'hui-3 : Spotify publie avec ~1-3 j de retard, sinon les
  dates les plus recentes renvoient 404.
- `--no-sync` sur les gros runs, puis une seule passe finale sans `--no-sync`.
- Les 31 regions gelees (`DISCONTINUED_REGIONS` dans le wrapper) sont
  **automatiquement exclues pour toute date > 2019-08-24** (elles n'existent
  pas cote Spotify apres -> 404 qui consomment un slot de pacer). Elles restent
  fetchees pour les dates <= cutoff (le wrapper split le lot du worker en deux
  sous-appels). `--include-discontinued-regions` desactive ce filtre ;
  `--regions` le court-circuite aussi.
- Ajouter de vraies sessions Spotify (`--workers` plafonne par
  `len(spotify_session*.json)`). Copier le meme fichier ne compte pas.

Exemple (prudent, sans hang, single worker) :

```powershell
python .\scripts\backfill_spotify_charts_history.py --gaps-from-csv global `
  --workers 1 --concurrency 6 --request-interval 1.0 --end 2026-08-27 --no-sync
```

### Etat des trous (audit 2026-08-30)

**Le vrai trou = l'historique worldwide PAR PAYS pour 2020-2025.** Seuls
`global` (pipeline dedie `global/daily.py`), `us`, `uk` ont une vraie
couverture recente ; `fr` ~55 % en 2024-2025. **Toutes les autres regions
(`de`, `br`, `es`, `it`, `jp`, `se`, `nl`, `mx`, `no`, `dk`, `pl`... ~30) :
2017-2019 puis quasi RIEN jusqu'a 2026.** Sur 731 jours de 2024-2025 ou Taylor
a charte en global 731 fois, `se` n'a que 10 jours. Un tiers de regions
(`au ca hk ie my nz ph sg tw kr`) a ~90 jours en 2024-2025 (sous-ensemble "gros
marches" collecte un temps). C'est CA qui fait le "total days" faux sur les
pages pays du site. -> **methode : `--per-region-sweep` sur 2024-2025** (puis
etendre 2022-2023 si Spotify sert encore).

`us`/`uk` ont aussi des trous 2025 (`us` : quasi tout Fev-Mai 2025 + Sept, ~107 j ;
`uk` : ~45 j epars) = collecte worldwide cassee sur cette periode, `global`
survivant seul.

**`--gaps-from-csv global` sur-selectionne** et vaut peu : les 649 "trous" sont
TOUS en 2018-2021 (zero apres 2021) = periode creuse de Taylor. Le chart global
existe mais **Taylor souvent hors top 200** (2020-03-21 : 0 de Taylor), et les
regionaux "live" n'existaient pas encore -> enormement de 404 legitimes. A
faire en dernier, ou pas.

Le store durable = `db/charts_history_<region>.csv` (suivi par git, lu par le
site). Le dossier `snapshots/spotify_charts/` local n'est PAS commit et souvent
tres incomplet sur une machine donnee — ne pas s'en servir pour juger de ce
qu'on "a".

Couverture des CSV a l'audit : `global` 82 % (~650 dates manquantes), `us`
80 %, `uk` 78 %, `fr` **32 %**. Les trous de `global` sont **5 blocs contigus,
tous avant 2022** :

- 2018-07-05 -> 2019-04-25 (295 j) — le gros morceau
- 2020-03-27 -> 2020-07-23 (119 j)
- 2021-07-07 -> 2021-09-16 (72 j)
- 2021-05-12 -> 2021-07-01 (51 j)
- 2020-10-22 -> 2020-11-24 (34 j)
- + ~50 j de micro-trous

Depuis 2022, `global`/`us`/`uk` sont quasi complets.

**31 regions figees exactement au 2019-08-23/24** (`ar bg bo cl co cr do ec eg
es fi gr gt hn in is it jp ma mx ni pa pe py ro sv th tr uy vn za`) : c'est un
ancien backfill TSM qui s'est arrete la, pas Spotify. La collecte reguliere a
repris plus tard avec seulement ~37 regions "live". Pour les trous <= 2019-08
elles etaient toutes suivies (sweep complet justifie) ; pour les trous
2020-2021 elles n'ont jamais ete collectees et Spotify renvoie souvent 404 sur
ces vieux charts regionaux (skip rapide avec `SPOTIFY_SKIP_LATEST_FALLBACK_ON_404=1`
que le wrapper pose deja). Ces 31 codes vivent dans
`backfill_spotify_charts_history.py::DISCONTINUED_REGIONS` (+ cutoff
`DISCONTINUED_REGION_CUTOFF`), auto-exclus au-dela du cutoff.

### Workflow recommande pour un gros rattrapage worldwide

**Methode par defaut : `--per-region-sweep`** (une region a la fois, une
requete par tick de pacer). C'est le bon outil pour le trou worldwide par-pays
2020-2025. Beaucoup plus doux que fetcher N regions/date (pas de 429 paralleles
qui declenchent GlobalPause) et le progres est granulaire (une region a la fois,
reprenable via `state['swept_regions']`).

```powershell
# 2024-2025, toutes les regions, historique le plus riche d'abord :
python .\scripts\backfill_spotify_charts_history.py --per-region-sweep `
  --start 2024-01-01 --end 2025-12-31 --request-interval 1.0 --no-sync
# puis la passe de sync (sans --no-sync)
python .\scripts\backfill_spotify_charts_history.py --per-region-sweep `
  --start 2024-01-01 --end 2025-12-31

# cible : juste us + uk 2025
python .\scripts\backfill_spotify_charts_history.py --per-region-sweep us uk `
  --start 2025-01-01 --end 2025-12-31
```

Reglages :
- `--request-interval 1.0` (defaut). Zero 429 sur plusieurs regions -> `0.7`.
  Des le premier `429 - pause globale` soutenu -> `1.5`-`2.0`.
- `--rate-limit-max 30` (defaut) : plafonne les pauses 429 (sinon 300s de
  silence = impression de hang).
- `--end` a ~aujourd'hui-3 : Spotify publie en retard (sinon 404, court-circuit).
- Les 31 regions gelees + les regions quasi-vides sont exclues du sweep bare
  (nommables explicitement). `--include-discontinued-regions` pour forcer.
- Detection auto de chart mort : une region qui 404 6x d'affilee dans un run est
  ecartee ; si `global` 404 pour une date, la date est court-circuitee.

**Reprise / progression** : le sweep checkpoint `state['swept_regions']` apres
chaque region. Pour un run non-sweep (`--gaps-from-csv`), un thread watcher
scanne les snapshots et checkpoint toutes les `--progress-interval` s (defaut
30) + `[PROGRESS] N/total (~Yh left)`. Un kill perd <=30s -> relancer reprend.
Le sync final n'enrichit que la plage touchee.

`--gaps-from-csv global --workers 1 --end 2026-08-27` : rattrape les trous
propres de `global` 2018-2021. Faible valeur (periode creuse), a faire en
dernier.

Reordonner la boucle "par region au lieu de par date" ne reduit pas beaucoup le
nombre de requetes (`regions x dates`) MAIS le sweep evite les 429 paralleles et
ne re-fetch pas les dates deja completes region par region (us/uk/ca deja a
~85% en 2024-25 ne repayent pas).

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
- `--regions CODE ...` / `--exclude-regions CODE ...` : restreint/exclut la
  liste de pays fetchee. Sous filtre, le snapshot date existant
  (`ts_worldwide_<date>.json`, fallback legacy) est relu et les regions hors
  perimetre sont mergees en retour intactes (`existing_by_track` +
  `already_done`), y compris sous `--force` — qui ne veut alors dire que
  "re-fetch les regions ciblees", jamais "ecrase tout le snapshot avec un
  sous-ensemble". `skipped_regions` de l'ancien run est aussi conserve (moins
  les regions resolues ce run). Permet de remplir un snapshot region par
  region. (Sans filtre, `--force` ignore le snapshot existant comme avant.)
- `--post-priority-region global|fr|us`
- `--post-priority-global-new`
- `--post-multi-song-regions`
- `--post-multi-song-regions-only`

**Priorite de post X (2026-08-28) :** `run_all_charts._build_env` pose
`TWITTER_POST_PRIORITY=1` dans l'env de tous ses sous-process. Quand streams
finalize poste en meme temps (defaut `3`, sweep album `4`), les tweets charts
prennent le slot de compte X en premier. Mecanisme + bareme -> skill
`data-rules` ; implementation -> `core/twitter.py::_twitter_account_slot`.
Ne concerne PAS l'ordre interne des cards charts (toujours `_card_priority` /
`_post_priority_global_new_card` etc.).

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

**Piege confirme et corrige (2026-08-17) : backoff `GlobalPause` sans plafond
= pause silencieuse pouvant depasser 1h, indistinguable d'un vrai hang.**
Quand les deux tokens Spotify se prennent un 429 dans le meme cycle
(`GlobalPause.trigger` dans `worldwide/daily.py`), la pause suit un backoff
multiplicatif `seconds * consecutive` (x1, x2, x3…) qui n'avait **aucun
plafond**. Sous rate-limit soutenu (IP/WARP tape par Spotify sur plusieurs
regions d'affilee — observe en meme temps qu'`update_streams.py` tournait en
parallele sur la meme machine), `consecutive` grimpe sans fin et un seul cycle
de pause peut durer des dizaines de minutes voire plus d'une heure — pendant
toute cette duree, zero requete reseau, zero CPU, zero ligne de log (le print
`[pause] tous tokens epuises` ne sort qu'une fois au debut du cycle). Observe
en prod le 2026-08-17 sur le run `--watch-release` du 16/08 : `worldwide`
figé 76 minutes apres la region `hu` sans qu'aucun timeout/retry visible ne se
declenche, force un kill manuel + relance du process. Corrige : `effective`
est desormais plafonne a `SPOTIFY_WORLDWIDE_RATE_LIMIT_MAX_SECONDS` (defaut
`300` = 5 min). Le comportement "jamais sauter de la vraie donnee" est
preserve (`consecutive` continue de grimper et le process retente
indefiniment) — seule la duree d'un cycle de pause individuel est bornee, ce
qui garantit un log toutes les <=5 min au lieu d'un silence potentiellement
illimite. Si un run parait fige sans nouvelle ligne de log pendant plus de
~5-6 min avec zero activite reseau/CPU sur le process, ce n'est plus ce
mecanisme (deja borne) — chercher ailleurs (Playwright, deadlock semaphore).

Variables d'environnement:

- `SPOTIFY_WORLDWIDE_SEMAPHORE`: concurrence regions, defaut `10`, backfill
  via run_all met `12`.
- `SPOTIFY_WORLDWIDE_RATE_LIMIT_MAX_SECONDS`: plafond d'un cycle de pause
  `GlobalPause` (tous tokens 429 epuises), defaut `300` (5 min). Voir piege
  ci-dessus.
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
- `SPOTIFY_IMMEDIATE_REENTRY_POST_MAX_ATTEMPTS` / `SPOTIFY_IMMEDIATE_REENTRY_POST_RETRY_SECONDS`
  (lus par `worldwide/daily.py`): retry du post standalone "immediate
  re-entry" (par pays, pendant la collecte, hors `global` — voir "Cards et
  images" plus bas), defaut `3` tentatives / `30s`.

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
        "streak": 1,
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

`peak_rank`, `total_days` et `streak` viennent tous de `chartEntryData`
(`peakRank` / `appearancesOnChart` / `consecutiveAppearancesOnChart`) sur chaque
requete de chart pays. **Fix 2026-08-29** : `daily.py` recopie maintenant
`streak` dans l'entry `by_track` (il ne mettait que `peak_rank`/`total_days`, du
coup seul `global` avait `streak` dans les snapshots anterieurs). Les vieux
snapshots gardent `streak` absent pour les pays non-global -> `charts.py`
retombe sur `current_streak` de `charts_discography/peaks_by_track.json`.

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
- `artists_global/tools/scripts/generate_filtered_artist_chart.py`

**Autonome depuis 2026-08-16, plus orchestre par `run_all_charts.py`.**
`artist_global_daily.py` collecte le chart, poste le chart de base (voir plus
bas), PUIS declenche lui-meme les charts filtres via
`_run_filtered_artist_charts()` (liste `_ACTIVE_FILTERED_ARTIST_CHARTS` en
haut du fichier, meme cles que `FILTERS` dans
`generate_filtered_artist_chart.py` — les deux listes doivent rester
synchronisees a la main, pas d'import croise). Voir "Run quotidien" plus haut
pour le detail des deux taches Task Scheduler et pourquoi ce decouplage.

**Bug confirme et corrige (2026-08-16) : le post "top 5 / top 10 most streamed
artists" ne se declenchait que sur un changement du rang PROPRE de Taylor,
jamais sur un remaniement du classement autour d'elle.** `generate_artist_chart_image.py`
choisit un mode (`top5`/`top10`/`solo`) selon le rang de Taylor Swift, mais son
garde-fou anti-spam (ajoute le 2026-08-03, `rank_unchanged(ts_artist)`) ne
regardait que le rang de Taylor, avant meme de savoir quel mode serait utilise
— donc en mode `top5`/`top10`, un reclassement des AUTRES artistes du top
(ex: Bad Bunny/Drake/Ariana Grande qui permutent #1/#2/#3) ne declenchait
jamais de post tant que Taylor restait a la meme position. Confirme sur
l'historique local : entre 2026-08-05 et 2026-08-08, le top 3 a change d'ordre
alors que Taylor est restee #4 en continu — aucun tweet n'est parti. En plus,
`artist_global_daily.py` avait sa PROPRE copie du meme garde-fou
(`_taylor_rank_unchanged`) qui coupait l'appel a
`generate_artist_chart_image.py` avant meme qu'il ne s'execute, rendant tout
fix cote `generate_artist_chart_image.py` seul inutile. Corrige : nouveau
helper `top_list_unchanged(artists, limit)` dans `generate_artist_chart_image.py`
qui reconstruit le classement precedent via le champ `previous_rank` deja
present sur chaque artiste (fourni par Spotify, meme source que
`rank_change_label`) et compare l'identite+ordre du top 5/10 courant vs
precedent (id Spotify si dispo, sinon nom normalise). Choix produit assume
(2026-08-16) : en mode `top5`/`top10`, le post part si le rang de Taylor A
CHANGE **ou** si le classement top 5/10 a change ; en mode `solo` (Taylor hors
top 10), seul son propre rang compte, comme avant. Le garde-fou duplique dans
`artist_global_daily.py` a ete supprime (avec `_taylor_rank_unchanged`/
`_taylor_swift_row`, devenus morts) — la decision post/skip vit desormais
uniquement dans `generate_artist_chart_image.py`, qui est le seul endroit a
connaitre le mode.

**Card artiste worldwide : supprimee le 2026-08-16 (`generate_artist_worldwide_card.py`
retire du repo).** Elle avait ete cassee un mois (juillet-aout) par une
regression `--post` (deja corrigee dans une session precedente), puis rendue
redondante par les charts filtres `us_artist_chart`/`uk_artist_chart`
ci-dessous (donnees pays plus completes : top5/10 entier, pas juste le rang
de Taylor). Decision produit explicite : retirer plutot que garder les deux.
Aucun consommateur frontend trouve (`charts_artists_global_worldwide.json`
n'etait lu nulle part dans `tsm-frontend`), suppression sans impact site.

**Charts artiste filtres (depuis 2026-08-16) : `generate_filtered_artist_chart.py`.**
Variante generique du chart artiste, centree sur Taylor Swift, mais restreinte
a un sous-ensemble d'artistes (ex: uniquement les femmes). Reutilise a 100% le
rendu du chart de base (`build_top5_html`/`build_top10_html`/`build_solo_html`
dans `generate_artist_chart_image.py`, desormais parametres par `title=`/
`rank_scope=`) — seule la liste d'artistes en entree change.

- `artist_id`/`artist_name`/`gender` viennent de `Artists.csv`
  (`artists_global/Artists.csv`, curation manuelle, exception explicite au
  `.gitignore` `*.csv` sinon ce fichier ne serait jamais commit). Valeurs
  `gender` : `F` / `M` / `Group` (un groupe/duo, mixte ou non, compte comme
  `Group` — pas de sous-decoupage plus fin). Le script complete automatiquement
  `Artists.csv` avec tout nouvel `artist_id` jamais vu (gender laisse vide a
  remplir a la main) a chaque execution — pas besoin de reseeder le fichier.
  Il logue aussi `[WARN] N artiste(s) du top 50 sans gender` pour signaler les
  trous a combler.
- `build_filtered_rows(artists, predicate, genders)` filtre puis **re-classe**
  (`rank` 1..N dans le sous-ensemble, pas le rang global Spotify).
  `attach_previous_ranks(...)` refait la meme operation sur le snapshot de la
  veille (`_load_previous_chart`, `J-1` calendaire) et reinjecte un
  `previous_rank` propre au sous-ensemble par `artist_id` — donc les badges
  NEW/RE/▲/▼ affiches reflettent un mouvement DANS le sous-ensemble, pas dans
  le chart global. **Peak/Streak/Days at Pos restent les valeurs globales**
  (choix produit assume le 2026-08-16 : simplicite, pas de reconstruction
  d'historique par sous-ensemble) — seuls Pos et +/- sont recalcules.
- Deux cadences de post, definies par filtre dans `FilterConfig.cadence` :
  - `"daily"` (ex: `female`) : meme regle que le chart de base — poste si le
    rang de Taylor dans le sous-ensemble a change **ou** si le top5/10 du
    sous-ensemble a change d'ordre/composition (reutilise directement
    `rank_unchanged`/`top_list_unchanged` de `generate_artist_chart_image.py`,
    qui operent deja sur n'importe quelle liste rank/previous_rank generique).
  - `"rank_up"` (ex: `starts_with_t`, `named_taylor`) : poste **uniquement** si
    le rang de Taylor dans le sous-ensemble s'est strictement ameliore vs la
    veille (pas de post sur stagnation ni sur degradation, meme si le reste du
    sous-ensemble bouge). Piege connu sur `named_taylor` : dans le top 200
    actuel, Taylor Swift est la SEULE artiste dont le nom contient "taylor"
    (`re.findall(r"[a-z]+", name)` contient `"taylor"`) — sous-ensemble de
    taille 1, donc son rang y est toujours #1 et ne peut jamais "s'ameliorer".
    Ce filtre ne postera donc quasiment jamais tant qu'aucune autre "Taylor"
    ne charte. Garde volontairement (demande explicite), pas un bug.

**Filtres regionaux (depuis 2026-08-16) : `us_artist_chart` / `uk_artist_chart`.**
Contrairement aux filtres ci-dessus (qui filtrent le chart GLOBAL deja
collecte, zero appel reseau), ces deux-la vont chercher en LIVE le chart
artiste propre a un pays Spotify — un classement totalement different du
chart global (ex: le 2026-08-14, Drake est #1 sur le chart artiste US alors
qu'il n'est pas #1 sur le global). Mecanique dans `FilterConfig.region` :
- `region="us"` -> chart_id `artist-us-daily` ; `region="gb"` -> `artist-gb-daily`
  (`gb` = code Spotify du Royaume-Uni, meme convention que `worldwide/daily.py`
  — ne pas utiliser `"uk"` comme code region, la clé de filtre `uk_artist_chart`
  reste `uk` mais son `region=` interne est `gb`).
- `fetch_region_chart(region, stats_date)` reutilise directement
  `_fetch_chart`/`_get_bearer_token` de `artist_global_daily.py` (import via
  `collectors.spotify.charts.artists_global.artist_global_daily`, meme cache
  disque de bearer token que la collecte globale — pas de double-Playwright si
  les deux filtres tournent l'un apres l'autre dans le meme run).
  `_parse_artist_entries` renvoie deja `previous_rank` (fourni par Spotify
  lui-meme sur ce chart pays, meme mecanisme que le chart global) donc pas
  besoin de recalculer une comparaison locale J-1 comme pour les filtres
  locaux. Testee en direct le 2026-08-16 (`--no-post`) : donnees US et UK
  bien distinctes et coherentes (rang Taylor #2 sur les deux ce jour-la,
  classements autour d'elle differents).
- `Days at Pos` n'est pas disponible sur ces cartes (`—` affiche) : ce champ
  vient d'un historique local jour par jour que ces fetchs live ne
  construisent pas — Peak/Streak restent eux les valeurs du chart GLOBAL de
  l'artiste (meme simplification que les filtres locaux), pas du chart pays.
- Cadence `rank_up` uniquement pour ces deux (poste seulement si le rang de
  Taylor sur le chart US/UK s'ameliore vs la veille). Pas de mode "daily" pour
  l'instant.
- Echec de fetch (chart indisponible, 404, erreur reseau/token) = skip
  silencieux (`sys.exit(0)`, pas une erreur fatale) — ce sont des charts
  bonus, pas la collecte principale ; `artist_global_daily.py` continue sur
  le filtre suivant meme si un `generate_filtered_artist_chart.py` echoue
  (juste un `[WARN]`, voir `_run_filtered_artist_charts`).
- Filtres actifs : `artist_global_daily.py::_ACTIVE_FILTERED_ARTIST_CHARTS`
  (liste de cles `FILTERS`). Ajouter un filtre = ajouter une entree dans
  `FILTERS` (`generate_filtered_artist_chart.py`) + sa cle dans cette liste —
  pas de nouveau script necessaire, mais **synchroniser les deux a la main**
  (pas d'import entre les deux fichiers, cf. note plus haut). Declenche a la
  fin de `artist_global_daily.py::main()`, juste apres le post du chart de
  base, uniquement si `period == "daily"` et pas `--no-post`.
- Sorties : image `artist_chart_{filter_key}_image.png`, lock
  `artist_chart_{filter_key}_{mode}_posted.lock`, meme dossier
  `spotify_chart_dir("artists_global", date)` que le chart de base.

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

**Covers (2026-08-29) :** toute cover est téléchargée au rendu via
`comp.img_fetch.fetch_data_uri` (cache disque persistant `db/discography/.image_cache/`
+ 3 retries + fallback tailles CDN Spotify). `generate_chart_image.py` (via
`tables_image.url_to_data_uri`) et `generate_card_images.py` y délèguent. Avant
ce fix, un seul timeout réseau vidait silencieusement UNE cover du run (ex.
*The Fate of Ophelia* absent du `global/chart_image.png` du 2026-08-27). Ne pas
réintroduire de `_url_to_data_uri` local à cache mémoire seul. Détail → skill
`image-gen` § "Fetch d'image résilient".

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

**Bug confirme et corrige (2026-08-15) : `sync_playwright()` imbrique faisait
disparaitre silencieusement une chanson "first single region entry" (ni
standalone, ni dans le thread).** `generate_card_images.py` garde son propre
navigateur Playwright ouvert (`with sync_playwright() as p:`) pendant toute la
boucle de generation des cards. L'ancien code appelait
`_post_first_single_region_standalone` (qui ouvre SA PROPRE
`sync_playwright()` via `post_with_image` dans `core/twitter.py`) direction
dans cette boucle, donc pendant que le navigateur exterieur etait encore
ouvert — deux `sync_playwright()` imbriques dans le meme thread, non supporte
par Playwright, qui leve "It looks like you are using Playwright Sync API
inside the asyncio loop. Please use the Async API instead." (message trompeur:
aucune boucle asyncio n'est en cause ici). Comme cet appel etait dans un
`try/except` qui logge juste un `[WARN]`, la chanson etait affectee au chemin
standalone (donc retiree du thread) mais son post standalone echouait aussi —
observe le 2026-08-14 sur "All Too Well (10 Minute Version) (Taylor's
Version)", jamais postee ce jour-la malgre `[ OK ] cards` en sortie de
`run_all_charts.py`. Corrige : les posts standalone "first single region
entry" sont desormais collectes dans `pending_standalone` pendant la boucle et
postes seulement APRES la fermeture du navigateur (`browser.close()`), au meme
endroit que le thread de cards classique qui faisait deja ca correctement.

**Immediate NEW/RE posting — pendant la collecte, par pays, hors `global`
(depuis 2026-08-15, generalise a NEW le 2026-08-17) :** une chanson absente de
TOUS les pays la veille (`prev_country_counts[track_id] == 0`, via le dernier
snapshot worldwide `ts_worldwide_<veille>.json`) qui apparait aujourd'hui dans
une region — que ce soit un vrai premier debut (`movement == "NEW"` /
`is_new`) ou un retour apres absence complete (`movement == "RE"` /
`is_re_entry`), hors `global` — deja gere separement par
`_post_priority_global_new_card`) — declenche desormais un tweet standalone
immediatement, des que CETTE region est collectee dans `worldwide/daily.py`
(Phase 1 ou Phase 2), sans attendre la fin de toute la collecte worldwide ni
l'etape `cards` separee de `run_all_charts.py`. Implemente dans
`worldwide/daily.py` : `_maybe_trigger_immediate_reentries` (appelee depuis le
wrapper `_fetch_and_notify` autour de chaque `_fetch_region`) lance un thread
daemon `_post_immediate_reentry_card` qui rend la chart_card (`comp/chart_card
.render_chart_card` + `write_chart_card_png`, meme composant que Phase 3) et
poste via `post_with_image`, avec retry
(`SPOTIFY_IMMEDIATE_REENTRY_POST_MAX_ATTEMPTS`/`_RETRY_SECONDS`, defaut `3`/
`30s`). Badge/verbe NEW vs RE calcules dynamiquement (`_immediate_entry_is_re`)
au lieu d'etre fige sur "RE" — meme logique que `_single_region_badge` /
`_build_tweet` dans `generate_card_images.py`.

**Correction (2026-08-17) : ce mecanisme etait limite a RE, jamais NEW,
malgre son nom "immediate re-entry".** Observe en prod le meme jour : "All Too
Well (10 Minute Version) (Taylor's Version)" (un premier debut single-region,
pas un retour) est apparu dans la region `ph`, mais comme `_maybe_trigger_
immediate_reentries` filtrait explicitement sur `movement == "RE"` /
`is_re_entry` uniquement, aucun post immediat ne s'est declenche — la chanson
n'a ete postee (standalone, badge correct) que ~5 minutes plus tard via
Phase 3 de `generate_card_images.py` (`_is_first_single_region_entry`), une
fois TOUTE la collecte worldwide terminee. Demande produit explicite : le NEW
merite le meme traitement immediat que le RE, pas seulement le retour d'un
titre du catalogue. Corrige : la condition de declenchement couvre desormais
NEW et RE (`movement in ("RE", "NEW")` ou `is_re_entry`/`is_new`).

**Choix produit assume :** un titre qui entre/re-entre dans plusieurs pays le
meme jour ne poste ce tweet standalone immediat qu'une fois — le verrou
`cards/first_single_region_posted.json` (cle `{slug}_chart_card`, meme
`_slugify`) est indexe par TITRE seul, pas par (titre, pays), donc la 2e
region a declencher la meme chanson le meme jour trouve le verrou deja pris et
ne reposte pas. Meme fichier/cle que Phase 3
(`_is_first_single_region_entry` dans `generate_card_images.py`, qui ne fire
que si la chanson n'a EXACTEMENT qu'un seul pays sur toute la journee) — les
deux mecanismes partagent ce verrou, donc Phase 3 ne re-poste jamais un titre
deja poste ici, meme si le titre a fini par charter dans plusieurs pays (donc
plus "single region" au sens strict de Phase 3). Desactive en
`--no-post`/`--backfill-mode`/`--dates`/`--dates-file`. Les threads sont
joints (timeout 600s chacun) juste avant le commit git final de `daily.py`
pour ne pas laisser le process sortir (threads daemon) avant qu'un post en
cours ne se termine.

**Sync CSV immediat par pays, pendant la collecte (depuis 2026-08-15) :**
`db/charts_history_<region>.csv` est normalement ecrit une seule fois par
`scripts/sync_spotify_country_charts_from_worldwide.py`, appele en toute fin
de `run_all_charts.py` apres la collecte COMPLETE des ~75 regions worldwide.
Ce script reste inchange et tourne toujours en filet de secours idempotent
(dedupe par `(date, track_id)`, utile en backfill/catchup). En plus,
`worldwide/daily.py` ecrit maintenant la meme ligne CSV directement des
qu'une region vient d'etre collectee (`_sync_region_csv_immediately`, meme
wrapper `_fetch_and_notify`), donc l'historique d'un pays est visible sans
attendre la fin de toute la collecte du jour. Applique `_apply_track_id_history`
sur la seule region concernee avant d'ecrire (meme fonction que la voie batch,
par-ligne/par-region donc valide en subset, idempotente si rappelee plus tard
sur les memes lignes) pour eviter d'ecrire un faux NEW/RE du a un changement
de titre. Independant de `--no-post` (ecrire l'historique n'est pas un post)
mais desactive en `--backfill-mode`/`--dates`/`--dates-file` (pipeline de sync
dedie a la place). Variables : `SPOTIFY_IMMEDIATE_REENTRY_POST_MAX_ATTEMPTS`/
`_RETRY_SECONDS` partagees avec le mecanisme de post immediat ci-dessus.

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

**Bug confirme et corrige (2026-08-25) : un `worldwide` marque "deja fait"
mais partiel (ex. 33/~75 pays, `global`/`fr` absents) bloquait cards/global-post
indefiniment, meme sur des reruns repetes de `run_all_charts.py`.** Racine :
`_validate_worldwide_snapshot` ne verifie que "le snapshot n'est pas vide" (au
moins une entree), jamais la presence des regions cles — donc une collecte
`worldwide` interrompue en cours de route (WARP/session, meme panne "lecture
infinie" documentee ailleurs) reste marquee valide/`updated.lock`, et
`_runner_done`/`_filter_pending_runners` empechent alors tout rerun du
subprocess `worldwide/daily.py` pour cette date (`[SKIP] deja fait: worldwide-data`).
Observe le 2026-08-24 : `global/ts_chart_2026-08-24.json` n'a jamais existe,
donc `_require_global_chart_data_before_cards` bloquait cards et
`global-post` echouait (`ts_chart_{date}.json absent`) a chaque rerun, sans
jamais tenter de reparer. Le mecanisme de reparation existait deja
(`_ensure_card_regional_data`, qui rappelle `worldwide/daily.py --no-post
<date>` pour combler les regions `global`/`us` manquantes) mais **n'etait
jamais atteint** : dans le bloc cards de `main()`, `_require_global_chart_data_before_cards`
tournait AVANT `_ensure_card_regional_data` et coupait court (`should_generate_cards
= False`) des que `global/ts_chart_*.json` etait absent — exactement le cas
que la reparation est censee couvrir. En plus, cet appel de reparation passait
`--force` a `worldwide/daily.py`, ce qui desactive le skip incrementiel
par-pays interne (`already_done`, base sur les pays deja presents dans
`charts_worldwide.json` pour la meme date) et aurait donc reussi seulement en
re-fetchant les ~75 pays au lieu des ~40 reellement manquants. Corrige : (1)
`_ensure_card_regional_data` appelle desormais `worldwide/daily.py --no-post
<date>` **sans** `--force` — le skip par-pays interne au script fait deja le
tri correctement (pays deja presents ignores, seuls les pays manquants sont
refetches, donnees existantes fusionnees, jamais ecrasees) ; (2) dans le bloc
cards, `_ensure_card_regional_data` tourne desormais AVANT
`_require_global_chart_data_before_cards` — ce dernier redevient un
garde-fou final ("toujours absent apres tentative de reparation = vraiment
pas encore publie, skip propre") au lieu d'un court-circuit qui empechait
toute reparation. Reste vrai : la validation `_validate_worldwide_snapshot`
elle-meme n'a pas ete durcie (toujours juste "non-vide") — un `worldwide`
partiel continuera d'etre marque "deja fait" au niveau collecte, mais le
palier cards se repare desormais correctement derriere.

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
- `artist_chart_{filter_key}_{mode}_posted.lock`: chart artiste filtre poste
  (`generate_filtered_artist_chart.py`).
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

## Notifications discographie (ntfy spcharts)

En fin de run reussi (`sync-country-charts` -> `build-country-discography`),
`run_all_charts.py::_notify_spcharts_events` diffe pour chaque region
`runtime/exports/web/site/data/charts_discography/<region>.json` contre
`<region>_previous.json`. **`_previous` n'est PAS le snapshot de la veille** :
`build_spotify_chart_discography.py` le recalcule a chaque run = tout
l'historique de la region *sauf sa derniere date de chart*. Le diff est donc
"effet de la derniere journee de chart de cette region", et il est
**re-emis a chaque run** (aucun etat/dedup) : pour une region gelee (plus de
collecte), la meme alerte repart indefiniment.

Alertes actives :

- **`total days passed`** et **`streak inactive`** : restreintes aux regions de
  `SPCHARTS_RANKED_HISTORY_REGIONS = {global, us, uk}`. Ailleurs l'historique
  est partiel/gele et le classement en jours cumules n'a pas de sens
  (ex. flood `[GR] I Knew It, I Knew You passed ...` : GR gele au 2026-06-06 +
  ligne worldwide corrompue `total_days: 24` au lieu de ~2).
- **`new peak rank`** : toutes regions (un meilleur pic reste vrai meme si la
  baseline "was #Y" peut etre imparfaite).
- **`new peak streams`** : **supprimee** (2026-08-27). L'historique charts ne
  remonte pas a 2017, donc pour un titre de catalogue le pic stocke n'est
  qu'un "record depuis le debut du tracking", pas un record all-time.

## Discography payload + vue "Overall" (par pays)

`build_spotify_chart_discography.py` ecrit, par region/pays, des `songs[]` avec
(entre autres) `peak_rank`, `peak_streams`/`peak_streams_date`, `total_days`,
`longest_streak`/`longest_streak_active`, et depuis 2026-08-28 :

- **`days_at_peak`** : nombre de jours OU on a effectivement observe la chanson
  au `peak_rank` dans ce pays. C'est un **minorant** (snapshots historiques
  clairsemes) — `0` = jamais observe a ce rang (souvent le cas quand le snapshot
  worldwide herite un `peak_rank: 1` "global" pour un pays ou la chanson n'a
  jamais ete #1). Ne jamais "corriger" cette valeur a la hausse.
- **`current_streak`** : streak consecutif courant, `0` si `last_date` != derniere
  date chartee du pays.

En plus des `{region}.json`, le script ecrit **`charts_discography/peaks_by_track.json`**
= `{"<track_id>|<country>": {peak_rank, peak_streams, peak_streams_date,
days_at_peak, total_days, current_streak, longest_streak,
longest_streak_active}}`. `tsm-frontend/api/routes/charts.py` (handler
`region == "worldwide"`) le merge dans chaque entry de `by_track`. **Le snapshot
Spotify est prioritaire** : `peak_rank`, `total_days` ET `streak`
(`consecutiveAppearancesOnChart`) sont donnes par Spotify sur CHAQUE requete de
chart pays -> `_parse_ts_entries` les parse tous, et depuis 2026-08-29
`daily.py` les recopie tous les trois dans l'entry `by_track` (avant cette date
`streak` etait oublie a la construction du dict — seul `global` l'avait). Le
lookup `peaks_by_track.json` ne remplit donc `peak_rank`/`total_days`/`streak`
(<- `current_streak`) que s'ils manquent — utile surtout pour les vieux
snapshots d'avant le fix. `peak_streams` et `days_at_peak` ne sont JAMAIS dans
la reponse Spotify (`chartEntryData` a `peakDate` mais pas de compteur de jours
au peak, ni de pic de streams historique) -> toujours du lookup ; `days_at_peak`
n'est affiche que si le `peak_rank` du lookup egale encore le `peak_rank`
(autoritaire) de l'entry. Cote UI c'est le tableau
par pays de `SongBlock.jsx` (vue Overall) : colonnes Region / Position / Streams
/ Total Days / Streak / Peak (`#N (xJ)`) / Peak Streams.

Un rebuild partiel (`--regions ...`) MERGE dans le `peaks_by_track.json` existant
(retire seulement les cles des regions rebuildees) — pas d'ecrasement total.

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

**Best rank since (2026-08-14)** : `compute_spotify_rank_since_highlight()` dans
`generate_home_highlights.py` marche a rebours l'historique rank de chaque
region (`_load_region_rank_points`, `charts_history_{global,fr,us,uk}.csv` via
`load_chart_csv_rows`) pour detecter le meilleur rang depuis au moins
`RANK_SINCE_MIN_DAYS` (14) jours, via le primitif partage
`collectors/spotify/core/rank_since.py`. Piege data connu : `track_id` est vide
sur une grosse partie des lignes anterieures a mi-2026 (filtrer les lignes
sans track_id) et il existe des doublons `(date, track_id)` (rang/streams
identiques, dedupe sans risque). Le highlight `best_rank_since` reste affiche
14 jours apres son declenchement via un mecanisme read-merge-write dans
`main()` (pas de recalcul from scratch a chaque run pour ce type-la) — voir
skill `data-rules` § "Home highlights" pour les seuils produits.

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
