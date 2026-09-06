#!/usr/bin/env python3
"""Single source of truth for R2 key prefixes/patterns used across scripts/ and collectors/.

Every constant here preserves its existing env-var override where one already
existed (do not hardcode away ops' ability to override in .env). This module
only centralizes NAMING -- it does not touch boto3 client construction, retry
logic, or bucket selection (see r2.py, upload_ap_r2.py, etc. for those,
unchanged).

Mirrored (NOT imported -- cross-repo/cross-language boundary) in
tsm-frontend/api/data/r2_keys.py. If a value here changes, update that file
in the same session -- see its docstring.
"""
from __future__ import annotations

import os

# --- Spotify streams history (scripts/r2.py, collectors/spotify/streams/fix_one.py) ---
STATIC_HISTORY_PREFIX = os.getenv("R2_STATIC_HISTORY_PREFIX", "history")
TRACK_HISTORY_PREFIX = os.getenv("SPOTIFY_R2_TRACK_PREFIX", "history-by-track")

# --- Static JSON/CSV/PNG data exported for the frontend, incl. dated
#     per-slug snapshots like data/swift_top_100_2026-08-01.json
#     (scripts/r2.py::upload_static_data) ---
STATIC_DATA_PREFIX = os.getenv("R2_STATIC_DATA_PREFIX", "data")

# --- Raw mirror of db/, overwritten in place -- doesn't grow over time
#     (scripts/r2.py::upload_db_files) ---
DB_PREFIX = os.getenv("R2_DB_PREFIX", "db")

# --- Apple Music track artwork cache, content-addressed by
#     md5(source Apple CDN url) -- the only R2 data category confirmed safe
#     to prune, see scripts/prune_apple_music_images.py
#     (scripts/r2.py::upload_apple_music_images,
#     scripts/download_apple_music_images.py) ---
IMAGES_APPLE_MUSIC_PREFIX = os.getenv("R2_IMAGES_PREFIX", "images/apple-music")

# --- Apple Music history/snapshots (scripts/upload_ap_r2.py) ---
APPLE_MUSIC_HISTORY_BY_SONG_PREFIX = "apple-music/history-by-song"
APPLE_MUSIC_DB_PREFIX = "apple-music/db"
# Read by tsm-frontend/api/data/loader.py for arbitrary historical dates --
# real historical data, never delete.
APPLE_MUSIC_SNAPSHOTS_PREFIX = "apple-music/snapshots"
APPLE_MUSIC_HISTORY_BY_DATE_PREFIX = "apple-music/history-by-date"

# --- Deezer history/snapshots (scripts/upload_deezer_r2.py) ---
DEEZER_HISTORY_BY_SONG_PREFIX = "deezer/history-by-song"
DEEZER_DB_PREFIX = "deezer/db"
# Read by tsm-frontend/api/data/loader.py for arbitrary historical dates --
# real historical data, never delete.
DEEZER_SNAPSHOTS_PREFIX = "deezer/snapshots"

# --- Billboard/TayBoard per-track chart points (scripts/chartr2.py) ---
# NOTE: intentionally named differently from TRACK_HISTORY_PREFIX above
# ("history-by-track") even though both are "per-track history points" for a
# similar concept. This is a KNOWN, LIVE-PROD naming inconsistency -- do NOT
# rename/unify without a data-migration/copy step (out of scope for the R2
# key-centralization pass that introduced this module). See
# .claude/skills/scripts-maintenance/SKILL.md for the full "pérenne vs cache"
# writeup.
CHART_HISTORY_GLOBAL_BY_TRACK_PREFIX = "chart-history-global-by-track"

# --- Small cross-repo cache, written by scripts/generate_home_highlights.py,
#     read/written by tsm-frontend/api/data/precompute_cache.py via
#     api/routes/version.py and api/routes/home_highlights.py ---
CACHE_HOME_HIGHLIGHTS_KEY = "cache/home_highlights.json"
CACHE_VERSION_KEY = "cache/version.json"

# --- Cross-repo convention: backend read side (scripts/fetch_issues.py,
#     scripts/fetch_hiring.py), frontend write side
#     (tsm-frontend/api/routes/hiring.py, report_issue.py). Bare (no
#     trailing slash) -- callers append "/" when building a Prefix= filter,
#     matching tsm-frontend/api/routes/hiring.py's existing convention. ---
HIRING_PREFIX = "hiring"
REPORT_PREFIX = "report-"
REPORT_IMG_PREFIX = "report-img-"

# --- Open Graph share screenshots: PNG captures of live site pages, written by
#     scripts/generate_og_screenshots.py, streamed to social crawlers by
#     tsm-frontend/api/routes/og_images.py as og:image. Regenerable cache
#     (2x/day), safe to prune. Key shape: og/<slug>.png where <slug> matches
#     _og_slug() on both sides (mirrored like this module -- keep in sync). ---
OG_SCREENSHOTS_PREFIX = "og"
