---
name: collector-itunes
description: "Work safely on collectors/itunes: the iTunes Store PURCHASE charts (Top Songs + Top Albums per country, legacy RSS feeds), Taylor Swift filtering, CSV snapshots, JSON export, R2 upload. Distinct from collectors/apple_music (which is Apple Music streaming/most-played). Data-only — never posts to X, never commits git. Use before auditing, debugging, running, or modifying iTunes collector code."
---

# Collector iTunes

Read `CONTEXTE.md` before changing or running anything under
`collectors/itunes`.

Also use:

- `collector-apple-music` — the sibling collector this one borrows storefront
  discovery + the MusicKit token cache + `core/csv_utils.py` semantics from.
- `data-rules` for exact-data decisions (no fake data, NEW vs RE rules).
- `pipeline-ops` when the task is about the scheduled local run or recovery.

## What this is (and is not)

- **iTunes Store = purchases / paid downloads.** `collectors/apple_music` =
  Apple Music streaming / most-played. Two different signals, two different
  collectors, two different frontend tabs. Do not merge them.
- Source: legacy RSS, no auth —
  `https://itunes.apple.com/{storefront}/rss/topsongs/limit=100/json` and
  `.../rss/topalbums/...`. Capped at 100 entries (plenty for Taylor).
- Rank = 1-based position in the feed, Taylor-filtered afterwards (kept
  entries keep their true chart position — same rule as
  `apple_music/country_all.py`).

## Core rules

- **Data-only**: never posts to X, never commits/pushes git. Only the R2
  upload distributes the data. `--no-post` on the runner is a legacy no-op.
- The legacy RSS host throttles bursts with **HTTP 403** — keep
  `ITUNES_WORKERS` low (default 3), rely on `_fetch`'s backoff retries + the
  sequential retry pass in `charts.py`. A 404 is a legitimately empty chart.
- Abort only when a critical storefront (`ITUNES_CRITICAL_STOREFRONTS`,
  default `us,gb`) is still missing after retry, or failures exceed
  `ITUNES_MAX_FAILURE_PCT` (default 25%). A 2-hourly snapshot missing a minor
  storefront is self-healing — do not tighten this back to the Spotify-style
  "never a partial day" rule. See `CONTEXTE.md` § Throttling.
- Match primarily by `apple_music_id` (the feed's `im:id` — iTunes and Apple
  Music share one id namespace); title fallback only for old rows without id.
- No NEW-by-inference: the local history is shallow, so a song missing from
  yesterday is RE, not NEW, unless its catalog `release_date` is inside the
  frontend's recent-release window (mirror `api/routes/apple_music.py`'s
  `_NEW_RELEASE_WINDOW_DAYS`).
- Storefronts come from `apple_music.core.storefronts.resolve_storefronts`
  (needs the Apple Music MusicKit token — shared cache). Override with
  `ITUNES_COUNTRIES="us,gb,jp,..."`. Fallback = `apple_music` `COUNTRIES`.
- `.gitignore` excludes every `.csv` and nothing here is force-added — a
  fresh machine has no local history. Seed `snapshots/itunes_charts/` from R2
  (`itunes/snapshots/`) or another machine before the first real run.

## Quick safe checks

```powershell
python .\collectors\itunes\run_itunes.py --help
# sandbox: a few storefronts, no export, throwaway date
$env:PYTHONPATH="$PWD;$PWD\collectors\itunes"; $env:ITUNES_SKIP_EXPORT="1"; $env:ITUNES_COUNTRIES="us,gb,ca,au"
python .\collectors\itunes\charts.py --date 2020-01-01 --scraped-at 2020-01-01T12:00:00
python .\scripts\export_itunes.py
python .\scripts\upload_itunes_r2.py --dry-run
```

## Frontend (built 2026-09-09)

Tab **iTunes Charts** at `/amcharts/itunes` (own page, modeled on Deezer not
the complex Apple Music page). Route `api/routes/itunes.py`, page
`frontend/src/pages/ITunes.jsx` + `styles/ITunes.css`. Full wiring list and
gotchas → `CONTEXTE.md` § "Frontend". Still open: OG screenshot path, and a
`/api/version` field for a dedicated data-refresh signal (currently piggybacks
Apple Music's). Deploy with the `deploy` skill.
