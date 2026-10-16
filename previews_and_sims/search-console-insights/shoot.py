"""Preview the Search Console admin module + per-page panel with FAKE data.

Servers (own ports, see vite.preview.config.mjs):
  API  : ADMIN_TOKEN=local-preview-token uvicorn api.index:app --port 8013 (tsm-frontend)
  Vite : npx vite --config <this folder>/vite.preview.config.mjs  (:3010)

Every /api/search-console/* call is answered here with a fabricated payload
of the real shape (api/routes/search_console.py) -- no Google call, nothing
written anywhere but cards/. Usage: python shoot.py [error]
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "cards"
BASE = "http://localhost:3010"
TOKEN = "local-preview-token"
ERROR_MODE = "error" in sys.argv[1:]


def fake_report(days: int, scale: float, with_pages: bool) -> dict:
    rnd = random.Random(days * 7 + int(scale * 10))
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    daily = []
    for i in range(days):
        d = start + timedelta(days=i)
        spike = 4 if i == days - 9 else 1
        clicks = int((20 + i * 0.6 + rnd.randint(0, 15)) * spike * scale)
        impr = int(clicks * rnd.uniform(18, 30))
        daily.append({"date": d.isoformat(), "clicks": clicks, "impressions": impr,
                      "ctr": clicks / impr if impr else 0, "position": round(rnd.uniform(6, 14), 1)})
    tc = sum(d["clicks"] for d in daily)
    ti = sum(d["impressions"] for d in daily)

    def rows(key, names):
        out = []
        for n, name in enumerate(names):
            c = max(1, int(tc / (n + 2.2) / 2))
            i = c * rnd.randint(8, 40)
            out.append({key: name, "clicks": c, "impressions": i, "ctr": c / i, "position": round(rnd.uniform(1.2, 25), 1)})
        return out

    report = {
        "property": "sc-domain:thetsmuseum.app",
        "days": days,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "final_until": (date.today() - timedelta(days=3)).isoformat(),
        "totals": {"clicks": tc, "impressions": ti, "ctr": tc / ti, "position": 9.4},
        "previous": {"clicks": int(tc * 0.71), "impressions": int(ti * 0.83), "ctr": tc / ti * 0.9, "position": 10.8},
        "daily": daily,
        "queries": rows("query", [
            "taylor swift spotify streams", "the tsm museum", "taylor swift most streamed songs",
            "cruel summer streams", "showgirl encore streams", "taylor swift apple music chart",
            "taylor swift itunes chart", "tayboard", "fate of ophelia spotify streams count",
            "taylor swift billion streams songs list", "swifties charts", "taylor swift youtube views",
        ]),
        "countries": rows("country", ["usa", "fra", "gbr", "bra", "phl", "mex", "deu", "can", "aus"]),
        "devices": [
            {"device": "MOBILE", "clicks": int(tc * 0.78), "impressions": int(ti * 0.8), "ctr": 0.04, "position": 9.1},
            {"device": "DESKTOP", "clicks": int(tc * 0.19), "impressions": int(ti * 0.17), "ctr": 0.03, "position": 10.2},
            {"device": "TABLET", "clicks": int(tc * 0.03), "impressions": int(ti * 0.03), "ctr": 0.02, "position": 11.0},
        ],
        "fetched_at": "FAKE",
    }
    if with_pages:
        report["pages"] = rows("page", [
            "/", "/spotifystreams/streams/latest", "/spotifystreams/songs/1BxfuPKGuaTgP7aM0Bbdwr",
            "/spotifystreams/albums/The%20Life%20of%20a%20Showgirl", "/amcharts/apple-music",
            "/spotifycharts/charts", "/youtube", "/tayboard", "/games", "/spotifystreams/milestones",
            "/spotifystreams/top-songs/date/latest",
        ])
    return report


def handle(route):
    if ERROR_MODE and "/status" not in route.request.url:
        route.fulfill(status=502, content_type="application/json", body=json.dumps({
            "detail": "Google says the service account has no access to sc-domain:thetsmuseum.app. Add "
                      "tsm@demo.iam.gserviceaccount.com as a user (Restricted is enough) in Search Console > "
                      "Settings > Users and permissions."}))
        return
    url = route.request.url
    days = int((url.split("days=")[1].split("&")[0]) if "days=" in url else 28)
    if "/status" in url:
        body = {"configured": True, "property": "sc-domain:thetsmuseum.app", "service_account": "tsm@demo.iam.gserviceaccount.com"}
    elif "/overview" in url:
        body = fake_report(days, 1.0, True)
    else:
        path = url.split("path=")[1].split("&")[0]
        body = {**fake_report(days, 0.3, False), "path": path, "url": "https://thetsmuseum.app" + path}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    suffix = "_error" if ERROR_MODE else ""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, vp, mobile in [("desktop", {"width": 1366, "height": 900}, False), ("phone", {"width": 375, "height": 812}, True)]:
            ctx = browser.new_context(viewport=vp, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1)
            ctx.add_init_script(f"sessionStorage.setItem('news_admin_token', '{TOKEN}');")
            ctx.route("**/api/search-console/**", handle)
            page = ctx.new_page()

            # Admin module
            page.goto(f"{BASE}/admin/console", wait_until="networkidle")
            if mobile:
                page.get_by_role("button", name="Open menu").first.click() if page.get_by_role("button", name="Open menu").count() else None
            page.get_by_role("button", name="Search Console").first.click()
            page.wait_for_selector(".gsc-kpis, .adm-callout--warn", timeout=15000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / f"admin_{label}{suffix}.png"), full_page=True)
            if not ERROR_MODE:
                page.locator(".gsc-key button").nth(1).click()
                page.wait_for_selector(".adm-gsc-crumb")
                page.wait_for_timeout(500)
                page.screenshot(path=str(OUT / f"admin_page_drill_{label}.png"), full_page=True)

            # Public page panel
            page.goto(f"{BASE}/spotifystreams/streams/latest", wait_until="networkidle")
            page.wait_for_selector(".gsc-fab", timeout=15000)
            page.screenshot(path=str(OUT / f"site_fab_{label}{suffix}.png"))
            page.click(".gsc-fab")
            page.wait_for_selector(".gsc-kpis, .gsc-panel-status--error", timeout=15000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / f"site_panel_{label}{suffix}.png"))
            overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            print(label, "horizontal overflow px:", overflow)
            ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
