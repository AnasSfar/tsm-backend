# Apple Music collectors bundle

This bundle provides a richer Apple Music collector package designed to drop into the same project structure you described.

## What is included
- `collectors/apple_music/ts_page.py`
- `collectors/apple_music/global.py`
- `collectors/apple_music/genre_charts.py`
- `collectors/apple_music/country_charts.py`
- `collectors/apple_music/country_albums.py`
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
- optional countries override via `APPLE_MUSIC_COUNTRIES`

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

## Optional environment configuration
- `APPLE_MUSIC_COUNTRIES=us,fr,gb,de,au,ca,jp,...`
- `APPLE_MUSIC_TIMEOUT=20`
- `APPLE_MUSIC_RETRY_TOTAL=3`
- `APPLE_MUSIC_RETRY_BACKOFF=1.0`

## Notes
- Country charts use the public RSS feed.
- Genre charts and artist/global endpoints use a public MusicKit token extracted from the Apple Music web app.
- Token is cached at `collectors/apple_music/tools/json/apple_music_token.json`.
