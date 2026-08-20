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
  cibles. Reserve a un cas verifie a la main (fusion/split cote Spotify, cf.
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
