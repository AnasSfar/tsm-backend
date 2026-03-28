from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parents[2]
ROOT        = _REPO_ROOT / "website"
COVERS_PATH = ROOT / "discography" / "albums" / "covers.json"

HEADLESS = True


def block_unneeded(route):
    request = route.request
    url = request.url.lower()
    resource_type = request.resource_type

    blocked_resource_types = {
        "image",
        "media",
        "font",
    }

    blocked_keywords = (
        "doubleclick",
        "googletagmanager",
        "google-analytics",
        "analytics",
        "facebook",
        "pixel",
        "ads",
        ".mp4",
        ".webm",
        ".mp3",
        ".wav",
        ".ogg",
        ".woff",
        ".woff2",
        ".ttf",
    )

    if resource_type in blocked_resource_types or any(x in url for x in blocked_keywords):
        route.abort()
    else:
        route.continue_()


def load_covers() -> dict:
    if not COVERS_PATH.exists():
        raise FileNotFoundError(f"Missing file: {COVERS_PATH}")
    return json.loads(COVERS_PATH.read_text(encoding="utf-8"))


def save_covers(data: dict) -> None:
    COVERS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def scrape_cover(page, spotify_url: str) -> str | None:
    try:
        page.goto(spotify_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1000)

        og = page.locator('meta[property="og:image"]')
        if og.count() > 0:
            content = og.first.get_attribute("content")
            if content and content.strip():
                return content.strip()

        twitter = page.locator('meta[name="twitter:image"]')
        if twitter.count() > 0:
            content = twitter.first.get_attribute("content")
            if content and content.strip():
                return content.strip()

        return None

    except PlaywrightTimeoutError:
        return None
    except Exception:
        return None


def main():
    data = load_covers()
    items = list(data.items())
    total = len(items)
    start = time.perf_counter()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()
        page.route("**/*", block_unneeded)

        try:
            for i, (slug, album) in enumerate(items, 1):
                title = album.get("title", slug)
                spotify_url = (album.get("spotify_url") or "").strip()

                if not spotify_url:
                    print(f"[{i}/{total}] {title} | skipped (no spotify_url)")
                    continue

                print(f"[{i}/{total}] {title} | fetching cover...")

                cover_url = scrape_cover(page, spotify_url)

                if cover_url:
                    data[slug]["cover_url"] = cover_url
                    save_covers(data)
                    print(f"[{i}/{total}] {title} | OK")
                else:
                    print(f"[{i}/{total}] {title} | NOT FOUND")

        finally:
            browser.close()

    elapsed = time.perf_counter() - start
    print()
    print(f"Finished in {int(elapsed // 60)}m {int(elapsed % 60)}s")


if __name__ == "__main__":
    main()