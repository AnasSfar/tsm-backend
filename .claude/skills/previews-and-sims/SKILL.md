# Previews & Simulations

Use this skill whenever a task involves fake/fabricated data to verify
something that isn't live yet: a new feature test, a dry-run of a pipeline
change, "simule vendredi" / "simule la sortie" type requests, or any
end-to-end check that needs data that doesn't exist for real yet.

**Never** write fake data into real project state to run a simulation:
`db/`, `snapshots/`, `runtime/`, any `tools/json/*state*.json`,
`tools/locks/`, or any file a real scheduled task also reads/writes. A
simulation that touches production files can corrupt real history
(`previous_rank`, `posted.lock`, exact totals) the moment a real run
follows it — this has already caused real incidents elsewhere in this repo
(see `data-rules` rule n°10, the backfill/baseline gap incidents). Fake data
belongs only inside this skill's folder, isolated from anything a real run
touches.

## Where everything goes

Root folder at the repo root: `previews_and_sims/`. Every generated
artifact type it produces (`*.csv`, `*.json`, `*.log`, `*.html`, `*.png`) is
already covered by the repo's global `.gitignore` — no extra ignore rule
needed. The harness script itself (`.py`) is NOT ignored and stays tracked —
it's real, reusable code, not throwaway output.

Each distinct thing being tested gets its own subfolder:

```
previews_and_sims/
  <feature-slug>/
    simulate.py          # the harness — fakes inputs, calls the real code, --no-post/--dry-run
    fixtures/            # fake CSV/JSON inputs it writes before running (optional subfolder)
    cards/                # PNG output, if the target generates images
    state.json, locks/    # fake state, isolated copy — never the real tools/json/*.json
    run.log               # captured stdout, if useful to keep
```

`<feature-slug>` = short kebab-case name of the feature or release being
tested (e.g. `apple-music-debut-progression`, `showgirl-encore-release`) —
**not** a date-only name. Reuse the same folder across a session and across
later sessions for the same feature: if `post_new_release_progression.py`
gets a fix next week, re-run the *existing*
`previews_and_sims/apple-music-debut-progression/simulate.py` (adjusting it
as needed) rather than creating a second folder for the same thing. Only
make a new subfolder when the thing being tested is genuinely different.

## Building the harness

1. Import the real target module directly (`sys.path.insert` + `import
   the_script`) — never copy/reimplement its logic. The point is testing the
   real code path.
2. Monkeypatch its output/state paths (`OUT_DIR`, `STATE_PATH`,
   `LOCKS_DIR`, CSV source paths, whatever it reads/writes) to point inside
   the sim's own subfolder, before calling anything else.
3. Monkeypatch time-dependent functions (`_now_paris`, `date.today()`, a
   `--date`/`--scraped-at` arg) to the scenario's fake "now" — don't wait for
   real time to pass to test a future date.
4. Write fake input fixtures (CSV rows, catalog entries) that match the
   real schema exactly (same fieldnames/order the real collector writes) —
   check the real writer script's `FIELDNAMES`/`SONG_FIELDNAMES` constant
   first, don't guess columns.
5. Force whatever no-post / dry-run flag the target script exposes. If a
   script has no such flag and posts unconditionally, do not run its posting
   path in the sim — monkeypatch the post function itself to a no-op logger
   instead of letting it hit a real API/browser session.
6. Run it, then actually look at the result with the Read tool (PNGs
   especially — "it didn't crash" is not "it looks right"; the Streams-block
   bug on the Apple Music debut cards was only caught this way, not by
   reading the template code).

### Pitfall: history lookups across days

If the target reads other days' files too (e.g. `post_new_release_progression`
peak lookup through `source["path_for"](date)`), patch that accessor as well,
not only the "today" path — otherwise the sim silently reads real
`snapshots/` files for the fake dates.

### Pitfall: stub signatures drift

