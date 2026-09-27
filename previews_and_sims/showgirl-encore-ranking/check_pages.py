"""Render check of the other pages whose Showgirl cover moved to the Encore art."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/")
OUT = Path(__file__).resolve().parent / "cards"
ENCORE = "the%20life%20of%20a%20showgirl%20the%20encore.webp"
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    for path, tag in [("/games", "games"), ("/album-ranking", "album_ranking"), ("/track-1-ranking", "track1_ranking"), ("/games/album-arcade", "album_arcade")]:
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(1200)
        srcs = page.evaluate("() => [...document.images].map(i => [i.src, i.complete && i.naturalWidth > 0])")
        encore = [ok for s, ok in srcs if ENCORE in s]
        old = [s for s, ok in srcs if s.endswith("the%20life%20of%20a%20showgirl.webp")]
        broken = [s for s, ok in srcs if not ok and "/covers/" in s]
        page.screenshot(path=str(OUT / f"{tag}.png"))
        print(f"{path}: encore imgs={len(encore)} (loaded={sum(encore)}) old-cover imgs={len(old)} broken covers={broken}")
    b.close()
