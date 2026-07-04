## TSM Frontend Rule

When the user says "frontend" for TSM, always work in:
`C:\Users\sfara\Documents\GitHub\tsm-frontend`

React/Vite UI lives in:
`C:\Users\sfara\Documents\GitHub\tsm-frontend\frontend`

Frontend API lives in:
`C:\Users\sfara\Documents\GitHub\tsm-frontend\api`

Do not edit:
`C:\Users\sfara\Documents\GitHub\tsm-backend\website`

unless the user explicitly asks for `website/`, the legacy static site, `website/site/data`, `website/site/history`, or generated static data.

## TSM Data Integrity Rules

TSM stats are exact data, not estimates or vibes. When working on streams,
charts, history, exports, snapshots, posts, or any numeric/statistical pipeline:

- Always fix the real root cause. Do not hide symptoms with broad fallbacks,
  silent skips, fake defaults, or "probably fine" logic.
- Never invent, predict, simulate, smooth, or approximate stream counts,
  daily values, totals, chart positions, dates, percentages, or milestones.
- If a value is ambiguous, missing, duplicated, stale, or inconsistent, keep it
  blocked/pending and log the exact reason. Do not publish or export it as if it
  were verified.
- A fallback is allowed only when it uses an exact source of truth already in
  the repo/data pipeline, such as a verified snapshot total, a same-date history
  row, or an explicit `historical_track_ids` mapping.
- Do not add general rules like "same_total means zero streams" or
  "same song_family means duplicate". These cases require explicit evidence.
- Use explicit mappings for duplicates and aliases. Prefer
  `historical_track_ids` or targeted metadata fixes over heuristics.
- `chart_extra` is not a collection exclusion rule by itself. Collection should
  cover every real DB track; only explicit historical/duplicate mappings should
  remove an ID from active collection.
- Before allowing posts/final exports, verify that the relevant active tracks
  have complete, exact data for the target date and required comparison date.
- If a correction touches historical data, compare DB rows against snapshots or
  another exact source and report what changed.
- When unsure, stop and ask or leave the run blocked. A blocked run is better
  than a wrong public stat.
