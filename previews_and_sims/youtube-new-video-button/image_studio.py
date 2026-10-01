"""Image Studio YouTube template preview (2026-09-30): real local data,
local-only ADMIN_TOKEN (the API must run with ADMIN_TOKEN=local-preview-token)."""
from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path(__file__).resolve().parent / "cards"
out.mkdir(exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1500, "height": 1100}).new_page()
    pg.add_init_script("sessionStorage.setItem('news_admin_token','local-preview-token')")
    pg.goto("http://localhost:3000/admin/console", wait_until="networkidle")
    pg.get_by_role("button", name="Image Studio").first.click()
    pg.wait_for_timeout(1500)
    pg.get_by_text("Top views — songs, music videos").click()
    pg.wait_for_function("!document.body.innerText.includes('Loading data')", timeout=90000); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(out / "studio_songs.png"), full_page=True)
    img = pg.locator("[class*='imgst-']").first
    try:
        img.screenshot(path=str(out / "studio_songs_image.png"))
    except Exception as e:
        print("no image locator", e)
    pg.get_by_role("button", name="Extras", exact=True).click(); pg.wait_for_timeout(500)
    pg.wait_for_function("!document.body.innerText.includes('Loading data')", timeout=90000); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(out / "studio_extras.png"), full_page=True)
    try:
        pg.locator("[class*='imgst-']").first.screenshot(path=str(out / "studio_extras_image.png"))
    except Exception as e:
        print("no image locator", e)
    b.close()
