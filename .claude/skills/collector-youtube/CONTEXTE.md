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

- Prod tourne en automatique via le **Planificateur de tâches Windows
  local** (tâche `TSM YouTube Videos Daily`), pas de service manuel à
  lancer. Ça tourne sur le PC d'Anas — si le PC est éteint/en veille à
  l'heure du run, le job ne s'exécute pas ce jour-là (pas de retry auto ; cf.
  `manual_trusted` / [[tsm-streams-pipeline-ops]] pour le pattern de
  rattrapage d'un jour manqué).
- Historique : du 2026-07-30 au 2026-08-17, ça tournait via `cron` sur un
  VPS OVH (`06:05` Europe/Paris) pendant que la tâche Windows était
  désactivée. Le VPS a été décommissionné le 2026-08-17 (coût, pas un
  problème technique) et la tâche Windows réactivée — retour à l'état
  ci-dessus. Détail complet : `REPO_CONTEXT.md` section « Déploiement VPS
  OVH » et `OVH.md`.

Le `.bat` local :

```text
collectors/youtube/run_youtube.bat
```

## Options utiles

```powershell
python -m collectors.youtube.videos.update_youtube --dry-run
python -m collectors.youtube.videos.update_youtube --debug
python -m collectors.youtube.videos.update_youtube --no-post
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

- `date`
- `snapshot_at` (depuis 2026-08-29) : horodatage UTC ISO 8601 exact du run
  (pris juste après le batch-fetch des stats). `daily_views` d'une date D =
  delta entre `snapshot_at(D-1)` et `snapshot_at(D)`. Lignes antérieures =
  colonne vide (ajout rétro-compatible en tête des `CSV_FIELDNAMES` /
  `TITLE_CSV_FIELDNAMES`, migration auto du header via
  `csv_utils._ensure_fieldnames` / `write_title_history`). Le frontend
  (`api/routes/youtube.py` → `window_start`/`window_end`, rendu par
  `pages/YouTube.jsx`) l'utilise pour afficher la vraie fenêtre horaire.
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
  `--no-post` (même flag que la notification ntfy).

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

- **TayBoard scoring** (ajoute 2026-08-14, `collectors/billboard/swift_top_100.py`) :
  `db/youtube_title_history.csv` (`daily_views` par titre groupe) alimente
  `units_youtube` (poids `YOUTUBE_WEIGHT`, defaut 0.3). Si le format de ce
  CSV change (colonnes renommees, grouping modifie), verifier
  `_weekly_youtube_views()` et le skill `collector-billboard`.

## Pieges

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
- Tâches Planificateur `TSM_YouTube_FirstDay_<video_id>` visibles dans
  Task Scheduler (racine, pas dans un dossier dédié) : normal, une par
  vidéo tout juste découverte, en attente de son déclenchement à
  `published_at+24h`. Elles se suppriment seules après exécution — si
  plusieurs traînent avec une date de déclenchement passée, ça signale un
  souci (`run_post_first_day` planté avant `_unschedule_first_day_task`,
  ou le run correspondant jamais arrivé) : vérifier via `Get-ScheduledTask
  -TaskName 'TSM_YouTube_FirstDay_*'` en PowerShell.
