#!/usr/bin/env python3
"""
generate_artist_chart_image.py — génère une image du Global Artist Chart Spotify.

Logique :
  - Si Taylor Swift est dans le top 5 → affiche le top 5 (avec TS surlignée)
  - Si Taylor Swift est dans le top 10 → affiche le top 10 (avec TS surlignée)
  - Si Taylor Swift n'est pas dans le top 10 → affiche uniquement la carte Taylor Swift

Usage :
    python generate_artist_chart_image.py
    python generate_artist_chart_image.py 2026-05-06
    python generate_artist_chart_image.py 2026-05-06 --no-post
"""
from __future__ import annotations

import argparse
import base64
import colorsys
import json
import os
import random
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

try:
    from PIL import Image as _PILImage
    _PIL = True
except ImportError:
    _PIL = False

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR      = Path(__file__).resolve()
ARTISTS_GLOBAL  = SCRIPT_DIR.parents[2]                    # artists_global/
REPO_ROOT       = SCRIPT_DIR.parents[6]                    # tsm-backend/
HEADERS_DIR     = REPO_ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "headers"
TWITTER_SESSION = REPO_ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))
from core.data_paths import legacy_spotify_chart_dir, spotify_chart_dir

HANDLE  = "@tsmusem13"
TS_NAME = "Taylor Swift"

_TWITTER_POST_LOCK = Path(tempfile.gettempdir()) / "tsm_twitter_post.lock"
_LOCK_TIMEOUT = 15 * 60


# ── Lock helpers ───────────────────────────────────────────────────────────────

