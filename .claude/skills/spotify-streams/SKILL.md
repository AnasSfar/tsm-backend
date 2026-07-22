---
name: spotify-streams
description: "Work safely on collectors/spotify/streams: exact Spotify stream total collection, daily deltas, pending/retry handling, history repairs, exports, R2 uploads, Twitter posting steps, locks, and recovery scripts. Use before auditing, debugging, running, optimizing, or modifying Spotify streams code."
---

# Spotify Streams

Read `CONTEXTE.md` before changing or running anything under
`collectors/spotify/streams`.

Also use:

- `data-rules` before any streams/history/export/posting change.
- `pipeline-ops` for scheduled run operations and recovery.
- `image-gen` before changing generated stream/card images.

Non-negotiable:

- Exact totals and daily deltas only.
- Missing non-extra data blocks final export/posting.
- Never convert ambiguous `same_total` or stale values into public zeros.
- Preserve `total(J) = total(J-1) + daily(J)` for every correction.

Safe checks:

```powershell
python .\collectors\spotify\streams\update_streams.py --dry-run
python .\collectors\spotify\streams\best_day_since.py --help
python .\collectors\spotify\streams\fix_one.py --help
```
