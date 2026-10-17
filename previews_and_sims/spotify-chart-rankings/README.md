# Latest Spotify Charts yearly rankings

Reference: the Spotify album-page card (`previews_and_sims/spotify-album-page-card`, e.g. TTPD 2026-10-12): cover-tone fade into #121212, hero band on top (cover, title, big date), then ONE full-width table with many columns. Never two side-by-side columns, never one detail block per row (owner decision 2026-10-17). All families use the latest available raw date for the chosen region/year. There is no historical fallback.

```powershell
python previews_and_sims/spotify-chart-rankings/preview.py
python previews_and_sims/spotify-chart-rankings/test_metrics.py
python collectors/spotify/charts/full_charts/generate_ranking_images.py --scope both
python collectors/spotify/charts/full_charts/generate_ranking_images.py --kind female_debuts female_albums --scope all
```

Open `latest/index.html` for the gallery. Each PNG has a 310px phone copy. The older `cards/` directory contains superseded examples, not used by the latest generator. No posting, upload or scheduling.

## Selection

- `--scope taylor`: every Taylor entry, with its real overall/subset rank. No top-50 cutoff.
- `--scope all`: top 15 only, generated only if Taylor is in that top 15.
- `--scope both` (CLI default): both sets.
- One table per image (up to 15 rows); longer lists paginate by 15 without losing an entry.
- Columns (every year-only stat says it in its header, owner request 2026-10-17): songs `Streams in 2026 | Days in 2026 | Peak in 2026 | Best day in 2026 | Debut | Now`; debuts `Debut streams | Debut | Streams in 2026 | Days in 2026 | Peak in 2026 | Best day in 2026 | Now`; albums `Streams in 2026 | Days in 2026 | Charted tracks | Best day in 2026 | First day in 2026 | Now`.
- Movement vs the previous chart day: an entry absent from yesterday's ranking = `NEW` (blue), the entries it passes get `▼1`. Covered by `test_new_debut_is_new_and_pushes_others_down` (in-memory row, nothing written).
- Kinds: `songs`, `female`, `debuts`, `female_debuts`, `albums`, `female_albums`.
- Debuts rank by exact first-ever chart-day streams when that debut fell in the selected year. Other kinds rank by the sum of this year's chart streams.
- Female means female solo lead artist, matching the existing stats convention. All-female groups are not in this solo-artist subset.

## Metrics

`ranking_metrics.py` calculates directly from verified daily Top 200 rows:

- Streams earned inside the region's Spotify Daily Top 200, not all Spotify streams.
- Days actually charting within the selected year, not lifetime appearances.
- Best daily position and daily stream peak within the year, not lifetime peaks.
- First-ever song debut date/rank/streams when it occurred in the year. Older debuts say Pre-2026, not fake zeros.
- Still charting at the as-of date with exact song rank, or OUT with last charting date.
- Albums sum charting tracks, use explicit existing album merges and exclude singles. Album days count days with at least one charting track; peak streams are the best daily sum of those tracks. Official album chart positions/debuts cannot be derived from song charts and are not invented. Instead, album rows show chart-track count and first charting day within the selected year.

Every CSV row from January 1 through the as-of date must match its raw Top 200 snapshot, with 200 ranks, unique track IDs and exact streams. Unknown lead classifications block current/comparison ranks when they could affect displayed entries. Canonical track IDs cannot overlap on a chart date. Album credit conflicts require a sourced `db/spotify_charts_full/album_credits.json` mapping. The mixed KPop Demon Hunters cast is not arbitrarily attributed to a track lead. Covers use exact IDs and the existing local cache.

Each ranking has an audit JSON with all selected rows and dates. `latest-manifest.json` lists ready, skipped and blocked families. Errors are logged with exact reasons. Regression tests use real local rows without modifying source state and cover missing/duplicate CSV rejection, unlimited Taylor entries, original ranks, year-only metrics, debut sorting and missing-classification blocking.

## Files and options

- `collectors/comp/spotify_ranking_page.py`: shared renderer.
- `collectors/spotify/charts/full_charts/generate_ranking_images.py`: local CLI.
- `collectors/spotify/charts/full_charts/ranking_metrics.py`: exact metrics.
- `preview.py`: latest-date real-data gallery and 310px QA artifacts.

Options: `--region` (global), `--year` (current year), `--as-of` (explicit date only), `--compare-to` (prior chart), `--output-dir` (latest/). `--top` accepts 15 for overalls and does not limit Taylor scope.
