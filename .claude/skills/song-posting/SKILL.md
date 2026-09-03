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
- Per-era best-day recap card (decision 2026-09-03): when ≥ 5 post-eligible best-day songs of one era hit a record the same day, a dedicated `{Era} - Best Day Recap` card posts before that era's album card (`--only-era-recap`, era-themed header; early lane driven by `finalize_update.ReadyEraRecapPoster`). Once it posts, that era's individual best-day song cards are suppressed for the day — except a biggest-day-of-the-year card. The songs still appear in the global recap. Detail in `data-rules` / `spotify-streams`.
- Top Songs / Top Eras / GAINERS cards now carry a `★ Title · since <date>` (or `· of the year` / `· of the month`) best-day marker in the Track/Album column, matching album update images (decision 2026-09-03). Helper `comp.tables_image.ledger_name_with_best_day`.

## Validation

Before finishing a posting change:

- Search the edited script for forbidden old paths if relevant: `spotlight`.
- Run `python -m py_compile` on changed Python scripts.
- Use `--no-post` for tweet/card previews.
- Confirm generated text includes the chart prefix, correct album emoji behavior, exact values, and the required date format.