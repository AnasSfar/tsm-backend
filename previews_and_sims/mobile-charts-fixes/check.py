"""Mobile checks for Apple Music / iTunes "Overall" cards against the local
preview stack (preview_api.py on :8003 + vite on :5173). Read-only.

  python previews_and_sims/mobile-charts-fixes/check.py [share]

Takes iPhone-sized screenshots; with `share`, clicks the first share button of
each page and reports console errors / downloads (the button swallows errors).
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PAGES = {
    "applemusic": "http://localhost:5173/amcharts/apple-music?section=global&tab=country_charts",
    "applemusic_ts": "http://localhost:5173/amcharts/apple-music?section=taylor_swift&tab=ts_top_songs",
    "itunes": "http://localhost:5173/amcharts/itunes",
}
SHARE = "share" in sys.argv[1:]

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(**p.devices["iPhone 13"], accept_downloads=True)
    for name, url in PAGES.items():
        page = ctx.new_page()
        logs = []
        page.on("console", lambda m, logs=logs: logs.append(f"{m.type}: {m.text}"[:400]) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e, logs=logs: logs.append(f"pageerror: {e}"[:400]))
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(HERE / f"{name}.png"), full_page=False)
        blocks = page.locator(".overall-song-block")
        if blocks.count():
            blocks.first.screenshot(path=str(HERE / f"{name}_block.png"))
        if SHARE:
            btn = page.locator(".share-image-btn").first
            print(name, "share buttons:", page.locator(".share-image-btn").count())
            try:
                with page.expect_download(timeout=30000) as dl:
                    btn.click()
                path = HERE / f"{name}_shared.png"
                dl.value.save_as(str(path))
                print(name, "DOWNLOAD OK", path.stat().st_size, "bytes")
            except Exception as exc:
                print(name, "NO DOWNLOAD:", type(exc).__name__)
        for line in logs[-8:]:
            print("  ", name, line)
        page.close()
    browser.close()
