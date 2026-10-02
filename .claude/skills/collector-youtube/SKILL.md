---
name: collector-youtube
description: "Work safely on collectors/youtube: Taylor Swift official YouTube video view collection, exact daily view deltas, title grouping, YouTube API usage, history CSVs, exports, posting controls, scheduler command, and missed-day handling. Use before auditing, debugging, running, or modifying YouTube collector code."
---

# Collector YouTube

Read `CONTEXTE.md` before changing or running anything under
`collectors/youtube`.

Use `data-rules` for exact-data decisions and `pipeline-ops` for scheduled local
runs.

Core rule: `daily_views` is an exact one-calendar-day delta. If a previous
calendar snapshot is missing, keep the one-day value blank and store the exact
multi-day gain as period data.

Page sections (`source` column of `youtube_title_history.csv`): `all`
(TayBoard, never filter it), `videos`, `audios`, `extras`, and `songs` =
videos + audios (2026-09-30). Details in CONTEXTE.md.

"New video" button feed (`core/new_releases.py`, 2026-09-30):
`db/youtube_new_releases.json`, rebuilt from the first-day registry after each
capture/post/daily run and pushed to R2 — the site shows the exact 24h figure
of the post, `waiting` before it, `missed` (no figure) if the window was missed.

"First 24 hours" posts (`core/first_day.py`, 2026-09-27): videos published
together = ONE release; Topic audios and main-channel videos are separate
posts (a 2-post thread when a release has both), one row per song, uploads of
a song summed within a post, Topic re-uploads of existing songs dropped, and
only with a view count captured within ±15 min of `published_at + 24h`.

"First week" daily posts (`core/first_week.py`, 2026-10-01): only main-channel
"Official Music Video" uploads whose first-24h post went out. Day N = views
between +(N-1)×24h and +N×24h from `published_at` (never the NY collection
day), each mark read at the exact second by task `TSM_YouTube_FirstWeek_<id>`.
One post per video per day, Day 2..Day 7 only: text `DAY 1 - X / DAY 2 - X (+%)`
+ that day's video card; Day 7 = first-week bar chart. A missed mark = Day N
and N+1 shown n/a, never estimated, nothing posted for an unknown day.
Status: `--first-week-status`.

A video published between NY midnight and the ~00:05 ET run gets no row for
the day that just ended: its release day counts all its views, including the
ones before the collector first saw it. To stop a post use
`--first-day-cancel`, never delete the Scheduled Tasks by hand.

Safe checks:

```powershell
python -m collectors.youtube.videos.update_youtube --dry-run
python -m collectors.youtube.videos.update_youtube --debug
python -m collectors.youtube.videos.update_youtube --first-day-status  # releases en attente
python -m collectors.youtube.videos.update_youtube --preview  # aperçu du post first-day (previews_and_sims/)
python previews_and_sims/youtube-first-day/simulate.py         # rejeu Encore, état isolé
```
