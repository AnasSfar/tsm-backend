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
- `--admin` (ajoute 2026-08-19, implique `--over` ET `--force`): accepte le
  total Spotify brut tel quel, daily inclus s'il est negatif (pas de clamp
  `compute_daily`). Ecrit `estimated_reason=admin_override`, ce qui fait
  aussi compter la ligne comme "faite" dans le check de completude
  (`load_history_track_ids_with_daily_for_date`) malgre un daily
  negatif/vide. Implique `--force` (ajoute 2026-08-20) car un track qui a
  deja une ligne partielle/vide pour cette date (ecrite par un run precedent
  bloque) est sinon skip comme "deja fait" par
  `already_done_for_stats_date` AVANT meme d'atteindre la logique override —
  observe en prod : deux runs `--admin` de suite sans effet sur les tracks
  cibles tant que `--force` n'etait pas aussi passe. Consequence : `--admin`
  re-scrape tout le catalogue (temps de run complet), pas juste les tracks
  cibles. Depuis 2026-08-25, si le total Spotify est encore identique au total
  de reference, `--admin` garde le track en pending et ne l'ecrit avec
  `daily=0` qu'apres 5 rounds de retry
  (`admin_override_same_total_after_retries`) ; l'override brut ne ferme plus
  un same-total au premier passage. Reserve a un cas verifie a la main (fusion/split cote Spotify, cf.
  incident Karma 2026-08-17 dans `pipeline-ops`) — ca contourne la garantie
  "jamais de daily negatif publie".
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
- `data/best_day_since.json` (lu par `tsm-frontend` `api/routes/best_day_since.py`
  -> badge etoile "best day since X" sur la liste Top Songs / page detail chanson)

## Bug fixe : best_day_since.json jamais uploade en prod (2026-08-30)

`export_for_web.py::export_best_day_since` regenere bien
`runtime/exports/web/site/data/best_day_since.json` a chaque run streams, mais
`scripts/r2.py` ne l'avait jamais dans sa liste `json_mappings` -> le fichier
n'etait push nulle part. En prod `/api/best-day-since` renvoyait donc
`{"items": [], "by_track": {}, "missing": "data/best_day_since.json"}` et le
badge etoile n'apparaissait jamais (il marchait en local car le frontend lit
le fichier directement sur disque via `TSM_BACKEND_ROOT`). Fix : ajout de
`("best_day_since.json", "data/best_day_since.json")` a `json_mappings` dans
`scripts/r2.py` (inclus dans le run `--streams-daily`, exclu de `--charts-only`).
Le premier run streams suivant le publie ; sinon upload manuel via
`python scripts/r2.py --streams-daily --skip-history-upload`.

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

Priorite de post X (2026-08-28) : `finalize_update._subprocess_env` pose
`TWITTER_POST_PRIORITY=3` par defaut pour toutes les etapes de post ; les posts
« priority early » pendant la collecte (debut releases, best-day-since early)
descendent a `0`, le sweep album quotidien (jours de semaine) monte a `4`. Quand charts
run_all (`TWITTER_POST_PRIORITY=1`) poste en meme temps, ses tweets passent
devant les etapes streams finalize. Mecanisme + bareme -> skill `data-rules`,
implementation -> `core/twitter.py::_twitter_account_slot`.

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
- Baisse de total accepte pour les extras (ajoute 2026-08-06) : dans
  `try_apply_track_update` (`update_streams.py`), quand `total < last_total`
  pour un track `chart_extra=true` (hors cas `missing_previous_day_total`),
  la baisse est acceptee directement (`reason="lower_than_previous_extra"`,
  `real_update=True`, `daily` reste vide via `compute_daily` qui renvoie
  `None` sur un diff negatif) au lieu de rester `pending` en attendant le
  1er du mois comme un non-extra. Avant ce fix, une extra en baisse hors
  1er du mois recevait `reason="lower_than_previous_not_month_start"` et
  restait bloquee indefiniment: la porte de sortie automatique du retry loop
  (`_complete_same_total_extra_as_zero`) ne gere que `reason=="same_total"`,
  donc ce cas retentait a l'infini (observe: retry round 1105) sans jamais
  fermer la collection ni debloquer le post final. Les non-extra gardent le
  comportement inchange (baisse acceptee seulement le 1er du mois, notif
  ntfy `chart_increasing` incluse — cette notif ne se declenche pas pour
  `lower_than_previous_extra`).
