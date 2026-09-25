---
name: song-posting
description: Rules for TSM song tweet/card captions and generated song posts. Use before creating or editing any song-level Twitter/X post, chart_card/song_card caption, Spotify stream gainer, best-day-since, chart entry/re-entry, or other Taylor Swift song posting script.
---

# TSM Song Posting

Use this skill whenever a script writes text for a song-level post or generates a song card.

## Caption Rules

- Start song posts with the chart emoji prefix: `📈 |`.
- Add the album emoji after the chart prefix when the post is about one song with a known album: `📈 | 🤍 "So High School" ...`.
- If the album is unknown, keep the chart emoji prefix only.
- Use `album_emoji(album, fallback="📈")` for album emoji selection.
- Use exact values only. Do not round, estimate, smooth, or infer streams/positions/percentages.
- Use `song_card` for song cards. Do not use `spotlight` for new song posts.

## Date Format

- Always format explicit dates as: `Monday (Jun 5, 2027)`.
- For daily runs where the stats date is actually yesterday, write: `yesterday, Monday (Jun 5, 2027)`.
- For historical or explicit dates that are not yesterday, write: `on Monday (Jun 5, 2027)`.
- Do not use long dates like `Monday, June 5th, 2027` in new song captions.

## Stream Gainer Copy

Use one of these two caption shapes, chosen randomly when both fit:

```text
📈 | 🤍 "So High School" earned 269,152 streams [+18.2%] yesterday, Monday (Aug 3, 2026)
```

```text
📈 | 🤍 "So High School" earned 269,152 streams, up 18.2%, yesterday, Monday (Aug 3, 2026)
```

Rules:

- Say `earned X streams`.
- Bracket version: put the signed percent immediately after `streams`: `[+18.2%]` or `[-18.2%]`.
- Direction version: write `up 18.2%` or `down 18.2%` without a sign.
- Do not append `vs the previous day` in the song gainer caption unless the user explicitly asks for the comparison label.
- Use `yesterday` only when true for the run date.

## Best-Day / Chart Copy

