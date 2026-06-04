# TSM Data Layout Audit

Generated for `C:\Users\sfara\Documents\GitHub\tsm-backend`.

## Canonical Layout

- `code`: `collectors/`
- `business_data`: `db/`
- `daily_snapshots`: `snapshots/`
- `runtime_outputs`: `runtime/`
- `web_exports`: `runtime/exports/web`
- `legacy_website_archive`: `data/_archive/legacy-website`

## File Counts

- `db`: 82 file(s), 70 tracked
- `data`: 15193 file(s), 388 tracked
- `snapshots`: 569 file(s), 7 tracked
- `runtime`: 546 file(s), 0 tracked
- `legacy_website`: 1579 file(s), 40 tracked
- `collectors`: 53913 file(s), 350 tracked
- `scripts`: 2789 file(s), 2759 tracked

## Risky Tracked Files

- Runtime/cache/log-like tracked files: 3209
- Backup files tracked: 24
- Legacy website export files tracked: 19
- Duplicate checksum groups: 542

## Recommended Actions

- `db/*.bak` -> `archive` into `data/_archive/manual-backups/YYYY-MM-DD/`
- `website/` -> `archive` into `data/_archive/legacy-website/`
- `website/site/data and history` -> `replace writes` into `runtime/exports/web/`
- `browser caches and logs` -> `ignore` into `runtime/cache and runtime/logs`
- `snapshots` -> `keep` into `snapshots/`

## Samples

### tracked_runtime_noise
- `collectors/spotify/charts/fr/run_daily.log`
- `collectors/spotify/charts/fr/tools/__pycache__/config.cpython-313.pyc`
- `collectors/spotify/charts/fr/tools/__pycache__/filter.cpython-313.pyc`
- `collectors/spotify/charts/fr/tools/__pycache__/twitter_config.cpython-313.pyc`
- `collectors/spotify/charts/fr/tools/scripts/__pycache__/config.cpython-313.pyc`
- `collectors/spotify/charts/fr/tools/scripts/__pycache__/git_ops.cpython-313.pyc`
- `collectors/spotify/charts/global/run_daily.log`
- `collectors/spotify/charts/global/tools/__pycache__/config.cpython-313.pyc`
- `collectors/spotify/charts/global/tools/__pycache__/filter.cpython-313.pyc`
- `collectors/spotify/charts/global/tools/__pycache__/global_filter_chart.cpython-313.pyc`
- `collectors/spotify/charts/global/tools/run_daily.log`
- `collectors/spotify/charts/global/tools/script/__pycache__/config.cpython-313.pyc`
- `collectors/spotify/charts/global/tools/script/__pycache__/git_ops.cpython-313.pyc`
- `collectors/spotify/core/__pycache__/__init__.cpython-313.pyc`
- `collectors/spotify/core/__pycache__/download.cpython-313.pyc`
- `collectors/spotify/core/__pycache__/fmt.cpython-313.pyc`
- `collectors/spotify/core/__pycache__/history.cpython-313.pyc`
- `collectors/spotify/core/__pycache__/logger.cpython-313.pyc`
- `collectors/spotify/core/__pycache__/notify.cpython-313.pyc`
- `collectors/spotify/core/__pycache__/twitter.cpython-313.pyc`
- `collectors/spotify/streams/__pycache__/export_for_web.cpython-312.pyc`
- `collectors/spotify/streams/__pycache__/export_for_web.cpython-313.pyc`
- `collectors/spotify/streams/__pycache__/fix_streams.cpython-313.pyc`
- `collectors/spotify/streams/__pycache__/generate_streams_image.cpython-313.pyc`
- `collectors/spotify/streams/__pycache__/update_streams.cpython-313.pyc`
- `collectors/spotify/streams/extras/__pycache__/export_for_web.cpython-313.pyc`
- `collectors/spotify/streams/history/2026/03/2026-03-21/posted.lock`
- `collectors/spotify/streams/history/2026/03/2026-03-22/posted.lock`
- `collectors/spotify/streams/history/2026/03/2026-03-23/posted.lock`
- `collectors/spotify/streams/history/2026/03/2026-03-24/posted.lock`

### tracked_backups
- `db/charts_history_uk.csv.bak.20260407-054202`
- `db/charts_history_us.csv.bak.20260404-234722`
- `db/charts_history_us.csv.bak.20260405-040119`
- `db/discography/albums/1989.json.bak`
- `db/discography/albums/1989_taylor_s_version.json.bak`
- `db/discography/albums/evermore.json.bak`
- `db/discography/albums/fearless.json.bak`
- `db/discography/albums/fearless_taylor_s_version.json.bak`
- `db/discography/albums/folklore.json.bak`
- `db/discography/albums/lover.json.bak`
- `db/discography/albums/midnights.json.bak`
- `db/discography/albums/red.json.bak`
- `db/discography/albums/red_taylor_s_version.json.bak`
- `db/discography/albums/reputation.json.bak`
- `db/discography/albums/speak_now.json.bak`
- `db/discography/albums/speak_now_taylor_s_version.json.bak`
- `db/discography/albums/taylor_swift.json.bak`
- `db/discography/albums/the_life_of_a_showgirl.json.bak`
- `db/discography/albums/the_taylor_swift_holiday_collection.json.bak`
- `db/discography/albums/the_tortured_poets_department.json.bak`
- `db/discography/features.json.bak`
- `db/discography/misc.json.bak`
- `db/discography/songs.json.bak`
- `website/site/history/2026-04-09.json.bak`

### tracked_legacy_website_exports
- `website/site/data/applemusic.json`
- `website/site/data/covers/latest.jpg`
- `website/site/data/headers/1989 (taylor's version).png`
- `website/site/data/headers/1989.png`
- `website/site/data/headers/evermore.png`
- `website/site/data/headers/fearless (taylor's version).png`
- `website/site/data/headers/fearless.png`
- `website/site/data/headers/folklore.png`
- `website/site/data/headers/lover.png`
- `website/site/data/headers/midnights.png`
- `website/site/data/headers/red (taylor's version).png`
- `website/site/data/headers/red.png`
- `website/site/data/headers/reputation.png`
- `website/site/data/headers/speak now (taylor's version).png`
- `website/site/data/headers/speak now.png`
- `website/site/data/headers/taylor swift.png`
- `website/site/data/headers/the life of a showgirl.jpg`
- `website/site/data/headers/the tortured poets department.png`
- `website/site/history/2026-04-09.json.bak`
