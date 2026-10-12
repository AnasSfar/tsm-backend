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

## Expanded membership (October 11, 2026)

The generator now reads `scripts/top_collection_memberships.json` through
`scripts/extended_top_collections.py`. This versioned manifest freezes exact
catalog titles and ordered tracklists; the generated frontend file freezes
their Spotify IDs. No frontend membership inference is performed.

There are 42 collections: the original seven, Track 1 through Track 18,
Album Openers, Album Closers, Bonus Tracks, From the Vault, Title Tracks,
Solo Written, Featuring Taylor, Acoustic Versions, Piano Versions,
Holiday Songs, four older tours, the two Hot 100 collections and Taylor's Version.

Taylor's Version has 103 explicit recordings from the four TV albums:
27 Fearless, 22 Speak Now, 32 Red, and 22 1989. This includes their bonus,
vault, acoustic/piano recordings, soundtrack rerecordings, If This Was a
Movie and the TV Bad Blood with Kendrick Lamar; remixes are excluded.
From the Vault remains its existing separate 27-track collection.

All 42 collection artworks are explicitly assigned in the manifest, using
existing era portraits, album covers, headers and catalog recording artwork.
The 18 Track Number collections have 18 different image paths. Cover crop
positions are shared by desktop rows, mobile cards and collection details.
No page layout, CSS design or theme-specific styling was introduced.

Track Numbers use the twelve original standard studio tracklists, with
lengths 11/13/14/16/13/15/18/16/15/13/16/12, in chronological album order.
Taylor's Version recordings replace the corresponding Fearless, Speak Now,
Red and 1989 recordings without changing original track numbers. Thus Our
Song, Change, Long Live and Clean remain their original album closers.
Deluxe tracks, The Anthology, 3am tracks and The Encore are bonus membership,
never numbered standard tracks. If This Was a Movie retains its original
Speak Now bonus classification despite its TV recording's catalog location.
Vault tracks are a separate collection, including You're Losing Me and
the ten-minute All Too Well. Alternate acoustic/piano recordings are kept
in their named version collections; remixes, karaoke and voice memos are
not classified as additional standard/bonus songs.

Tours use reference concerts, with covers and surprise slots excluded;
Taylor-song mashup components use separate studio recordings:
[Fearless, Jacksonville May 1, 2009](https://en.wikipedia.org/wiki/Fearless_Tour#Set_list),
[Speak Now, Newark July 19, 2011](https://en.wikipedia.org/wiki/Speak_Now_World_Tour#Standard_set_list),
[Red, Newark March 28, 2013](https://en.wikipedia.org/wiki/The_Red_Tour#Set_list),
[1989, Tokyo May 5, 2015](https://en.wikipedia.org/wiki/The_1989_World_Tour#Set_list).
The regular Fearless encore I'm Only Me When I'm With You is included.

Hot 100 membership is by composition that reached the US chart threshold,
represented by its TV recording where available. It does not claim that
every selected TV recording independently reached that peak. Original and
TV versions are never both summed. There are 16 #1 songs and 74 top-ten
songs as of the October 10, 2026 chart, including the four Encore songs.
New Romantics is excluded: neither its original nor its TV chart peak
qualifies. Sources:
[Billboard, Patient Zero and career counts](https://billboard.com.br/taylor-swift-patient-zero-top-100-billboard/),
[US peak positions and linked Billboard references](https://en.wikipedia.org/wiki/Taylor_Swift_singles_discography),
[Billboard, Opalite and earlier #1s](https://billboard.com.co/opalite-y-todos-los-no-1-de-taylor-swift-en-el-billboard-hot-100/).

Solo Written uses one principal recording per composition (TV where
available), not every alternate performance. Sources:
[album-by-album writing credits](https://www.capitalfm.com/artists/taylor-swift/own-songs-solo-writing-credits/),
[recording credits including Beautiful Eyes and Carolina](https://en.wikipedia.org/wiki/List_of_songs_by_Taylor_Swift).
Featuring Taylor covers the existing studio guest/co-lead recordings,
including I Don't Wanna Live Forever; live guest appearances are excluded.
Holiday Songs contains the six Holiday Collection songs plus Christmas
Tree Farm's principal recording.

### Explicit unresolved identity

Acoustic Versions is integrated but its aggregate remains pending.
Teardrops On My Guitar - Acoustic Version has two catalog IDs,
`0g5eBFfWwNP7PXFGON6SB7` and `17Kqa6MX5uZzLK7EOEeAxo`, without an explicit
historical alias mapping. Both Spotify pages identify Taylor Swift's acoustic
recording. Neither title equality nor a snapshot's zero total establishes
which identity to count. The manifest records this unresolved membership
and `collectionStats` blocks the entire aggregate, even if the other eleven
recordings have exact data. No ID is silently selected or combined.

The standard Showgirl title track uses `5eXgqtg3T8Av0m1FUaGHex`; its catalog
row explicitly maps the other same-title ID `6EqHzZp61G5uQ12QEiibpT` as a
historical identity. That targeted resolution changes no catalog/history row.

Expanded integrity QA: all 42 memberships checked against October 8/9
snapshots, exact sums compared, duplicate IDs/slugs rejected, and both
Songwriting and unresolved Acoustic Versions confirmed pending. Build and
source hygiene pass. Headless browser QA uses those same snapshots at
375px: all 42 cards, seven filter types, Track Numbers search, track detail,
103-song TV detail, 27-song Vault detail, 74-song Hot 100 detail, pending
reason, light/dark, and 1440px desktop.
Screenshots are under `runtime/top-collections/`; no horizontal page scroll
or clipped mobile row titles. No stream histories were modified.
