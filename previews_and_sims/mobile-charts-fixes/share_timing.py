"""Times click -> image ready for the share buttons (read-only, local preview stack)."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
CASES = [
    ("am_overall", "http://localhost:5173/amcharts/apple-music?section=global&tab=country_charts", ".overall-song-block .share-image-btn"),
    ("am_ts", "http://localhost:5173/amcharts/apple-music?section=taylor_swift&tab=ts_top_songs", ".share-image-btn"),
    ("itunes_overall", "http://localhost:5173/amcharts/itunes", ".overall-song-block .share-image-btn"),
]
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(**p.devices["iPhone 13"], accept_downloads=True)
    for name, url, sel in CASES:
        page = ctx.new_page()
        errs = []
        page.on("console", lambda m, errs=errs: errs.append(m.text[:200]) if m.type == "error" and "cssRules" not in m.text and "CSS rules" not in m.text else None)
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
        t0 = time.time()
        try:
            with page.expect_download(timeout=120000) as dl:
                page.locator(sel).first.click()
            dl.value.save_as(str(HERE / f"{name}_shared.png"))
            print(f"{name}: image ready in {time.time()-t0:.1f}s")
        except Exception as exc:
            print(f"{name}: FAILED after {time.time()-t0:.1f}s ({type(exc).__name__})")
        for e in errs[-5:]:
            print("   ", e)
        page.close()
    browser.close()
