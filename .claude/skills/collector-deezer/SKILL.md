---
name: collector-deezer
description: "Work safely on collectors/deezer: Deezer global chart and Taylor Swift top-tracks collectors, fan-count stats, CSV snapshots, export/upload, and TayBoard scoring integration. Use before auditing, debugging, running, or modifying Deezer collector code."
---

# Collector Deezer

Read `CONTEXTE.md` before changing or running anything under
`collectors/deezer`.

Also use:

- `data-rules` for exact-data and posting decisions.
- `pipeline-ops` when the task is about scheduled runs or operational
  recovery.
- `collector-billboard` when touching the TayBoard scoring integration
  (`swift_top_100.py`'s `DEEZER_*` weight constants and readers).

Core rules:

- Deezer's public API needs no auth (50 req/5s per IP) — do not add token
  logic; `core/http.py` is intentionally generic.
- `/chart/0/tracks` is geolocated by request IP, not a literal worldwide
  chart — never relabel it as "global" without noting this caveat; see
  "Known caveat" below.
- No NEW/RE inference from release dates in v1 (Deezer gives no
  `release_date` on chart list items) — `previous_rank` blank means blank,
  never guess a NEW badge.
- Match primarily by `deezer_track_id`; title fallback exists only when the
  ID lookup misses.

Quick safe checks:

```powershell
python .\collectors\deezer\run_deezer.py --help
python .\scripts\export_deezer.py
python .\scripts\upload_deezer_r2.py --dry-run
```
