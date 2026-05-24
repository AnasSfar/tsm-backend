#!/usr/bin/env python3
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.spotify.core.twitter import _launch, _restore_storage_state


def main() -> int:
    session = Path("collectors/spotify/charts/global/tools/json/twitter_session.json")
    profile_dir = session.parent / "chrome_profile"
    with sync_playwright() as p:
        context = _launch(p, profile_dir)
        _restore_storage_state(context, session)
        page = context.new_page()
        page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(8_000)
        print("URL=" + page.url)
        print("TITLE=" + page.title())
        print("TEXTAREA=" + str(page.locator("[data-testid='tweetTextarea_0']").count()))
        body = page.locator("body").inner_text(timeout=5_000)
        print("BODY=" + body[:1200].replace("\n", " | "))
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
