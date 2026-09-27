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
python -m collectors.youtube.videos.update_youtube --no-post     # aucun post X (ni first-day) + pas de ntfy
python -m collectors.youtube.videos.update_youtube --no-notify   # pas de ntfy, posts first-day OK (utilisé par le .bat)
python -m collectors.youtube.videos.update_youtube --first-day-status   # releases first-day en attente
python -m collectors.youtube.videos.update_youtube --first-day-cancel   # ne rien poster pour elles
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
- **Vidéo publiée après la fin du jour d'activité (fix 2026-09-27)** : le run de
  ~00:05 ET écrit le jour D qui vient de finir ; une vidéo publiée entre minuit
  ET et ce run (**l'heure des sorties de Taylor**) n'existait pas le jour D →
  `main()` ne lui écrit **ni ligne D ni état delta** (`rows_after_day`, date de
  publication calculée dans `YOUTUBE_COLLECTION_TZ`). Le run suivant la voit
  pour la 1re fois, baseline 0 (`_is_recent_publish`), donc son jour de sortie
  compte **toutes** ses vues, y compris celles d'avant sa détection. Avant : les
  27 uploads Topic d'Encore (publiés à 00:01 ET le 25/09) avaient une ligne au
  24/09 (veille de la sortie, 0 à 1 114 vues, 3 127 au total) et autant en
  moins sur le 25/09 (Patient Zero 2 254 193 au lieu de 2 255 307). Ces lignes
  historiques n'ont PAS été réparées (réparation proposée à Anas, en attente).
  Seul cas de tout l'historique. Vérifié par le scénario D de
  `previews_and_sims/youtube-first-day/simulate.py` (vrai `main()`).
- `total_views` vient de YouTube Data API.
- `daily_views` est uniquement le delta exact entre deux snapshots calendaires
  consecutifs.
- Si une journee manque, ne pas classer/poster le delta multi-jours comme daily.
  Utiliser `period_gain_views`, `period_days`, `period_label`.
- Ne pas melanger videos YouTube et YouTube Music charts.
- **Collaborations : `feat`/`ft` obligatoire dans le titre video (fix
  2026-09-26).** Incident : `Taylor Swift - The Life of a Showgirl: The Encore
  STATION` et des titres Topic nus ont ete combines avec `The Life of a
  Showgirl (feat. Sabrina Carpenter)` parce que `_title_aliases()` retirait les
  parentheses et proposait l'alias nu `The Life of a Showgirl`. Regle :
  lorsqu'une entree discographie a `featured_artists`, les alias automatiques
  doivent conserver ou generer un credit explicite `feat`/`ft <artiste>` ; un
  titre video sans ce credit ne matche pas la chanson feat. Si le titre nu
  collide exactement avec une chanson feat, `match_video_title()` le met dans
  `*_no_feature_credit` pour eviter qu'un autre titre court (ex. `ME!`) le
  vole. Rebuild applique avec `python -m scripts.rebuild_youtube_title_history
  --apply` depuis `db/youtube_views_history.csv`.
- **Source `songs` YouTube (ajout 2026-09-26).** Les niveaux historiques
  `all`/`main`/`topic` gardent leur sens brut : videos officielles et topics,
  donc `main` peut contenir annonces, lives, premieres, stations, shorts, etc.
  Pour lire uniquement les chansons, utiliser `source=songs` dans
  `youtube_title_history.csv` et l'API frontend. Ce niveau est reconstruit
  depuis les memes lignes exactes que `all`, mais ne garde que les groupes qui
  matchent le catalogue chanson ou un groupe manuel catalogue ; il exclut les
  groupes video/promo comme `*_station`, `*_live`, announcements, shorts, etc.
  En `mode=videos&source=songs`, l'API filtre les videos via les `video_ids`
  des groupes `source=songs`.
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
- `core/first_day.py`: posts "first 24 hours" par release (voir section dédiée).
- `core/git_ops.py`: commit si demande.
- `core/config.py`: chemins/env.

## Variables

- `YOUTUBE_API_KEY`: requis.
- `NTFY_TOPIC_YOUTUBE`: topic ntfy, defaut `taylormuseum-youtube`.

## Posts "first 24 hours" — une release = un post (refonte 2026-09-27)

Code : `core/first_day.py` (appelé par `update_youtube.py`). Rendu :
`comp/youtube_card.py` (`render_youtube_card` = card seule,
`render_youtube_debut_table` = tableau). Rejouer le cas Encore :
`python previews_and_sims/youtube-first-day/simulate.py` (état isolé, rien de
posté).

**Incident qui a motivé la refonte (Encore, 25-27/09/2026).** La chaîne Topic a
ré-uploadé tout le deluxe (2 uploads par chanson, anciennes chansons de
Showgirl comprises), puis les 4 lyric videos sont sorties le lendemain soir.
L'ancienne logique (1 tâche + 1 post par vidéo, + filet de sécurité quotidien
« 2e apparition CSV ») a posté **~34 cards séparées** : doublons (2 × Babylon,
2 × Honey…), ré-uploads de chansons vieilles d'un an présentés comme des
débuts, et des chiffres mesurés à +28 h (lyric videos) ou +52 h (STATION,
12,9M) étiquetés « first 24 hours ». Anas avait supprimé les tâches
planifiées pour bloquer les posts : le filet de sécurité les a postés quand
même. Les 31 tâches du 25/09 n'ont jamais tourné (supprimées avant).