- Keep the chart emoji prefix even when the album emoji is present.
- Keep existing product wording like `earned its BEST DAY...` when the post is a best-day-since card, but convert dates to the required short format when adding explicit dates.
- **"has once again earned its BEST DAY since …" (decision 2026-09-03):** when the beaten day is recent — `best_day_since.is_recent_repeat_record(row)`, i.e. `kind == "since"` and the beaten day is within `RECENT_REPEAT_RECORD_DAYS` (60) days — the caption verb becomes `has once again earned` instead of `earned` (two comparable big days close together, never consecutive). Applies to **songs** (`twitter.text.best_day_since_tweet(repeat=…)`) **and albums** (album update card first line + its appended song best-day note: `once again earned its …` / `once again had its …`). `best day ever` has no beaten day → never a repeat.
- **Album best-day = first line of the album update card, no separate card (decision 2026-09-03):** `generate_album_update_image._album_best_day_row` (best-day-since ≥ 30 days, or biggest day of year/month, or best ever) rewrites `received N streams` → `earned its <LABEL> with N streams on <date>`. `post_best_day_since_twitter --only-album` and the standalone album best-day card are gone. Consequence: no album best-day on weekends (album cards are weekday-only).
- For chart entry/re-entry posts, include exact chart position/streams if the source has them; if missing, do not invent.
- Any `is_biggest_day_of_year` best-day record is posted unconditionally — its own card, early, with no per-album / per-era / daily cap and no score gate (decision 2026-08-29). Gating detail lives in `data-rules` and `spotify-streams` CONTEXTE; do not re-add a cap for these rows.
- Exception: a "The Taylor Swift Holiday Collection" song posts **no** best-day-since card outside the Christmas window (Nov 25 – Jan 7), not even a biggest-day-of-the-year (decision 2026-09-03). The seasonal block beats the unconditional rule (`_holiday_collection_out_of_season`).
- Finalize best-day-since batch is 3 standard / 5 max (was 10); slots 4–5 need a >90-day gap or a `score_best_day_since` ≥ 90 (decision 2026-09-03). Detail in `data-rules` / `spotify-streams`.
- Per-era best-day recap card (decision 2026-09-03): when ≥ 5 post-eligible best-day songs of one era hit a record the same day, a dedicated `{Era} - Best Day Recap` card posts before that era's album card (`--only-era-recap`, era-themed header — era photo, dark masthead forced every day except Holiday Collection; early lane driven by `finalize_update.ReadyEraRecapPoster`). The era label in the title and tweet is the base name via `best_day_since.era_display_name` — never "(Taylor's Version)" (decision 2026-09-04). Once it posts, that era's individual best-day song cards are suppressed for the day — except a biggest-day-of-the-year card. The songs still appear in the global recap. Detail in `data-rules` / `spotify-streams`.
- Top Songs / Top Eras / GAINERS cards now carry a `★ Title · since <date>` (or `· of the year` / `· of the month`) best-day marker in the Track/Album column, matching album update images (decision 2026-09-03). Helper `comp.tables_image.ledger_name_with_best_day`.
- **Song overtake caption is unchanged for the same-album variant (decision 2026-09-06):** when the two songs in a total-streams overtake are on the same album, `post_song_overtakes.py` swaps the STREAMS ledger card for that album's update image (flat list ranked by total, `▲/▼` per row, swapped pair highlighted) but still uses the standard `song_overtake_tweet` wording (`"X" has now surpassed "Y" and is now Taylor Swift's Nth most streamed song ever.` + full-history link). Cross-album overtakes are unchanged. Detail in `image-gen` / `data-rules`.

## "Once Again" Repeat Wording (streams AND charts)

- **Decision 2026-09-19: "once again" means the SAME track also hit a record the day right before, not "the beaten record is under some fixed number of days old".** `streams/best_day_since.py::is_recent_repeat_record(row)` used to check `row["days_since"] <= RECENT_REPEAT_RECORD_DAYS` (60) — owner rejected this: a record beaten 45 days ago read as "once again" purely because 45 <= 60, even when yesterday had nothing to do with it. Rewritten to recompute `compute_best_day_since` for `row["date"] - 1 day` on the same track and check its `kind` is `"since"`/`"best_ever"` too — true back-to-back record days. `RECENT_REPEAT_RECORD_DAYS` constant removed (no longer used). Same change applied to the chart-rank feature below (`_spcharts_had_record_yesterday`, mirrors this exactly for rank/filtered-streams instead of exact streams).
- Verified on real data (2026-09-18 snapshot): Blank Space's chart-rank tweet reads "once again" because its rank on 2026-09-17 (#70) was itself already a since-record (last matched 2023-11-12, way past the 21-day floor) — a genuine two-days-in-a-row record, not just "under 60 days".

## Chart Rank Record Copy

- **"best chart position since <date>" (decision 2026-09-19):** backend-only signal, computed by `run_all_charts.py::_collect_spcharts_rank_record_since` straight from `db/charts_history_<region>.csv`, only the 3 `SPCHARTS_RANKED_HISTORY_REGIONS`. No fixed window — mirrors `streams/best_day_since.py::compute_best_day_since`: walks the full history back for the most recent day that already matched/beat today's rank. Never found -> true all-time best, already covered by the separate `new peak rank` alert, skipped here. Beaten record was just yesterday -> not newsworthy, skipped. Verb becomes "has once again reached" instead of "reached" when the beaten record is under `SPCHARTS_RANK_RECORD_RECENT_REPEAT_DAYS` (60) days old.
- A fixed rolling window (first tried, same day, dropped) produces misleading claims — e.g. a song reads as "highest in 12 months" while it actually charted just as well a few years back, just outside the window. Always prefer the "since" search for this kind of record.
- Historical `db/charts_history_<region>.csv` rows are missing `track_id` before ~2025-09 — never key a multi-year history search on `_song_key` (track_id-first), it silently breaks continuity between old and new rows for the same song. Use `_song_title_key` (title-only) for that lookup instead; `track_id` from the current day's row is still fine for the tweet's `Full history:` link.
- Tweet text lives in `collectors/twitter/text.py::spotify_chart_rank_record_since_tweet` — same trophy prefix (`🏆 |`) as the streams best-day-since tweets, `Full history: {chart_song_url(track_id, region=region)}` footer. **No stream count** in the main line — unlike the streams best-day-since tweets, the chart-position claim is rank-only (`at #{rank}`); the "filtered streaming" record (see below) is what carries a stream count. The ntfy notification body sent by `_notify_spcharts_events` IS this ready-to-copy tweet text, not a plain alert line.
- **Link: use `chart_song_url`, never `song_url`, for this caption (bug found and fixed 2026-09-19).** `collectors/twitter/links.py::song_url` points to the Spotify *streams* song page (`/songs/:id`, legacy-redirected to `/spotifystreams/songs/:id`) — correct for every other song tweet (best-day-since, overtakes), but wrong here: a chart-rank record must link to the Spotify *Charts* song page instead (`chart_song_url(track_id, region=region)` -> `/spotifycharts/charts/songs/:id?region=<global|us|uk>`). Pass the raw region key (`region`), not the display label (`region_label`) — the URL query param needs `global`/`us`/`uk` lowercase, not "Global"/"US"/"UK".
- The old frontend path (Text Studio Records tab `recordsNotify` toggle + `/api/admin/notify`) is removed — it depended on the admin page being open and never persisted the toggle. Text Studio's Records tab still lets you compose the `highest_rank` caption manually (still fixed-window, not yet aligned on the "since" logic), but sends no auto-notification anymore for any of its 4 metrics.
- **Combined with a same-day "filtered streaming" record (decision 2026-09-19):** "filtered streams" = the `streams` figure Spotify Charts itself publishes per chart entry — the `streams` column already in `db/charts_history_<region>.csv` — a DIFFERENT, coarser number than the exact daily total in `db/streams_history.csv` used by `streams/best_day_since.py` (that pipeline also lags charts by ~2 days, so it usually can't even be checked for the same date). **Do not confuse the two** — an earlier version of this feature wrongly cross-checked `streams/best_day_since.py`'s output for this, which is a different metric on a different schedule. `_spcharts_streams_record_lookup` computes it self-contained from the same chart-history `points` already loaded for the rank search: same "since" algorithm applied to `streams` instead of `rank` (most recent day with a streams figure `>=` today's), same `SPCHARTS_RANK_RECORD_MIN_DAYS_SINCE`/`SPCHARTS_RANK_RECORD_RECENT_REPEAT_DAYS` gates. When it fires alongside the rank record, the tweet gets a second paragraph via `_spcharts_filtered_streaming_extra_line` — `"The song also earned its best filtered streaming day since <date> with <streams> streams [<pct>]."` (verb "has once again earned" under the repeat-days rule) — one tweet instead of two for the same song/day.
- **Standalone filtered-streams record, no rank record the same day (decision 2026-09-23):** previously a day where only the filtered-streams figure cleared a record (rank didn't) posted nothing at all — the streams check only ever ran as an add-on inside the rank-record branch. Real case caught in testing: "The Fate of Ophelia" FR had a genuine filtered-streams-only record every day from 2026-09-01 through 2026-09-18 (rank never itself a record in that window) that silently posted nothing. Fixed: `_collect_spcharts_rank_record_since` now evaluates the rank record and the streams record independently; when only the streams one qualifies, it posts on its own via `collectors/twitter/text.py::spotify_chart_filtered_streams_record_tweet` — `"<title>" earned/has once again earned its best filtered streaming day since <date> on the <region> Spotify chart with <streams> streams, currently at #<rank>.` (no rank-record preamble, still the 🏆 prefix and `Full history:` footer). Card scope: main stores only — `SPCHARTS_RANK_RECORD_REGIONS = {global, us, uk, fr}` (separate from `SPCHARTS_RANKED_HISTORY_REGIONS`, which stays `{global, us, uk}` and only gates the total-days/streak alerts, not this feature).
- **Rank-record card visuals (2026-09-23):** region shown as a full country name + inline SVG flag icon (`extra`/`extra_icon_svg` on `render_chart_card`) — deliberately NOT a flag emoji, confirmed by a real render that Windows/Chromium headless does not draw flag emoji sequences (shows the bare "US"/"GB" fallback text instead; a plain emoji like 🌍 renders fine, it's specifically country flags Windows refuses). Card also shows day-over-day rank change (`▲N`/`▼N`/`=`) and streams change (signed count + signed `%`), `None`/no badge when yesterday's row is missing rather than a fabricated `0`. Whenever a filtered-streams record qualifies (combined with a rank record, or standalone), the card also shows `"Best filtered streaming day since <date>"` under the streams number (`metric_note` param) — not just in the tweet text.

## Apple Music / iTunes debut chart-movement posts (2026-09-24)

`collectors/apple_music/post_new_release_progression.py` is the first Apple
Music/iTunes script that posts to X (that pipeline is otherwise data-only —
see `collector-apple-music`). Convention for this post type only:

- **Night of 2026-09-24 rewrite (supersedes the text rules below):** prefix is the
  album emoji (`collectors/twitter/albums.py::album_emoji`, ❤️‍🔥 for Showgirl), not
  `🎧`; the tweet carries the real rank of the best event (debut / new #1 / new peak /
  climb), other key markets grouped by rank, and on EVERY tweet `🌍 Now #1 in N
  countries, top 10 in M and charting in K on <platform> worldwide.` counted over every
  storefront (`worldwide_sentence`). Built by `build_tweet_text`. Validated by the owner
  2026-09-25. Never a single-country card: since 2026-09-25 the card lists EVERY region the song charts in (all iTunes countries / Global + all Apple Music countries); only key markets (+ Global) trigger a post and feed the sentence.
  Same format for the iTunes Top Albums card of the new edition ("... on the iTunes
  albums chart in the US"). The normal Global Apple Music card during the window:
  `🌍 | Taylor Swift songs on the Global Apple Music chart right now:` + link. Separate
  Apple Music **Pop** card per key market (2026-09-25), only on #1 / top-10 debut or entry /
  new peak inside the top 10: `🇨🇦 | "<title>" is now #1 on the Apple Music Pop chart in
  Canada!` (single-region posts — Pop card, iTunes per-country album card — use the country
  FLAG as prefix, built from the storefront code; album emoji only as fallback; country
  always written in full, never "DE") (or `debuts at #N ...`), `Also: "X" #3, ...`, `Taylor Swift songs on the Apple
  Music Pop chart in <market> right now:` + link. Several songs in one cycle = ONE thread per
  platform (2026-09-25): opener `🧵 | "<album>"'s songs on the Apple Music charts.` + the best
  song's tweet (link dropped first if too long), replies = the other songs; album/Global/Pop
  cards stay separate posts. Apple Music album card (Top Albums per country) added the same
  day, same format as the iTunes one. Cards highlight the 5 key stores (★ + tinted row). Posting rules (volume, retry, auto release detection) →
  `collector-apple-music` CONTEXTE « Refonte nuit du 2026-09-24 ».
- (Before the rewrite) chart emoji prefix: `🎧 |`.
- **Apple Music and iTunes are posted separately** (3rd correction
  2026-09-24): one card + one tweet per (track, platform, cycle). Tweet names
  the platform (`"<title>" moves on 6 Apple Music charts`, `debuts at #3 on
  iTunes United States`) and links `amcharts/applemusic` or `amcharts/itunes`.
  Album subtitle uses `display_title_for_album` and the covers.json album
  cover (The Encore art/title for Showgirl).
- **Within a platform, one card per track per cycle, listing every chart it
  currently places on** (decision 2026-09-24, 2nd correction — a 1st version split one card
  per chart/region, rejected: "on mets toutes les régions dans une seule
  card"). Only posts when at least one of that track's charts moved this
  cycle; the card then shows every chart it's currently on (moved or not),
  moved ones sorted first. Tweet text stays short (`"<title>" debuts on N
  charts` / `"<title>" moves on N charts` / a specific single-chart sentence
  when only one exists) — the per-chart numbers live in the card table, not
  spelled out in the tweet.
- Card = port of the site's own Apple Music / iTunes share image
  (`.overall-song-block` from `pages/AppleMusic.jsx` / `pages/ITunes.jsx`,
  decision 2026-09-24 "match the frontend's actual design") — see
  `collector-apple-music` CONTEXTE for the details. Two deliberate deviations from the site
  card: footer says `@swiftiescharts` (not "THE TAYLOR SWIFT MUSEUM : A
  Taylor Swift fan project"), and the iTunes card uses the same logo+name
  brand row as Apple Music (`collectors/apple_music/itunes_logo.svg`). Both headers' right side: logo + platform
  name, date · hour of the cycle, "vs <previous time>", then always the 4
  pills "X #1 / X top 10 / X top 50 / X charting" (shown even at 0). Table has a
  PEAK column (best rank on that chart since release, from real collected
  cycles). A "NEW PEAK" badge marks a rank that beats the previous
  peak; a "RE-PEAK" badge (owner 2026-09-25) marks a rank back exactly at
  the peak after having been lower; a first appearance on a chart gets a "NEW"
  badge there instead, never "NEW PEAK". Accent color comes from the cover
  (`comp.chart_card._cover_palette`), not a fixed per-platform color. **Not**
  `chart_card.py::render_chart_card` (Spotify Charts branding).
- Link footer: `collectors/twitter/links.py::amcharts_url("applemusic"|"itunes")`.

## Validation

Before finishing a posting change:

- Search the edited script for forbidden old paths if relevant: `spotlight`.
- Run `python -m py_compile` on changed Python scripts.
- Use `--no-post` for tweet/card previews.
- Confirm generated text includes the chart prefix, correct album emoji behavior, exact values, and the required date format.