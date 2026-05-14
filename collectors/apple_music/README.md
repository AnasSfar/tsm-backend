# Apple Music collectors bundle

This bundle provides a richer Apple Music collector package designed to drop into the same project structure you described.

## What is included
- Daily/current chart collectors:
  - `collectors/apple_music/global.py`
  - `collectors/apple_music/global_albums.py`
  - `collectors/apple_music/genre_charts.py`
  - `collectors/apple_music/country_charts.py`
  - `collectors/apple_music/country_albums.py`
  - `collectors/apple_music/genre_album_charts.py`
  - `collectors/apple_music/music_video_charts.py`
- Artist-page collectors, available for separate use but not part of the daily chart runner:
  - `collectors/apple_music/ts_page.py`
  - `collectors/apple_music/top_music_videos.py`
- shared core utilities under `collectors/apple_music/core/`

## Improvements over the original scripts
- shared HTTP session with retries
- shared token extraction and token cache
- shared CSV helpers
- normalized rank matching across days
- rerun-safe CSV rewriting for the current day
- richer CSV schema with Apple Music IDs and URLs
- richer metadata (duration, release date, ISRC, content rating, genres)
- cleaner separation between fetching, filtering, and persistence
- automatic Apple Music storefront discovery, with optional countries override via `APPLE_MUSIC_COUNTRIES`

## Expected project layout after merge
Copy the `collectors/apple_music/` folder into your repository and keep your existing `db/` and `scripts/` folders.

These scripts expect:
- `db/` to exist at repo root
- `scripts/export_apple_music.py` to exist if you want post-run export

## CSV outputs
- `db/apple_music_ts_top_songs.csv`
- `db/apple_music_global.csv`
- `db/apple_music_genre_charts.csv`
- `db/apple_music_country_charts.csv`
- `db/apple_music_country_albums.csv`
- `db/apple_music_genre_album_charts.csv`
- `db/apple_music_music_video_charts.csv`
- `db/apple_music_ts_top_videos.csv`

## Optional environment configuration
- `APPLE_MUSIC_COUNTRIES=us,fr,gb,de,au,ca,jp,...` to limit countries; when unset, the collectors fetch all storefronts exposed by Apple Music
- `APPLE_MUSIC_CHART_LIMIT=200` controls country/genre/chart depth
- `APPLE_MUSIC_WORKERS=12` controls concurrent genre chart requests
- `APPLE_MUSIC_TIMEOUT=20`
- `APPLE_MUSIC_RETRY_TOTAL=3`
- `APPLE_MUSIC_RETRY_BACKOFF=1.0`

## Notes
- Country, album, genre, video, artist, and global endpoints use a public MusicKit token extracted from the Apple Music web app.
- `global_albums.py` is kept as a legacy helper only; Apple Music does not expose a true global albums storefront through this endpoint, so it is not part of the daily runner.
- Token is cached at `collectors/apple_music/tools/json/apple_music_token.json`.
