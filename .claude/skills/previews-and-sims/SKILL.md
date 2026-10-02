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
