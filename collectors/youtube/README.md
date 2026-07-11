# TSM YouTube Collectors

This directory is reserved for YouTube-related collectors.

## YouTube Videos

Tracks daily view counts for videos uploaded to Taylor Swift's official YouTube
channel (`UCqECaJ8Gagnn7YCbPEzWH6g`).

Tracking convention:

- YouTube daily snapshots should run just after the US Eastern day closes.
- The Windows Task Scheduler job `TSM YouTube Videos Daily` is set to run at
  `06:05` Europe/Paris, which corresponds to about `00:05` US Eastern.
- `daily_views` is an exact delta between two collected total-view snapshots.
  If the previous calendar snapshot is missing, do not treat the delta as a
  one-day chart value.

Canonical command:

```powershell
python -m collectors.youtube.videos.update_youtube
```

Backward-compatible command:

```powershell
python -m collectors.youtube.update_youtube
```

Useful options:

```powershell
python -m collectors.youtube.videos.update_youtube --dry-run
python -m collectors.youtube.videos.update_youtube --debug
python -m collectors.youtube.videos.update_youtube --no-post
python -m collectors.youtube.videos.update_youtube --bootstrap
python -m collectors.youtube.videos.update_youtube --commit
python -m collectors.youtube.videos.update_youtube --force --commit
```

Environment:

- `YOUTUBE_API_KEY`: required YouTube Data API v3 key.
- `NTFY_TOPIC_YOUTUBE`: optional ntfy topic, defaults to `taylormuseum-youtube`.

Output columns:

- `date`
- `video_id`
- `title`
- `rank`
- `previous_rank`
- `rank_change`
- `total_rank`
- `previous_total_rank`
- `total_rank_change`
- `published_at`
- `duration`
- `thumbnail_url`
- `total_views` (`viewCount` from YouTube)
- `daily_views`
- `daily_change`
- `daily_change_pct`
- `period_gain_views`
- `period_days`
- `period_label`
- `like_count`
- `comment_count`
- `category_id`
- `live_broadcast_content`
- `privacy_status`
- `upload_status`
- `tags` as a JSON array string

Files:

- `db/youtube_views_history.csv`
- `db/youtube_title_history.csv`
- `collectors/youtube/tools/json/video_db.json`
- `collectors/youtube/tools/json/youtube_history.json`

`youtube_views_history.csv` keeps one row per video. `youtube_title_history.csv`
groups those rows by matched song/title so official videos, lyric videos,
official audios, and visualizers can be compared as one title-level total.
When a calendar day is missed, the exact delta is stored as `period_gain_views`
with a label such as `2-day gain`; `daily_views` stays empty so it is not ranked
or posted as a one-day value.

This collector is only for YouTube video uploads. YouTube Music charts should be
implemented separately, for example under `collectors/youtube_music/`.
