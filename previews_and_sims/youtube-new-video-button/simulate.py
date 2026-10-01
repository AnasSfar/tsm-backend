"""Preview of the YouTube page's New video button (2026-09-30).

1. Backend: runs the real core/new_releases.build() on a fake first-day
   registry (pending + captures + a resolved release) kept in this folder —
   the real collectors/youtube/tools/json/first_day is never touched.
   Writes fixtures/feed.json.
2. Frontend: opens the real page (local API :8003 + Vite :3000 must be
   running) with /api/youtube/new-releases answered by feed.json through
   Playwright routing, screenshots desktop + mobile, panel open.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from collectors.youtube.core import first_day, new_releases  # noqa: E402

state = HERE / "state"
first_day.FIRST_DAY_DIR = state
first_day.PENDING_DIR = state / "pending"
first_day.CAPTURES_DIR = state / "captures"
first_day.RELEASES_DIR = state / "releases"
first_day.POSTED_LOCK_DIR = state / "first_day_posted"
for d in (first_day.PENDING_DIR, first_day.CAPTURES_DIR, first_day.RELEASES_DIR):
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("*.json"):
        f.unlink()

now = datetime.now(timezone.utc).replace(microsecond=0)
iso = first_day._iso
# Waiting: 24h mark in 1h40.
wait_pub = now - timedelta(hours=22, minutes=20)
first_day._write_json(first_day.PENDING_DIR / "FAKEwait001.json", {
    "video_id": "FAKEwait001", "title": "Taylor Swift - Patient Zero (Official Music Video)",
    "channel": "main", "published_at": iso(wait_pub), "due_at": iso(wait_pub + timedelta(hours=24)),
    "thumbnail_url": "https://i.ytimg.com/vi/mw3kSNIxjqo/maxresdefault.jpg", "tags": [],
})
# Captured, not posted yet (post task runs 15 min after).
cap_pub = now - timedelta(hours=24, minutes=5)
first_day._write_json(first_day.PENDING_DIR / "FAKEcap0001.json", {
    "video_id": "FAKEcap0001", "title": "Taylor Swift - Patient Zero (Official Lyric Video)",
    "channel": "main", "published_at": iso(cap_pub), "due_at": iso(cap_pub + timedelta(hours=24)),
    "thumbnail_url": "https://i.ytimg.com/vi/mw3kSNIxjqo/hqdefault.jpg", "tags": [],
})
first_day._write_json(first_day.CAPTURES_DIR / "FAKEcap0001.json", {
    "video_id": "FAKEcap0001", "views": 4213377, "captured_at": iso(cap_pub + timedelta(hours=24)),
    "due_at": iso(cap_pub + timedelta(hours=24)), "offset_seconds": 0, "source": "scheduled_task",
})
# Resolved release 3 days ago: one posted, one missed, one Topic re-upload (hidden).
old_pub = now - timedelta(days=3)
member = lambda vid, title, ch, cap, result: {  # noqa: E731
    "video_id": vid, "title": title, "channel": ch, "published_at": iso(old_pub),
    "due_at": iso(old_pub + timedelta(hours=24)), "thumbnail_url": "", "tags": [],
    "capture": cap, "result": result,
}
first_day._write_json(first_day.RELEASES_DIR / "FAKEold0001.json", {
    "anchor": "FAKEold0001", "status": "posted", "members": [
        member("FAKEold0001", "Babylon", "topic", {"views": 1804220}, "included"),
        member("FAKEold0002", "Taylor Swift - Babylon (Visualizer)", "main", None, "expired"),
        member("FAKEold0003", "Shake It Off", "topic", {"views": 90000}, "reupload"),
    ],
})

feed = new_releases.build(now)
(HERE / "fixtures").mkdir(exist_ok=True)
(HERE / "fixtures" / "feed.json").write_text(json.dumps(feed, indent=2), encoding="utf-8")
for r in feed["releases"]:
    for v in r["videos"]:
        print(f"{v['video_id']:12} {v['state']:9} {v['views_24h']}  {v['title']}")

if "--no-browser" in sys.argv:
    raise SystemExit(0)

from playwright.sync_api import sync_playwright  # noqa: E402

body = json.dumps(feed)
out = HERE / "cards"
out.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, vp, mobile in [("desktop", {"width": 1440, "height": 900}, False),
                             ("mobile", {"width": 390, "height": 844}, True)]:
        ctx = browser.new_context(viewport=vp, device_scale_factor=2 if mobile else 1,
                                  is_mobile=mobile, has_touch=mobile)
        page = ctx.new_page()
        page.route("**/api/youtube/new-releases*",
                   lambda route: route.fulfill(status=200, content_type="application/json", body=body))
        page.goto("http://localhost:3000/youtube", wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path=str(out / f"{name}_closed.png"))
        page.click(".youtube-new-btn")
        page.wait_for_timeout(600)
        page.screenshot(path=str(out / f"{name}_open.png"))
        ctx.close()
    browser.close()
print("screens ->", out)