def _wait_for_twitter_lock() -> None:
    start = time.time()
    while True:
        try:
            fd = os.open(str(_TWITTER_POST_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            os.close(fd)
            return
        except FileExistsError:
            elapsed = time.time() - start
            if elapsed > _LOCK_TIMEOUT:
                print("[WARN] Twitter post lock timeout — forcing continue")
                return
            if elapsed < 5:
                print("[INFO] Twitter posting in progress — attente...", flush=True)
            time.sleep(2)


def _release_twitter_lock() -> None:
    try:
        _TWITTER_POST_LOCK.unlink()
    except FileNotFoundError:
        pass


# ── Image helpers ──────────────────────────────────────────────────────────────

_img_cache: dict[str, str] = {}


def url_to_data_uri(url: str) -> str:
    if not url or not url.startswith("http"):
        return url
    if url in _img_cache:
        return _img_cache[url]
    for _ in range(2):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=8) as resp:
                mime = resp.headers.get_content_type() or "image/jpeg"
                data = base64.b64encode(resp.read()).decode()
                result = f"data:{mime};base64,{data}"
            _img_cache[url] = result
            return result
        except Exception:
            pass
    _img_cache[url] = url
    return url


def pick_header_image() -> Path | None:
    if not HEADERS_DIR.exists():
        return None
    imgs = [p for p in HEADERS_DIR.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    return random.choice(imgs) if imgs else None


def get_dominant_color(img_path: Path) -> str:
    if not _PIL:
        return "#1db954"
    try:
        img = _PILImage.open(img_path).convert("RGB").resize((60, 60), _PILImage.LANCZOS)
        pixels = list(img.getdata())
        filtered = [
            (r, g, b) for r, g, b in pixels
            if not (r > 210 and g > 210 and b > 210)
            and not (r < 40 and g < 40 and b < 40)
        ]
        if not filtered:
            filtered = pixels
        r = sum(p[0] for p in filtered) // len(filtered)
        g = sum(p[1] for p in filtered) // len(filtered)
        b = sum(p[2] for p in filtered) // len(filtered)
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        s = min(1.0, s * 1.8)
        v = min(1.0, max(0.55, v))
        r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
        return f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"
    except Exception:
        return "#1db954"


# ── Data helpers ───────────────────────────────────────────────────────────────

def find_latest_date() -> str:
    latest = None
    for root in (
        REPO_ROOT / "data",
        ARTISTS_GLOBAL / "history",
    ):
        if not root.exists():
            continue
        for day_dir in sorted(root.rglob("*")):
            if day_dir.is_dir() and (day_dir / "artist_global_daily.json").exists():
                latest = day_dir.name
    if not latest:
        raise FileNotFoundError("No artist_global_daily.json found in data/")
    return latest


def load_chart(stats_date: str) -> dict:
    path = spotify_chart_dir("artists_global", stats_date) / "artist_global_daily.json"
    if not path.exists():
        path = legacy_spotify_chart_dir("artists_global", stats_date) / "artist_global_daily.json"
    if not path.exists():
        raise FileNotFoundError(f"Chart not found: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rank_change_label(rank: int, previous_rank) -> tuple[str, str]:
    if previous_rank is None:
        return "NEW", "chg-new"
    delta = int(previous_rank) - int(rank)
    if delta > 0:
        return f"▲{delta}", "chg-up"
    elif delta < 0:
        return f"▼{abs(delta)}", "chg-dn"
    return "=", "chg-eq"


def fmt_streak(days) -> str:
    if days is None:
        return "—"
    return f"{int(days)}d"


# ── CSS (same light glassmorphism as generate_chart_image.py) ─────────────────

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  background:
    radial-gradient(circle at 12% 18%, rgba(29,185,84,.13), transparent 30%),
    radial-gradient(circle at 84% 16%, rgba(126,87,255,.10), transparent 32%),
    linear-gradient(180deg,#f4f7f8 0%,#edf3f4 100%);
  width:800px;
  padding:0;
  color:#101828;
}
.container{overflow:hidden}
/* Header */
.hdr{
  padding:49px 22px;
  display:flex;align-items:center;gap:16px;
}
.hdr-logo{width:52px;height:52px;flex-shrink:0}
.hdr-title{color:#fff;font-size:22px;font-weight:800;letter-spacing:-.3px}
.hdr-sub{color:rgba(255,255,255,.85);font-size:13px;margin-top:4px}
/* Column headers */
.col-heads{
  display:grid;
  grid-template-columns:52px 56px minmax(200px,1fr) 80px 80px;
  column-gap:8px;
  padding:7px 14px;
  background:rgba(241,245,246,.95);
  border-bottom:1px solid rgba(16,24,40,.07);
}
.col-heads span{
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#667085;
  display:flex;align-items:center;
}
.col-heads .right{justify-content:flex-end}
/* Artist cards */
.artist-card{
  display:grid;
  grid-template-columns:52px 56px minmax(200px,1fr) 80px 80px;
  column-gap:8px;
  align-items:center;
  padding:9px 14px;
  background:rgba(255,255,255,.82);
  border-bottom:1px solid rgba(16,24,40,.05);
}
.artist-card.row-odd{background:rgba(248,250,251,.88)}
.artist-card.row-gold{
  background:linear-gradient(90deg,#fff7d6 0%,#fffdf5 40%,rgba(255,255,255,.92) 100%);
  border-left:3px solid #ebc44c;
}
/* Rank */
.col-rank{
  font-size:17px;font-weight:900;color:#0b1f44;
  letter-spacing:-.04em;
  display:flex;align-items:center;justify-content:center;
}
/* Change */
.col-chg{
  font-size:11px;font-weight:700;
  display:flex;align-items:center;justify-content:center;
}
.chg-up{color:#067647}
.chg-dn{color:#b42318}
.chg-eq{color:#9ca3af}
.chg-new{color:#5bbde4;font-size:10px;font-weight:800}
/* Artist */
.col-artist{display:flex;align-items:center;gap:10px;min-width:0}
.avatar{
  width:42px;height:42px;border-radius:50%;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
}
.avatar-ph{
  width:42px;height:42px;border-radius:50%;
  background:#dde3ea;flex-shrink:0;
}
.artist-name{
  font-size:13px;font-weight:700;color:#101828;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
/* Numeric columns */
.col-num{
  font-size:12px;color:#344054;font-weight:500;
  display:flex;align-items:center;justify-content:flex-end;
}
/* Footer */
.ftr{
  background:rgba(241,245,246,.96);
  padding:8px 16px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid rgba(16,24,40,.07);
}
.ftr-handle{font-size:11px;font-weight:700}
.ftr-date{font-size:11px;color:#667085;font-weight:500}
/* Solo card */
.solo-wrap{
  padding:28px 24px 24px;
  display:flex;align-items:center;gap:24px;
  background:rgba(255,255,255,.82);
}
.solo-avatar{
  width:90px;height:90px;border-radius:50%;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 4px 16px rgba(0,0,0,.15);
}
.solo-info{flex:1;min-width:0}
.solo-name{font-size:22px;font-weight:900;color:#101828;letter-spacing:-.4px}
.solo-rank{font-size:15px;font-weight:600;color:#344054;margin-top:5px}
.solo-meta{display:flex;gap:24px;margin-top:10px}
.solo-stat{display:flex;flex-direction:column;gap:2px}
.solo-stat-label{
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#667085;
}
.solo-stat-val{font-size:15px;font-weight:700;color:#101828}
"""

SPOTIFY_SVG = """<svg class="hdr-logo" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
</svg>"""


# ── HTML builders ──────────────────────────────────────────────────────────────

def _artist_row_html(artist: dict, idx: int) -> str:
    rank = artist["rank"]
    chg_label, chg_cls = rank_change_label(rank, artist.get("previous_rank"))
    img_uri = url_to_data_uri(artist.get("image_url", ""))
    name = artist["artist_name"]
    peak = artist.get("peak_rank", "—")
    streak = fmt_streak(artist.get("streak"))
    is_ts = name.lower() == TS_NAME.lower()

    if is_ts:
        card_cls = "artist-card row-gold"
    elif idx % 2 != 0:
        card_cls = "artist-card row-odd"
    else:
        card_cls = "artist-card"

    img_tag = (
        f'<img class="avatar" src="{img_uri}" alt="">'
        if img_uri.startswith("data:")
        else '<div class="avatar-ph"></div>'
    )
    return f"""<div class="{card_cls}">
  <div class="col-rank">#{rank}</div>
  <div class="col-chg {chg_cls}">{chg_label}</div>
  <div class="col-artist">
    {img_tag}
    <div class="artist-name">{name}</div>
  </div>
  <div class="col-num">#{peak}</div>
  <div class="col-num">{streak}</div>
</div>"""


def _hdr_style(header_img: Path | None) -> tuple[str, str]:
    if header_img:
        handle_color = get_dominant_color(header_img)
        img_url = header_img.as_posix()
        style = (
            f'style="background-image:linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45)),'
            f'url(\'file:///{img_url}\');background-size:100% 100%;"'
        )
    else:
        handle_color = "#1db954"
        style = 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"'
    return style, handle_color


def build_top5_html(artists: list[dict], stats_date: str, header_img: Path | None) -> str:
    date_fmt = datetime.strptime(stats_date, "%Y-%m-%d").strftime("%B %d, %Y")
    hdr_style, handle_color = _hdr_style(header_img)
    top5 = [a for a in artists if a["rank"] <= 5]
    rows_html = "\n".join(_artist_row_html(a, i) for i, a in enumerate(top5))
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="container">
  <div class="hdr" {hdr_style}>
    {SPOTIFY_SVG}
    <div>
      <div class="hdr-title">Taylor Swift · Global Artist Chart</div>
      <div class="hdr-sub">Top 5 · {date_fmt}</div>
    </div>
  </div>
  <div class="col-heads">
    <span>Pos</span>
    <span>Chg</span>
    <span>Artist</span>
    <span class="right">Peak</span>
    <span class="right">Streak</span>
  </div>
  {rows_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div></body></html>"""

def build_top10_html(artists: list[dict], stats_date: str, header_img: Path | None) -> str:
    date_fmt = datetime.strptime(stats_date, "%Y-%m-%d").strftime("%B %d, %Y")
    hdr_style, handle_color = _hdr_style(header_img)
    top10 = [a for a in artists if a["rank"] <= 10]
    rows_html = "\n".join(_artist_row_html(a, i) for i, a in enumerate(top10))
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="container">
  <div class="hdr" {hdr_style}>
    {SPOTIFY_SVG}
    <div>
      <div class="hdr-title">Taylor Swift · Global Artist Chart</div>
      <div class="hdr-sub">Top 10 · {date_fmt}</div>
    </div>
  </div>
  <div class="col-heads">
    <span>Pos</span>
    <span>Chg</span>
    <span>Artist</span>
    <span class="right">Peak</span>
    <span class="right">Streak</span>
  </div>
  {rows_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div></body></html>"""


def build_solo_html(ts_artist: dict, stats_date: str, header_img: Path | None) -> str:
    date_fmt = datetime.strptime(stats_date, "%Y-%m-%d").strftime("%B %d, %Y")
    hdr_style, handle_color = _hdr_style(header_img)
    rank = ts_artist["rank"]
    chg_label, chg_cls = rank_change_label(rank, ts_artist.get("previous_rank"))
    streak = fmt_streak(ts_artist.get("streak"))
    peak = ts_artist.get("peak_rank", "—")
    img_uri = url_to_data_uri(ts_artist.get("image_url", ""))
    img_tag = (
        f'<img class="solo-avatar" src="{img_uri}" alt="Taylor Swift">'
        if img_uri.startswith("data:")
        else '<div class="solo-avatar" style="background:#dde3ea"></div>'
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="container">
  <div class="hdr" {hdr_style}>
    {SPOTIFY_SVG}
    <div>
      <div class="hdr-title">Taylor Swift · Global Artist Chart</div>
      <div class="hdr-sub">{date_fmt}</div>
    </div>
  </div>
  <div class="solo-wrap">
    {img_tag}
    <div class="solo-info">
      <div class="solo-name">Taylor Swift</div>
      <div class="solo-rank">Ranked #{rank} globally</div>
      <div class="solo-meta">
        <div class="solo-stat">
          <span class="solo-stat-label">Change</span>
          <span class="solo-stat-val {chg_cls}">{chg_label}</span>
        </div>
        <div class="solo-stat">
          <span class="solo-stat-label">Streak</span>
          <span class="solo-stat-val">{streak}</span>
        </div>
        <div class="solo-stat">
          <span class="solo-stat-label">Peak</span>
          <span class="solo-stat-val">#{peak}</span>
        </div>
      </div>
    </div>
  </div>
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div></body></html>"""


# ── Screenshot ─────────────────────────────────────────────────────────────────

def generate_image(html_content: str, out_path: Path) -> None:
    html_tmp = out_path.parent / "_artist_chart_tmp.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_tmp.write_text(html_content, encoding="utf-8")
    _wait_for_twitter_lock()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 800, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{html_tmp.as_posix()}", wait_until="load")
            page.wait_for_load_state("networkidle", timeout=3000)
            try:
                full_h = page.evaluate("() => document.body.scrollHeight")
                full_h = max(200, min(int(full_h) if full_h else 200, 6000))
                page.set_viewport_size({"width": 800, "height": full_h})
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        _release_twitter_lock()
        html_tmp.unlink(missing_ok=True)
    print(f"Image saved → {out_path}")


# ── Tweet text ─────────────────────────────────────────────────────────────────

def build_tweet(ts_artist: dict, mode: str, stats_date: str) -> str:
    date_fmt = datetime.strptime(stats_date, "%Y-%m-%d").strftime("%B %d, %Y")

    if mode == "top10":
        return f"The top 10 most streamed artists on Spotify Charts yesterday ({date_fmt}) :"
    
    elif mode == "top5":
        return f"The top 5 most streamed artists on Spotify Charts yesterday ({date_fmt}) :"
    
    else:
        return f"Taylor Swift on Spotify Top Artists charts yesterday ({date_fmt}) :"


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Spotify Global Artist Chart image.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD (default: latest available)")
    parser.add_argument("--no-post", action="store_true", help="Generate image but skip Twitter posting")
    parser.add_argument("--session", help="Path to a Twitter session JSON file (overrides default)")
    args = parser.parse_args()

    stats_date = args.date or find_latest_date()
    print(f"Date: {stats_date}")

    data = load_chart(stats_date)
    artists = data["artists"]

    ts_artist = next((a for a in artists if a["artist_name"].lower() == TS_NAME.lower()), None)
    if not ts_artist:
        print("Taylor Swift not found in chart data — skipping.")
        sys.exit(0)

    ts_rank = ts_artist["rank"]
    print(f"Taylor Swift: rank #{ts_rank}")

    header_img = pick_header_image()
    if ts_rank <= 5:
        mode = "top5"
        html = build_top5_html(artists, stats_date, header_img)
        print("Mode: Top 5")
    elif ts_rank <= 10:
        mode = "top10"
        html = build_top10_html(artists, stats_date, header_img)
        print("Mode: Top 10")
    else:
        mode = "solo"
        html = build_solo_html(ts_artist, stats_date, header_img)
        print(f"Mode: Solo card (Taylor Swift is #{ts_rank})")

    out_path = spotify_chart_dir("artists_global", stats_date) / "artist_chart_image.png"
    generate_image(html, out_path)

    if not args.no_post:
        twitter_session = Path(args.session) if args.session else TWITTER_SESSION
        if not twitter_session.exists():
            print(f"Twitter session not found: {twitter_session} — skipping post.")
            return
        tweet = build_tweet(ts_artist, mode, stats_date)
        print(f"\nTweet:\n{tweet}\n")
        try:
            from core.twitter import post_with_image
            success = post_with_image(tweet, out_path, twitter_session)
            if success:
                print("✓ Posté avec succès.")
            else:
                print("✗ Échec du post Twitter.")
                sys.exit(1)
        except ImportError as e:
            print(f"Twitter module not available: {e}")
    else:
        print("Twitter post suppressed (--no-post).")


if __name__ == "__main__":
    main()
