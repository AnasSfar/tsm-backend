---
name: collector-apple-music
description: "Work safely on collectors/apple_music: Apple Music chart collectors, combined country/genre runs, CSV snapshots, export/upload, image generation, MusicKit token handling, storefront discovery, and exact Apple Music chart data. Use before auditing, debugging, running, or modifying Apple Music collector code."
---

# Collector Apple Music

Read `CONTEXTE.md` before changing or running anything under
`collectors/apple_music`.

Also use:

- `data-rules` for exact-data and posting decisions.
- `image-gen` before changing generated Apple Music chart/card images.
- `pipeline-ops` when the task is about scheduled local runs or operational
  recovery.

Core rules:

- Do not publish partial Apple Music snapshots as complete data.
- Match primarily by `apple_music_id`; title fallback exists only for old rows
  without IDs.
- Do not treat already released songs as `NEW` just because old Apple Music
  history is incomplete.
- Do not run subset country/genre commands against the real current date unless
  the goal is explicitly a partial snapshot.

Quick safe checks:

```powershell
python .\collectors\apple_music\run_apple_music.py --help
python .\scripts\export_apple_music.py --help
python .\scripts\upload_ap_r2.py --dry-run
```
