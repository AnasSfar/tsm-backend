"""Screenshots of the TS Top Songs "Charts" column (desktop + mobile 500px).

fixtures/cycle_rows.json = ONE real ts_page_all.py cycle (2026-10-02 ~15:00,
collected with its CSV write intercepted — never written to snapshots/),
aggregated with the real backend code (compute_final_rows +
normalize_song_entry). Its chart_rank is grafted (by apple_music_id, then
name) onto the real /api/apple-music* responses of the preview API (:8013).
Faked: previous_chart_rank (none exists yet) -> movement badges in the Charts
column are simulated for the first rows to check the layout.
Needs preview_api.py (:8013) + vite (:3010) running.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from collectors.apple_music.core.ts_top_songs_daily import compute_final_rows  # noqa: E402
from scripts.export_apple_music import normalize_song_entry  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

cycle = [{k: str(v) for k, v in r.items()} for r in json.loads((HERE / "fixtures" / "cycle_rows.json").read_text(encoding="utf-8"))]
final = [normalize_song_entry(r) for r in compute_final_rows("2026-10-02", cycle, [])]
charted = [e for e in final if e.get("chart_rank")]
by_id = {e["apple_music_id"]: e for e in charted}
by_name = {e["song_name"].lower(): e for e in charted}
FAKE_PREV = {1: 2, 2: 1, 3: 3, 5: 9, 8: None}  # layout check only
stats = {"grafted": 0}


def graft(obj):
    if isinstance(obj, dict):
        if "song_name" in obj and "rank" in obj and "storefront_ranks" in obj:
            hit = by_id.get(obj.get("apple_music_id")) or by_name.get((obj.get("song_name") or "").lower())
            if hit:
                obj["chart_rank"] = hit["chart_rank"]
                obj["previous_chart_rank"] = FAKE_PREV.get(hit["chart_rank"], hit["chart_rank"] + 1)
                stats["grafted"] += 1
        for v in obj.values():
            graft(v)
    elif isinstance(obj, list):
        for v in obj:
            graft(v)


def handle(route):
    resp = route.fetch()
    try:
        data = resp.json()
    except Exception:
        return route.fulfill(response=resp)
    graft(data)
    route.fulfill(response=resp, body=json.dumps(data), headers={**resp.headers, "content-type": "application/json"})


URL = "http://localhost:3010/amcharts/apple-music?section=taylor_swift&tab=ts_top_songs"
with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, vp in (("desktop", {"width": 1440, "height": 1100}), ("mobile", {"width": 390, "height": 900})):
        page = browser.new_page(viewport=vp)
        page.route("**/api/apple-music**", handle)
        page.goto(URL, wait_until="networkidle", timeout=120000)
        page.wait_for_selector(".am-store-header", timeout=60000)
        page.wait_for_timeout(1500)
        table = page.locator(".am-chart-table").first
        table.scroll_into_view_if_needed()
        page.screenshot(path=str(HERE / f"{name}.png"))
        if name == "mobile":
            table.evaluate("el => { el.scrollLeft = 200; }")
            page.wait_for_timeout(300)
            page.screenshot(path=str(HERE / "mobile-scrolled.png"))
        page.close()
    browser.close()
print("grafted entries:", stats["grafted"])
