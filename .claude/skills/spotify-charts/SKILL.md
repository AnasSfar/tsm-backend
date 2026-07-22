---
name: spotify-charts
description: Work safely on the TSM Spotify Charts collectors, daily chart posting, worldwide snapshots, regional chart exports, backfill/rebuild scripts, Spotify session/token handling, chart cards, locks, and exact-data history pipelines under collectors/spotify/charts. Use when editing, debugging, optimizing, explaining, or running Spotify Charts code for TSM.
---

# Spotify Charts

## Start Here

Read `CONTEXTE.md` before changing or running anything in `collectors/spotify/charts`.
It is the local map for this pipeline: entrypoints, folder roles, output paths,
locks, backfill flow, exact-data rules, and common commands.

When the task is narrow, load only the relevant section from `CONTEXTE.md`:

- Daily orchestration: read "Entrypoints" and "Run quotidien".
- Backfill: read "Backfill historique" and "Optimisation sans perte de data".
- Worldwide collection: read "worldwide/" and "Snapshots worldwide".
- Regional posting/export: read "global/, fr/, us/, uk/" and "Locks".
- Cards/images: read "Cards et images".
- Data repairs: read "Regles d'integrite TSM" and "Rebuild/sync".

## Operating Rules

Preserve exact stats. Never invent streams, chart positions, dates, regions,
rank movement, total days, percentages, milestones, or active/inactive status.
If data is missing or ambiguous, leave the run blocked or pending with the exact
reason.

Prefer root-cause fixes over broad fallbacks. Use explicit mappings and verified
snapshots/history rows when repairing historical data. Do not create generic
rules such as "same total means zero streams" or "same family means duplicate".

Use `run_all_charts.py` for normal daily work unless the task specifically
targets an underlying script. Use `scripts/backfill_spotify_charts_history.py`
for historical backfills when you need advanced flags such as `--no-sync`,
`--limit`, or `--sleep`.

Do not edit generated history/snapshot files casually. If a correction touches
historical data, compare against an exact source and report what changed.

## Verification

After code changes, run the smallest safe check available:

- CLI shape: `python .\collectors\spotify\charts\run_all_charts.py --help`
- Backfill wrapper shape: `python .\scripts\backfill_spotify_charts_history.py --help`
- Worldwide collector shape: `python .\collectors\spotify\charts\worldwide\daily.py --help`

For data-changing commands, prefer `--dry-run` first when the script supports it.
Networked Spotify collection, R2 upload, Twitter posting, WARP, and git push may
need explicit user approval or a deliberate manual run.