- Pour toute correction historique, comparer DB, snapshots et exports affectes.
- Convention `collection_incident_*` (ajoutee 2026-08-01) : quand le total
  scrape lui-meme est fige/faux sur une fenetre connue pour un non-extra
  (donc daily faux mais total "reellement scrape", pas un simple gap sans
  ligne), on ne touche JAMAIS aux totaux deja ecrits (ancien ET nouveau bord
  de la fenetre restent intouches, y compris les totaux du jour meme —
  regle du proprietaire, cf. incident folklore ci-dessous). On vide
  uniquement `daily_streams` sur la fenetre affectee avec
  `estimated_reason` prefixe `collection_incident_` (ex.
  `collection_incident_folklore_2026-03-09_to_2026-04-18`).
  `export_for_web.normalize_daily_streams_from_totals` doit sauter tout
  reason qui commence par `collection_incident_` (comme `manual_trusted`),
  sinon il recalcule le daily depuis les totaux consecutifs (inchanges,
  donc toujours faux) et ecrase le blank a l'export. Les streams reels non
  comptabilises restent un trou permanent dans le total a vie (aucune
  recuperation possible sans casser soit "jamais toucher un total deja
  scrape" soit "jamais estimer un non-extra").
- Incident connu : folklore (13 titres standard edition : cardigan, my
  tears ricochet, seven, august, this is me trying, illicit affairs,
  invisible string, mad woman, epiphany, betty, peace, hoax, exile) —
  total quasi fige (daily ~1-2% de la normale) du 2026-03-09 au 2026-04-18
  (2026-03-10 pour exile, 2026-03-09 deja vide chez lui). Confirme via
  `db/2026 & 2025 - Daily Archive 2026.csv` (archive pivot par titre,
  correle a 100% avec le live sur toute la periode 2025-01-01→2026-04-18
  sauf exactement cette fenetre) : ~117M streams non comptabilises au
  total, jamais rattrapes par le scraping normal repris le 2026-04-19.
  "the 1", "the last great american dynasty", "mirrorball", "the lakes"
  (meme album) ne sont PAS touches — verifier au cas par cas, ne pas
  supposer un album entier atteint sans comparer a l'archive titre par
  titre. Fixe le 2026-08-01 (`db/streams_history.csv` + normalize dans
  `export_for_web.py`).
- Piege decouvert en corrigeant l'incident folklore : 4 des 13 titres
  (cardigan, my tears ricochet, seven, august) ont un ancien track_id liste
  dans `historical_track_ids` qui a continue a etre collecte normalement
  (donnees reelles, jamais buggees) en parallele du track_id actif pendant
  toute la fenetre de l'incident — pas juste 1-2 lignes isolees, un
  historique parallele complet. `merge_history_by_kept_track` choisissait
  jusqu'ici le "meilleur" candidat par jour via un max() sur
  streams/daily bruts ; le jour ou les deux totaux convergent exactement
  (04-18 ici), ce max() repechait la ligne de l'ancien id et ecrasait le
  blank volontaire du track_id actif. Fix : `merge_history_by_kept_track`
  donne desormais priorite absolue a toute entree portant un
  `estimated_reason` protege (`manual_trusted` ou `collection_incident_*`),
  peu importe les valeurs brutes en concurrence. Reflexe a garder : apres
  toute correction manuelle touchant un track ayant des
  `historical_track_ids`, verifier si l'ancien id a aussi des lignes sur la
  meme fenetre avant de considerer le fix termine — sinon l'export peut
  silencieusement ressusciter l'ancienne valeur.

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

## Anti-doublon best-day-since (2026-08-22)

`post_best_day_since_twitter.py` ne doit jamais poster la meme reference
"best day since X" (ou "best day ever") sur deux jours consecutifs pour le
meme morceau — meme si le record tient techniquement encore le lendemain
(streams toujours au-dessus de tout ce qui precede X), c'est un doublon a
l'affichage. `_track_posted_lock_path(...).lock` stocke desormais un JSON
`{"best_day_since": ..., "kind": ...}` (au lieu d'un fichier vide) via
`_write_track_lock`, et `_is_repeat_of_previous_day` compare la valeur du
jour au lock de la veille avant de poster (dans `_find_all_rows` pour le
flux batch, et dans `_post_single_track_early` pour `--only-track`). Si le
lock de la veille n'existe pas ou est vide (anciens locks pre-fix), aucune
suppression n'a lieu — comportement par defaut sur, pas de faux negatif.
Cette regle ne s'applique qu'aux posts individuels par morceau ; le post
recap (`_find_recap_rows`) et les posts album ne sont pas concernes.

## Priorite best-day-since > 3 mois (2026-08-22)

Un "best day since" dont l'ecart (`days_since`) depasse
`PRIORITY_BEST_DAY_SINCE_MIN_DAYS = 90` (kind="since" uniquement, pas
"best_ever") est garanti d'etre poste : `_is_priority_best_day_since(row)`
fait sauter, pour ces rows, le cap `MAX_BEST_DAY_SONG_POSTS_PER_ALBUM = 3`
et le cap quotidien `POST_COLLECTION_MAX_SONG_POSTS = 5`, au lieu de
concourir avec les autres candidats du jour pour une place limitee — dans
`_pick_rows` (flux batch, priority_rows ajoutees avant le cap par album),
`_validated_song_rows_for_post` (le cap quotidien final ne compte que les
rows non-priority), et `_post_single_track_early` (`--only-track`, cap
quotidien + cap par album sautes si `is_priority`). Elles comptent quand
meme dans `album_post_counts`/`locked_track_ids` une fois postees, donc
elles reduisent la marge des posts normaux suivants pour le meme album/jour
— mais ne sont jamais elles-memes bloquees. `_passes_song_post_gate` n'a
pas besoin de changement : son seuil `ALWAYS_POST_BEST_DAY_SINCE_AFTER_DAYS
= 60` est deja plus permissif que 90 jours.

## Biggest day of the year = post inconditionnel, en early, sans limite (2026-08-29)

Toute chanson qui decroche un record de daily de l'annee
(`row["is_biggest_day_of_year"]`, calcule par
`best_day_since.period_record_flags`) recoit sa card individuelle, postee
pendant la collecte (priorite 0), en contournant TOUS les caps. Nouveau
predicat `_is_unconditional_best_day(row)` dans `post_best_day_since_twitter.py` :

- replie dans `_is_priority_best_day_since` (donc traite aussi comme priority
  partout ou ce dernier est teste).
- `_post_single_track_early` : `is_unconditional` fait sauter le cap total
  `EARLY_BEST_DAY_EXCEPTIONAL_MAX_POSTS = 5`, le cap par ere
  `EARLY_BEST_DAY_MAX_POSTS_PER_ERA = 1`, le cap par album, et le gate de
  score (`needs_score = not is_unconditional and (...)`). Retourne
  `"posted_unconditional"` -> **exit code 2** (nouveau ; `main()` mappe 0/2/3/1).
- `best_day_since.best_day_since_for_track(keep_year_record=True)` : renvoie la
  row meme si `passes_filters` echoue (ex. `days_since` < `min_days`). Le
  early lane passe ce flag.
- `_pick_rows` / `_validated_song_rows_for_post` : les rows unconditional ne
  comptent pas dans le cap quotidien et ne sont jamais tronquees par `limit`.
- `finalize_update.ReadyBestDaySincePoster` : `_done()` ne s'arrete plus au
  cap `max_posts` (scanne tous les tracks surveillES) ; exit 2 traite comme
  un post normal (spacing + exclude).
- `update_streams.build_priority_best_day_track_ids` : ajoute les tracks dont
  la veille est deja a >= `EARLY_BEST_DAY_YEAR_RECORD_WATCH_RATIO` (0.80) du
  pic de l'annee ET >= `EARLY_BEST_DAY_YEAR_RECORD_MIN_DAILY` (20k), plafonne
  a `EARLY_BEST_DAY_YEAR_RECORD_WATCH_LIMIT` (50). Watchlist standard passee a
  `EARLY_BEST_DAY_TRACK_LIMIT = 100`, floor `EARLY_BEST_DAY_WATCHLIST_MIN_DAILY_STREAMS = 20_000`.

Limite connue : un record annuel sur une chanson < 20k/j n'est pas surveille
en early (pas de spawn subprocess pour lui pendant la collecte) mais reste
poste individuellement et sans cap dans le batch best-day-since de finalize.

## Batch best-day-since finalize : 3 standard / 5 max (2026-09-03)

Decision proprietaire : le batch best-day-since de `finalize_update`
(`post_best_day_since_twitter.py` sans `--only-track`) ne poste
plus jusqu'a 10 cards chanson par jour mais **3 standard, 5 maximum**. Aligne
sur la voie early (deja `EARLY_BEST_DAY_STANDARD_MAX_POSTS = 3` ->
`EARLY_BEST_DAY_EXCEPTIONAL_MAX_POSTS = 5`, inchangee).

- `POST_COLLECTION_MAX_SONG_POSTS` : 10 -> **5** (aussi le defaut de `--limit`).
- Nouveau `POST_COLLECTION_STANDARD_SONG_POSTS = 3`.
- `_validated_song_rows_for_post(..., standard_limit=3, min_days=...)` : au-dela
  de 3 rows "capped", les slots 4-5 ne sont accordes qu'a un record
  **exceptionnel** — `_is_priority_best_day_since(row)` (ecart >90 j) OU score
  `score_best_day_since.score_best_day_since` (batch, pas single) >=
  `EARLY_BEST_DAY_EXCEPTIONAL_MIN_SCORE` (90). Le score batch est calcule une
  seule fois (map `{track_id: score}` lazy) — jamais par row ; un echec de
  scoring n'empeche jamais un post (les candidats slots 4-5 sont alors traites
  comme non-exceptionnels).
- Inchange : `_is_priority_best_day_since` (>90 j) et `_is_unconditional_best_day`
  (biggest day of the year) contournent toujours entierement le cap et ne
  comptent pas dedans.

## Bonus "stature" (chanson majeure) dans le score best-day-since (2026-09-03)

Probleme : une chanson phare (All Too Well (Taylor's Version), Blank Space...)
qui bat un record best-day-since vieux de plusieurs mois avec un mouvement
jour/jour modeste scorait sous 58 et ratait la voie early (postee seulement
dans le batch finalize). Le proprietaire veut ces titres en early.

Fix dans `score_best_day_since.py` : nouveau `_stature_bonus(metrics)` ajoute
au dict `score_adjustments` des DEUX scorers (single-candidate early ET batch).

- Base : `max(daily_streams du jour, expected_daily baseline)` — capte "grosse
  chanson" meme un jour plus calme ; une petite chanson sur un spike rare n'est
  pas touchee.
- Rampe log : 0 sous `STATURE_BONUS_MIN_SCALE` (150k), montee vers
  `STATURE_BONUS_MAX` (14.0) a `STATURE_BONUS_FULL_SCALE` (1.2M).
- Ex. verifie 2026-09-01 : champagne problems 62 -> 75 (passe le gate early 58),
  Back To December 66 -> 76, my tears ricochet 34 -> 46 (reste dehors, jour
  +0.7% faible — correct). Le gate `EARLY_BEST_DAY_MIN_SCORE`/`--early-min-score`
  (58) n'a PAS bouge — seul le score monte.
- Explication ajoutee dans `_explanations` : "major song clearing a
  long-standing best day".

## Blocage saisonnier Holiday Collection etendu aux cards chanson (2026-09-03)

Avant : `generate_album_update_image.holiday_collection_post_block_reason` (hors
saison 25 nov-7 jan) n'etait teste QUE pour la card best-day-since **album**.
Les cards **chanson** d'un titre de "The Taylor Swift Holiday Collection"
(Last Christmas, Santa Baby, White Christmas, Silent Night, Christmas Tree
Farm...) partaient hors saison — dont "Christmas Tree Farm" en early le
2026-09-01 comme biggest day of the year.

Decision proprietaire : **le blocage saisonnier bat toutes les autres regles,
y compris "biggest day of the year = inconditionnel"**. Hors saison, AUCUN
contenu best-day-since d'un titre Holiday Collection ne sort.

- Nouveau helper `is_holiday_collection_season(target_date)` (wrapper public de
  `_is_holiday_collection_season`) dans `generate_album_update_image.py`.
- `post_best_day_since_twitter._holiday_collection_out_of_season(album, date)` :
  `is_holiday_collection_album(album) and not is_holiday_collection_season(date)`.
- Applique dans `_find_all_rows` + `_find_recap_rows` (couvre le batch ET le
  recap) et dans `_post_single_track_early` (voie early + grower). Verifie
  2026-09-01 : 0 row Holiday Collection dans le recap.
- En saison : rien ne change, les titres repassent par les gates normaux.
- Hors scope : "Christmas Tree Farm" est bien dans l'album Holiday Collection
  (donc couvert). Les autres singles de Noel hors album (ex. throwbacks) ne le
  sont pas — a rediscuter si besoin.

## Best-day-since ALBUM : plus de card separee, fondu dans la caption (2026-09-03)

Decision proprietaire : **plus aucune card best-day-since album independante.**
Quand l'album total decroche un best-day, on **reecrit la premiere ligne de sa
card update quotidienne** au lieu de poster une 2e card.

- `generate_album_update_image._build_album_post_text` (base) : nouveau helper
  `_album_best_day_row(album_name, canonical_name, target_date)` — memes regles
  que l'ancienne card (`compute_album_best_day_since`, >= 2 tracks
  standard-edition, `passes_filters(min_days=ALBUM_BEST_DAY_MIN_DAYS=30)` OU
  `is_biggest_day_of_year` OU `best_ever`). Si un row qualifie :
  `📈| "X" earned its <LABEL> with N streams on <date>.<pct>` (label via
  `_best_day_post_label`, tous types + suffixe `(combined)`), sinon la ligne
  `received N streams` inchangee. Jamais applique le jour anniversaire TTPD.
- **"has once again earned its ..."** : `best_day_since.is_recent_repeat_record(row)`
  (`RECENT_REPEAT_RECORD_DAYS = 60`) — `kind == "since"` et jour battu a <= 60 j
  (deux grosses journees rapprochees, jamais consecutives : `compute_best_day_since`
  refuse deja un jour battu <= 1 j). Verbe `has once again earned` (album +
  chanson `best_day_since_tweet(repeat=...)`) / `once again earned` /
  `once again had its` (note best-day chanson de la card album). `best_ever` =
  jamais un repeat.
- Consequence assumee : **pas de best-day album le week-end** (les cards album
  ne postent pas sam/dim).
- Supprime : `post_best_day_since_twitter` `--only-album` / `--album-limit` /
  `--album-min-days` / `--no-albums`, `_pick_album_rows`, `_album_row`,
  `_build_album_best_day_tweet`, `_generate_album_best_day_since_image`,
  `_post_album_best_day_rows`, `_album_best_day_lock_path` /
  `_write_album_best_day_lock`, `ALBUM_BEST_DAY_MIN_DAYS` (deplace dans
  `generate_album_update_image`). `finalize_update.ReadyAlbumBestDaySincePoster`
  -> `ReadyEraRecapPoster` (ne fait plus que la card recap par ere ; l'early
  album update card reste `ReadyAlbumUpdatePoster`, weekday-only). Cote
  `update_streams` : `album_best_day_since_poster` -> `era_recap_poster`,
  `stop()` ne renvoie plus rien.
- `_exceptional_primary_albums` (finalize) garde `compute_album_best_day_since`
  pour **prioriser l'ordre** des cards album early — c'est desormais la seule
  mise en avant d'un best-day album.

## Recap best-day-since : header fixe "all eras", plus de theme d'album (2026-09-03)

Le recap best-day-since (`_generate_recap_image` / `_generate_recap_images`)
n'a plus de theming par album. Avant : `_album_recap_theme` choisissait un
album quand > 25 % de ses tracks battaient un record (`ALBUM_RECAP_THEME_THRESHOLD_RATIO`),
ce qui pilotait le header (dossier de headers de l'album), le titre
(`{album} - Best Day Recap`) et l'override light Holiday Collection. Fonction +
constante supprimees.

Desormais, chaque jour :
- **Header fixe** = `collectors/spotify/streams/tools/headers/best_day_recap/all-eras.jpg`
  (constante `RECAP_HEADERS_DIR`) — la bande "all eras" (2200x254) construite a
  partir des portraits du site officiel Eras Tour, un panel par ere dans
  l'ordre des albums. Le dossier ne contient que cette image, donc
  `pick_header_image` la renvoie toujours. Source dans `assets/eras-tour-hero/all-eras.jpg`
  (copie du build frontend `tsm-frontend/scripts/build_eras_strip.py` ->
  `tsm-frontend/frontend/public/headers/all-eras.jpg` ; si les panels changent,
  re-copier depuis le frontend). Fichiers `.jpg` de `tools/headers/**` deja
  suivis par git.
- **Masthead "BEST DAY"** toujours actif (avant : seulement en mode theme).
- **Titre** toujours `Best Day Since - Full Recap`.
- **Thème** = `masthead_theme_for_date(target_date)` (light lun-ven / dark
  sam-dim) — plus d'override Holiday Collection (le theme d'ere n'existe plus).
- Nom de fichier de sortie : `best_day_since_recap[_partXofY]_{date}.png` (plus
  de suffixe `_{slug}`).

## Card recap best-day-since PAR ERE (2026-09-03)

En plus du recap **global** (header fixe ci-dessus), une **card recap dediee a
une ere** part quand cette ere a une grosse journee :

- **Declencheur** : `>= ERA_RECAP_MIN_SONGS = 5` chansons d'une meme **ere**
  (`best_day_since.era_key` : Red + Red (TV), 1989 + 1989 (TV), Midnights +
  3am/Til Dawn... comptent ensemble) qui, le meme jour, decrochent un
  best-day-since **et** passent le gate de post individuel (daily >= 80k, ou
  jour/jour > +10 %, ou ecart > 60 j, ou biggest day of the year).
  `best_day_since.era_recap_groups(...)`.
- **Timing** : postee **avant la card album update de l'ere**. En finalize
  (`_post_era_recaps_batch`, sans cap), OU tot pendant la collecte via
  `post_best_day_since_twitter.py --only-era-recap ALBUM` (exit 0 poste / 3 pas
  encore 5 chansons / 1 erreur), declenche par `ReadyEraRecapPoster`
  (une fois par ere, `_era_recap_checked`). **Cap early = 2**
  (`EARLY_ERA_RECAP_MAX_POSTS`), **ne consomme pas** le quota chanson early.
- **Header = theme de l'ere** : `_generate_recap_image(era_display=...)` reprend
  la logique de l'ancien `_album_recap_theme` (pool de headers de l'album via
  `generate_album_update_image.header_images_for_album`, masthead "BEST DAY",
  titre `{Ere} - Best Day Recap`, override light Holiday Collection).
  **Piege corrige (2026-09-03)** : `header_images_for_album(...)` filtre bien la
  liste par album, mais le code ne s'en servait que pour deriver un
  `headers_dir` (`album_headers[0].parent`) passe a `build_table_html`, qui
  rappelle `tables_image.pick_header_image(headers_dir)` — une fonction
  generique qui **re-scanne tout le dossier** au hasard. Pour la plupart des
  albums (structure "plate" heritee, tous les fichiers directement sous
  `db/discography/headers/`, pas de sous-dossier par album — seuls
  `reputation/` et `the life of a showgirl/` en ont un), `headers_dir`
  pointait sur la racine **partagee par tous les albums** : une card recap
  "Red" pouvait afficher le header d'une autre ere (ex. Debut/`taylor
  swift.png`) au hasard. Fix : `build_table_html` accepte maintenant un
  `header_image: Path | None` explicite (bypass le re-scan) ;
  `_generate_recap_image` le renseigne via le picker deja correctement scope
  `generate_album_update_image.pick_header_image(era_display, masthead_theme)`
  au lieu de compter sur `headers_dir` seul. Reflexe pour toute nouvelle card
  qui reutilise `build_table_html(headers_dir=...)` avec une intention
  "scopee a un sous-ensemble" : verifier que `headers_dir` n'est pas en fait
  un dossier partage avec d'autres contenus non voulus.
- **Additive** : les chansons restent dans le recap global.
- **Suppression** : une fois la card d'une ere postee (lock
  `best_day_since_era_recap_locks/{slug}.lock`), **aucune card best-day-since
  chanson individuelle de cette ere** ce jour-la (`_era_recap_posted_for` dans
  `_find_all_rows` + `_post_single_track_early`, growers inclus) — **sauf** un
  biggest day of the year (carte inconditionnelle maintenue, decision owner).
- `--no-era-recap` pour sauter.

## Marqueur ★ since sur Top Songs / Top Eras / GAINERS (2026-09-03)

Ces 3 cards ledger affichent le marqueur `★ Titre · since <date longue>` (ou
`· of the year` / `· of the month`) dans la colonne Track/Album, **comme les
images d'album update**. Helper `comp.tables_image.ledger_name_with_best_day` +
`best_day_since.best_day_marker_text(row)` / `best_day_marker_labels(track_ids,
date)`. Top Songs / GAINERS = record **solo** par track (records combined non
affiches, meme regle que `_best_day_labels_for_sections`) ; Top Eras = record
**combined** de l'ere (`compute_album_best_day_since` par `era_key`). Lookup
toujours en try/except : jamais bloquant (regle data #5). Pas de filtre
saisonnier Holiday Collection ici (parite avec l'image d'album).

## best_day_since.json enrichi + push R2 early (2026-09-03)

- `export_for_web.export_best_day_since()` ecrit en plus `era_recaps` (memes
  groupes que la card par ere) et `all_items` (tous les records du jour, sans
  gate `min_days`) dans `data/best_day_since.json`.
- `update_streams._upload_best_day_since_list(stats_date)` pousse ce seul
  fichier vers R2 (`Key="data/best_day_since.json"`) **pendant la collecte** :
  callback `on_post` des watchers best-day (`ReadyBestDaySincePoster` /
  `ReadyEraRecapPoster`) + une fois apres `.stop()`. Best effort,
  jamais bloquant, independant du lock d'export R2 complet.

## Score par album pour l'ordre de post (2026-08-29)

`tools/scripts/score_album_update.py` — nouveau module read-only, calque sur
`score_best_day_since.py` (il reutilise ses helpers `_daily_on`,
`_rarity_value`, `_grower_value`, `_pct_score`, `_gain_score`,
`_log_age_score`, `_pct_change`, les bonus `BIGGEST_DAY_OF_YEAR_BONUS` /
`BEST_EVER_BONUS`).

- Serie daily de l'album = somme par jour des dailies de ses tracks
  standard-edition (`best_day_since.combine_points`, meme garde de span
  complet que `compute_album_best_day_since`).
- Sous-scores (memes poids-esque que le scorer chanson, adaptes) : `age`
  (`days_since` du best-day album), `daily_abs_gain` (cap 5M),
  `daily_pct_gain`, `weekly_pct_gain`, `rarity` (MAD sur 90j),
  `grower` (streak + acceleration). `base_score = somme ponderee * 100`.
- Bonus : biggest day of the year album (+18), best ever album (+8),
  majorite de tracks en hausse d-o-d (jusqu'a +6, proportionnel a
  `(ratio-0.5)*2`), nombre de tracks de l'album avec un best-day record ce
  jour (jusqu'a +8, log). **Pas de penalite de fraicheur** (tous les albums
  postent chaque jour, rien a faire tourner).
- API : `score_albums(names, target: date)` -> liste triee, `rank_albums(names,
  target_date: str)` -> liste de noms best-first (ne leve jamais). Cache
  process-local : `_history()`, `_RECORD_IDS_CACHE`, `_SCORE_CACHE`,
  `_ALBUM_TRACK_IDS_CACHE` — `finalize_update` score les albums plusieurs fois
  par run (primary / extra / all-albums), le 1er appel paie ~35-40s
  (`_album_record_track_ids` scanne les ~700 tracks + `load_album_sections`
  x17), les suivants sont instantanes.
- Cablage : `finalize_update._rank_albums_for_posting(albums, stats_date)`
  wrappe `score_albums` avec fallback tri par `_album_daily_total` si ca
  echoue (l'ordre ne doit jamais bloquer un post). Applique dans
  `_post_all_albums` (sweep), `_post_album_updates` scope `extra` et `primary`,
  et la boucle primary de `run_final_update_tasks`.

## Bug fixe : badge "of the year" invisible dans l'update album (2026-08-22)

`generate_album_update_image.py::_best_day_labels_for_sections` et
`_best_day_rows_for_sections` excluaient les rows avec
`is_biggest_day_of_year=True` (`and not row.get("is_biggest_day_of_year")`),
alors que `_format_best_day_marker_label` / `_best_day_post_label` savent
deja afficher "of the year" pour ce cas — code mort depuis son ajout
(commit `c2aff8e1f`, 2026-08-12), donc aucun morceau ayant son "best day of
the year" n'affichait jamais l'etoile/colonne ni la mention dans la caption
de l'update album, meme quand ce record etait poste en card individuelle.
Repere sur "I Knew You Were Trouble." (Red) le 2026-08-20 :
`is_biggest_day_of_year=True`, `best_day_since=2025-08-30`, poste en card
mais absent de la table Red. Exclusion supprimee dans les deux fonctions ;
`passes_filters` (min_days=30 pour un kind="since") reste le seul gate.

## Cover manquante dans l'album update — fix fetch resilient (2026-08-29)

La petite vignette `.hdr-cover` de `generate_album_update_image.py` etait
telechargee via un `_url_to_data_uri` local (cache memoire seul, 0 retry,
`except -> ""`). Un seul timeout reseau au moment du rendu -> placeholder vert
vide (`.hdr-cover-ph`), alors que le visuel d'en-tete (fichier local
`db/discography/headers/`) s'affichait normalement. Observe : *Speak Now
(Taylor's Version)* le 2026-08-26.

Fix : `_url_to_data_uri` delegue maintenant a `comp.img_fetch.fetch_data_uri`
(cache disque persistant `db/discography/.image_cache/` + 3 retries + fallback
tailles CDN Spotify). Une cover fetchee une fois avec succes n'est plus jamais
re-telechargee. `load_cover_url` (covers.json -> image_url album) est inchange —
ce n'etait pas un probleme de resolution d'URL. Detail -> skill `image-gen`
§ "Fetch d'image resilient".

## Update album "table dark/light" : Showgirl + reputation (2026-08-27)

`generate_album_update_image.py` a un style de table alternatif optionnel
(`TABLE_DARK_CSS`, `_table_dark_theme(album_name, header_accent, variant)`,
suffixe fichier `_update_table_dark.png` / `_update_table_light.png`). Les
albums listes dans `TABLE_DARK_DEFAULT_ALBUMS` (casefold) prennent
automatiquement le variant **dark** quand `style == "default"`
(`effective_album_update_style`). Contenu actuel : `the life of a showgirl`,
`reputation`.

- **Styles CLI** : `--style=table-dark` (defaut pour ces albums),
  `--style=table-light`. Le variant light passe `variant="light"` a
  `_table_dark_theme`. Seuls `reputation` et `tortured poets` ont un vrai bloc
  de palette light dedie (`if variant == "light" and key == ...`) ; les autres
  albums retombent sur leur palette unique (souvent deja claire).
- **Palettes reputation** : dark = fond quasi-noir `#080808` ; light = blanc
  `#f1f1f1` / panneaux gris `#e8e8e8`. Les deux : titre blackletter
  (`'Old English Text MT','UnifrakturCook','Cloister Black',Georgia,serif`),
  hero `grayscale(1)`, accent gris (`MONOCHROME_ALBUM_ACCENTS` force `#6b6b6b`
  comme accent header).
- **Header par variant** : `db/discography/headers/preferences.json`. La valeur
  d'un album peut etre une string (tous variants) OU un objet
  `{"dark": "...", "light": "..."}`. `reputation` utilise l'objet :
  `71bed3bf...png` (photo N&B) en dark, `a4666c26...png` (marbre + citation
  blackletter, deja recadree pour que la citation reste dans la bande haute)
  en light. `_preferred_header_for_album(album, variant)` /
  `pick_header_image(album, variant)` lisent ce variant ; `generate()` calcule
  `header_variant` depuis `style`.
- Les fichiers image sont gitignore (`*.png`/`*.jpg` global) ; ceux de
  reputation ont ete force-add (`git add -f`) pour survivre a un reset
  machine, comme les headers d'ere top-niveau (`folklore.png`...).
- `--style=table-light` / `table-dark` ne sont **pas** postables (`main()`
  bloque : "Experimental styles cannot be posted directly") — le post daily
  auto utilise toujours `style="default"` -> dark. Le light est une
  generation manuelle.
- Pour ajouter une ere : nom casefold dans `TABLE_DARK_DEFAULT_ALBUMS`, bloc
  `elif key ==` (et `if variant == "light" and key ==` si besoin d'un light
  distinct) dans `_table_dark_theme`, header(s) + entree `preferences.json`.

## Fusion catalogue Spotify active (dedup dynamique)

Depuis 2026-08-21 : Spotify fusionne parfois deux track_id du catalogue en un
seul total affiche (constate le 2026-08-17 sur ~20 track_id : Karma / Karma
feat. Ice Spice, Shake It Off / Best Work Edition, Love Story / Pop Mix,
Our Song / International Mix, Long Live / feat. Paula Fernandes, etc.), puis
annule parfois cette fusion quelques jours plus tard — sans prevenir, et de
facon instable (certaines paires restent fusionnees des jours durant,
d'autres redivergent). Traiter ca comme un vrai evenement catalogue Spotify,
jamais comme une donnee a "corriger" vers un chiffre invente (cf. memoire
`spotify-streams-0817-corruption`).

`history_store.pick_active_catalog_merge_losers(totals_by_track_id,
track_meta_by_id)` detecte dynamiquement, pour UNE date donnee, les track_id
dont le total exact est identique a un autre track_id actif (garde-fou
`MIN_TOTAL_FOR_MERGE_DETECTION = 1_000_000` : en dessous, des extras a tres
faible volume type karaoke/instrumental peuvent coincider par pur hasard —
observe le 2026-08-21 sur plusieurs bonus tracks Red dans la fourchette
100k-150k). Garde le track non-extra (ou le plus "reel"), marque l'autre
comme "loser" pour cette date uniquement. Le CSV brut n'est pas modifie; en
revanche l'export web compact `history/YYYY-MM-DD.json` marque ces points
avec `m: true` pour que le frontend/API dedoublent les anciennes dates. Si
Spotify redivise les totaux le lendemain, les deux redeviennent normaux
automatiquement, sans intervention manuelle.

Cablage actuel :
- `export_for_web.py` : les "losers" du jour sont exclus de `rank_total`/
  `rank_daily` (`add_ranks(..., exclude_from_rank=...)`) — la chanson reste
  visible sur sa propre page/section d'album avec son vrai total, elle
  n'occupe juste pas une 2e place dans le classement du site.
- `post_gainer_thread.py::_pick_gainers` : les "losers" du jour sont retires
  du pool de candidats avant selection du top gainers (couvre aussi
  `post_stream_highlights_thread.py`, qui reutilise `_pick_gainers`).
- `generate_streams_image.py::build_top_n` (l'image "Daily Streams #1-20"
  postee sur Twitter, `streams_image_*.png`) : ajoute le 2026-08-22 apres que
  le proprietaire ait remarque Shake It Off / Best Work Edition ET
  Love Story / Pop Mix encore doublonnes dans l'image du 2026-08-21 — le
  premier cablage (2026-08-21) ne couvrait que le site et les posts gainers,
  pas cette image. `build_top_n` filtre desormais `today_rows` via
  `_drop_active_catalog_merge_duplicates` avant dedup-par-titre/tri.
  `generate_weekend_streams_image.py` reutilise `build_top_n`, donc deja
  couvert. Reflexe : ce fichier avait deja eu un bug de chargeur de
  catalogue incomplet en 2026-08-08 (voir Pieges ci-dessous) — verifier ses
  fonctions en parallele de celles d'`export_for_web.py`/`history_store.py`
  avant de considerer une regle de classement comme entierement cablee.
- `export_for_web.py::enrich_albums_payload` et
  `generate_albums_image.py::build_album_rows` (2026-08-25) : les losers du
  jour sont aussi exclus des sommes publiques `total_streams_sum`/
  `daily_streams_sum` albums/eras et de l'image Top Eras. La row chanson
  reste visible avec son total Spotify brut, mais elle ne gonfle plus l'album
  ou l'era parent pendant une fusion active.
- `export_for_web.py` (2026-08-25) : les fichiers
  `runtime/exports/web/site/history/YYYY-MM-DD.json` portent aussi `m: true`
  sur les losers detectes pour la date exportee. Les pages frontend Albums,
  AlbumDetail, Streams/Top Songs, Image Studio et les APIs home/studio peuvent
  donc dedoubler les jours historiques sans inventer ni modifier de total.
- Image Top Eras / albums (2026-08-27) : les dailies negatifs issus d'un split
  post-fusion ne doivent jamais servir de baseline `vs last week`. Incident
  2026-08-25 : le J-7 etait 2026-08-18, avec sept lignes invalides
  negatives apres la fusion Spotify du 2026-08-17 (`Karma`, `Karma feat. Ice
  Spice`, `Mine`, `The Story Of Us`, `Long Live`, `The Joker And The Queen`,
  `Teardrops On My Guitar - Radio Single Remix`).
  Les totals 2026-08-18 ont ete conserves, mais `daily_streams` vide avec
  `estimated_reason=collection_incident_spotify_merge_2026-08-17_to_2026-08-18`.
  `generate_albums_image.build_album_rows(..., target_date=...)` propage
  desormais une comparaison album/era en `None` des qu'un track officiel sorti
  a une baseline manquante ou negative; le PNG affiche alors `--` dans la
  colonne concernee au lieu de sommer une valeur fausse. Les extras restent
  non bloquantes: elles sont additionnees seulement si leur daily est valide.
- `history_store.load_history_track_ids_with_daily_for_date` ne doit pas traiter
  un `admin_override` comme automatiquement complet si son `daily_streams` est
  vide ou negatif. Un override humain peut garder un total exact, mais il ne
  rend pas une daily negative publiable.

Si un nouveau generateur de classement/top N est ecrit, l'appeler aussi sur
les track_id candidats de ce generateur plutot que de dupliquer la logique.

### Fix a la racine du 2026-08-17 (2026-08-23)

Le dedup dynamique ci-dessus corrige les classements *courants* mais pas les
agregats sur periode (recap "most streamed" cote `tsm-frontend`, qui somme
les daily jour par jour) : le pic du 08-17 (reel, mais un transfert catalogue
ponctuel, pas du streaming organique) dominait n'importe quelle somme
periode/YoY contenant cette date — et ce pour les DEUX cotes de la paire, pas
juste le "loser" (verifie : le delta du "gagnant" ce jour-la correspond
grosso modo au total pre-fusion de l'autre cote, pas un vrai gain organique).
Cote frontend, `tsm-frontend/api/routes/period_recaps.py::_merge_affected_ids_for_day`
exclut desormais les deux cotes pour ce jour-la specifiquement (voir aussi
`KNOWN_ORPHAN_MERGE_DAYS` pour le cas "The Joker And The Queen (feat. Taylor
Swift)", fusionne avec l'original Ed Sheeran non tracke dans notre
catalogue).

Plutot que de continuer a patcher chaque consommateur un par un (classement,
image top20, recap periode, best-day-since, milestones, home highlights...),
fix a la racine dans `db/streams_history.csv` : pour les 20 track_id de
l'incident, `daily_streams` vide a la date `2026-08-17`,
`estimated_reason=collection_incident_spotify_merge_2026-08-17_to_2026-08-17`
(meme convention que l'incident folklore, voir regle `collection_incident_*`
plus haut) — **le total n'est jamais touche**. `export_for_web.py::normalize_daily_streams_from_totals`
respecte deja le prefixe `collection_incident_`, donc ce fix se propage tout
seul a tous les exports sans toucher chaque script individuellement.

**Piege decouvert en deployant ce fix** : regenerer l'export local
(`python collectors/spotify/streams/extras/export_for_web.py`) ne suffit PAS
a corriger la prod. Ce script appelle `scripts/r2.py` avec
`--skip-history-upload` par defaut (seul le "static"/`--streams-daily` est
uploade ; `history/{date}.json` et `history-by-track/{track_id}.json` ne le
sont JAMAIS via ce chemin). Pour republier une date historique corrigee, il
faut lancer `scripts/r2.py` a la main SANS `--skip-history-upload`, avec
`--new-date 2026-08-17` (ce flag ne restreint que l'upload static single-
date ; le rebuild `history-by-track` reconstruit toujours depuis TOUS les
fichiers locaux du dossier history, donc `--new-date` ne le limite pas — pas
destructif, juste plus lent). Verifier apres coup en lisant l'objet R2
directement (`boto3` via `scripts/r2.py::get_s3_client()`), pas seulement le
fichier local — le dry-run de `r2.py` ne fait pas de vraie comparaison de
hash (tout ressort "changed"), donc il ne prouve rien sur l'etat reel de R2.

**Suite le 2026-08-23 (meme jour) — le fix "a la racine" ne couvrait pas 3
generateurs d'image** : le proprietaire a demande de regenerer les images
top-songs du 08-17 au 08-21 (`post_streams_twitter.py <date> --no-post
--top-n 20`) pour verifier le fix visuellement. Premiere regeneration du
08-17 : "Teardrops On My Guitar - Radio Single Remix" ressortait quand meme
en #5 avec 14 742 858 streams/day, alors que la ligne CSV est bien vide
(`daily_streams=''`, `estimated_reason=collection_incident_spotify_merge_...`).
Cause : `generate_streams_image.py::load_history()`,
`generate_album_update_image.py::load_history_for_album()` et
`generate_albums_image.py::load_history()` ont chacun leur PROPRE fonction
`_fill_missing_daily()` dupliquee (3 copies quasi-identiques), qui recalcule
`daily_streams = total(J) - total(le plus proche J anterieur disponible)`
des que la colonne CSV est vide — sans jamais lire `estimated_reason`. Ces 3
generateurs ne passent PAS par `export_for_web.py::normalize_daily_streams_from_totals`
(qui lui respecte deja `collection_incident_*`/`manual_trusted`) : ils lisent
`db/streams_history.csv` directement avec leur propre loader. Donc un daily
intentionnellement vide pour un incident etait recalcule a la volee a partir
du total brut — exactement la valeur gonflee par la fusion qu'on venait de
planquer. Fix : ajout de `estimated_reason` a l'entree parsee dans les 3
loaders + garde `if reason == "manual_trusted" or reason.startswith("collection_incident_"): continue`
dans chaque `_fill_missing_daily` (et `_fill_missing_daily_from_latest` pour
`generate_streams_image.py`, qui a une 2e passe de fill via le row le plus
proche anterieur). Verifie par regeneration des 5 images 08-17→08-21 : plus
aucun track de l'incident dans le top 20, image du 08-17 confirmee visuellement
propre (top clairement domine par The Fate of Ophelia, Blank Space, etc., plus
aucune trace de Long Live/Our Song/Love Story/Teardrops fusionnes).
**Lecon (renforce la lecon du 2026-08-22 ci-dessus)** : dans ce repo, un
"fix a la racine" en base ne suffit QUE pour les consommateurs qui passent
par le chargeur canonique (`history_store.py` + `normalize_daily_streams_from_totals`
d'`export_for_web.py`). Tout script qui a son propre `csv.DictReader` +
son propre calcul de daily (grep `_fill_missing_daily\|_parse_optional_int`
sous `collectors/spotify/streams/`) doit etre audite/patche separement pour
respecter `estimated_reason`. Pas de refactor pour unifier ces loaders fait
ce jour-la (hors scope demande), juste le meme garde ajoute aux 3 copies.

## Probe non-extra uniquement (2026-08-24)

Le probe Spotify API en debut de run (`build_probe_tracks` /
`_probe_via_api`, `update_streams.py`) ne selectionne plus que des tracks
`chart_extra=False` (non-extra). Avant, l'echantillon melangeait 15
non-extra + 15 extra (`PROBE_EXTRA_SAMPLE_SIZE`, supprime) meme si seul le
compteur non-extra (`updated_non_extra_probes` >= `PROBE_REQUIRED_UPDATED`)
faisait demarrer le run complet — les extras probes ne servaient donc a
rien decisionnellement. `PROBE_SAMPLE_SIZE` vaut maintenant
`PROBE_NON_EXTRA_SAMPLE_SIZE` (15). Le fallback ChartSnapshot
(`probe_chartsnapshot_update`) n'est pas concerne : il ne selectionne pas de
tracks, il filtre juste les lignes non-extra dans le flux externe qu'il
recoit deja.

## Top eras + top songs poste aussi le week-end (2026-08-24)

Avant, le samedi/dimanche, `finalize_update.py` sautait les posts separes
"top eras" (`_post_albums_daily` / `post_albums_twitter.py`) et "top 20
songs" (`_post_streams_image` / `post_streams_twitter.py`), au motif que la
combined recap card du week-end (`post_weekend_streams_twitter.py`,
`_post_daily_recap_card`, toujours postee en premier) les couvrait deja.
Decision produit : ces deux posts doivent maintenant sortir aussi le
week-end, en plus de la recap combinee. Gardes weekend supprimees dans
`finalize_update.py` (`_post_streams_image`, `_post_albums_daily`) et dans
`post_albums_twitter.py::main` (meme garde dupliquee cote script). Les deux
posts restent dans l'ordre d'etapes existant (`top eras post` avant `top 20
songs post`, apres `daily recap card`/`weekend song gainers`), rien
d'autre n'a change dans l'ordonnancement. Ne pas confondre avec la regle
distincte "pas de cards album individuelles le week-end"
(`_post_album_updates`, toujours sautee le week-end — non concernee par ce
changement).

## Bug fixe : track chart_extra en erreur API bloquait tout le finalize (2026-08-24)

Incident 2026-08-23/24 : "Love Story - Pop Mix" (`5lA0yK8S5tP3xoaRMCp4Ug`,
`chart_extra=true`) a echoue en `status="error"` (`reason="api_error"`) a
chaque round de retry — probablement le meme phenomene de fusion catalogue
Spotify que la section "Fusion catalogue Spotify active" ci-dessus (le track
apparait aussi dans les 3 "merge losers" du jour cote export), mais cette
fois cote collecte : l'appel API echoue completement au lieu de renvoyer un
total identique a un autre track_id.

Consequence : les tracks `status="error"`/`"timeout"` sont ecrits dans
`summary["failed_results"]`, jamais dans `summary["results"]` — donc leur
slot dans `results[]` reste `None` et disparait de `filtered_results`
(`run_update`, `update_streams.py:2078`). `summary["all_done"]`
(`len(updated/skipped) >= total_tracks`, `update_streams.py:2092`) reste
`False` pour toujours des que cela arrive, alors que ces tracks ne sont pas
non plus comptes "pending" (donc invisibles dans `Pending (retry)` et dans
`blocking_pending_ids` — la boucle `while` de retry ne les revoit jamais, et
le message final affichait quand meme "All target tracks updated or
explicitly closed."). Resultat observe : `finalize_update.run_final_update_tasks`
imprime juste "Finalization stopped: streams collection is not complete."
(`finalize_update.py:1569`) et **saute tous les posts ET le commit git** —
export web sauf car lance en thread separe avant l'appel — sans aucun signal
clair dans les logs sur quel track en est la cause (il faut lire
`snapshots/spotify_streams/{date}/last_unfinished_updates.json` pour
l'identifier).

Fix (`update_streams.py`, juste apres "All target tracks updated or
explicitly closed.") : `summary["all_done"]` est force a `True` des que
`blocking_pending_ids` est vide, puisque ce dernier est deja le vrai
garde-fou de completude (il ignore volontairement `error`/`not_found`/
`timeout`, seul `pending` bloque). `all_done` doit rester synchronise avec
cette decision au lieu de la re-verifier avec une formule plus stricte qui
ne sait pas fermer un extra en erreur permanente. N'affecte pas la boucle de
guard non-extra (`missing_non_extra`, plus bas dans la fonction) qui reste
inchangee et continue de retenter indefiniment un non-extra manquant.

Reflexe a garder : si un run se termine avec "All target tracks updated or
explicitly closed." mais que rien n'est poste/commit, verifier
`last_unfinished_updates.json` de la date concernee pour un track en
`error`/`timeout` avant de suspecter autre chose.

## Top Songs : le header photo ne s'affichait jamais (2026-08-24)

`generate_streams_image.py::_render_html` (utilisee par `generate_thread_images`,
donc par le flux reel `post_streams_twitter.py`) rendait le HTML via
`page.set_content(html)` — la page reste alors sur `about:blank`. Chromium
bloque le chargement d'une ressource `file://` (le header CSS
`url('file:///...')` choisi par `pick_header_image`/`_headers_dir_for_top_songs`)
depuis une page qui n'est pas elle-meme sur une origine `file://` : l'image de
fond echouait silencieusement, ne laissant que l'overlay noir semi-transparent
sur le fond clair du body — d'ou le bandeau "top songs" plat gris observe en
prod, quelle que soit la photo presente dans `tools/headers/top_songs/`
(`all eras.png` ou toute autre). Repere en comparant avec Top Eras
(`generate_albums_image.py`), qui affichait bien sa photo car il ecrit deja le
HTML dans un fichier temporaire puis `page.goto(f"file:///{tmp.as_posix()}")`
avant de screenshot. Fix : meme pattern applique a `_render_html` (ecrit un
`.tmp.html` a cote du PNG de sortie, `goto` dessus, supprime le tmp apres).
Verifie par regeneration reelle de `post_streams_twitter.py --top-n 20`.
Reflexe si un futur generateur affiche un bandeau plat/sans photo : verifier
`set_content` vs `goto(file://...)`, pas la photo elle-meme — les autres
generateurs a header photo local (`generate_weekend_streams_image.py`,
`post_stream_highlights_thread.py`, les generateurs de charts) utilisaient
deja le pattern sur, seul celui-ci avait la regression.

## Header "Editorial Masthead" pour Top Songs / Top Eras (2026-08-25)

`generate_streams_image.py::build_html` et `generate_albums_image.py::build_html`
passent maintenant `masthead_word="SONGS"` / `"ERAS"` a
`build_table_html` (`collectors/comp/tables_image.py`). Ca active un style de
header alternatif (opt-in, n'affecte aucun autre appelant de
`build_table_html` — Apple Music `generate_snapshot_images.py`,
`post_song_overtakes.py` continuent avec le header classique par defaut) :
la photo du pool `headers_dir` reste le fond, mais avec un gros wordmark
fantome ("SONGS"/"ERAS", police Google Fonts "Big Shoulders Display",
`mix-blend-mode:overlay`, `rgba(255,255,255,.5)`) plaque a droite du bandeau,
et un filet de 1px en bas colore par `get_dominant_color(header_img)` (meme
couleur que le handle @). Design valide via artifact (mockups "Chart Ribbon"
/ "Cover Mosaic" / "Editorial Masthead"), iterations d'opacite/position
faites en direct sur l'artifact avant portage ici. Verifie par regeneration
reelle `generate_streams_image.generate('2026-08-23')` et
`generate_albums_image.generate('2026-08-23')` — PNG inspectes visuellement.

Reflexe : `build_table_html` charge la police via un `<link>` Google Fonts
uniquement quand `masthead_word` est fourni (aucune autre card du pipeline ne
depend d'une police externe) — si le rendu tourne sans reseau, le wordmark
retombe sur `sans-serif` sans casser le reste de l'image (degrade gracieux,
pas bloquant).

Ajustement le meme jour : bandeau agrandi (`.hdr.masthead` 118px -> 168px,
`.mast-word` font-size assorti) suite a retour "trop petit". Les 3 photos de
header utilisees par ce style (`top_songs/all eras.png`,
`top_albums/5cd68095cd9c82411ef8ecdeedae73e9.png`,
`top_eras/5cd68095cd9c82411ef8ecdeedae73e9.png` — les deux dernieres sont des
copies identiques du meme fichier) ont ete upscalees vers 3840px de large
(Lanczos, `Image.LANCZOS`, ratio conserve) car sources natives a 2212x608 :
une IA d'upscale (4KAgent / Real-ESRGAN) a ete evaluee mais jugee disproportionnee
pour une simple photo de fond decorative (basicsr casse sous Python 3.13,
poids de plusieurs GB, GPU/CUDA a installer) — Lanczos suffit largement a ce
niveau d'opacite/blend. Reflexe : si une future photo de `tools/headers/**`
parait floue apres agrandissement du bandeau, verifier sa resolution native
(`PIL.Image.open(p).size`) avant de suspecter le CSS — `pick_header_image`
ne fait aucun redimensionnement, la nettete depend entierement du fichier
source.

## Table "Ledger" (dark/light) pour Top Songs / Top Eras (2026-08-25)

Suite au masthead, le corps du tableau (lignes) a aussi ete refait pour ne
plus utiliser le style "glassmorphism" classique (`build_table_html(...)`
sans `masthead_word`), toujours reserve a Apple Music
(`generate_snapshot_images.py`) et `post_song_overtakes.py`. Quand
`masthead_word` est fourni, `build_table_html` bascule aussi les lignes/
colonnes/footer vers un nouveau style "ledger" (`comp/tables_image.py`,
classes `.ledger-*`) :

- **Theme** : `masthead_theme="dark"` ou `"light"` — bascule toutes les
  couleurs (fond, texte, bordures, +/- vert/rouge) ET l'overlay du header
  (assombrissant en dark, eclaircissant vers blanc en light) ET la couleur de
  base du wordmark fantome (blanc en dark, sombre en light) via
  `_LEDGER_THEME_TOKENS` — pas de media query, chaque PNG est genere une fois
  pour un theme donne. **Depuis 2026-08-26, branche selon le jour** :
  `build_html(masthead_theme=None)` appelle
  `comp.tables_image.masthead_theme_for_date(target_date)` -> `"light"`
  lun-ven, `"dark"` sam/dim. Passer `masthead_theme=` explicitement force
  encore un theme (tests, overrides d'ere). Voir la sous-section
  "Thème selon le jour" plus bas.
- **Palette light = blanc propre** (2026-08-26) : `_LEDGER_THEME_TOKENS["light"]`
  est passe du beige `#f6f1ea` a un blanc `#ffffff` (panneaux `#f4f6f8`, texte
  `#1a1d24`, vert/rouge `#067647`/`#b42318`) — demande proprietaire « blanc
  light theme ». Le beige n'existe plus nulle part.
- **Rang colore par ere** : `era_accent_color(album)` (dict fige
  `ERA_ACCENT_COLORS`, initialement calque sur les accents de
  `tsm-frontend/frontend/src/utils/anniversaries.js` puis corrige a la main
  le 2026-08-25 sur 3 entrees — le proprietaire a juge `1989` trop grisatre
  (repasse en vrai bleu ciel `#4fb8e8`), `The Life of a Showgirl` trop jaune
  (repasse en orange `#e2712c`, la couleur "showgirl" du frontend est un
  gold `#c9a227` qui ne convenait pas ici) et `THE TORTURED POETS DEPARTMENT`
  pas assez beige (`#d4c3a3`) — **ces 3 valeurs divergent volontairement du
  frontend, ne pas les re-synchroniser aveuglement s'il change** ; le reste
  du dict suit toujours `anniversaries.js`) donne au gros chiffre de rang
  (police "Big Shoulders Display", meme famille que le wordmark du header)
  la couleur de l'album/ere de la ligne, **peu importe le rang du jour**
  (ex: `1989` est toujours bleu ciel que ce soit #1 ou #7). Si l'album n'a
  pas de couleur figee (single hors-catalogue type "I Knew It, I Knew You"
  de Toy Story 5, Holiday Collection...), fallback sur
  `dominant_color_from_data_uri(cover)` —
  extraction de couleur dominante depuis la cover deja telechargee en cache
  (pas de nouvel appel reseau, reutilise `_dominant_color_from_image`
  partagee avec `get_dominant_color`). **Pas de traitement special pour le
  rang #1** (l'ancien fond "gold" + `.row-gold` a ete retire sur demande —
  la seule distinction visuelle du #1 est desormais sa couleur d'ere, comme
  toutes les autres lignes).
- **Piege corrige** : `.ledger-rank` doit avoir
  `display:flex;align-items:center;justify-content:center` — sans ça le
  chiffre de rang colle a gauche de sa colonne pendant que le badge +/- reste
  centre a cote, ce qui cree un grand espace vide qui a l'air d'un bug de
  layout alors que c'est juste un `text-align` par defaut jamais force.
- **Titre sur 2 lignes** : `.ledger-name` utilise `-webkit-line-clamp:2` (pas
  de troncature ellipsis 1 ligne comme l'ancien `.entity-name`) — un titre
  long (ex. "All Too Well (10 Minute Version) (Taylor's Version) (From The
  Vault)") passe sur 2 lignes et reste centre verticalement dans la ligne
  puisque `.ledger-row` garde `align-items:center` sans hauteur fixe. Ne pas
  redonner de `height` fixe a `.ledger-row` (l'ancien `_ALBUMS_EXTRA_CSS`
  avait `height:48px`, retire) sinon un titre sur 2 lignes se fait tronquer.
- **Daily signe** : la colonne DAILY affiche `+1 917 692` (jamais juste
  `1 917 692`) via `comp/fmt.py::fmt_signed` (nouveau, calque sur le
  `fmt_signed` deja existant dans `generate_album_update_image.py` mais copie
  independante — ne pas dedupliquer avec ce fichier, il a son propre
  `fmt_num` a espacement different et est sensible aux regressions de largeur
  de colonne, cf. [[tsm-image-numeric-column-overflow]]).
- **Titres de colonnes blancs (dark)** : `--ledger-col-label` (nouveau token,
  distinct de `--ledger-faint` bien que meme valeur cote light) — dark =
  `#ffffff`, light = `#8a7c68`. Ne pas re-fusionner avec `--ledger-faint` :
  le proprietaire a explicitement demande le blanc pour les libelles de
  colonnes (RANK/+/-/TRACK/...) en dark alors que `--ledger-faint` reste
  sombre ailleurs (dates, "=" neutre).
- **Logo header** : `.mast-logo-badge` — cercle blanc de 38px contenant le
  logo Spotify a 19px (icone forcee en `fill:#161616` via
  `.mast-logo-badge .hdr-logo path`), remplace le gros logo blanc plein
  (64px) de l'ancien header classique. Uniquement actif quand
  `masthead_word` est fourni.

## Carte recap quotidienne "Masthead" + monthly listeners / followers (2026-08-26)

`generate_weekend_streams_image.py` (nom historique) genere la carte
one-card postee **chaque jour** par `post_weekend_streams_twitter.py`
(appelee par `finalize_update._post_daily_recap_card`, `--force-weekday`
ajoute en semaine, lock `weekend_streams_posted.lock`). Elle a ete refaite
en design **"Masthead"** — le meme parti-pris editorial que le header Top
Songs / Top Eras (cf. section ci-dessus) mais sur toute la carte :

- **Header** : `_masthead_header_style()` (local, `_header_style` reste
  inchange pour throwback/debut) utilise **la meme image que le post Top
  Eras standalone** — le pool partage `headers/top_eras/` via
  `generate_albums_image._headers_dir_for_top_eras()` + `pick_header_image`,
  PAS une photo era-specifique (choix proprietaire 2026-08-26 : « dans le
  header on met plutot l'image de top eras comme d'hab »). Overlay
  directionnel **selon le theme** (`_MH_THEME[theme]["mh-head-overlay"]` :
  quasi-noir `rgba(9,10,13,.88->.58->.74)` en dark, quasi-blanc
  `rgba(250,250,251,.90->.60->.80)` en light), `_masthead_header_style(theme)`
  ; `accent` = `get_dominant_color(header_img)`. Par-dessus : **badge logo
  Spotify** (`.mh-logo`, cercle `--mh-logo-bg` 42px, glyphe `SPOTIFY_SVG`
  force en `--mh-logo-fill` — blanc/logo-sombre en dark, sombre/logo-blanc en
  light), wordmark fantome `STREAMS` (Google Fonts "Big Shoulders Display",
  `--mh-ghost`) et filet 3px `var(--accent)` en bas du bandeau. Texte du
  header (`--mh-head-text`) blanc en dark, quasi-noir en light.
- **Corps** : theme "ledger" **selon le jour** (cf. sous-section plus bas) —
  `_MH_THEME["dark"]` (`body{background:#131417}`, texte `#eef0f2`) le
  week-end, `_MH_THEME["light"]` (`#ffffff` / texte `#1a1d24`) en semaine.
  Chaque couleur de `MASTHEAD_CSS` est une `var(--mh-*)` avec la valeur dark
  en fallback ; `build_html` injecte le jeu de tokens du theme dans
  `<body style>` (via `_mh_theme_vars`). Police **Inter** partout,
  "Big Shoulders Display" pour le wordmark + les gros chiffres de rang
  (`.mh-pos`). Le set dark est calque sur
  `comp.tables_image._LEDGER_THEME_TOKENS["dark"]`. Choix 2026-08-26 : passage
  de la 1re maquette (papier creme + serif Fraunces) a ledger + Inter.
  `--accent-ink` est assombri (mix vers `#101828`) en light — un mix vers
  blanc serait invisible sur fond blanc ; `--accent-wash` (rang #1) est
  aussi reduit (0.14 vs 0.24) pour ne pas ecraser le blanc.
- **Bande stats (haut)** : flex `[.mh-band-main | .mh-band-side]`. Gauche
  `.mh-band-main` = gros `+total daily` + chg jour / semaine / all-time
  (aligne a gauche). Droite `.mh-band-side` (`flex:1`,
  `justify-content:center`) = 1 ou 2 cellules `.mh-astat` **centrees**
  (texte centre aussi), monthly listeners (valeur + delta + `#rang world`
  avec fleche) et followers (valeur + `+delta today`) — quand followers
  absent, la seule cellule reste centree, pas collee au bord droit.
  Placement + centrage demandes par le proprietaire (2026-08-26). Titres de
  section `Top Eras` / `Top Songs` (`.mh-sec-h`) centres aussi. Puis 2
  tables (`.mh-thd` + `.mh-tr`), footer handle/date.
- **Choix design (via artifact)** : 5 concepts proposes (Counter/Masthead/
  Scoreboard/Ledger/Panorama), "Counter" choisi puis abandonne, "Masthead"
  retenu pour rester dans la meme famille que Top Eras / Top Songs.
  Iterations d'opacite/tailles sur l'artifact avant portage. Rang #1 : leger
  wash + numeral en `--accent-ink` (`_mix(accent,#fff,.55)`, lisible sur le
  fond sombre). Deltas vert/rouge fixes (semantiques).
- **Piege specificite CSS (corrige 2026-08-26)** : `.pos`/`.neg` etaient
  declares AVANT `.mh-n` et `.mh-chg .v` dans `MASTHEAD_CSS` — a specificite
  egale ou inferieure, les regles de base gagnaient et les deltas
  Delta Day / Delta Week + chg jour/semaine restaient gris au lieu de
  vert/rouge (meme famille de bug que spotlight `.stat-card.highlight` et
  `generate_album_update_image.py`, cf. skill `image-gen`). Fix : regles
  dediees `.mh-n.pos, .mh-n.pos b, .mh-n.pos i{...}` et `.mh-chg .v.pos{...}`
  placees APRES les regles de base. Reflexe : toute couleur semantique
  (vert hausse / rouge baisse) doit etre verifiee sur un vrai rendu, pas
  juste presente dans la feuille de style.
- **Renderer additif** : tout le style Masthead vit dans ce fichier
  (`MASTHEAD_CSS`, `MASTHEAD_FONTS`, `_mh_*`) ; `CSS`, `_row_html`,
  `_section_html`, `_header_style`, `_theme_vars_from_color`, `SPOTIFY_SVG`
  **restent intacts** car `post_throwback_thread.py` et
  `post_debut_releases.py` les importent (`generate_weekend_streams_image.X`).
  Ne pas les fusionner/supprimer.
- **Monthly listeners + followers** : lus depuis `db/discography/artist.json`
  par `_load_artist_stats()` (rafraichi chaque run avant la finalisation par
  `update_artist_metadata`). "Monthly listeners" s'affiche des que
  `monthly_listeners` est present (+ delta vs `previous_monthly_listeners`,
  + `#rang world` avec fleche vs `previous_monthly_rank`). "Followers" ne
  s'affiche **que si `followers` est present** dans `artist.json` — sinon la
  bande n'a qu'une cellule. **Jamais de placeholder** (regle data #1). Tant
  qu'aucun run pipeline n'a tourne depuis l'ajout, `artist.json` n'a pas de
  cle `followers` et la carte affiche juste les listeners — c'est voulu.
- **Collecte followers** (`artist_metadata.py`) : la page artiste
  deconnectee n'affiche plus le nombre de followers dans le DOM.
  `attach_artist_stats_capture(page)` pose un listener sur les reponses
  `pathfinder`/`queryArtistOverview` et extrait `stats.followers`
  (`_dig_for_stats`, tolerant a la forme). `extract_followers_from_text`
  reste comme premier essai (au cas ou Spotify re-affiche le compteur).
  Best-effort, jamais bloquant. `update_artist_metadata` ajoute `followers`
  + `previous_followers` au JSON (meme logique de bascule que les monthly
  via `updated_at`) ; le CSV `artist_monthly_listeners_history.csv` garde
  son schema (listeners/rank) — le delta followers vient de `artist.json`.
- Verifie par `generate('2026-08-24')` (PNG inspecte : listeners seuls +
  followers synthetiques a 2 colonnes) et un scrape reel
  `artist_metadata.scrape_artist_metadata()` (followers ~163M captures).

## Thème masthead selon le jour (light semaine / dark week-end) — 2026-08-26

Décision proprietaire : **toutes les cards a en-tete masthead / corps ledger
rendent le theme light (blanc propre) sur les posts de semaine (lun-ven) et
dark le week-end (sam/dim)**. Base sur la **date des donnees postees**
(`target_date`), pas sur `datetime.now()`.

- **Helper unique** : `comp.tables_image.masthead_theme_for_date(target_date)`
  -> `"dark"` si `weekday() >= 5`, sinon `"light"` ; `None`/inparsable ->
  `"dark"`. Ne jamais reimplementer la regle jour-de-semaine ailleurs.
- **Cards concernees** (toutes passent `masthead_theme=masthead_theme_for_date(
  target_date)`) :
  - `generate_streams_image.py` (Top Songs, "SONGS") — `build_html(
    masthead_theme=None)` calcule tout seul ; idem `generate_thread_images`.
  - `generate_albums_image.py` (Top Eras, "ERAS") — pareil.
  - `post_stream_highlights_thread.py` (Spotlight Gainers, "GAINERS") — l'appel
    `build_table_html` passait `masthead_theme="dark"` en dur, remplace.
  - `generate_weekend_streams_image.py` (recap quotidien) — `build_html` /
    `generate` calculent ; CLI `--light` / `--dark` force pour tester.
  - `post_best_day_since_twitter.py::_generate_recap_image` (recap Best Day
    Since, "BEST DAY") — header fixe "all eras" + masthead toujours actif
    depuis 2026-09-03 (cf. section "Recap best-day-since : header fixe" plus
    haut), suit `masthead_theme_for_date` sans override.
- **Plus d'override d'ere** (depuis 2026-09-03) : le recap Best Day Since n'a
  plus de theming par album, donc plus d'exception « Holiday Collection reste
  light ». Il suit la regle du jour comme les autres cards. TTPD/Showgirl
  restent un systeme `theme_variant` separe sur `generate_album_update_image.py`,
  hors de cette regle.
- **Recap Best Day Since** : ses lignes utilisent les classes classiques
  `.data-row`/`.col-*` (pas `.ledger-*`), qui ont un fond quasi-blanc et un
  texte sombre code en dur. Donc son theme "dark" n'assombrit que le header +
  les rails ledger (le corps reste clair) ; en "light" tout est coherent.
  Ecart pre-existant, pas regle ici — a garder en tete si un jour on refait
  ce recap sur des vraies `.ledger-row`.
- **Verifie** : recap regénéré sur 2026-08-24 (lun, light) / 08-23 (dim, dark)
  / 08-22 (sam, dark) / 08-20 (jeu, light) ; Top Eras + Top Songs regénérés
  sur 08-24 (light) — PNG inspectes, vert/rouge OK, contrastes OK.

## Pieges

- Bug fixe le 2026-08-08 : `generate_streams_image.py::load_song_db()` (et
  `_get_song_family_single_image_map()`, meme fichier) ne lisaient que
  `albums/*.json` + `songs.json`, contrairement au chargeur canonique
  `history_store.py::load_extra_sections_flat()` qui inclut aussi
  `features.json` et `misc.json`. Consequence : tout track standalone/extra
  ou feature (ex. "I Knew It, I Knew You", `misc.json`, edition `extras`)
  etait bien collecte dans `streams_history.csv` (donnees reelles, daily
  correct) mais disparaissait silencieusement du top N genere — `_dedup_by_title`
  fait `song_db.get(tid)` puis `continue` si absent, donc la ligne est droppee
  sans erreur ni log, meme si son daily aurait du la placer en haut du
  classement (observe : #2 daily manquant, tout le monde decale d'un rang
  sans que ce soit visible). Fix : `load_song_db()` et
  `_get_song_family_single_image_map()` boucent maintenant sur
  `("songs.json", "features.json", "misc.json")` comme `history_store.py`.
  Reflexe a garder : tout nouveau chargeur de catalogue ecrit a la main dans
  un script `generate_*`/`post_*` doit couvrir les 3 fichiers extras, pas
  seulement `songs.json` — sinon un track qui streame fort mais qui n'est pas
  sur un album (single hors-album, feature, bonus) peut manquer dans
  n'importe quel classement/top N genere depuis ce chargeur.
- Audit complet le 2026-08-09 (suite au bug ci-dessus + celui de
  `swift_top_100.py` cote skill `collector-billboard`) : 12 autres chargeurs
  de catalogue ecrits a la main dans `collectors/spotify/streams/` (et
  `scripts/r2.py`, `scripts/chartr2.py`, `collectors/comp/discography.py`,
  `collectors/spotify/charts/worldwide/daily.py` partages avec d'autres
  collecteurs) avaient le meme trou (`features.json`/`misc.json` jamais lus,
  parfois meme `songs.json` casse car traite comme une liste de tracks plate
  au lieu d'une liste de sections). Tous fixes le meme jour : `spotlight.py`
  (`load_all_tracks`, `_get_song_family_single_image_map`),
  `best_day_since.py` (`load_song_sections`, mode `--include-extras`),
  `fix_one.py`, `fix_streams.py`, `seed_streams.py` (qui avait EN PLUS un
  `_REPO_ROOT = _SCRIPT_DIR.parents[2]` casse — pointait sur
  `collectors/spotify/db/...` au lieu de `db/...`, donc **tout** le script,
  y compris `HISTORY_PATH`, ne trouvait jamais rien, corrige en `parents[4]`),
  `extras/import_daily_streams.py`, `tools/scripts/post_best_day_since_twitter.py`,
  `tools/scripts/post_debut_releases.py` (`_load_misc_tracks` ne lisait en
  fait que `songs.json`, meme piege de nommage que `swift_top_100.py`),
  `scripts/fill_streams_from_archive.py`. Seul `history_store.py` et
  `extras/export_for_web.py` (`EXTRA_SECTION_SOURCES`) etaient deja corrects
  et servent de reference. Reflexe permanent : `db/discography` a exactement
  4 sources de tracks (`albums/*.json`, `songs.json`, `misc.json`,
  `features.json`) — avant d'ecrire ou de modifier un chargeur de catalogue
  a la main, verifier qu'il couvre les 4, sinon prefer reutiliser
  `history_store.load_extra_sections_flat()` / `comp.discography` plutot que
  reimplementer.
- Regression corrigee le 2026-08-16 : le commit `c2aff8e1f` (2026-08-13,
  "charts run all 2026-08-12") a supprime la constante
  `RECAP_BEST_DAY_MIN_DAYS = 30` de `post_best_day_since_twitter.py` et l'a
  remplacee par `_find_recap_rows()` qui appelait
  `best_day_since.passes_filters(row, min_days=1)`. Un seuil de 1 jour ne
  filtre quasiment rien (des qu'un titre bat son propre daily de la veille,
  ca compte comme "best day since"), donc l'image recap
  (`best_day_since_recap_{date}.png`) s'est retrouvee avec des dizaines de
  titres en trop et des dates "since" vieilles de quelques jours au lieu de
  semaines (observe : 124 chansons le 2026-08-14, seuil attendu ~1 mois).
  Fixe en restaurant `RECAP_BEST_DAY_MIN_DAYS = 30` et en l'utilisant dans
  `_find_recap_rows`, memes seuil et comportement qu'avant le 2026-08-12.
  Reflexe a garder : les seuils `min_days`/`min_pct_change` de ce fichier
  sont des regles produit, pas des details d'implementation — ne jamais les
  remplacer par une valeur en dur sans verifier l'intention d'origine.
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
- `compute_best_day_since_combined` retombe sur la somme `song_family` (toutes
  versions, ex. "Red" original + "Red (Taylor's Version)") seulement quand le
  record solo du track echoue. Le tableau `generate_album_update_image.py`
  affiche des chiffres DAILY/CHG solo par ligne — jamais montrer l'etoile
  `★ ... since ...` sur ce tableau quand `row["combined"]` est vrai (sinon
  l'etoile contredit un CHG negatif affiche a cote, car le record vient d'une
  autre version). `_best_day_labels_for_sections` filtre deja `not
  row.get("combined")`. Partout ou un record combined est quand meme annonce
  (post Twitter, song card, recap) le texte doit le dire : `row_label()` /
  `_best_day_post_label()` ajoutent automatiquement le suffixe `(combined)`
  quand `row["combined"]` est vrai — ne pas dupliquer cette logique ailleurs,
  passer par ces fonctions.
