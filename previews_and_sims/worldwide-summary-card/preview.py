"""Preview of the Spotify Charts worldwide summary card ("Taylor Swift charted
X songs on the Spotify Charts" thread opener) rendered from REAL worldwide
snapshots, read-only. Nothing is written outside this folder, nothing is posted.

Usage:
    python previews_and_sims/worldwide-summary-card/preview.py 2026-09-23 2026-09-19 2025-10-04

Outputs (per date) in cards/:
    <date>.png        full-res card, same pipeline as generate() (3x + export frame)
    <date>_phone.png  same image downscaled to ~X mobile timeline width (legibility check)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPTS = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_card_images as g  # noqa: E402

OUT = HERE / "cards"
PHONE_WIDTH = 310  # CSS px an image gets in the X mobile timeline (indented under the avatar)


def render(chart_date: str, page, *, min_countries: int = 1) -> Path:
    data = g._load_json(g._worldwide_data_path(chart_date))
    by_track = data.get("by_track", {})
    songs_raw = g._load_json(g.SONGS_JSON)
    songs_list = songs_raw.get("songs", songs_raw) if isinstance(songs_raw, dict) else songs_raw
    song_meta = {s["track_id"]: s for s in songs_list if "track_id" in s}
    prev_by_track = g._load_prev_by_track(chart_date)
    g._enrich_missing_stream_changes(by_track, prev_by_track)
    prev_country_counts = g._load_prev_country_counts(chart_date)

    tracks = [(tid, e) for tid, e in by_track.items() if len(e) >= min_countries]
    tracks.sort(key=lambda item: g._card_priority(item[0], item[1], song_meta.get(item[0], {})))
    palette, theme = g._dominant_album_theme(tracks, song_meta, g.THEMES["showgirl"])

    html_doc = g._build_summary_html(tracks, song_meta, palette, chart_date, prev_country_counts)
    (OUT / f"{chart_date}.html").write_text(html_doc, encoding="utf-8")
    page.set_content(html_doc, wait_until="domcontentloaded")
    card = page.locator("#card")
    card.wait_for(state="visible", timeout=5000)
    out = OUT / f"{chart_date}.png"
    card.screenshot(path=str(out))
    g.add_export_frame(out, device_scale_factor=3)

    im = Image.open(out)
    phone_h = round(im.height * PHONE_WIDTH / im.width)
    im.resize((PHONE_WIDTH, phone_h), Image.LANCZOS).save(OUT / f"{chart_date}_phone.png")
    print(f"{chart_date}: {len(tracks)} tracks, theme={theme}, {im.width}x{im.height} -> {out}")
    return out


def main() -> int:
    dates = sys.argv[1:] or ["2026-09-23"]
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": 1400, "height": 2200}, device_scale_factor=3)
        for d in dates:
            render(d, page)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
