#!/usr/bin/env python3
"""Capture Open Graph share screenshots of live thetsmuseum.app pages -> R2.

Social crawlers (X/Twitter, Discord, iMessage, Slack) read `og:image` from the
server-rendered SPA shell (tsm-frontend `api/index.py::_render_spa_html`). For
the site's main pages it points `og:image` at `{site}/api/og/<slug>.png`, and
`tsm-frontend/api/routes/og_images.py` streams the PNG this script writes to R2
under `og/<slug>.png` (falling back to `preview.png` until a capture exists).

Captured: the site's main / section pages (MAIN_PAGES), the collectors'
overview pages (COLLECTOR_PAGES), every game (GAME_PAGES) and every era museum
(ERA_PAGES) -- NOT per-song or per-album pages, which reuse the home capture
(og/home.png) as their og:image.

A real screenshot needs a browser. The frontend's Vercel Python function has
none, so this runs in tsm-backend (Playwright is already a dependency) on its
own Task Scheduler task, a couple of times a day.

What it does per target route:
  1. open `{base_url}<route>` in a fresh Chromium context (no stored state ->
     default theme, logged out),
  2. block ad / analytics / consent-CMP network requests so nothing overlays
     the page,
  3. wait for the SPA to finish its first data fetch,
  4. screenshot the top 1200x630,
  5. upload to R2 `og/<slug>.png` (content-hash dedup, only changed PNGs go up).

`<slug>` is `_og_slug(path)` and MUST stay identical to `_og_slug()` in
tsm-frontend `api/index.py` (mirrored like scripts/r2_keys.py).

CLI:
    python scripts/generate_og_screenshots.py
        [--base-url URL] [--only SUBSTR] [--no-upload] [--out DIR] [--headful]
        [--force] [--workers N]

Static pages (STATIC_PAGES: games, museums, about) are skipped when the
deployed frontend build is the same as at their last capture and that capture
is < STATIC_MAX_AGE old (state: tools/json/og_screenshots_state.json).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import r2 as r2mod  # noqa: E402  (imports load .env via load_repo_dotenv())
import r2_keys  # noqa: E402

DEFAULT_BASE_URL = "https://thetsmuseum.app"
VIEWPORT = {"width": 1200, "height": 630}
DEVICE_SCALE = 2
NAV_TIMEOUT_MS = 35_000
DEFAULT_WORKERS = 4  # pages captured concurrently (one browser, N contexts)
SETTLE_MS = 1_800  # after networkidle: let fonts swap + entry animations finish

# Hosts whose requests get aborted before the page paints: ads, tag managers,
# analytics and Google's consent CMP (funding choices). Keeps every capture
# free of ad iframes and cookie/consent overlays without any frontend flag.
BLOCK_HOST_SUBSTRINGS = (
    "pagead2.googlesyndication.com",
    "googlesyndication.com",
    "googletagmanager.com",
    "google-analytics.com",
    "analytics.google.com",
    "doubleclick.net",
    "fundingchoicesmessages.google.com",
    "googletagservices.com",
    "adservice.google.com",
)

HIDE_CSS = (
    ".adsbygoogle,ins.adsbygoogle,[data-ad-slot],[id*='google_ads'],"
    "#onetrust-banner-sdk,.fc-consent-root,.grecaptcha-badge"
    "{display:none!important;visibility:hidden!important}"
)


def _og_slug(path: str) -> str:
    """Mirror of tsm-frontend api/index.py::_og_slug. Keep in sync."""
    text = re.sub(r"[^a-z0-9]+", "-", unquote(path or "").strip().lower())
    return text.strip("-") or "home"


# --- Route manifest --------------------------------------------------------
# Main / section pages, games and era museums get a screenshot -- NOT per-song
# or per-album pages (those reuse the home capture). ALL_PAGES is
# MIRRORED in tsm-frontend api/index.py::_OG_SCREENSHOT_PATHS (the frontend
# only points og:image at /api/og/<slug>.png for these paths). Keep in sync.
# The `.../latest` variants are stable URLs the SPA resolves to the newest
# snapshot, so no date lookup is needed here.
MAIN_PAGES: list[str] = [
    "/",
    "/spotifystreams/streams/latest",
    "/spotifystreams/top-songs/date/latest",
    "/spotifystreams/albums/date/latest",
    "/spotifystreams/milestones",
    "/spotifycharts/charts",
    "/amcharts/apple-music",
    "/youtube",
    "/tayboard",
    "/games",
    "/eras-gallery",
    "/about",
    "/journalist-department",
]

# Collector overview pages not in MAIN_PAGES (what the nav links to) -- never
# per-song / per-album detail pages.
COLLECTOR_PAGES: list[str] = [
    "/charts-gallery",
    "/spotifystreams/streams/recap",
    "/spotifycharts/charts/all-artists",
    "/amcharts/itunes",
]

# Games: the hubs + every playable game linked from /games (GamesPage.jsx).
GAME_PAGES: list[str] = [
    "/games/eras",
    "/games/album-arcade",
    "/games/hall-of-fame-eras-run",
    "/swift-day",
    "/swift-day/swiftie-level",
    "/swift-day/track-13-ranking",
    "/pfp-maker",
    "/taystory/character",
    "/album-ranking",
    "/track-1-ranking",
    "/soundtrack-ranking",
    "/number-ones-ranking",
    "/debut-ranking",
    "/folklore-ranking",
    "/fearless-ranking",
    "/speak-now-ranking",
    "/red-ranking",
    "/1989-ranking",
    "/reputation-ranking",
    "/lover-ranking",
    "/evermore-ranking",
    "/midnights-ranking",
    "/players",
    "/2yearsofttpd",
    "/2yearsofttpd/song",
    "/2yearsofttpd/tierlist",
    "/2yearsofttpd/lyrics",
    "/2yearsofttpd/timer",
    "/showgirl/song",
    "/showgirl/ranking",
]

# Era museums linked from /eras-gallery (ErasGallery.jsx / ErasStripHeader.jsx).
ERA_PAGES: list[str] = [
    "/debut/museum",
    "/fearless/museum",
    "/speaknow/museum",
    "/red/museum",
    "/1989/museum",
    "/reputation/museum",
    "/lover/museum",
    "/folklore/museum",
    "/evermore/museum",
    "/midnights/museum",
    "/2yearsofttpd/museum",
    "/showgirl/museum",
]

ALL_PAGES: list[str] = MAIN_PAGES + COLLECTOR_PAGES + GAME_PAGES + ERA_PAGES

# Pages whose top 1200x630 only changes when the frontend is redeployed (no
# daily data above the fold). They are re-captured only when the deployed
# build changed since their last capture, or when that capture is older than
# STATIC_MAX_AGE (safety net: leaderboards, copy fetched at runtime...).
# Everything else (streams, charts, Apple Music, home, players...) is data
# driven and captured every run. --force captures everything.
STATIC_PAGES: frozenset[str] = frozenset(
    [p for p in GAME_PAGES if p != "/players"]
    + ERA_PAGES
    + ["/games", "/eras-gallery", "/about"]
)
STATIC_MAX_AGE = timedelta(days=3)
STATE_PATH = ROOT / "tools" / "json" / "og_screenshots_state.json"


# --- Skip unchanged static pages -------------------------------------------

def _build_id(base_url: str) -> str:
    """Fingerprint of the deployed frontend = hash of the hashed /assets/ file
    names referenced by the SPA shell (Vite renames them on any code change).
    Empty string when it can't be read -> nothing is skipped."""
    try:
        req = urllib.request.Request(f"{base_url}/", headers={"User-Agent": "tsm-og-bot"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"[build-id] unreadable ({exc}) -> capturing every page")
        return ""
    assets = sorted(set(re.findall(r"/assets/[^\"'\s>]+\.(?:js|css)", html)))
    if not assets:
        return ""
    return hashlib.sha1("\n".join(assets).encode()).hexdigest()[:16]


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _is_fresh(entry: dict | None, build_id: str, now: datetime) -> bool:
    if not entry or not build_id or entry.get("build") != build_id:
        return False
    try:
        captured = datetime.fromisoformat(entry["captured_at"])
    except (KeyError, ValueError):
        return False
    return now - captured < STATIC_MAX_AGE


# --- Capture ---------------------------------------------------------------

def _should_block(url: str) -> bool:
    return any(host in url for host in BLOCK_HOST_SUBSTRINGS)


def capture_all(
    targets: list[str],
    *,
    base_url: str,
    out_dir: Path | None,
    upload: bool,
    headful: bool,
    on_captured=None,
    workers: int = DEFAULT_WORKERS,
) -> int:
    return asyncio.run(
        _capture_all_async(
            targets,
            base_url=base_url,
            out_dir=out_dir,
            upload=upload,
            headful=headful,
            on_captured=on_captured,
            workers=max(1, workers),
        )
    )


async def _capture_one(page, url: str) -> bytes:
    try:
        await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
    except Exception:
        # networkidle can time out on pages with long-poll/analytics;
        # fall back to domcontentloaded + fixed settle.
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    await page.add_style_tag(content=HIDE_CSS)
    try:
        await page.wait_for_selector("main, #root > *, h1", timeout=8_000)
    except Exception:
        pass
    await page.wait_for_timeout(SETTLE_MS)
    return await page.screenshot(
        clip={"x": 0, "y": 0, "width": VIEWPORT["width"], "height": VIEWPORT["height"]}
    )


async def _capture_all_async(
    targets: list[str],
    *,
    base_url: str,
    out_dir: Path | None,
    upload: bool,
    headful: bool,
    on_captured,
    workers: int,
) -> int:
    """`workers` pages are captured concurrently, each worker in its own
    browser context (same isolation as before: fresh state, default theme,
    logged out). Same waits per page as the old sequential loop."""
    from playwright.async_api import async_playwright

    client = r2mod.get_s3_client() if upload else None
    bucket = r2mod.get_env("R2_BUCKET") if upload else ""
    counts = {"uploaded": 0, "unchanged": 0, "failed": 0}
    queue: asyncio.Queue = asyncio.Queue()
    for item in enumerate(targets, 1):
        queue.put_nowait(item)

    def _upload(slug: str, png: bytes) -> bool:
        return r2mod.upload_bytes_if_changed(
            client=client,
            bucket=bucket,
            key=f"{r2_keys.OG_SCREENSHOTS_PREFIX}/{slug}.png",
            data=png,
            content_type="image/png",
            dry_run=False,
            cache_control="public, max-age=3600, s-maxage=86400",
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headful)

        async def worker() -> None:
            context = await browser.new_context(
                viewport=VIEWPORT,
                device_scale_factor=DEVICE_SCALE,
                locale="en-US",
                timezone_id="Europe/Paris",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 tsm-og-bot"
                ),
            )
            await context.route(
                "**/*",
                lambda route: route.abort() if _should_block(route.request.url) else route.continue_(),
            )
            page = await context.new_page()
            try:
                while True:
                    try:
                        i, path = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    slug = _og_slug(path)
                    try:
                        png = await _capture_one(page, f"{base_url}{path}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[{i}/{len(targets)}] {path}  [fail] {exc}")
                        counts["failed"] += 1
                        continue

                    if out_dir:
                        out_dir.mkdir(parents=True, exist_ok=True)
                        (out_dir / f"{slug}.png").write_bytes(png)

                    status = "captured"
                    if upload and client is not None:
                        try:
                            changed = await asyncio.to_thread(_upload, slug, png)
                            status = "uploaded" if changed else "unchanged"
                            counts[status] += 1
                            if on_captured is not None:
                                on_captured(path)
                        except Exception as exc:  # noqa: BLE001
                            status = f"upload fail: {exc}"
                            counts["failed"] += 1
                    print(f"[{i}/{len(targets)}] {path}  ->  og/{slug}.png  ({status})")
            finally:
                await context.close()

        await asyncio.gather(*(worker() for _ in range(min(workers, len(targets)))))
        await browser.close()

    print(
        f"\nDone: {len(targets)} targets, "
        f"{counts['uploaded']} uploaded, {counts['unchanged']} unchanged, {counts['failed']} failed."
    )
    return 1 if counts["failed"] and not counts["uploaded"] and not counts["unchanged"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"default {DEFAULT_BASE_URL}")
    parser.add_argument("--only", default="", help="substring filter on the route path")
    parser.add_argument("--no-upload", action="store_true", help="do not push to R2")
    parser.add_argument("--out", default="", help="also write PNGs into this directory")
    parser.add_argument("--headful", action="store_true", help="show the browser (debug)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"pages captured in parallel (default {DEFAULT_WORKERS}, 1 = sequential)")
    parser.add_argument("--force", action="store_true", help="also re-capture static pages whose build didn't change")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    targets = list(ALL_PAGES)
    if args.only:
        needle = args.only.lower()
        targets = [t for t in targets if needle in unquote(t).lower()]

    upload = not args.no_upload
    build_id = _build_id(base_url)
    state = _load_state()
    pages_state = state.setdefault("pages", {})
    if upload and not args.force:
        now = datetime.now(timezone.utc)
        skipped = [t for t in targets if t in STATIC_PAGES and _is_fresh(pages_state.get(t), build_id, now)]
        if skipped:
            print(
                f"Skipping {len(skipped)} static page(s): build {build_id} unchanged "
                f"and captured < {STATIC_MAX_AGE.days} d ago (--force to override)"
            )
        targets = [t for t in targets if t not in skipped]
    if not targets:
        print("No targets to capture.")
        return 0

    def _record(path: str) -> None:
        pages_state[path] = {"build": build_id, "captured_at": datetime.now(timezone.utc).isoformat()}
        _save_state(state)

    started = time.time()
    print(f"{len(targets)} target route(s) from {base_url} (build {build_id or '?'})\n")
    code = capture_all(
        targets,
        base_url=base_url,
        out_dir=Path(args.out) if args.out else None,
        upload=upload,
        headful=args.headful,
        on_captured=_record if upload else None,
        workers=args.workers,
    )
    print(f"({time.time() - started:.0f}s)")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
