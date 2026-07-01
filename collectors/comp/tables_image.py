from __future__ import annotations

import argparse
import base64
import colorsys
import html
import random
import re
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

try:
    from .discography import get_album_cover
    from .fmt import fmt_pct, fmt_streams, get_pct, nan_to_none, pct_cls
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from comp.discography import get_album_cover
    from comp.fmt import fmt_pct, fmt_streams, get_pct, nan_to_none, pct_cls

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

_img_cache: dict[str, str] = {}


def _image_url_candidates(url: str) -> list[str]:
    candidates = [url]
    if "scdn.co/image/" in url or "spotifycdn.com/image/" in url:
        for size_marker in ("0000b273", "00001e02", "00004851", "00001e03"):
            alt = re_sub_spotify_size(url, size_marker)
            if alt not in candidates:
                candidates.append(alt)
    return candidates


def re_sub_spotify_size(url: str, size_marker: str) -> str:
    return re.sub(r"ab67616d[0-9a-f]{8}", f"ab67616d{size_marker}", url, count=1)


def download_as_data_uri(url: str) -> str:
    """One-shot download returning a base64 data URI, or empty string on failure."""
    if not url:
        return ""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as resp:
            mime = resp.headers.get_content_type() or "image/jpeg"
            data = base64.b64encode(resp.read()).decode()
            return f"data:{mime};base64,{data}"
    except Exception:
        return ""


def url_to_data_uri(url: str) -> str:
    """Fetch an image URL as a base64 data URI, or return empty on failure."""
    if not url or not url.startswith("http"):
        return url
    if url in _img_cache:
        return _img_cache[url]
    last_exc = None
    for candidate in _image_url_candidates(url):
        for _ in range(2):
            try:
                req = Request(candidate, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=8) as resp:
                    mime = resp.headers.get_content_type() or "image/jpeg"
                    data = base64.b64encode(resp.read()).decode()
                    result = f"data:{mime};base64,{data}"
                _img_cache[url] = result
                _img_cache[candidate] = result
                return result
            except Exception as e:
                last_exc = e
    print(f"[warn] url_to_data_uri: failed for {url} ({last_exc})")
    _img_cache[url] = ""
    return ""


