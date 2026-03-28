#!/usr/bin/env python3
"""
generate_album_update_image.py — génère le PNG "Album Daily Update" pour un album donné.

Pour chaque section (Standard Edition, Acoustic Edition, etc.), liste les chansons
avec rang, titre, daily streams, changement vs hier (abs + %), total streams.
Affiche les totaux par section et un grand total.

Usage:
  python generate_album_update_image.py "The Life of a Showgirl"
  python generate_album_update_image.py "The Life of a Showgirl" 2026-03-25
  python generate_album_update_image.py "The Life of a Showgirl" --post
  python generate_album_update_image.py "The Life of a Showgirl" 2026-03-25 --post
"""
from __future__ import annotations

import base64
import colorsys
import concurrent.futures
import csv
import json
import re
import sys
import urllib.request
from datetime import date as date_cls, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR      = Path(__file__).resolve().parent          # streams/tools/scripts/
_TOOLS          = SCRIPT_DIR.parent                        # streams/tools/
ROOT            = SCRIPT_DIR.parents[1]                    # streams/
REPO_ROOT       = SCRIPT_DIR.parents[4]                    # repo root
DB_DIR          = REPO_ROOT / "db"
HISTORY_PATH    = DB_DIR / "streams_history.csv"
ALBUMS_JSON     = DB_DIR / "discography" / "albums.json"
COVERS_PATH     = DB_DIR / "discography" / "covers.json"
HEADERS_DIR     = DB_DIR / "discography" / "headers"
TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"
HANDLE          = "@swiftiescharts"

sys.path.insert(0, str(ROOT.parent))   # collectors/spotify/ for core.*

