#!/usr/bin/env python3
"""Capture Open Graph share screenshots of live thetsmuseum.app pages -> R2.

Social crawlers (X/Twitter, Discord, iMessage, Slack) read `og:image` from the
server-rendered SPA shell (tsm-frontend `api/index.py::_render_spa_html`). For
the site's main pages it points `og:image` at `{site}/api/og/<slug>.png`, and
`tsm-frontend/api/routes/og_images.py` streams the PNG this script writes to R2
under `og/<slug>.png` (falling back to `preview.png` until a capture exists).

Only the site's main / section pages are captured (see MAIN_PAGES) -- NOT
per-song or per-album pages, which keep the generic preview image.

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
"""
from __future__ import annotations

import argparse
import re
import sys
import time
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
SETTLE_MS = 1_200

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
# Only the site's main / section pages get a screenshot -- NOT per-song or
# per-album pages (those keep the generic preview image). This list is
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
) -> int:
    from playwright.sync_api import sync_playwright

    client = r2mod.get_s3_client() if upload else None
    bucket = r2mod.get_env("R2_BUCKET") if upload else ""
    uploaded = unchanged = failed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE,
            locale="en-US",
            timezone_id="Europe/Paris",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 tsm-og-bot"
            ),
        )
        context.route("**/*", lambda route: route.abort() if _should_block(route.request.url) else route.continue_())
        page = context.new_page()

        for i, path in enumerate(targets, 1):
            url = f"{base_url}{path}"
            slug = _og_slug(path)
            print(f"[{i}/{len(targets)}] {path}  ->  og/{slug}.png")
            try:
                try:
                    page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
                except Exception:
                    # networkidle can time out on pages with long-poll/analytics;
                    # fall back to domcontentloaded + fixed settle.
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                page.add_style_tag(content=HIDE_CSS)
                try:
                    page.wait_for_selector("main, #root > *, h1", timeout=8_000)
                except Exception:
                    pass
                page.wait_for_timeout(SETTLE_MS)
                png = page.screenshot(
                    clip={"x": 0, "y": 0, "width": VIEWPORT["width"], "height": VIEWPORT["height"]}
                )
            except Exception as exc:  # noqa: BLE001
                print(f"    [fail] {exc}")
                failed += 1
                continue

            if out_dir:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"{slug}.png").write_bytes(png)

            if upload and client is not None:
                try:
                    changed = r2mod.upload_bytes_if_changed(
                        client=client,
                        bucket=bucket,
                        key=f"{r2_keys.OG_SCREENSHOTS_PREFIX}/{slug}.png",
                        data=png,
                        content_type="image/png",
                        dry_run=False,
                        cache_control="public, max-age=3600, s-maxage=86400",
                    )
                    if changed:
                        uploaded += 1
                    else:
                        unchanged += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"    [upload fail] {exc}")
                    failed += 1

        context.close()
        browser.close()

    print(
        f"\nDone: {len(targets)} targets, "
        f"{uploaded} uploaded, {unchanged} unchanged, {failed} failed."
    )
    return 1 if failed and not uploaded and not unchanged else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"default {DEFAULT_BASE_URL}")
    parser.add_argument("--only", default="", help="substring filter on the route path")
    parser.add_argument("--no-upload", action="store_true", help="do not push to R2")
    parser.add_argument("--out", default="", help="also write PNGs into this directory")
    parser.add_argument("--headful", action="store_true", help="show the browser (debug)")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    targets = list(MAIN_PAGES)
    if args.only:
        needle = args.only.lower()
        targets = [t for t in targets if needle in unquote(t).lower()]
    if not targets:
        print("No targets after filtering.")
        return 0

    started = time.time()
    print(f"{len(targets)} target route(s) from {base_url}\n")
    code = capture_all(
        targets,
        base_url=base_url,
        out_dir=Path(args.out) if args.out else None,
        upload=not args.no_upload,
        headful=args.headful,
    )
    print(f"({time.time() - started:.0f}s)")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
