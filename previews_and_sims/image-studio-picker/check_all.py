"""Opens every Image Studio format and every settings tab on the preview
server (see preview.py) and reports page errors + the tabs each format has."""
from pathlib import Path
from playwright.sync_api import sync_playwright

NAMES = ["Spotify Charts", "Spotify Streams", "Apple Music", "YouTube",
         "All collectors", "TS Top Songs", "Song card", "Trends"]
out = Path(__file__).resolve().parent / "cards"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 1000})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.add_init_script("sessionStorage.setItem('news_admin_token','local-preview-token')")
    pg.goto("http://localhost:3010/admin/console", wait_until="networkidle")
    pg.locator("button:visible", has_text="Image Studio").first.click()
    pg.wait_for_timeout(1500)
    for name in NAMES:
        pg.get_by_role("tab", name=name, exact=True).click()
        pg.wait_for_timeout(2500)
        tabs = pg.locator(".imgstudio-tab").all_inner_texts()
        for tab in tabs:
            pg.locator(".imgstudio-tab", has_text=tab).click()
            pg.wait_for_timeout(300)
        slug = name.lower().replace(" ", "_")
        pg.locator(".imgstudio-tab").first.click()
        pg.screenshot(path=str(out / f"check_{slug}.png"))
        print(f"{name:16} tabs={tabs} errors={len(errors)}")
    for e in errors:
        print("ERR", e)
    b.close()