INCLUDED_EDITIONS = {"standard", "deluxe", "acoustic", "anthology", "original"}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _shorten_title(t: str) -> str:
    t = re.sub(r"\(feat\.\s*", "(ft. ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bDressing Room\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\bRehearsal\b", "Reh.", t, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", t).strip()


def _dominant_color(img_path: Path) -> str:
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


def _url_to_data_uri(url: str) -> str:
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            return f"data:{ct};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return ""


def fmt_num(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", "\u202f")


def fmt_chg(change, pct) -> tuple[str, str, str]:
    """Returns (change_str, pct_str, css_class)."""
    if change is None:
        return "—", "", "neutral"
    cls = "pos" if change >= 0 else "neg"
    chg_s = ("+" if change >= 0 else "−") + fmt_num(abs(change))
    pct_s = ""
    if pct is not None:
        sign = "+" if pct >= 0 else "−"
        pct_s = f"{sign}{abs(pct):.1f}%"
    return chg_s, pct_s, cls


# ── Data loading ───────────────────────────────────────────────────────────────

def load_album_sections(album_name: str) -> list[dict]:
    """
    Returns list of sections for the given album, each with:
      {name, tracks: [{track_id, title_clean, version_tag, display_order, image_url}]}
    Only editions in INCLUDED_EDITIONS. Tracks sorted by display_order.
    """
    if not ALBUMS_JSON.exists():
        return []
    try:
        raw = json.loads(ALBUMS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[album_update] Erreur lecture albums.json: {e}")
        return []

    sections = []
    for sec in raw:
        if sec.get("album") != album_name:
            continue
        tracks = []
        for t in sec.get("tracks", []):
            edition = (t.get("edition") or "").strip().lower()
            if edition not in INCLUDED_EDITIONS:
                continue
            url = (t.get("url") or t.get("spotify_url") or "").strip()
            m = re.search(r"track/([A-Za-z0-9]+)", url)
            if not m:
                continue
            tracks.append({
                "track_id":     m.group(1),
                "title_clean":  (t.get("title_clean") or t.get("title") or "").strip(),
                "version_tag":  (t.get("version_tag") or "").strip(),
                "display_order": t.get("display_order") or 9999,
                "image_url":    (t.get("image_url") or "").strip(),
            })
        if not tracks:
            continue
        tracks.sort(key=lambda x: x["display_order"])
        name = (
            sec.get("display_section")
            or sec.get("section", "").replace("_", " ").title()
        )
        sections.append({"name": name, "tracks": tracks})

    return sections


def load_history_for_album(
    sections: list[dict], target_date: str
) -> dict[str, dict]:
    """
    Returns {track_id: {streams, daily, change, pct}} for target_date.
    change = daily_today - daily_yesterday
    pct    = change / daily_yesterday * 100  (None if yest == 0)
    """
    yesterday = str(date_cls.fromisoformat(target_date) - timedelta(days=1))
    all_ids = {t["track_id"] for sec in sections for t in sec["tracks"]}

    today_data: dict[str, dict] = {}
    yest_data:  dict[str, dict] = {}

    with open(HISTORY_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = row["date"]
            if d not in (target_date, yesterday):
                continue
            tid = row["track_id"]
            if tid not in all_ids:
                continue
            entry = {
                "streams":       int(row["streams"] or 0),
                "daily_streams": int(row["daily_streams"] or 0),
            }
            if d == target_date:
                today_data[tid] = entry
            else:
                yest_data[tid] = entry

    result = {}
    for tid in all_ids:
        t = today_data.get(tid)
        if t is None:
            result[tid] = {"streams": None, "daily": None, "change": None, "pct": None}
            continue
        y = yest_data.get(tid, {})
        daily    = t["daily_streams"]
        streams  = t["streams"]
        yest_d   = y.get("daily_streams", 0)
        change   = daily - yest_d
        pct      = (change / yest_d * 100) if yest_d != 0 else None
        result[tid] = {
            "streams": streams,
            "daily":   daily,
            "change":  change,
            "pct":     pct,
        }
    return result


def load_cover_url(album_name: str) -> str:
    if not COVERS_PATH.exists():
        return ""
    try:
        covers = json.loads(COVERS_PATH.read_text(encoding="utf-8"))
        for v in covers.values():
            if v.get("title") == album_name:
                return v.get("cover_url", "")
    except Exception:
        pass
    return ""


def pick_header_image(album_name: str) -> Path | None:
    base = album_name.lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = HEADERS_DIR / (base + ext)
        if p.exists():
            return p
    return None


def get_latest_date() -> str:
    latest = ""
    with open(HISTORY_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"] > latest:
                latest = row["date"]
    if not latest:
        raise ValueError("streams_history.csv est vide")
    return latest


# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  background:#ffffff;
  width:880px;
  padding:20px;
  color:#101828;
}
.container{
  border-radius:20px;
  overflow:hidden;
  box-shadow:0 10px 30px rgba(16,24,40,.08),0 2px 8px rgba(16,24,40,.05);
  background:#ffffff;
}
/* ── header ── */
.hdr{
  height:110px;
  display:flex;align-items:center;gap:0;
  position:relative;overflow:hidden;
  background:linear-gradient(135deg, rgba(29,185,84,.15) 0%, rgba(21,136,62,.08) 100%);
  border-bottom:2px solid rgba(29,185,84,.15);
}
.hdr-cover{
  width:84px;height:84px;border-radius:12px;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 4px 14px rgba(0,0,0,.15);
  margin-left:24px;
}
.hdr-cover-ph{
  width:84px;height:84px;border-radius:12px;
  background:linear-gradient(135deg,#e8f5ee 0%,#d4f1e0 100%);
  flex-shrink:0;margin-left:24px;
}
.hdr-text{
  margin-left:24px;display:flex;flex-direction:column;gap:4px;
}
.hdr-title{color:#101828;font-size:24px;font-weight:800;letter-spacing:-.4px;line-height:1.2}
.hdr-sub{color:#667085;font-size:14px;font-weight:600;line-height:1.3}
.hdr-handle{font-size:12px;font-weight:700;line-height:1.3}
/* ── column headers ── */
.col-heads{
  display:grid;
  grid-template-columns:40px minmax(150px,1fr) 120px 110px 110px;
  column-gap:10px;
  padding:10px 18px;
  background:#f5f8f5;
  border-bottom:1px solid rgba(29,185,84,.08);
}
.col-heads span{
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.08em;color:#9aa5b4;
  display:flex;align-items:center;
}
.col-heads .center{justify-content:center}
.col-heads .right{justify-content:flex-end}
/* ── song rows ── */
.song-row{
  display:grid;
  grid-template-columns:40px minmax(150px,1fr) 120px 110px 110px;
  column-gap:10px;
  align-items:center;
  padding:8px 18px;
  height:40px;
  border-bottom:1px solid rgba(16,24,40,.04);
  background:#ffffff;
}
.song-row.alt{background:var(--alt-row)}
.col-rank{
  font-size:12px;color:#b0bac8;font-weight:600;
  text-align:center;
}
.col-song{display:flex;flex-direction:column;justify-content:center;min-width:0}
.song-title{
  font-size:13px;font-weight:600;color:#101828;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.song-title.has-tag{font-size:12.5px}
.song-ver{
  font-size:11px;color:#9aa5b4;font-weight:400;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.col-num{
  font-size:12px;color:#344054;font-weight:700;
  display:flex;align-items:center;justify-content:flex-end;
}
.col-num.daily-val{color:#101828;font-size:13px;font-weight:700}
.delta-wrap{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.delta-num{font-size:12px;font-weight:700}
.delta-pct{font-size:10px;font-weight:500;opacity:.80}
.pos .delta-num,.pos .delta-pct{color:#067647}
.neg .delta-num,.neg .delta-pct{color:#b42318}
.neutral .delta-num{color:#667085}
/* ── section total ── */
.sec-total{
  display:grid;
  grid-template-columns:40px minmax(150px,1fr) 120px 110px 110px;
  column-gap:10px;
  align-items:center;
  padding:10px 18px;
  height:44px;
  border-left:5px solid var(--sec-accent);
  background:var(--sec-bg);
  font-weight:700;
}
.sec-label{
  grid-column:1/3;
  font-size:12px;color:#101828;
  padding-left:2px;
}
.sec-num{
  font-size:13px;
  display:flex;align-items:center;justify-content:flex-end;color:#101828;
  font-weight:700;
}
.sec-chg{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.sec-chg-num{font-size:12px;font-weight:700}
.sec-chg-pct{font-size:10px;font-weight:600;opacity:.80}
/* ── grand total ── */
.era-total{
  display:grid;
  grid-template-columns:40px minmax(150px,1fr) 120px 110px 110px;
  column-gap:10px;
  align-items:center;
  padding:12px 18px;
  height:48px;
  background:linear-gradient(135deg, #0d1117 0%, #1a1f26 100%);
  border-top:2px solid rgba(29,185,84,.1);
}
.era-label{
  grid-column:1/3;
  font-size:14px;font-weight:800;color:rgba(255,255,255,.95);
  padding-left:2px;
}
.era-num{
  font-size:14px;font-weight:800;color:rgba(255,255,255,.95);
  display:flex;align-items:center;justify-content:flex-end;
}
.era-chg{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.era-chg-num{font-size:13px;font-weight:800}
.era-chg-pct{font-size:11px;font-weight:600;opacity:.85}
/* ── footer ── */
.ftr{
  background:#f5f8f5;
  padding:12px 18px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid rgba(29,185,84,.08);
}
.ftr-handle{font-size:12px;font-weight:700}
.ftr-date{font-size:12px;color:#667085;font-weight:500}
"""


# ── HTML builders ──────────────────────────────────────────────────────────────

def _css_hsl(h_deg: float, s_pct: float, l_pct: float) -> str:
    return f"hsl({h_deg:.1f},{s_pct:.1f}%,{l_pct:.1f}%)"


def _edition_css(dominant_hex: str, bi: int) -> tuple[str, str]:
    """Returns (accent_css, bg_css) for section total row."""
    m = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", dominant_hex.lower())
    if not m:
        h, s, bg_l = 142.0, 60.0, 96.5
    else:
        r, g, b = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)
        h_f, s_f, l_f = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        h = h_f * 360
        s = max(40.0, min(s_f * 100, 75.0))
        bg_l = max(92.0, 96.8 - bi * 1.2)
    accent = _css_hsl(h, s, 42.0)
    bg     = _css_hsl(h, min(s, 45.0), bg_l)
    return accent, bg


def build_song_row_html(si: int, track: dict, hdata: dict, alt: bool) -> str:
    title  = _shorten_title(track["title_clean"])
    daily  = hdata.get("daily")
    change = hdata.get("change")
    pct    = hdata.get("pct")
    streams = hdata.get("streams")

    daily_s  = ("+" + fmt_num(daily)) if daily is not None else "—"
    chg_s, pct_s, chg_cls = fmt_chg(change, pct)

    alt_cls = " alt" if alt else ""

    return f"""<div class="song-row{alt_cls}">
  <div class="col-rank">{si + 1}</div>
  <div class="col-song">
    <div class="song-title">{title}</div>
  </div>
  <div class="col-num daily-val">{daily_s}</div>
  <div class="col-num {chg_cls}">
    <div class="delta-wrap">
      <span class="delta-num">{chg_s}</span>
      {f'<span class="delta-pct">{pct_s}</span>' if pct_s else ''}
    </div>
  </div>
  <div class="col-num">{fmt_num(streams)}</div>
</div>
"""


def build_section_total_html(sec_name: str, tracks: list[dict],
                              hist: dict, accent: str, bg: str) -> str:
    sec_daily  = sum(hist.get(t["track_id"], {}).get("daily") or 0 for t in tracks)
    sec_str    = sum(hist.get(t["track_id"], {}).get("streams") or 0 for t in tracks)
    sec_change = sum(hist.get(t["track_id"], {}).get("change") or 0 for t in tracks)
    sec_yest   = sec_daily - sec_change
    sec_pct    = (sec_change / sec_yest * 100) if sec_yest != 0 else None

    chg_s, pct_s, chg_cls = fmt_chg(sec_change, sec_pct)
    chg_color = "#067647" if sec_change >= 0 else "#b42318"

    return f"""<div class="sec-total" style="--sec-accent:{accent};--sec-bg:{bg}">
  <div class="sec-label">{sec_name}&nbsp;&nbsp;—&nbsp;&nbsp;Total</div>
  <div class="sec-num">+{fmt_num(sec_daily)}</div>
  <div class="sec-num">
    <div class="sec-chg">
      <span class="sec-chg-num" style="color:{chg_color}">{chg_s}</span>
      {f'<span class="sec-chg-pct" style="color:{chg_color}">{pct_s}</span>' if pct_s else ''}
    </div>
  </div>
  <div class="sec-num">{fmt_num(sec_str)}</div>
</div>
"""


def build_html(
    album_name: str,
    sections: list[dict],
    hist: dict,
    target_date: str,
    cover_uri: str,
    header_img: Path | None,
    dominant_hex: str,
) -> str:
    from datetime import datetime
    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")

    # header background - light with accent color
    m = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", dominant_hex.lower())
    if m:
        r, g, b = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)
        h, s, l = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        # Create light background with accent
        accent_light = _css_hsl(h * 360, s * 100, 92.0)
        accent_mid = _css_hsl(h * 360, s * 100, 88.0)
    else:
        accent_light = "#e8f5ee"
        accent_mid = "#d4f1e0"
    
    hdr_bg = f"background:linear-gradient(135deg, {accent_light} 0%, {accent_mid} 100%);"

    # album cover img or placeholder
    if cover_uri:
        cover_html = f'<img class="hdr-cover" src="{cover_uri}" />'
    else:
        cover_html = '<div class="hdr-cover-ph"></div>'

    # alternate row color based on dominant
    m = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", dominant_hex.lower())
    if m:
        dr, dg, db = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)
    else:
        dr, dg, db = 29, 185, 84
    alt_row_css = f"rgba({dr},{dg},{db},0.05)"

    # build song rows + section totals
    rows_html = ""
    total_daily   = 0
    total_streams = 0
    total_change  = 0

    for bi, sec in enumerate(sections):
        accent, bg = _edition_css(dominant_hex, bi)
        for si, track in enumerate(sec["tracks"]):
            hd = hist.get(track["track_id"], {"daily": None, "change": None, "pct": None, "streams": None})
            rows_html += build_song_row_html(si, track, hd, si % 2 != 0)
        rows_html += build_section_total_html(sec["name"], sec["tracks"], hist, accent, bg)

        for t in sec["tracks"]:
            hd = hist.get(t["track_id"], {})
            total_daily   += hd.get("daily") or 0
            total_streams += hd.get("streams") or 0
            total_change  += hd.get("change") or 0

    # grand total
    total_yest = total_daily - total_change
    total_pct  = (total_change / total_yest * 100) if total_yest != 0 else None
    tot_chg_s, tot_pct_s, _ = fmt_chg(total_change, total_pct)
    accent_color = dominant_hex

    era_html = f"""<div class="era-total">
  <div class="era-label">Total</div>
  <div class="era-num">+{fmt_num(total_daily)}</div>
  <div class="era-num">
    <div class="era-chg">
      <span class="era-chg-num" style="color:{accent_color}">{tot_chg_s}</span>
      {f'<span class="era-chg-pct" style="color:{accent_color}">{tot_pct_s}</span>' if tot_pct_s else ''}
    </div>
  </div>
  <div class="era-num">{fmt_num(total_streams)}</div>
</div>
"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
{CSS}
:root {{ --alt-row: {alt_row_css}; }}
</style>
</head><body>
<div class="container">
  <div class="hdr" style="{hdr_bg}">
    {cover_html}
    <div class="hdr-text">
      <div class="hdr-title">{album_name}</div>
      <div class="hdr-sub">Taylor Swift &middot; {date_fmt}</div>
      <div class="hdr-handle" style="color:{dominant_hex}">{HANDLE}</div>
    </div>
  </div>
  <div class="col-heads">
    <span class="center">#</span>
    <span>SONG</span>
    <span class="right">DAILY</span>
    <span class="right">CHG</span>
    <span class="right">TOTAL</span>
  </div>
  {rows_html}
  {era_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{dominant_hex}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div>
</body></html>"""


# ── Main generate function ─────────────────────────────────────────────────────

def generate(album_name: str, target_date: str | None = None) -> Path:
    if target_date is None:
        target_date = get_latest_date()
    print(f"[album_update] Album: {album_name}  Date: {target_date}")

    sections = load_album_sections(album_name)
    if not sections:
        raise ValueError(f"Aucune section trouvée pour l'album: {album_name!r}")
    print(f"[album_update] {sum(len(s['tracks']) for s in sections)} tracks dans {len(sections)} section(s)")

    hist = load_history_for_album(sections, target_date)

    cover_url    = load_cover_url(album_name)
    header_img   = pick_header_image(album_name)
    dominant_hex = _dominant_color(header_img) if header_img else "#1db954"

    # prefetch cover image
    print("[album_update] Téléchargement de la cover...")
    cover_uri = _url_to_data_uri(cover_url) if cover_url else ""

    html = build_html(album_name, sections, hist, target_date, cover_uri, header_img, dominant_hex)

    album_slug = re.sub(r"[^a-z0-9]+", "_", album_name.lower()).strip("_")
    out_dir    = ROOT / "history" / target_date[:4] / target_date[5:7] / target_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path   = out_dir / f"{album_slug}_update.png"
    tmp_html   = out_dir / f"_{album_slug}_tmp.html"
    tmp_html.write_text(html, encoding="utf-8")

    print("[album_update] Rendu Playwright...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 800, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()

    print(f"[album_update] Image générée : {out_path}")
    return out_path


def post(album_name: str, image_path: Path, target_date: str) -> bool:
    from datetime import datetime
    if not TWITTER_SESSION.exists():
        print(f"[album_update] Session Twitter introuvable : {TWITTER_SESSION}")
        return False

    try:
        from core.twitter import post_with_image
    except ImportError as e:
        print(f"[album_update] Impossible d'importer core.twitter: {e}")
        return False

    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    tweet    = f"Taylor Swift · {album_name}\nDaily Streams Update — {date_fmt}"

    print(f"[album_update] Publication Twitter : {tweet[:60]}...")
    ok = post_with_image(tweet, image_path, TWITTER_SESSION)
    if ok:
        print("[album_update] Tweet publié avec succès.")
    else:
        print("[album_update] Échec de la publication Twitter.")
    return ok


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    do_post    = "--post" in args
    clean_args = [a for a in args if a != "--post"]

    album_name  = clean_args[0] if len(clean_args) > 0 else None
    target_date = clean_args[1] if len(clean_args) > 1 else None

    if not album_name:
        print("Usage: generate_album_update_image.py <album_name> [date] [--post]")
        sys.exit(1)

    image_path = generate(album_name, target_date)
    resolved_date = target_date or get_latest_date()

    if do_post:
        album_slug = re.sub(r"[^a-z0-9]+", "_", album_name.lower()).strip("_")
        lock_path  = image_path.parent / f"{album_slug}_update.lock"

        if lock_path.exists():
            print(f"[album_update] Déjà posté ({lock_path.name}). Rien à faire.")
            return

        ok = post(album_name, image_path, resolved_date)
        if ok:
            lock_path.write_text(f"posted {resolved_date}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
