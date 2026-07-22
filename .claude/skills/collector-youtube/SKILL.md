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

Safe checks:

```powershell
python -m collectors.youtube.videos.update_youtube --dry-run
python -m collectors.youtube.videos.update_youtube --debug
```
