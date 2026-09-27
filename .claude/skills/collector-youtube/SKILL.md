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

"First 24 hours" posts (`core/first_day.py`, 2026-09-27): videos published
together = ONE release; Topic audios and main-channel videos are separate
posts (a 2-post thread when a release has both), one row per song, uploads of
a song summed within a post, Topic re-uploads of existing songs dropped, and
only with a view count captured within ±15 min of `published_at + 24h`.

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
