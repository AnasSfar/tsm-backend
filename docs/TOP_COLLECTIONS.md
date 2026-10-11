# Top Collections

Frontend branch: `redesign/eras-theme`.

The page lives at `/spotifystreams/top-collections`; date URLs use
`/date/YYYY-MM-DD`, and details use `/:collectionSlug/date/YYYY-MM-DD`.
The existing Spotify Streams date picker changes both list and detail dates.
Search, collection type and daily/total/change sorting are available.

## Membership

Edit `scripts/build_top_collections.py` and regenerate the frontend's
`frontend/src/data/collections.json` with `--output` pointing to that file.
This resolves exact catalog file + full title to one explicit Spotify ID at
build time, refusing missing/ambiguous entries and repeated collection IDs.
The frontend never matches by title, song family or inferred category.

The selected recording is Taylor's Version when available on Spotify; other
studio versions are not added to its total. Setlists are editorially scoped:

- Eras 2023: Glendale opening-night main set, excluding surprise songs and
  spoken interludes (42 recordings).
- Eras 2024: Paris May 9 main set, excluding surprise songs, counting each
  song in the TTPD mashup separately (44 recordings).
- reputation Tour: regular main set, excluding surprise songs and guests,
  counting mashup components separately (22 recordings).
- Singles, Soundtracks and Collabs are initial curated selections, not claims
  of exhaustive discography coverage.
- Songwriting starts with This Is What You Came For and Little Big Town's
  Better Man. Extend with verified credits and exact recording IDs.

Setlist references:
[Eras main sets](https://en.wikipedia.org/wiki/The_Eras_Tour#Standard_set_list),
[reputation Arlington](https://www.setlist.fm/setlist/taylor-swift/2018/atandt-stadium-arlington-tx-23e970ff.html).
Spotify identity and songwriting credit:
[This Is What You Came For](https://open.spotify.com/track/50adZYk0RNbzLz3Jj6juBT),
[official recording credits](https://www.youtube.com/watch?v=zCh4jIWOKrI),
[existing Better Man recording](https://open.spotify.com/track/3EXetjrGgwIjDRNn6YA8sa).

## Exact data

`collectionStats.mjs` requires a safe integer total and daily count for every
member on the target date and previous calendar day. Missing, estimated,
merge-loser, duplicate and invalid data blocks that collection's aggregates.
It exposes the exact affected ID/date/reason in its detail page. Pending
collections have no fabricated zero totals. All rank numbers stay pending
until every collection is complete. A percentage with a zero denominator
is undefined and not presented as 0%.

Better Man already has exact rows in the main catalog history; it is reused
without adding a second Spotify identity or rewriting historical rows.
This Is What You Came For is absent from the main catalog and uses the
separate manifest `db/collections/songwriting_tracks.json` and history
`db/collections/songwriting_history.json`. No external history is inserted
into Taylor's DB, artist totals, chart ranking or post generation.

`collection_streams.collect_songwriting` is called after a complete normal
current-date daily run. Dry runs, local tests, debug runs, historical date
overrides and throwbacks do not collect it. It uses the same Spotify API
and token manager, requires the pipeline's current stats date, logs failures,
and never overwrites an existing dated observation with a later live total.
The first observation is a baseline with daily pending. Equal/decreasing
counters remain pending. Three consecutive exact observations are needed
for a target date's daily count and the previous day's daily comparison.

The collector uploads `collections/songwriting/history.json` into the main
data R2 bucket when its existing upload configuration is available. The
frontend API `/api/collections/songwriting-history?date=YYYY-MM-DD` returns
only the target and comparison days. A missing history object is explicitly
pending; transport/configuration errors remain errors. Local development
reads the same separate file from `TSM_BACKEND_ROOT`.

## Validation

From frontend: `npm run check` and
`node scripts/check-collections.mjs C:/path/to/tsm-backend`.
The statistics check uses existing exact October 8/9, 2026 snapshots.
Mobile QA: list, detail, search, date changes, pending state and internal
table scroll at 375px; light/dark and desktop also checked. No page-level
horizontal scroll. No historical stream data was changed by this feature.
