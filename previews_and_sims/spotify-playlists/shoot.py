"""Preview of the public /playlists page (branch claude/spotify-playlists).

FAKE: the /api/playlists payload below (names, counts, dates) — the real store
is empty until the TSM Spotify account is authorized. Covers = real album
covers from db/discography/covers.json. Everything else comes from the prod API.

Needs: npx vite --config vite.preview.config.mjs  (port 3040)
Usage: python shoot.py
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:3040"
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)
COVERS = json.loads((Path(__file__).parents[2] / "db/discography/covers.json").read_text(encoding="utf-8"))


def cover(album):
    return COVERS.get(album, {}).get("cover_url", "")


def pl(pid, name, kind, desc, n, album, date=""):
    return {"id": pid, "name": name, "description": desc, "kind": kind, "source": "", "track_count": n,
            "spotify_url": "https://open.spotify.com/playlist/37i9dQZF1DX5KpP2LN299J", "image_url": cover(album),
            "track_ids": [], "updated_at": "2026-10-15T08:15:00Z", "data_date": date}


PAYLOAD = {"playlists": [
    pl("tsm-chart-climbers", "TSM Chart Climbers", "auto", "Taylor Swift songs climbing the Spotify daily chart. Updated 2026-10-14 by thetsmuseum.app", 23, "Midnights", "2026-10-14"),
    pl("tsm-daily-climbers", "TSM Daily Climbers", "auto", "Taylor Swift songs with the biggest day-over-day Spotify stream gains. Updated 2026-10-14 by thetsmuseum.app", 50, "Lover", "2026-10-14"),
    pl("tsm-most-streamed", "TSM Most Streamed Today — a very long playlist name to test wrapping", "auto", "", 100, "", "2026-10-14"),
    pl("deep-cuts", "Deep Cuts", "manual", "Hand-picked album tracks that deserve more streams, from debut to The Life of a Showgirl.", 42, "folklore"),
    pl("vault", "From The Vault", "manual", "Every vault track in one place.", 51, "Red (Taylor's Version)"),
]}


def shoot(browser, name, **ctx_kw):
    ctx = browser.new_context(**ctx_kw)
    ctx.route("**/*", lambda r: r.abort() if r.request.method not in ("GET", "HEAD", "OPTIONS") else r.continue_())
    page = ctx.new_page()
    page.route("**/api/playlists*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(PAYLOAD)))
    page.goto(f"{BASE}/playlists", wait_until="networkidle")
    page.wait_for_timeout(1500)
    sw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    print(name, "scrollWidth/innerWidth =", sw)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    ctx.close()


with sync_playwright() as p:
    b = p.chromium.launch()
    shoot(b, "phone-375", viewport={"width": 375, "height": 812}, device_scale_factor=2, is_mobile=True, has_touch=True)
    shoot(b, "desktop-1280", viewport={"width": 1280, "height": 900})
    b.close()