def pick_header_image(headers_dir: Path) -> Path | None:
    """Returns a random image from the given headers folder."""
    if not headers_dir.exists():
        return None
    imgs = [p for p in headers_dir.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    return random.choice(imgs) if imgs else None


def get_dominant_color(img_path: Path) -> str:
    """Returns a vibrant hex colour extracted from the image."""
    if not _PIL:
        return "#1db954"
    try:
        img = Image.open(img_path).convert("RGB").resize((60, 60), Image.LANCZOS)
        pixels = list(img.getdata())
        filtered = [
            (r, g, b) for r, g, b in pixels
            if not (r > 210 and g > 210 and b > 210)
            and not (r < 40  and g < 40  and b < 40)
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


# ---------------------------------------------------------------------------
# Chart logic
# ---------------------------------------------------------------------------

def rank_change(rank, previous_rank, total_days=None, peak_rank=None):
    if previous_rank is None:
        if peak_rank is not None and int(peak_rank) != int(rank):
            return "RE-ENTRY", "chg-re"
        if total_days and int(total_days) > 1:
            return "RE-ENTRY", "chg-re"
        return "NEW", "chg-new"
    delta = int(previous_rank) - int(rank)
    if delta > 0:
        return f"▲{delta}", "chg-up"
    elif delta < 0:
        return f"▼{abs(delta)}", "chg-dn"
    return "=", "chg-eq"


# ---------------------------------------------------------------------------
# Shared CSS / SVG assets (charts: compact 800px fixed layout)
# ---------------------------------------------------------------------------

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
.container{
  overflow:hidden;
}
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
  grid-template-columns:52px 60px minmax(180px,1fr) 112px 74px 74px 50px 60px;
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
/* Song cards */
.song-card{
  display:grid;
  grid-template-columns:52px 60px minmax(180px,1fr) 112px 74px 74px 50px 60px;
  column-gap:8px;
  align-items:center;
  padding:9px 14px;
  background:rgba(255,255,255,.82);
  border-bottom:1px solid rgba(16,24,40,.05);
}
.song-card.row-odd{background:rgba(248,250,251,.88)}
.song-card.row-gold{
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
.chg-re{color:#5bbde4;font-size:10px;font-weight:800}
/* Song */
.col-song{display:flex;align-items:center;gap:10px;min-width:0}
.art{
  width:42px;height:42px;border-radius:6px;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
}
.art-ph{
  width:42px;height:42px;border-radius:6px;
  background:#dde3ea;flex-shrink:0;
}
.song-info{min-width:0}
.song-title{
  font-size:13px;font-weight:700;color:#101828;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.song-artist{font-size:11px;color:#667085;margin-top:2px}
/* Numeric columns */
.col-num{
  font-size:12px;color:#344054;font-weight:500;
  display:flex;align-items:center;justify-content:flex-end;
}
.pos{color:#067647;font-weight:600}
.neg{color:#b42318;font-weight:600}
.neutral{color:#667085}
/* Footer */
.ftr{
  background:rgba(241,245,246,.96);
  padding:8px 16px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid rgba(16,24,40,.07);
}
.ftr-handle{font-size:11px;color:#1db954;font-weight:700}
.ftr-date{font-size:11px;color:#667085;font-weight:500}
/* Day separator (multi-date) */
.day-hdr{
  padding:6px 14px;
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#344054;
  background:rgba(29,185,84,.06);
  border-top:2px solid rgba(29,185,84,.20);
  border-bottom:1px solid rgba(16,24,40,.07);
}
/* Section separator (FR streaming vs FR Pop) */
.section-hdr{
  padding:8px 14px;
  font-size:11px;font-weight:800;text-transform:uppercase;
  letter-spacing:.08em;color:#fff;
}
/* OUT section */
.out-hdr{
  padding:6px 14px;
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#b42318;
  background:rgba(180,35,24,.05);
  border-top:2px solid rgba(180,35,24,.25);
  border-bottom:1px solid rgba(180,35,24,.10);
}
.out-card{
  display:grid;
  grid-template-columns:52px 60px minmax(180px,1fr);
  column-gap:8px;
  align-items:center;
  padding:9px 14px;
  background:rgba(240,240,240,.60);
  border-bottom:1px solid rgba(16,24,40,.05);
  opacity:0.80;
}
.col-out-badge{
  font-size:10px;font-weight:800;color:#fff;
  display:flex;align-items:center;justify-content:center;
  background:#b42318;border-radius:4px;padding:3px 6px;
  width:fit-content;margin:auto;
}
.col-out-last{
  font-size:12px;font-weight:600;color:#9ca3af;
  display:flex;align-items:center;justify-content:center;
}
"""

SPOTIFY_SVG = """<svg class="hdr-logo" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
</svg>"""

COL_HEADS_HTML = """<div class="col-heads">
    <span>Pos</span>
    <span>Chg</span>
    <span>Track</span>
    <span class="right">Streams</span>
    <span class="right">Daily</span>
    <span class="right">Weekly</span>
    <span class="right">Streak</span>
    <span class="right">Total</span>
  </div>"""


# ---------------------------------------------------------------------------
# HTML row builders (charts)
# ---------------------------------------------------------------------------

def build_out_rows_html(
    out_songs: list[dict],
    track_album_map: dict,
    cover_map: dict,
    track_image_map: dict,
    chart_date: str,
) -> str:
    if not out_songs:
        return ""
    yesterday = str((datetime.strptime(chart_date, "%Y-%m-%d").date()) - timedelta(days=1))
    html = ""
    for row in out_songs:
        track       = str(row.get("track_name") or "")
        artist      = str(row.get("artist_names") or "")
        rank        = row.get("rank")
        scraped_img = row.get("image_url") or ""
        cover_url   = url_to_data_uri(get_album_cover(track, track_album_map, cover_map, track_image_map, scraped_img))
        art_html    = (
            f'<img class="art" src="{cover_url}" />'
            if cover_url
            else '<div class="art-ph"></div>'
        )
        rank_txt = f"#{int(rank)}" if rank else "—"
        html += f"""<div class="out-card">
  <div class="col-out-badge">OUT</div>
  <div class="col-out-last">{rank_txt}</div>
  <div class="col-song">
    {art_html}
    <div class="song-info">
      <div class="song-title">{track}</div>
      <div class="song-artist">{artist} · last: {yesterday}</div>
    </div>
  </div>
</div>
"""
    return html


def build_rows_html(
    rows,
    history: dict,
    chart_date: str,
    track_album_map: dict,
    cover_map: dict,
    track_image_map: dict,
    ref_streams_fn: Callable,
) -> str:
    """ref_streams_fn(track_hist, track, ref_date) → int | None — region-specific lookup."""
    date_obj  = datetime.strptime(chart_date, "%Y-%m-%d").date()
    yesterday = str(date_obj - timedelta(days=1))
    week_ago  = str(date_obj - timedelta(days=7))

    html = ""
    for i, row in enumerate(rows):
        track       = str(row.get("track_name") or "")
        artist      = str(row.get("artist_names") or "")
        rank        = nan_to_none(row.get("rank"))
        prev_rank   = nan_to_none(row.get("previous_rank"))
        peak_rank   = nan_to_none(row.get("peak_rank"))
        streams     = nan_to_none(row.get("streams"))
        streak      = nan_to_none(row.get("streak"))
        total_days  = nan_to_none(row.get("total_days"))
        scraped_img = row.get("image_url") or ""

        if rank is None:
            continue
        rank = int(rank)

        chg_text, chg_css = rank_change(
            rank,
            int(prev_rank) if prev_rank else None,
            total_days,
            int(peak_rank) if peak_rank else None,
        )
        cover_url = url_to_data_uri(get_album_cover(track, track_album_map, cover_map, track_image_map, scraped_img))

        track_hist   = history.get(track, {})
        try:
            prev_streams = ref_streams_fn(track_hist, track, yesterday, row)
            week_streams = ref_streams_fn(track_hist, track, week_ago, row)
        except TypeError:
            prev_streams = ref_streams_fn(track_hist, track, yesterday)
            week_streams = ref_streams_fn(track_hist, track, week_ago)
        streams_int  = int(streams) if streams else None

        daily_pct  = get_pct(streams_int, prev_streams)
        weekly_pct = get_pct(streams_int, week_streams)

        streams_fmt    = fmt_streams(streams_int)
        daily_txt      = fmt_pct(daily_pct)
        weekly_txt     = fmt_pct(weekly_pct)
        consec_txt     = str(int(streak)) + "d" if streak else "—"
        total_days_txt = str(int(total_days)) + "d" if total_days else "—"

        art_html = (
            f'<img class="art" src="{cover_url}" />'
            if cover_url
            else '<div class="art-ph"></div>'
        )

        card_cls = "song-card"
        if rank == 1:
            card_cls += " row-gold"
        elif i % 2 != 0:
            card_cls += " row-odd"

        html += f"""<div class="{card_cls}">
  <div class="col-rank">#{rank}</div>
  <div class="col-chg {chg_css}">{chg_text}</div>
  <div class="col-song">
    {art_html}
    <div class="song-info">
      <div class="song-title">{track}</div>
      <div class="song-artist">{artist}</div>
    </div>
  </div>
  <div class="col-num">{streams_fmt}</div>
  <div class="col-num {pct_cls(daily_pct)}">{daily_txt}</div>
  <div class="col-num {pct_cls(weekly_pct)}">{weekly_txt}</div>
  <div class="col-num">{consec_txt}</div>
  <div class="col-num">{total_days_txt}</div>
</div>
"""
    return html


# ---------------------------------------------------------------------------
# Streams / albums table CSS and builder (parameterized via CSS variables)
# ---------------------------------------------------------------------------

STREAMS_TABLE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  background:
    radial-gradient(circle at 12% 18%, rgba(29,185,84,.13), transparent 30%),
    radial-gradient(circle at 84% 16%, rgba(126,87,255,.10), transparent 32%),
    linear-gradient(180deg,#f4f7f8 0%,#edf3f4 100%);
  width:var(--body-w,800px);
  padding:0;color:#101828;
}
.container{overflow:hidden}
.hdr{padding:22px 26px;display:flex;align-items:center;gap:18px}
.hdr-logo{width:64px;height:64px;flex-shrink:0}
.hdr-title{color:#fff;font-size:26px;font-weight:800;letter-spacing:-.3px}
.hdr-sub{color:rgba(255,255,255,.85);font-size:15px;margin-top:5px}
.col-heads{
  display:grid;
  grid-template-columns:var(--grid-cols);
  column-gap:var(--col-gap,8px);
  padding:9px 18px;
  background:rgba(241,245,246,.95);
  border-bottom:1px solid rgba(16,24,40,.07);
}
.col-heads span{
  font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#667085;
  display:flex;align-items:center;
}
.col-heads .right{justify-content:flex-end}
.data-row{
  display:grid;
  grid-template-columns:var(--grid-cols);
  column-gap:var(--col-gap,8px);
  align-items:center;
  padding:7px 18px;
  background:rgba(255,255,255,.82);
  border-bottom:1px solid rgba(16,24,40,.05);
}
.data-row.row-odd{background:rgba(248,250,251,.88)}
.data-row.row-gold{
  background:linear-gradient(90deg,#fff7d6 0%,#fffdf5 40%,rgba(255,255,255,.92) 100%);
  border-left:3px solid #ebc44c;
}
.col-rank{
  font-size:21px;font-weight:900;color:#0b1f44;
  letter-spacing:-.04em;
  display:flex;align-items:center;justify-content:center;
}
.col-chg{
  font-size:13px;font-weight:700;
  display:flex;align-items:center;justify-content:center;
}
.chg-up{color:#067647}.chg-dn{color:#b42318}
.chg-eq{color:#9ca3af}
.chg-new{color:#5bbde4;font-size:11px;font-weight:800}
.col-entity{display:flex;align-items:center;gap:12px;min-width:0}
.art{
  width:var(--art-size,54px);height:var(--art-size,54px);border-radius:7px;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
}
.art-ph{
  width:var(--art-size,54px);height:var(--art-size,54px);border-radius:7px;
  background:#dde3ea;flex-shrink:0;
}
.entity-info{min-width:0}
.entity-name{
  font-size:15px;font-weight:700;color:#101828;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.entity-sub{font-size:13px;color:#667085;margin-top:3px}
.col-num{
  font-size:14px;color:#344054;font-weight:500;
  display:flex;align-items:center;justify-content:flex-end;
}
.pos{color:#067647;font-weight:600}
.neg{color:#b42318;font-weight:600}
.neutral{color:#667085}
.delta-wrap{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.delta-num{font-size:13px;font-weight:600}
.delta-pct{font-size:11px;font-weight:500;opacity:.85}
.ftr{
  background:rgba(241,245,246,.96);
  padding:11px 20px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid rgba(16,24,40,.07);
}
.ftr-handle{font-size:13px;color:#1db954;font-weight:700}
.ftr-date{font-size:13px;color:#667085;font-weight:500}
"""


def build_table_html(
    *,
    title: str,
    subtitle: str,
    col_heads: list[tuple[str, bool]],
    grid_cols: str,
    rows_html: str,
    handle: str,
    date_str: str,
    headers_dir: Path,
    body_width: int = 800,
    art_size: int = 54,
    col_gap: int = 8,
    extra_css: str = "",
    header_background: str | None = None,
    handle_color_override: str | None = None,
) -> str:
    """Build a complete glassmorphism table image HTML document.

    col_heads: list of (label, right_aligned) tuples.
    """
    header_img = None if header_background else pick_header_image(headers_dir)
    handle_color = handle_color_override or "#1db954"
    if header_background:
        hdr_style = f'style="background:{header_background};"'
    elif header_img:
        handle_color = get_dominant_color(header_img)
        img_url = header_img.as_posix()
        hdr_style = (
            f'style="background-image: linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45)),'
            f"url('file:///{img_url}'); background-size:cover; background-position:center;\""
        )
    else:
        hdr_style = 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"'

    col_heads_html = '<div class="col-heads">\n'
    for label, right in col_heads:
        cls = ' class="right"' if right else ""
        col_heads_html += f"  <span{cls}>{label}</span>\n"
    col_heads_html += "</div>"

    css_vars = (
        f":root{{--body-w:{body_width}px;--grid-cols:{grid_cols};"
        f"--art-size:{art_size}px;--col-gap:{col_gap}px}}"
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{css_vars}{STREAMS_TABLE_CSS}{extra_css}</style></head>
<body>
<div class="container">
  <div class="hdr" {hdr_style}>
    {SPOTIFY_SVG}
    <div>
      <div class="hdr-title">{title}</div>
      <div class="hdr-sub">{subtitle}</div>
    </div>
  </div>
  {col_heads_html}
  {rows_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{handle}</span>
    <span class="ftr-date">{date_str}</span>
  </div>
</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Playwright rendering
# ---------------------------------------------------------------------------

def render_html_to_png(
    html_text: str,
    out_path: Path,
    tmp_path: Path,
    width: int = 800,
    *,
    keep_html: bool = False,
) -> Path:
    """Write html_text to tmp_path, screenshot with Playwright, delete tmp_path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(html_text, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": width, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_path.as_posix()}", wait_until="load")
            page.wait_for_load_state("networkidle", timeout=3000)
            try:
                full_h = page.evaluate("() => document.body.scrollHeight")
                full_h = int(full_h) if full_h else 200
                full_h = max(200, min(full_h, 6000))
                page.set_viewport_size({"width": width, "height": full_h})
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_path.exists() and not keep_html:
            tmp_path.unlink()
    return out_path


def _preview_imports():
    repo_root = Path(__file__).resolve().parents[2]
    streams_dir = repo_root / "collectors" / "spotify" / "streams"
    streams_scripts_dir = streams_dir / "tools" / "scripts"
    for path in (repo_root / "collectors", streams_dir, streams_scripts_dir):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
    import best_day_since
    import spotlight

    return best_day_since, spotlight


def _latest_preview_date(best_day_since) -> str:
    history = best_day_since.load_history()
    latest = best_day_since.latest_history_date(history)
    if latest is None:
        raise SystemExit("No streams history date found for table preview generation.")
    print(f"[tables-preview] Using latest streams date: {latest.isoformat()}", flush=True)
    return latest.isoformat()


def _preview_rows(best_day_since, spotlight, stats_date: str, limit: int) -> list[dict]:
    target_day = datetime.strptime(stats_date, "%Y-%m-%d").date()
    previous_day = target_day - timedelta(days=1)
    week_day = target_day - timedelta(days=7)
    history = best_day_since.load_history()
    tracks = spotlight.load_all_tracks()
    covers = spotlight.load_covers()
    print(f"[tables-preview] Loaded {len(tracks)} track(s) and {len(covers)} cover fallback(s).", flush=True)

    candidates = []
    for track in tracks:
        point_by_day = {point.day: point for point in history.get(track["track_id"], [])}
        current = point_by_day.get(target_day)
        if current is None or current.total is None or current.daily is None or current.daily <= 0:
            continue
        previous = point_by_day.get(previous_day)
        week = point_by_day.get(week_day)
        candidates.append({
            "track": track,
            "total": current.total,
            "daily": current.daily,
            "previous_daily": previous.daily if previous else None,
            "week_daily": week.daily if week else None,
            "cover_url": spotlight.get_cover_url(track, covers),
        })

    if not candidates:
        raise SystemExit(f"No stream rows found for {stats_date}.")
    sample_size = min(max(limit, 1), len(candidates))
    rows = random.sample(candidates, sample_size)
    rows.sort(key=lambda row: row["daily"], reverse=True)
    print(f"[tables-preview] Selected {len(rows)} real stream row(s).", flush=True)
    return rows


def _preview_rows_html(rows: list[dict]) -> str:
    output = []
    for index, row in enumerate(rows, 1):
        track = row["track"]
        daily_pct = get_pct(row["daily"], row["previous_daily"])
        weekly_pct = get_pct(row["daily"], row["week_daily"])
        cover = url_to_data_uri(row["cover_url"])
        art_html = f'<img class="art" src="{cover}" />' if cover else '<div class="art-ph"></div>'
        row_class = "data-row row-gold" if index == 1 else ("data-row row-odd" if index % 2 else "data-row")
        output.append(f"""<div class="{row_class}">
  <div class="col-rank">#{index}</div>
  <div class="col-entity">
    {art_html}
    <div class="entity-info">
      <div class="entity-name">{html.escape(track.get("title") or "")}</div>
      <div class="entity-sub">{html.escape(track.get("album") or track.get("artist") or "")}</div>
    </div>
  </div>
  <div class="col-num">{fmt_streams(row["daily"])}</div>
  <div class="col-num">{fmt_streams(row["total"])}</div>
  <div class="col-num {pct_cls(daily_pct)}">{fmt_pct(daily_pct)}</div>
  <div class="col-num {pct_cls(weekly_pct)}">{fmt_pct(weekly_pct)}</div>
</div>""")
    return "\n".join(output)


def generate_preview(
    *,
    output_dir: Path,
    stats_date: str | None = None,
    limit: int = 10,
    keep_html: bool = True,
) -> Path:
    print("[tables-preview] Building tables_image preview...", flush=True)
    best_day_since, spotlight = _preview_imports()
    target_date = stats_date or _latest_preview_date(best_day_since)
    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows = _preview_rows(best_day_since, spotlight, target_date, limit)
    html_text = build_table_html(
        title="Spotify Streams Preview",
        subtitle=f"Real stream rows - {date_text}",
        col_heads=[
            ("Pos", False),
            ("Track", False),
            ("Daily", True),
            ("Total", True),
            ("Vs Day", True),
            ("Vs Week", True),
        ],
        grid_cols="52px minmax(260px,1fr) 108px 118px 82px 82px",
        rows_html=_preview_rows_html(rows),
        handle="@tsmuseum13",
        date_str=date_text,
        headers_dir=Path(__file__).resolve().parent / "headers",
        body_width=920,
        art_size=48,
        header_background="linear-gradient(135deg,#1db954 0%,#0f5132 100%)",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"tables_image_preview_{target_date}.png"
    html_path = output_dir / f"tables_image_preview_{target_date}.html"
    print(f"[tables-preview] Writing HTML: {html_path}", flush=True)
    print(f"[tables-preview] Rendering PNG: {out_path}", flush=True)
    return render_html_to_png(html_text, out_path, html_path, width=920, keep_html=keep_html)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tables_image preview PNG/HTML.")
    parser.add_argument("--date", help="Stats date YYYY-MM-DD. Defaults to latest date in streams history.")
    parser.add_argument("--limit", type=int, default=10, help="Number of real stream rows to include.")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "previews"),
        help="Directory for generated previews.",
    )
    parser.add_argument("--keep-html", action="store_true", default=True, help="Keep the generated HTML preview.")
    parser.add_argument("--no-keep-html", action="store_false", dest="keep_html", help="Delete the temporary HTML.")
    args = parser.parse_args()

    path = generate_preview(
        output_dir=Path(args.output_dir),
        stats_date=args.date,
        limit=args.limit,
        keep_html=args.keep_html,
    )
    print(f"Generated table preview: {path}", flush=True)


if __name__ == "__main__":
    main()
