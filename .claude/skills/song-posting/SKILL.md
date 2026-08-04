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
- For chart entry/re-entry posts, include exact chart position/streams if the source has them; if missing, do not invent.

## Validation

Before finishing a posting change:

- Search the edited script for forbidden old paths if relevant: `spotlight`.
- Run `python -m py_compile` on changed Python scripts.
- Use `--no-post` for tweet/card previews.
- Confirm generated text includes the chart prefix, correct album emoji behavior, exact values, and the required date format.