**Pipeline (3 étapes découplées) :**

1. **Enregistrement** (run quotidien, à la découverte) : vidéo publiée il y a
   moins de `MAX_PUBLISH_LAG_DAYS` (4 j) → `tools/json/first_day/pending/<id>.json`.
   Jamais en `--bootstrap` ni `--no-post`.
2. **Capture exacte** : le chiffre « first 24h » = `viewCount` cumulé lu à
   **±`CAPTURE_TOLERANCE` (15 min) de `published_at + 24h`**, par :
   - la tâche one-off `TSM_YouTube_FirstDay_<id>` à `published_at+24h`
     (`--capture-first-day <id>`, capture seulement, ne poste pas ; se
     désinscrit ; si elle part hors fenêtre — PC en veille puis
     `StartWhenAvailable` — elle refuse de capturer) ;
   - ou une ligne du run quotidien dont le `snapshot_at` tombe dans la fenêtre
     (cas normal d'une sortie à minuit ET : le run de 00:05 ET est à ~+24h05).
   Figée dans `first_day/captures/<id>.json` (jamais écrasée). Hors fenêtre :
   pas de capture → la vidéo est écartée, **jamais** postée avec un total à
   +28 h étiqueté 24 h.
3. **Post** (`run_tick`, idempotent) : les vidéos en attente publiées à
   ≤ `RELEASE_GAP` (2 h) l'une de l'autre = **une release**. Quand plus aucun
   membre ne peut être capturé et que `POST_GRACE` (15 min) est passé après la
   dernière échéance, **un seul post** part (tâche
   `TSM_YouTube_FirstDayPost_<anchor>`, `--first-day-post`, re-planifiée à
   chaque run si la release grossit ; rattrapage par le tick de fin de run
   quotidien). Verrou `first_day/releases/<anchor>.posting` (O_EXCL) contre
   les doubles posts concurrents. Plus de post 48 h après la dernière
   échéance (`POST_MAX_DELAY`) : release marquée `skipped`. Échec du post →
   nouvel essai au tick suivant (run quotidien), avec les mêmes captures figées.

**Contenu du post :**

- **Audios Topic et vidéos de la chaîne principale ne sont jamais mélangés
  (décision Anas 2026-09-27)** : un post pour les audios, un pour les vidéos ;
  si la release a les deux → **un thread de 2 posts** (audios d'abord, puis
  vidéos — `GROUP_ORDER`, via `core.twitter.post_image_thread`). Sorties à un
  jour d'écart (cas Encore) = deux releases, deux posts séparés.
- Lignes = **chansons** (matching `title_groups` + `video_groups.json`) : dans
  un même post, les uploads d'une même chanson sont **additionnés** (les 2
  audios Topic d'Encore ; clip + lyric video sortis ensemble) ; sous-titre de
  ligne `Official Audio · 2 uploads` / `Lyric Video` / `Music Video + Lyric
  Video · 2 uploads`. Vidéo main non reconnue (annonce, live…) = sa propre
  ligne avec son titre complet.
- **Upload Topic d'une chanson qui avait déjà une vidéo YouTube avant la
  release = ré-upload, exclu** (ex. les 12 chansons du standard ré-uploadées
  avec le deluxe). Les vidéos de la chaîne principale sont toujours gardées
  (un nouveau clip/lyric video est une actu même si la chanson existe).
- Par post, 1 ligne → card seule (`🎥 | ❤️‍🔥 "<titre vidéo>" debuts with N views
  in its first 24 hours on YouTube.` ; audio : `"<chanson>" debuts with … on
  YouTube (official audio, 2 uploads combined).`) ; ≥ 2 lignes → tableau
  (header rouge YouTube, 900 px, miniatures 16:9, lignes triées par vues) +
  tweet `🎥 | ❤️‍🔥 "<album>" new songs in their first 24 hours on YouTube
  (official audio):` / `… lyric videos in their first 24 hours on YouTube:`
  (`music videos`… si un seul type, `new videos` sinon) puis `Titre —
  2,759,591` par ligne (`+N more` au-delà de la limite de caractères). Album = album catalogue
  commun à toutes les lignes (`display_title_for_album` → « The Life of a
  Showgirl: The Encore »), sinon tag album commun des uploads Topic (chansons
  pas encore backfillées), sinon « Taylor Swift ».
- Vues d'avant la détection : le chiffre posté est le `viewCount` cumulé à
  +24 h, donc les vues faites entre la sortie et le moment où le collecteur
  voit la vidéo (quelques secondes/minutes) sont toujours incluses.
- Rejeu Encore avec la nouvelle logique : **1 post** au lieu de 22 le 26/09
  (Patient Zero 2,759,591 · Babylon 2,080,524 · Pink Clouding 1,655,327 ·
  Cleveland! 1,651,000, chiffres réels mesurés 1-5 min après +24 h), STATION
  et lyric videos (tâches supprimées) → rien, faute de chiffre exact.

**Traçabilité :** chaque release résolue (`posted` / `skipped` / `cancelled`)
est archivée dans `first_day/releases/<anchor>.json` (membres, captures,
résultat par vidéo : `included` / `reupload` / `expired`) ; ses vidéos
reçoivent le lock historique `first_day_posted/<id>.lock` (= « déjà traitée »)
et quittent `pending/`/`captures/`. Tout `tools/json/` est commité par
`--commit`.

**Commandes :**

```powershell
python -m collectors.youtube.update_youtube --first-day-status   # releases en attente
python -m collectors.youtube.update_youtube --preview            # image + tweet, previews_and_sims/youtube-first-day/preview/
python -m collectors.youtube.update_youtube --first-day-cancel   # NE RIEN POSTER pour ce qui est en attente
```

**Pour empêcher un post : `--first-day-cancel`, pas la suppression des tâches**
(le run quotidien capturerait et posterait quand même — c'est exactement ce
qui s'est passé le 27/09 avec l'ancienne logique).

**Card seule (design inchangé, `render_youtube_card`) :** titre vidéo complet
(ne PAS retirer le préfixe "Taylor Swift ... -"), jusqu'à 4 lignes, paliers
de police `_title_font_size`, une seule case stat `+842,391 views` centrée,
titre + stat + date de sortie (`Released {mois} {jour}, {année} · {heure}
UTC`) dans un seul bloc `.body` centré verticalement. **Fix 2026-09-27** : la
taille de la valeur dépend de sa longueur (`_stat_font_size`) — à 46 px fixe,
`+12,966,141 views` débordait de la case sur le vrai post du 26/09.

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
- **Audit + nettoyage du 2026-09-05 — chansons vraiment sans vidéo officielle
  nulle part** (vérifié sur la chaîne principale UCqECaJ8Gagnn7YCbPEzWH6g +
  la chaîne auto-générée **"Taylor Swift - Topic"** `UCPC0L1d253x-KuMNwa05TpA`,
  qui couvre en "official audio" quasi tout le catalogue streamé y compris
  les deep cuts — 2122 vidéos, second catalogue utile pour l'association
  tsm_song_id↔vidéo mais PAS suivi pour les vues quotidiennes) : seulement 3
  vraies impasses — **Invisible** (Taylor Swift, 2006), **September -
  Recorded at The Tracking Room Nashville** (cover 2018), **Hold On (feat.
  Taylor Swift) [Live]** (chanson de Jack Ingram — aucun upload officiel
  trouvé même sur sa chaîne à lui). Le reste des ~50 restants (matching sur
  la seule chaîne principale) a un official audio sur la chaîne Topic, ou (3
  vrais collabs) sur la chaîne du collaborateur : Highway Don't Care →
  TimMcGrawVEVO, The Joker And The Queen → chaîne officielle Ed Sheeran,
  Both of Us → chaîne officielle B.o.B. `primary_artist` était mis à tort à
  "Taylor Swift" pour ces 4 collabs (+ Hold On/Jack Ingram) dans
  `db/discography/misc.json` — corrigé le 2026-09-05 (champ `artists` aussi
  mis à jour).
- **`song_family` pollué par le suffixe d'édition — corrigé le 2026-09-05**
  sur 5 tracks dont le `song_family` avait été auto-généré depuis le titre
  complet au lieu d'un identifiant propre, les empêchant de matcher leur
  vidéo pourtant déjà présente : `right where you left me - bonus track`
  (`..._bonus_track` → `right_where_you_left_me`), `it's time to go - bonus
  track` (idem → `it_s_time_to_go`), `the lakes - bonus track` (idem →
  `the_lakes`, aligné sur les 2 autres éditions de la chanson), `exile
  (feat. Bon Iver) - the long pond studio sessions` (`exile_feat_bon_iver` →
  `exile`, aligné sur l'entrée album), et la paire `Carolina` (Where The
  Crawdads Sing / Video Edition — 2 entrées quasi-doublons, `song_family`
  fusionné sur `carolina`). Fixé directement dans `db/discography/` (backup
  + `assign_tsm_ids.py --apply` pour resynchroniser le registre — 3
  renommages détectés via Spotify id partagé, comportement normal).
- **Audit de `video_groups.json` (390 → 369 entrées) le 2026-09-05** : ce
  fichier d'overrides manuels (édité via `scripts/youtube_grouping_editor/`,
  **commité dans git** malgré une note antérieure ici le disant gitignoré —
  vérifié, aucun des 3 JSON de ce dossier n'a de pattern `.gitignore`)
  contenait des clés figées d'avant le fix du bug de catalogue ci-dessus :
  26 entrées utilisaient une clé synthétique ad hoc (ex.
  `breathe_ft_colbie_caillat` au lieu de `breathe`, `end_game_behind_the_scenes`
  au lieu de `end_game`) au lieu du `song_family` réel — conséquence :
  appliquer les overrides (`load_manual_groups`, comme le fait
  `build_title_rows` en prod) faisait *régresser* le matching par rapport à
  l'automatique seul (68 chansons non matchées avec overrides vs 49 sans),
  touchant des singles connus (Breathe, End Game, Run, Safe & Sound, The
  Story Of Us...). Corrigé : renommage en place ou fusion dans le groupe
  propre déjà existant (dédoublonnage des `video_ids`) pour les 26 ; 4
  entrées ajoutées par erreur pour Carolina/exile/les 2 bonus tracks se sont
  révélées redondantes avec des groupes propres déjà présents et ont été
  supprimées — c'est le `song_family` en DB qui était fautif (cf. ci-dessus),
  pas l'association vidéo. Reste ~140 entrées à 1 seule vidéo qui ne
  correspondent à aucun `song_family` (contenu non-chanson légitime : vlogs,
  annonces de tournée, BTS, interviews — normal, pas un bug).
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
- Tâches Planificateur visibles à la racine du Task Scheduler pendant une
  sortie : `TSM_YouTube_FirstDay_<video_id>` (capture à `published_at+24h`,
  une par vidéo) et `TSM_YouTube_FirstDayPost_<anchor>` (post de la release,
  dernière échéance +15 min). Normal ; elles se suppriment à la résolution de
  la release. **Ne pas les supprimer à la main pour bloquer un post** →
  `--first-day-cancel`. État réel : `--first-day-status`.
- **Live projection trigger (ajoute 2026-09-23)** : `update_youtube.py::main()`
  appelle `collectors/billboard/live_trigger.py::trigger_live_projection()`
  juste avant le print final `[OK] Collecte terminée.`, apres
  `maybe_upload_youtube_to_r2()`. Deja hors d'atteinte en `--dry-run`/
  `--preview` (ces modes retournent plus tot dans `main()`). Best-effort,
  jamais bloquant. Voir `collector-billboard/CONTEXTE.md` § "Live projection".
