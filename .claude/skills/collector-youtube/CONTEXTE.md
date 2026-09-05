# Contexte Collector YouTube

## Role

`collectors/youtube` suit les vues quotidiennes des videos uploadees sur la
chaine officielle Taylor Swift:

```text
UCqECaJ8Gagnn7YCbPEzWH6g
```

Ce collector ne concerne pas YouTube Music Charts.

## Entrypoints

Commande canonique:

```powershell
python -m collectors.youtube.videos.update_youtube
```

Commande compat:

```powershell
python -m collectors.youtube.update_youtube
```

Scheduler:

- Prod tourne via le **Planificateur de tâches Windows local** (tâche `TSM
  YouTube Videos Daily`, action = `powershell -EncodedCommand` →
  `collectors/youtube/run_youtube.bat` → `python -m
  collectors.youtube.videos.update_youtube --commit --no-notify`, tous les
  jours à 06:05 Europe/Paris). Tourne sur le PC d'Anas — PC éteint à
  l'heure du run = pas de collecte ce jour-là (pas de retry auto ; rattrapage
  `--date AAAA-MM-JJ` = **jour d'activité voulu**, pas la date du run, cf.
  `manual_trusted` / [[tsm-streams-pipeline-ops]]).
- **`--no-notify` (pas `--no-post`) depuis le 2026-08-30** : `--no-post`
  coupait aussi toute la logique first-day (planif + filet de sécurité), donc
  depuis l'arrivée de la feature le 2026-08-25 aucune card "first 24h views"
  ne partait automatiquement en local. `--no-notify` ne coupe que la ntfy
  quotidienne.
