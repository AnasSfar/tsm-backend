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

- Depuis le 2026-07-30 : prod tourne via `cron` sur un VPS OVH
  (`~/tsm-backend/run_youtube_vps.sh`, timezone `Europe/Paris`), toujours
  `06:05` Europe/Paris. Le job Windows `TSM YouTube Videos Daily` est
  désactivé. Détail complet du déploiement : `REPO_CONTEXT.md` section
  « Déploiement VPS OVH » et `OVH.md`.

Le `.bat` local (obsolète, gardé pour référence/rollback) :

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

## Pieges

- Ne pas utiliser un delta multi-jours comme record quotidien.
- `--commit` est volontaire; ne pas committer sans demande.
- `--force` peut remplacer des lignes existantes; verifier la date et les
  sorties avant usage.