Monkeypatched stubs (`_render_card_png = lambda html, out: ...`, `fake_sources(today)`) break
silently-looking (a TypeError caught by the target's own crash handler -> "0 posts") as soon
as the real function gains a parameter (2026-09-25: `scale=`, `express=`, `path=` on
post_new_release_progression). Write stubs as `lambda *a, **k:` / `def fake(today, *a, **k):`
and compare the sim's totals with the previous run before trusting it.

### Pattern: previewing a new API field in the real frontend

When the backend adds a field the prod API doesn't serve yet, run the real
tsm-frontend FastAPI app locally on **:8003** (the Vite dev proxy target) with
`TSM_DATA_SOURCE=local` (reads real local exports, read-only) and monkeypatch
only the loader the route uses to inject the sim payload (see
`previews_and_sims/ts-top-songs-live/preview_api.py`), then `npx vite --port
5173` + headless Chrome `--screenshot`. Stop both servers by exact PID after.

### Pitfall: the owner's dev servers + frontend/.env.local

The owner runs `tsm-frontend/dev.bat` (API :8003 `--reload` + Vite :3000), and
dev.bat **kills whatever listens on :8003** when it starts — a preview API on
:8003 can vanish mid-session and be replaced by theirs (real ADMIN_TOKEN ->
your admin pages bounce to the login screen). Also `frontend/.env.local` sets
`VITE_API_BASE` (straight to :8003, bypassing any proxy) and a dev admin token.
For admin/Image Studio previews use separate ports + no env files:
`previews_and_sims/image-studio-picker/vite.preview.config.mjs` (Vite :3010,
proxy -> API :8013, `envDir` pointed at an empty folder) with the API started
as `ADMIN_TOKEN=local-preview-token python -m uvicorn --app-dir . api.index:app
--port 8013`, and `sessionStorage.news_admin_token` set by an init script.
Never stop the owner's processes; stop yours by exact PID.

### Pattern: mock one API route in the browser instead of the loader

When the page's new data comes from its own small route, Playwright
`page.route("**/api/<route>*", lambda r: r.fulfill(...))` answers it with the
sim payload while the rest of the page uses the real local API — no loader
monkeypatch needed (`previews_and_sims/youtube-new-video-button/simulate.py`,
which also rebuilds the payload with the real backend code on an isolated
registry). Pitfall: `page.click("text=Audios")` matches any element whose text
*contains* the word (a hint like "Music videos + audios") — use
`get_by_role(..., name=...)`.

### Pitfall: `@supabase/supabase-js` missing from local node_modules

Since the accounts feature (8cbbdc48) `src/account/client.js` imports
`@supabase/supabase-js`, which is in package.json but may not be installed
locally → Vite dev shows a full-screen import-analysis overlay and a local
`npx vite build` fails (Vercel installs it from frontend/package.json). Do not `npm install` in the owner's checkout for a
preview: alias it to a stub in the preview Vite config
(`previews_and_sims/search-console-insights/vite.preview.config.mjs` +
`supabase-stub.js`).

### Pattern: admin module + admin-only floating panel, fake API in the browser

`previews_and_sims/search-console-insights/shoot.py [error]` (2026-10-15): API on :8013
(`ADMIN_TOKEN=local-preview-token`), Vite :3010, `sessionStorage.news_admin_token` set
by init script, every `/api/search-console/**` answered with a fabricated payload of
the real shape (`error` = Google 502). Shoots the admin section, a page drill-down and
the public "Google" panel at 1366px and 375px, and prints the page's horizontal overflow.

### Pitfall: `/api/apple-music` live path skips `load_apple_music`

Since 2026-09-24 the page route reads the history-by-date index + day files,
not `load_apple_music()` — monkeypatching the loader (as `ts-top-songs-live/preview_api.py`
does for its own `/ts-top-songs-live` route) has NO effect on the main
payload. To graft a new field onto it, use the browser route mock below on
`**/api/apple-music**` (`route.fetch()` the real response, edit, `fulfill`):
`previews_and_sims/ts-top-songs-charts-column/shoot.py`.

### Pattern: X account-slot contention (no browser, no post)

`previews_and_sims/twitter-account-slot-spacing/simulate.py` runs several real processes
(`multiprocessing`, spawn) through the real `core/twitter._twitter_account_slot` with
`TWITTER_COORD_DIR` redirected into the sim folder and spacings scaled down via env (set in
each child BEFORE importing `twitter`, constants are read at import). A "post" = slot +
1 s fake browser + `_wait_account_spacing` + `_mark_account_posted`. Reuse it for any change
to post priority/spacing/locks.

### Pattern: finalize pacing / X scheduling / live log (no browser, no post)

`previews_and_sims/finalize-pacing-schedule-logs/simulate.py` imports the real
`finalize_update`, `core.twitter`, `live_log` and `reporting`, redirects
`finalize_update.update_streams_dir` and `core.twitter.TWITTER_COORD_DIR` into the sim
folder, then checks the paced X-schedule slot chain across a simulated interruption
(state reloaded from disk), the in-process schedule gap / too-close fallback of
`_env_schedule_at`, `_run_subprocess` piping and the `[PHASE]`/`[STATUS]` log lines
(`live_log.install(..., own_file=True, log_dir=<sim>)`, `STATUS_EVERY_SECONDS` lowered).
Since 2026-10-07 it also prints the `post_pacing` plans for realistic days (availability
hour x post count), checks multi-post steps advance the curve, and runs two updates 40 min
apart against the schedule registry (no slot < 180 s apart, live-post guard wait).
Since 2026-10-10 the same folder has `pacing_compare.py` (current curve vs
variants given as `name:base:growth:maxmin:window`, plain `plan_slots`, no state) and
(`order_check.py` was removed with `_round_robin`/`_spread`.)
`previews_and_sims/post-priority-order/simulate.py [dates]` replays the finalize post
order by importance on REAL past days, read-only: posting scripts are run with
`--force --count-units` (force = ignore the locks already written for those days,
count = return before rendering/posting); best-day candidates are skipped because
`--list-batch-candidates` writes its stash into the real snapshots/ day folder.
Pitfalls: fake "now" must be in the FUTURE (registry pruning uses the real clock) —
patch `finalize_update.datetime` with a `datetime` subclass whose `now()` returns it;
give each fake stats date its own `update_streams_dir` folder or the second update
"resumes" the first one's pacing state.
Pitfall: running `update_streams.py --help` for a smoke test creates a real
`snapshots/.../logs/*_attemptNN.txt` + rewrites `.latest.txt` (CollectorRunLog) — delete it
and restore `.latest.txt` afterwards.

### Pattern: preview a posting script on REAL dates without touching snapshots/

`--no-post` is NOT a preview mode: the script still writes its cards into the real
`snapshots/.../<date>/` (incident 2026-10-09: milestone cards + old spotlight PNGs written
there during a redesign, cleaned up by hand). Import the script, monkeypatch its
`update_streams_dir` (and the post function, as a safety net), then call `main()` with
`sys.argv` set: `previews_and_sims/milestone-cards/simulate.py real`. Same harness, `edge`
mode = fake values rendered straight through the comp component, files prefixed `FAKE_`.

### Pattern: preview a frontend worktree branch against the prod API

`previews_and_sims/era-symbols/` : `vite.preview.config.mjs` (port 3030, root = worktree
`tsm-frontend-era-symbols`) + `vite.redesign.config.mjs` (3031, worktree `tsm-frontend-redesign`),
`/api` -> prod en lecture, `shoot.py [steps]` (`ERA_BASE=http://localhost:3031` pour le redesign).
Worktree neuf : junction `frontend/node_modules` vers le checkout principal + copie de
`node_modules` racine (`@dnd-kit` y vit, pas dans frontend/). Pièges : choisir le thème en
cliquant dans le ThemePicker (un `ts-museum-store` injecté en localStorage n'est pas appliqué) ;
le fallback `<Suspense>` des routes ne s'affiche qu'au 1er chargement d'une page (navigation
interne = transition) → `page.route("**/src/pages/X.jsx*", lambda r: None)` puis `goto` direct.

### Pattern: new card design on real data (no pipeline touched)

`previews_and_sims/spotify-album-page-card/simulate.py ["Album" | all] [DATE] [--weekly|--daily] [--overtake]` (2026-10-13;
Sat/Sun dates render the weekend "vs last week" card unless `--daily`; `--overtake` = flat
same-album overtake list, exits if the date has none; announced sections + Day N built in;
test only): album update restyled as a Spotify album page. Reads real data read-only through
`generate_album_update_image` loaders (`load_album_sections`, `load_history_for_album`,
`_counts_in_album_total`) and renders its own HTML into `cards/` (+ `_phone.png`). Music-video
badges come from `db/youtube_views_history.csv` + `collectors.youtube.core.title_groups.video_category`
(main-channel "videos" whose title is "(Official [Music] Video)" or bare "Taylor Swift - Song").
Facts of the day (`facts.py`, 2026-10-13): 9 exact-data facts (new billion 100, anniversary ±3 d 95/80, song passed a 100M step 90, Global chart #≤10 85 / else 55, streak ≥3 d 45+5/d, next milestone ≤7 d 60 (75 if a billion) / ≤30 d 35, #1 era 70 / era up 45 / else 20, all songs up 60 / only one up 45 / biggest gainer 30, positive vs 30-day avg 55/35, billion club 30, top song share 15); ≥70 always shown, the rest trimmed lowest-first in the browser (`TRIM_JS`) while the side column is taller than the list; wide layout = top 3 under the artist line.
Pitfall: a harness importing `collectors.youtube...` needs the repo root on `sys.path`
(works from repo root, `ModuleNotFoundError` from the sim folder).

### Pattern: restyle proposals for the other streams images

`previews_and_sims/spotify-app-cards/simulate.py [DATE] [recap|songs|eras|gainers]` (2026-10-13,
proposals only): recap / top songs / top eras / gainers as Spotify artist / playlist / discography /
Charts pages, built on the real loaders (`generate_streams_image.load_history` + `build_top_n`,
`generate_albums_image.build_album_rows`, `generate_weekend_streams_image.build_totals` on
`site_history.json`, `post_gainer_thread._pick_gainers`) and `album_page_card` helpers (header tone,
TV/feat. title cleanup). `--theme=DATE` = real stats of DATE but the timeline theme (`day_theme.py`) of another date, files suffixed `_theme-<date>` (preview only). Pitfall: `artist.json` only holds TODAY's listeners/followers, so a past
date shows current artist stats (prod recap does the same).

### Pattern: debut card of a song family (real catalogue, fake history rows)

`previews_and_sims/patient-zero-versions-debut/simulate.py [day1|day2|swap|single|all]` (2026-10-16):
imports `post_debut_releases`, monkeypatches `_load_rows_for_date` (fake history rows per day),
`update_streams_dir` (-> `cards/<scenario>/`) and the post functions (raise), then calls the real
`_build_post_threads` on the real catalogue and prints each tweet. Run with `PYTHONIOENCODING=utf-8`
(the 📈 prefix crashes a cp1252 console).

### Pitfall: headless Chrome mobile width

`--window-size=390,...` is silently clamped (Chrome min window width ~500px):
the screenshot looks cropped on the right and suggests a fake horizontal
overflow. Use `--window-size=500,...` (still under the 600px breakpoint).

## Reporting back

Tell the user what was simulated, what was faked (so they know it's not
real), where the folder is, and call out anything that looked wrong even if
it "worked" (a crash-free run with a broken layout is not a pass). Fix real
bugs found this way in the actual source file, not in the sim harness.

## Cleanup

Leave the folder after the session — it's a durable log of what was tested,
reusable next time the same feature needs checking, and costs nothing
(gitignored generated files). Only delete a subfolder if the user explicitly
asks, or if it was a one-off exploratory scratch that turned out useless
before anything was learned from it.

## Maintenance (obligatoire)

Nouveau pattern de simulation qui marche bien, ou piège rencontré en
construisant un harness (schema CSV, monkeypatch qui ne prend pas, etc.) →
mets à jour cette skill dans la même session.
