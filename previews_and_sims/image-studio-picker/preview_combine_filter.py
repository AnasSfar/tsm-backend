"""Image Studio — "Combine & filter" card in the Data tab (2026-10-02).

Same servers as preview.py (API :8013, Vite :3010). Screens the Spotify
Streams panel: Data (Daily / Albums) and Rows (Daily).
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:3010"
out = Path(__file__).resolve().parent / "cards"
out.mkdir(exist_ok=True)


def wait_ready(pg):
    pg.wait_for_timeout(800)
    pg.wait_for_function("!document.body.innerText.includes('Loading data')", timeout=120000)
    pg.wait_for_timeout(1500)


def panel(pg, name):
    pg.locator(".imgstudio-panel").screenshot(path=str(out / name))


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1500, "height": 1000})
    pg = ctx.new_page()
    pg.add_init_script("sessionStorage.setItem('news_admin_token','local-preview-token')")
    pg.goto(f"{BASE}/admin/console", wait_until="networkidle")
    pg.locator("button:visible", has_text="Image Studio").first.click()
    wait_ready(pg)
    for label, slug in [("Daily", "daily"), ("Albums", "albums")]:
        pg.locator(".imgstudio-panel .adm-segment", has_text=label).first.click()
        wait_ready(pg)
        panel(pg, f"cf_streams_{slug}_data.png")
    pg.locator(".imgstudio-panel .adm-segment", has_text="Daily").first.click()
    wait_ready(pg)
    pg.get_by_role("tab", name="Rows").click()
    pg.wait_for_timeout(800)
    panel(pg, "cf_streams_daily_rows.png")
    ctx.close()
    b.close()
print("screens ->", out)