- Tenté sur GitHub Actions le 2026-08-28 (`run-data-only-collectors.yml`),
  re-basculé en local le 2026-08-29 : `schedule:` GitHub trop peu fiable. Le
  workflow reste `workflow_dispatch` manuel + `disabled` côté GitHub, comme
  escape hatch de rattrapage. `scripts/ci_data_collector_gate.py` y route les
  inputs. **Ne jamais remettre de garde-fou basé sur l'heure exacte** dans un
  workflow GitHub (les crons partent avec ~1 h de retard variable → un test
  d'heure pile fait skip presque tous les jours).
- Historique : `cron` sur VPS OVH (`06:05` Europe/Paris) du 2026-07-30 au
  2026-08-17 (VPS décommissionné, coût). Détail : `REPO_CONTEXT.md` section
  « Déploiement VPS OVH » et `OVH.md`.

Le `.bat` local :

```text
collectors/youtube/run_youtube.bat
```

## Options utiles

```powershell
python -m collectors.youtube.videos.update_youtube --dry-run
python -m collectors.youtube.videos.update_youtube --debug
python -m collectors.youtube.videos.update_youtube --no-post     # aucun post X + pas de ntfy
python -m collectors.youtube.videos.update_youtube --no-notify   # pas de ntfy, cards first-day OK (utilisé par le .bat)
python -m collectors.youtube.videos.update_youtube --bootstrap
python -m collectors.youtube.videos.update_youtube --commit
python -m collectors.youtube.videos.update_youtube --force --commit
```

## Donnees

CSV:

- `db/youtube_views_history.csv`: une ligne par video.
- `db/youtube_title_history.csv`: lignes groupees par titre/song.

JSON legacy/cache:

- `collectors/youtube/tools/json/video_db.json`
- `collectors/youtube/tools/json/youtube_history.json`

Colonnes importantes:

- `date` = **jour d'activité** = date du run − 1 jour (décision 2026-09-02, cf.
  « Regles data » ci-dessous). Le run planifié tourne à 06:05 Europe/Paris ≈
  00:05 America/New_York (`YOUTUBE_COLLECTION_TZ`), soit pile minuit NY : le
  delta de `viewCount` depuis le run précédent couvre la journée calendaire NY
  qui vient de se **terminer**. `main()` calcule `activity_date =
  _youtube_collection_date() − 1j` (variable `run_date` gardée séparément pour
  l'instant du run). `--date D` = jour d'activité voulu directement (pas de −1).
- `snapshot_at` (depuis 2026-08-29) : horodatage UTC ISO 8601 exact du run
  (`datetime.now(timezone.utc)`, pris juste après le batch-fetch). ≈ `date`+1
  à 06:05 Paris (mesure prise à la fin de la journée d'activité). `daily_views`
  d'une date D = delta entre le `snapshot_at` de la **ligne précédente** et
  celui de la ligne D. Lignes antérieures au 2026-08-29 = colonne vide (ajout
  rétro-compatible en tête des `CSV_FIELDNAMES` / `TITLE_CSV_FIELDNAMES`,
  migration auto du header via `csv_utils._ensure_fieldnames` /
  `write_title_history`). Le frontend (`api/routes/youtube.py` →
  `window_start`/`window_end`, rendu par `pages/YouTube.jsx`) l'utilise pour
  afficher la vraie fenêtre horaire.
- `video_id`
- `title`
- `rank`, `previous_rank`, `rank_change`
- `total_rank`, `previous_total_rank`, `total_rank_change`
- `published_at`
- `duration`
- `thumbnail_url`
- `total_views`
- `daily_views`
- `daily_change`, `daily_change_pct`
- `period_gain_views`, `period_days`, `period_label`
- `like_count`, `comment_count`
- `category_id`
- `live_broadcast_content`
- `privacy_status`
- `upload_status`
- `tags`

## Regles data

- **Dating : `date` = jour d'activité, pas date du run (décision Anas 2026-09-02).**
  Avant, chaque ligne était étiquetée avec la date du run (minuit NY), donc le
  delta — qui couvre la journée *écoulée* — était daté +1. Fix : le collector
  écrit `run_date − 1` ; l'historique complet a été décalé de −1 une fois par
  `scripts/shift_youtube_dates_back_one_day.py` (2 CSV `db/youtube_*_history.csv`,
  seule la colonne `date` bouge, `.bak` créés, gitignore `*.csv.*.bak`), puis
  ré-uploadé R2 (`r2.upload_youtube()`) + `generate_home_highlights.py` re-run.
  `ci_data_collector_gate.py::youtube_day_pending` cherche `run_date − 1`.
  TayBoard : semaines passées gelées (snapshots R2), pas de recalcul ; futures
  semaines prennent les dates corrigées (`_weekly_youtube_views` somme par
  `date` dans la semaine ISO — 1 semaine de transition peut avoir 6/8 jours YT,
  négligeable à `YOUTUBE_WEIGHT` 0.3).
- `total_views` vient de YouTube Data API.
- `daily_views` est uniquement le delta exact entre deux snapshots calendaires
  consecutifs.
- Si une journee manque, ne pas classer/poster le delta multi-jours comme daily.
  Utiliser `period_gain_views`, `period_days`, `period_label`.
- Ne pas melanger videos YouTube et YouTube Music charts.
- **Exception pour une vidéo tout juste sortie (fix 2026-08-26)** : à sa toute
  première ligne CSV (`prev_views` absent), si `published_at` est récent
  (`_is_recent_publish`, seuil `FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS` = 4 jours)
  et qu'on n'est pas en `--bootstrap`, `daily_views` = `total_views` (baseline
  0, puisque la vidéo n'existait pas avant aujourd'hui) au lieu de rester vide.
  Avant ce fix, une vidéo qui accumulait beaucoup de vues avant sa toute
  première collecte quotidienne (ex. sortie tard dans la journée, ou qui
  explose immédiatement) n'apparaissait pas du tout dans le chart "Daily
  views" ce jour-là (daily_views vide = trié comme 0 côté frontend), alors
  que ces vues appartiennent bien à ce jour calendaire. En `--bootstrap`
  (découverte de tout le catalogue existant) ou si `published_at` est trop
  ancien (vidéo juste rendue publique/listée tardivement), `daily_views`
  reste vide — impossible de savoir comment répartir un total déjà ancien.
  **Backfill fait le 2026-08-26** pour "Taylor Swift Performance - The Icon
  Sessions at the Grammy Museum" (`_9jaJtmraXA`, découverte le 2026-08-25) :
  `daily_views` patché à `total_views` (1 368 926) sur la ligne CSV déjà
  écrite, rang du jour recalculé via `enrich_chart_rows`/`build_title_rows`
  (mêmes fonctions que le run normal, pas de refetch live pour ne pas casser
  l'historique exact), CSV video+titre réécrits, ré-upload R2. Ça a fait
  passer les autres vidéos du 2026-08-25 d'un rang de moins sur "Daily views"
  (effet en cascade normal, pas un bug).

## Core

- `core/api.py`: pages uploads, metadata videos, API YouTube.
- `core/channel.py`: chaine officielle/config.
- `core/title_groups.py`: groupement officiel/lyric/audio/visualizer par titre.
- `core/csv_utils.py`: CSV.
- `core/git_ops.py`: commit si demande.
- `core/config.py`: chemins/env.

## Variables

- `YOUTUBE_API_KEY`: requis.
- `NTFY_TOPIC_YOUTUBE`: topic ntfy, defaut `taylormuseum-youtube`.

## Posting "first 24h views" (ajouté 2026-08-25, planification exacte ajoutée le même jour)

Quand une vidéo tout juste découverte franchit ses 24h depuis
`published_at`, le collector poste automatiquement une card sur
@swiftiescharts avec "views in its first 24 hours" (card dédiée
`comp/youtube_card.py`, thumbnail YouTube en cover, logo YouTube).

**Chemin principal — tâche Planificateur Windows one-off (précis à la
minute) :**

- À la découverte d'une vidéo (1ère écriture CSV, dans `main()` juste après
  `append_rows`), `_schedule_first_day_task(video_id, published_at)` crée
  une tâche Planificateur de tâches Windows **one-off** nommée
  `TSM_YouTube_FirstDay_<video_id>` déclenchée à `published_at + 24h`
  (heure locale, via PowerShell `Register-ScheduledTask` — évite les
  ambiguïtés de format `schtasks.exe` selon la locale). La tâche relance
  `python -m collectors.youtube.update_youtube --post-first-day <video_id>`.
  `-StartWhenAvailable` : si le PC est éteint/en veille pile à l'heure
  cible, la tâche se déclenche au prochain réveil au lieu d'être perdue.
- Si `published_at + 24h` est déjà dans le passé au moment de la
  découverte (vidéo découverte plus de 24h après sa propre sortie —
  jusqu'à `FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS` = 4 jours après
  publication pour être encore suivie du tout ; au-delà, aucune
  planification), **aucun post first-day n'est fait** : la fenêtre exacte
  des 24h est passée, impossible d'obtenir une donnée juste sans fake data
  (cf. `run_post_first_day` ci-dessous — avant le 2026-08-26 le code
  postait quand même immédiatement, ce qui était le bug corrigé ce jour-là).
- `run_post_first_day(video_id)` (`--post-first-day`) : fetch live le
  `viewCount` actuel et le poste **tel quel** comme total "first 24 hours"
  (génère la card, poste, écrit le lock, puis **se désinscrit elle-même**
  de la tâche Planificateur via `_unschedule_first_day_task` — une tâche
  one-off n'a plus rien à faire après son unique déclenchement, qu'elle
  ait réussi ou échoué ; l'échec est repris par le filet de sécurité
  ci-dessous, pas par une replanification). `viewCount` est cumulatif
  depuis la sortie (0 à `published_at`), donc un fetch fait pile à
  `published_at+24h` EST déjà le vrai total des 24 premières heures — pas
  besoin de soustraire quoi que ce soit.
  **Bug corrigé le 2026-08-26** : le code soustrayait auparavant le
  `total_views` enregistré à la *découverte* (1ère écriture CSV), en le
  traitant comme une baseline à t=0. Faux : le collector tourne 1x/jour,
  donc une vidéo peut déjà avoir engrangé une grosse partie de ses vues du
  1er jour plusieurs heures avant d'être vue pour la 1ère fois — soustraire
  cette valeur sous-comptait fortement le vrai total (ex. "Taylor Swift
  Performance - The Icon Sessions at the Grammy Museum" : posté à
  721 390 vues alors que le total réel à +24h était ~2,09M, car la vidéo
  avait déjà 1 368 926 vues ~11h après sa sortie, au moment de sa
  découverte par la collecte quotidienne). Le filet de sécurité
  `post_first_day_views` avait le même défaut (delta `daily_views` depuis
  la découverte au lieu du `total_views` cumulé) et a été corrigé pareil.
- Pas de planification en `--bootstrap` (découverte en masse du catalogue
  entier — aucune de ces vidéos n'est "tout juste" publiée) ni avec
  `--no-post`. **`--no-notify` NE bloque PAS la planification** (c'est tout
  l'intérêt du split fait le 2026-08-30) — seule la ntfy quotidienne est
  coupée. Le `.bat` de prod utilise `--no-notify`.

**Filet de sécurité — vérif quotidienne (`post_first_day_views`) :**

- `_first_daily_video_ids()` : détecte, parmi les vidéos collectées le jour
  même, celles avec exactement une seule apparition CSV antérieure à
  `today` ET un `published_at` récent (`FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS`,
  4 jours) — ce 2e garde-fou évite qu'une vieille vidéo qui vient juste
  d'être *découverte* (rendue publique/listée tardivement, mais uploadée il
  y a longtemps, donc déjà avec un vrai total de vues) ne soit faussement
  présentée comme "premières 24h".
- `post_first_day_views()` tourne à chaque collecte quotidienne (~24h après
  la 1ère apparition CSV, potentiellement décalé par rapport à
  `published_at+24h` exact) et sert uniquement de rattrapage si la tâche
  one-off n'a pas pu être créée ou ne s'est jamais déclenchée. Même fichier
  de lock que le chemin principal — donc quel que soit celui qui poste en
  premier, l'autre est un no-op silencieux.

**Commun aux deux chemins :**

- Génération de la card : `comp/youtube_card.py::render_youtube_card`
  (réutilise les helpers génériques de `comp/song_card.py` — fetch/palette
  de la thumbnail, logo TSM footer, rendu Playwright HTML->PNG). PNG écrits
  dans `snapshots/youtube/videos/YYYY/MM/YYYY-MM-DD/`.
- Post via `collectors/spotify/core/twitter.py::post_with_image` sur la
  même session que @swiftiescharts
  (`collectors/spotify/charts/global/tools/json/twitter_session.json`).
- Lock anti-doublon par vidéo :
  `collectors/youtube/tools/json/first_day_posted/<video_id>.lock`.
- Erreurs non bloquantes pour le reste de la collecte (`try/except` autour
  des deux appels dans `main()`).

**Design de la card :**

- Le titre vidéo complet est affiché tel quel (ne PAS retirer le préfixe
  "Taylor Swift ... -", il fait partie du titre officiel). Les titres
  YouTube sont de vraies phrases longues, contrairement aux titres de
  chansons courts — d'où une card dédiée (`comp/youtube_card.py`,
  `render_youtube_card`) plutôt que de détourner `comp/song_card.py`
  (jamais utilisé en prod avec `best_since=False`, donc `song_card.py` est
  resté intact). Titre jusqu'à 4 lignes (`-webkit-line-clamp:4`), paliers
  de police propres à `youtube_card.py` (`_title_font_size`, généreux —
  titre + stat box doivent remplir l'espace vertical dispo, pas rester
  petits avec du vide autour), une seule case stat (pas "First 24h" +
  "Total" séparées : sur une vidéo qui vient d'être publiée les deux sont
  quasi identiques) au format `+842,391 views` (signe + et mot "views"
  inclus dans la valeur, pas juste le nombre brut), contenu de la case
  centré horizontalement (`text-align:center` sur `.stat`).
  Titre + stat box + date de sortie (`"Released {mois} {jour}, {année} · {heure}
  UTC"`, depuis `published_at` qui est déjà en UTC — pas de conversion locale,
  l'heure affichée est directement celle de l'upload) forment un seul bloc flex
  (`.body` dans `youtube_card.py`, conteneur `.body-wrap` avec
  `justify-content:center`) : ce bloc est centré verticalement dans l'espace
  entre le header et le footer, donc l'espace au-dessus du bloc = l'espace
  en dessous. Ne pas séparer la date dans un élément hors de `.body` (footer
  ou position absolue) sous peine de casser cet équilibre.
- `--preview` (`run_preview()`) : aperçu à la demande, sans attendre le run
  réel du lendemain. Prend la vidéo qui n'a qu'une seule ligne CSV (donc en
  attente de son 1er daily_views), refait un fetch live YouTube pour son
  `total_views` actuel, et utilise `live_total - total_views_enregistré`
  comme delta d'aperçu — n'écrit pas le CSV, ne poste pas. Ce delta n'est
  PAS le vrai daily_views (qui ne sera calculé que par le run réel du
  lendemain sur un vrai intervalle de ~24h) : il sert à vérifier la card et
  le texte du tweet avec des chiffres réels, pas pour valider la donnée.
  Contrairement au run réel (`post_first_day_views`, HTML temporaire
  supprimé après le rendu Playwright), `--preview` appelle
  `_generate_first_day_views_image(..., keep_html=True)` : le `.html` reste
  sur disque à côté du `.png` (même dossier, même nom de base) pour pouvoir
  inspecter/retoucher le rendu directement.

## Consommateurs

- **Frontend `tsm-frontend` — page YouTube Charts** (`api/routes/youtube.py` →
  `pages/YouTube.jsx`). Rappel : sur un snapshot « jour manqué » (`period_days > 1`,
  `daily_views` vide, `period_gain_views` rempli), depuis 2026-09-02 :
  - le chart est quand même **classé** (`_metric_for_rank` : rang sur
    `period_gain_views` faute de `daily_views`) — le collector, lui, ne classe
    jamais ces lignes (`rank`/`previous_rank`/`rank_change` vides au CSV) ;
    l'API backfille ces trois champs à partir du re-classement quand le CSV les
    a laissés vides, sans jamais écraser une valeur déjà présente.
  - la colonne **« +/- previous »** compare le gain multi-jours exact à la
    **période de même durée juste avant** : `total(previous_date) −
    total(previous_date − period_days)`, c.-à-d. les N jours qui précèdent
    immédiatement la fenêtre courante (décision propriétaire 2026-09-02 :
    récence > alignement jour-de-semaine, et surtout jamais de moyenne/jour).
    N'est affiché **que si la date `previous_date − period_days` existe
    exactement** comme snapshot (sinon on aurait une fenêtre de longueur
    différente → comparaison trompeuse) ; sinon colonne vide
    (`period_change`/`period_change_pct` vides). Champs API : `period_prev_gain`,
    `period_change`, `period_change_pct`, `period_compare_start/end` (par ligne)
    + `period_days`, `compare_window_start/end` (niveau payload).
  - le header de la colonne métrique affiche `{N}-day gain` (depuis
    `payload.period_days`), celui de « +/- » affiche « +/- Previous ».
  - la fenêtre comptée + la fenêtre de comparaison sont affichées dans un
    encadré `.youtube-window-box` **dans le subnav**, empilé sous les contrôles
    Prev / calendrier / Next : `.history-date-controls` + le box sont enveloppés
    dans `.youtube-date-stack` (flex column, `align-items:flex-end`) pour que le
    box tombe pile sous le sélecteur de date et s'aligne sur son bord droit
    (`.site-subnav-links:has(.youtube-date-stack)` passe en `align-items:flex-start`).
    Clés i18n
    `youtube_window_note` (« Views counted {start} → {end} ») +
    `youtube_compare_window_note` (« "+/- previous" compares with the {days}
    days before ({start} → {end}) »). La borne de début est raccourcie (sans
    année) via `SHORT_DATETIME_OPTS`/`SHORT_DATE_OPTS`.
  Un snapshot quotidien propre (`period_days == 1`) est inchangé : « +/- Yesterday »,
  comparaison jour/jour telle qu'écrite par le collector, encadré = juste la
  fenêtre comptée.

- **TayBoard scoring** (ajoute 2026-08-14, `collectors/billboard/swift_top_100.py`) :
  `db/youtube_title_history.csv` (`daily_views` par titre groupe) alimente
  `units_youtube` (poids `YOUTUBE_WEIGHT`, defaut 0.3). Si le format de ce
  CSV change (colonnes renommees, grouping modifie), verifier
  `_weekly_youtube_views()` et le skill `collector-billboard`.

## Pieges

- **Bug corrigé le 2026-09-05 dans `core/title_groups.py`** : le catalogue de
  matching titre-vidéo→chanson ignorait silencieusement les 17 fichiers
  `db/discography/albums/*.json` (forme dict `{"album","sections"}` depuis
  avril 2026 — `_iter_discography_sections` n'acceptait qu'une liste
  racine), réduisant le catalogue réel à ~28 chansons sur ~338. Ça faussait
  `youtube_title_history.csv` (donc `units_youtube` TayBoard) et le
  regroupement par chanson côté frontend pour la quasi-totalité des tracks
  d'album. Fix + deux correctifs liés dans le même commit : les entrées
  `chart_extra`/`excluded_from_public_stats` (bruit de chart-snapshot) ne
  génèrent plus d'alias (elles pouvaient voler le match d'une vraie chanson
  au même titre, ex. "Starlight" volé par une entrée "Instrumental With
  Background Vocals" bruitée) ; `normalize_text()` normalise les guillemets
  courbes (`’`/`‘`) en apostrophe droite au lieu de les faire disparaître
  (cassait le match sur "Who's Afraid of Little Old Me?" etc.). Après ce
  fix, ~49 chansons "réelles" (hors bruit chart) restent sans vidéo
  matchée — certaines sont de vrais trous (deep cuts jamais promues en
  vidéo), d'autres restent des cas limites (ex. parenthèses de
  désambiguïsation strippées par `_clean_video_title`, mojibake déjà présent
  dans le titre stocké en DB).
- **Audit du 2026-09-05 — chansons vraiment sans vidéo officielle nulle part**
  (vérifié sur la chaîne principale UCqECaJ8Gagnn7YCbPEzWH6g + la chaîne
  auto-générée **"Taylor Swift - Topic"** `UCPC0L1d253x-KuMNwa05TpA`, qui
  couvre en "official audio" quasi tout le catalogue streamé y compris les
  deep cuts — 2122 vidéos, second catalogue utile pour l'association
  tsm_song_id↔vidéo mais PAS suivi pour les vues quotidiennes) : seulement 3
  vraies impasses — **Invisible** (Taylor Swift, 2006), **September -
  Recorded at The Tracking Room Nashville** (cover 2018), **Hold On (feat.
  Taylor Swift) [Live]** (chanson de Jack Ingram — aucun upload officiel
  trouvé même sur sa chaîne à lui). Le reste des ~49 a un official audio sur
  la chaîne Topic, ou (3 vrais collabs) sur la chaîne du collaborateur :
  Highway Don't Care → TimMcGrawVEVO, The Joker And The Queen → chaîne
  officielle Ed Sheeran, Both of Us → chaîne officielle B.o.B. `primary_artist`
  était mis à tort à "Taylor Swift" pour ces 4 collabs (+ Hold On/Jack
  Ingram) dans `db/discography/misc.json` — corrigé le 2026-09-05 (champ
  `artists` aussi mis à jour). 4 associations vidéo↔chanson ajoutées à
  `video_groups.json` (Carolina ×2 fusionnées sur une seule entrée faute de
  family propre en DB, exile/long pond sessions, les 2 bonus tracks
  evermore) — ces vidéos existaient déjà dans `video_db.json` mais leur
  `song_family` en DB était pollué par le suffixe d'édition
  (`..._bonus_track`, `..._feat_bon_iver`), empêchant le match auto.
- **⚠️ `video_groups.json` (390 groupes) contient des overrides manuels
  probablement gelés à une époque où le catalogue de matching était cassé
  (voir bug ci-dessus) — certains ne couvrent qu'UNE vidéo sur une clé
  synthétique ad hoc au lieu du `song_family` réel de la DB (ex.
  `breathe_ft_colbie_caillat` pour la seule vidéo de "Breathe", au lieu de
  `breathe` ; `end_game_ft_ed_sheeran_future` / `end_game_behind_the_scenes`
  au lieu de `end_game`). Conséquence découverte le 2026-09-05 : appliquer
  les overrides manuels (`load_manual_groups`, comme le fait
  `build_title_rows` en prod) fait *régresser* le nombre de chansons
  matchées par rapport au matching automatique seul (68 chansons non
  matchées avec overrides vs 49 sans) — Breathe, End Game, Everything Has
  Changed, Forever & Always, Run, Safe & Sound, The Last Time, The Story Of
  Us et une dizaine d'autres singles bien connus en font les frais. **Pas
  corrigé** (chantier séparé, ~390 entrées à auditer contre le catalogue
  maintenant fixé, potentiellement via `scripts/youtube_grouping_editor/` ou
  un script d'audit dédié) — à traiter avant de considérer l'association
  tsm_song_id↔YouTube comme fiable en prod.
- Ne pas utiliser un delta multi-jours comme record quotidien.
- `--commit` est volontaire; ne pas committer sans demande.
- `--force` peut remplacer des lignes existantes; verifier la date et les
  sorties avant usage.
- **Changement de méthodologie YouTube le 2026-08-24** : `viewCount` compte
  désormais une vue dès le lancement de la lecture pour toutes les vidéos
  (avant : seulement les Shorts). Ça peut faire apparaître un `daily_views`
  anormalement élevé le 2026-08-24 (et dans les jours suivants) pour
  TOUTES les vidéos du catalogue, pas seulement les nouvelles — ce n'est pas
  une vraie hausse d'audience organique, ne pas le traiter comme un "best
  day" légitime si ça touche une vidéo ancienne autour de cette date.
- **Bug corrigé le 2026-08-30 (`KeyError: 'video_id'`)** : `update_video_db`
  (`core/channel.py`) faisait `v.pop("video_id")` sur les dicts de
  `new_videos`, que `main()` réutilise ~150 lignes plus loin
  (`{v["video_id"] for v in new_videos}` sur le chemin first-day-views). La
  compréhension étant évaluée sans condition, le run **crashait dès qu'une
  nouvelle vidéo était découverte** (jour d'un nouveau clip TS) — après
  l'écriture du CSV views mais avant title history / upload R2 / commit git,
  d'où un `LastTaskResult=0x1` et une ligne du jour présente en local mais
  jamais commitée. Fix : `update_video_db` ne mute plus son input +
  `new_video_ids` capturé juste après la découverte. Réflexe : si la tâche
  YouTube sort `0x1` un jour où TS a posté une vidéo, checker ici en premier.
- Tâches Planificateur `TSM_YouTube_FirstDay_<video_id>` visibles dans
  Task Scheduler (racine, pas dans un dossier dédié) : normal, une par
  vidéo tout juste découverte, en attente de son déclenchement à
  `published_at+24h`. Elles se suppriment seules après exécution — si
  plusieurs traînent avec une date de déclenchement passée, ça signale un
  souci (`run_post_first_day` planté avant `_unschedule_first_day_task`,
  ou le run correspondant jamais arrivé) : vérifier via `Get-ScheduledTask
  -TaskName 'TSM_YouTube_FirstDay_*'` en PowerShell.
