---
name: spotify-core
description: "Work safely on collectors/spotify/core shared helpers: data paths, Twitter posting, git operations, notifications, chart comments, retention cleanup, download utilities, formatting, and gates used by Spotify streams/charts pipelines. Use before modifying shared Spotify collector infrastructure."
---

# Spotify Core

Read `CONTEXTE.md` before changing `collectors/spotify/core`.

Also load the downstream collector skill affected by the helper:

- `spotify-streams` for stream collection/export/posting.
- `spotify-charts` for chart collection/cards/posting.
- `data-rules` for any exact-data or posting behavior.

Because these helpers are shared, verify every affected caller, not just the
file you edit.
