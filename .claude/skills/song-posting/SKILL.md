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
- **Combined with a same-day "filtered streaming" record (decision 2026-09-19):** "filtered streams" = the `streams` figure Spotify Charts itself publishes per chart entry — the `streams` column already in `db/charts_history_<region>.csv` — a DIFFERENT, coarser number than the exact daily total in `db/streams_history.csv` used by `streams/best_day_since.py` (that pipeline also lags charts by ~2 days, so it usually can't even be checked for the same date). **Do not confuse the two** — an earlier version of this feature wrongly cross-checked `streams/best_day_since.py`'s output for this, which is a different metric on a different schedule. `_spcharts_filtered_streaming_extra_line` computes it self-contained from the same chart-history `points` already loaded for the rank search: same "since" algorithm applied to `streams` instead of `rank` (most recent day with a streams figure `>=` today's), same `SPCHARTS_RANK_RECORD_MIN_DAYS_SINCE`/`SPCHARTS_RANK_RECORD_RECENT_REPEAT_DAYS` gates. When it fires alongside the rank record, the tweet gets a second paragraph — `"The song also earned its best filtered streaming day since <date> with <streams> streams [<pct>]."` (verb "has once again earned" under the repeat-days rule) — one tweet instead of two for the same song/day.

## Validation

Before finishing a posting change:

- Search the edited script for forbidden old paths if relevant: `spotlight`.
- Run `python -m py_compile` on changed Python scripts.
- Use `--no-post` for tweet/card previews.
- Confirm generated text includes the chart prefix, correct album emoji behavior, exact values, and the required date format.