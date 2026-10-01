"""Image Studio editor preview (app-icon dock + tabbed editor, 2026-09-30).

Real local page and data. Needs, from tsm-frontend/:
  ADMIN_TOKEN=local-preview-token python -m uvicorn --app-dir . api.index:app --port 8013
  cd frontend && node ./node_modules/vite/bin/vite.js --config <this folder>/vite.preview.config.mjs
(ports 3010/8013 so the owner's dev.bat on 3000/8003 is never touched; the
token is local-only).
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:3010"
out = Path(__file__).resolve().parent / "cards"
out.mkdir(exist_ok=True)


def wait_ready(pg):
    pg.wait_for_timeout(800)
    pg.wait_for_function("!document.body.innerText.includes('Loading data')", timeout=120000)
    pg.wait_for_timeout(1200)


with sync_playwright() as p:
    b = p.chromium.launch()
    for name, vp, mobile in [("desktop", {"width": 1500, "height": 1000}, False),
                             ("mobile", {"width": 390, "height": 844}, True)]:
        ctx = b.new_context(viewport=vp, device_scale_factor=2 if mobile else 1, is_mobile=mobile, has_touch=mobile)
        pg = ctx.new_page()
        pg.add_init_script("sessionStorage.setItem('news_admin_token','local-preview-token')")
        pg.goto(f"{BASE}/admin/console", wait_until="networkidle")
        if mobile:
            pg.locator(".adm-topbar-menu").first.click()
            pg.wait_for_timeout(500)
        pg.locator("button:visible", has_text="Image Studio").first.click()
        wait_ready(pg)
        pg.screenshot(path=str(out / f"{name}_streams_data.png"), full_page=not mobile)
        pg.get_by_role("tab", name="YouTube").click()
        wait_ready(pg)
        pg.screenshot(path=str(out / f"{name}_youtube_data.png"), full_page=not mobile)
        pg.get_by_role("tab", name="Rows").click()
        pg.wait_for_timeout(600)
        pg.locator(".imgstudio-panel").scroll_into_view_if_needed()
        pg.screenshot(path=str(out / f"{name}_youtube_rows.png"), full_page=not mobile)
        pg.get_by_role("tab", name="Style").click()
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(out / f"{name}_youtube_style.png"), full_page=not mobile)
        ctx.close()
    b.close()
print("screens ->", out)
