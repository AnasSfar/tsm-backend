from __future__ import annotations

import base64
import colorsys
import html as _html
import random
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from .discography import get_album_cover
    from .export_frame import add_export_frame
    from .fmt import fmt_pct, fmt_streams, get_pct, nan_to_none, pct_cls
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from comp.discography import get_album_cover
    from comp.export_frame import add_export_frame
    from comp.fmt import fmt_pct, fmt_streams, get_pct, nan_to_none, pct_cls

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

# Image fetching (persistent disk cache + retries + Spotify size fallback) lives
# in comp.img_fetch — shared by every PNG generator so a transient network blip
# can never blank a cover that has worked before. See that module's docstring.
try:
    from .img_fetch import fetch_data_uri, image_url_candidates, re_sub_spotify_size  # noqa: F401
except ImportError:
    from comp.img_fetch import fetch_data_uri, image_url_candidates, re_sub_spotify_size  # noqa: F401

_image_url_candidates = image_url_candidates  # back-compat alias


def download_as_data_uri(url: str) -> str:
    """Download an image URL as a base64 data URI, or "" on failure.

    Backed by comp.img_fetch (persistent cache + retries). Kept as a name for
    callers that prefetch covers concurrently (generate_streams_image, etc.).
    """
    return fetch_data_uri(url) if url and str(url).startswith("http") else ""


def url_to_data_uri(url: str) -> str:
    """Fetch an image URL as a base64 data URI. Non-http input is returned as-is."""
    return fetch_data_uri(url)


def pick_header_image(headers_dir: Path) -> Path | None:
    """Returns a random image from the given headers folder."""
    if not headers_dir.exists():
        return None
    imgs = [p for p in headers_dir.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    return random.choice(imgs) if imgs else None


def _dominant_color_from_image(img) -> str:
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


def get_dominant_color(img_path: Path) -> str:
    """Returns a vibrant hex colour extracted from the image."""
    if not _PIL:
        return "#1db954"
    try:
        img = Image.open(img_path).convert("RGB").resize((60, 60), Image.LANCZOS)
        return _dominant_color_from_image(img)
    except Exception:
        return "#1db954"


def dominant_color_from_data_uri(data_uri: str | None) -> str | None:
    """Same extraction as get_dominant_color, but from an already-fetched
    base64 data URI (e.g. a cover already in an image_cache) — no network
    call. Returns None (not a fallback color) if extraction isn't possible,
    so callers can fall back to their own default."""
    if not _PIL or not data_uri or not data_uri.startswith("data:"):
        return None
    try:
        _, b64 = data_uri.split(",", 1)
        raw = base64.b64decode(b64)
        img = Image.open(BytesIO(raw)).convert("RGB").resize((60, 60), Image.LANCZOS)
        return _dominant_color_from_image(img)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Chart logic
# ---------------------------------------------------------------------------

def rank_change(rank, previous_rank, total_days=None, peak_rank=None):
    if previous_rank is None:
        if peak_rank is not None and int(peak_rank) != int(rank):
            return "RE", "chg-re"
        if total_days and int(total_days) > 1:
            return "RE", "chg-re"
        return "NEW", "chg-new"
    delta = int(previous_rank) - int(rank)
    if delta > 0:
        return f"&#9650; {delta}", "chg-up"
    elif delta < 0:
        return f"&#9660; {abs(delta)}", "chg-dn"
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
  display:flex;align-items:center;justify-content:center;
  justify-self:center;
  min-width:36px;padding:3px 7px;border-radius:20px;
  font-size:12px;font-weight:800;line-height:1;letter-spacing:.01em;
}
.col-chg.chg-up{background:#dcfce7;color:#15803d}
.col-chg.chg-dn{background:#fee2e2;color:#b91c1c}
.col-chg.chg-eq{background:#f1f5f9;color:#64748b}
.col-chg.chg-new{background:#dbeafe;color:#1d4ed8;font-size:10px}
.col-chg.chg-re{background:#ede9fe;color:#6d28d9;font-size:10px}
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
    <span>+/-</span>
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
    track_cover_cache: dict | None = None,
) -> str:
    if not out_songs:
        return ""
    yesterday = str((datetime.strptime(chart_date, "%Y-%m-%d").date()) - timedelta(days=1))
    html = ""
    for row in out_songs:
        track       = str(row.get("track_name") or "")
        artist      = str(row.get("artist_names") or "")
        rank        = row.get("rank")
        track_id    = str(row.get("track_id") or "").strip()
        scraped_img = row.get("image_url") or ""
        cover_url   = url_to_data_uri(get_album_cover(
            track, track_album_map, cover_map, track_image_map, scraped_img,
            track_id=track_id, track_cover_cache=track_cover_cache,
        ))
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
    track_cover_cache: dict | None = None,
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
        track_id    = str(row.get("track_id") or "").strip()

        if rank is None:
            continue
        rank = int(rank)

        chg_text, chg_css = rank_change(
            rank,
            int(prev_rank) if prev_rank else None,
            total_days,
            int(peak_rank) if peak_rank else None,
        )
        cover_url = url_to_data_uri(get_album_cover(
            track, track_album_map, cover_map, track_image_map, scraped_img,
            track_id=track_id, track_cover_cache=track_cover_cache,
        ))

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
  display:flex;align-items:center;justify-content:center;
  justify-self:center;
  min-width:38px;padding:4px 8px;border-radius:20px;
  font-size:12px;font-weight:800;line-height:1;letter-spacing:.01em;
}
.col-chg.chg-up{background:#dcfce7;color:#15803d}
.col-chg.chg-dn{background:#fee2e2;color:#b91c1c}
.col-chg.chg-eq{background:#f1f5f9;color:#64748b}
.col-chg.chg-new{background:#dbeafe;color:#1d4ed8;font-size:11px}
.col-chg.chg-re{background:#ede9fe;color:#6d28d9;font-size:11px}
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
/* Editorial masthead header (opt-in via build_table_html(masthead_word=...)) */
.hdr.masthead{
  height:168px;
  padding:24px 22px;
  display:flex;align-items:center;justify-content:space-between;
  overflow:hidden;
  position:relative;
  border-bottom:1px solid rgba(255,255,255,.08);
}
.hdr.masthead .hdr-title{
  color:var(--mast-title-color,#fff);
  font-size:34px;
  font-weight:900;
  line-height:1.05;
  letter-spacing:0;
}
.hdr.masthead .hdr-sub{
  color:var(--mast-sub-color,rgba(255,255,255,.85));
  font-size:18px;
  font-weight:650;
  line-height:1.25;
  margin-top:7px;
}
.mast-left{display:flex;align-items:center;gap:16px;position:relative;z-index:1;max-width:720px}
.mast-logo-badge{
  width:56px;height:56px;border-radius:50%;background:#fff;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 2px 8px rgba(0,0,0,.35);
}
.mast-logo-badge .hdr-logo{width:28px;height:28px}
.mast-logo-badge .hdr-logo path{fill:#161616}
.mast-word{
  position:absolute;
  right:-6px;top:50%;
  transform:translateY(-50%);
  font-family:"Big Shoulders Display",sans-serif;
  font-weight:900;
  font-size:168px;
  letter-spacing:.01em;
  line-height:1;
  white-space:nowrap;
  color:var(--mast-word-color,rgba(255,255,255,.5));
  mix-blend-mode:overlay;
  pointer-events:none;
}
.mast-rule{position:absolute;left:0;right:0;bottom:0;height:1px}

/* Ledger table (opt-in, paired with the masthead header) */
.ledger-cols{
  display:grid;grid-template-columns:var(--grid-cols);column-gap:var(--col-gap,10px);
  padding:10px 20px;background:var(--ledger-cols-bg);border-bottom:1px solid var(--ledger-row-border);
}
.ledger-cols span{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--ledger-col-label);display:flex;align-items:center}
.ledger-cols .right{justify-content:flex-end}
.ledger-row{
  display:grid;grid-template-columns:var(--grid-cols);column-gap:var(--col-gap,10px);
  align-items:center;padding:10px 20px;background:var(--ledger-bg);
  border-bottom:1px solid var(--ledger-row-border);
}
.ledger-rank{
  font-family:"Big Shoulders Display",sans-serif;font-weight:900;font-size:26px;
  letter-spacing:-.02em;color:var(--ledger-text);
  display:flex;align-items:center;justify-content:center;text-align:center;
}
.ledger-chg{font-size:11px;font-weight:700;color:var(--ledger-faint);display:flex;align-items:center;justify-content:center;gap:2px}
.ledger-chg.chg-up{color:var(--ledger-pos)}
.ledger-chg.chg-dn{color:var(--ledger-neg)}
.ledger-chg.chg-new{color:#5b8fd6}
.ledger-chg.chg-re{color:#a06bd6}
.ledger-entity{display:flex;align-items:center;gap:12px;min-width:0}
.ledger-art,.ledger-art-ph{width:var(--art-size,44px);height:var(--art-size,44px);border-radius:7px;flex-shrink:0;object-fit:cover;box-shadow:0 2px 8px rgba(0,0,0,.3)}
.ledger-art-ph{background:var(--ledger-row-border)}
.ledger-info{min-width:0}
.ledger-name{
  font-size:14px;font-weight:700;color:var(--ledger-text);line-height:1.25;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;white-space:normal;
}
.ledger-sub{font-size:11px;color:var(--ledger-muted);margin-top:2px}
.ledger-name .best-day-star{color:#e8b923}
.ledger-name .best-day-note{font-size:10px;font-weight:700;color:var(--ledger-muted)}
.ledger-num{text-align:right;font-variant-numeric:tabular-nums}
.ledger-daily{font-size:13.5px;font-weight:800;color:var(--ledger-text)}
.ledger-total{font-size:12.5px;color:var(--ledger-muted);font-weight:600}
.ledger-delta{display:flex;flex-direction:column;align-items:flex-end;gap:1px}
.ledger-delta-num{font-size:11.5px;font-weight:700}
.ledger-delta-pct{font-size:10px;font-weight:500;opacity:.85}
.ledger-delta.pos .ledger-delta-num,.ledger-delta.pos .ledger-delta-pct{color:var(--ledger-pos)}
.ledger-delta.neg .ledger-delta-num,.ledger-delta.neg .ledger-delta-pct{color:var(--ledger-neg)}
.ledger-delta.neutral .ledger-delta-num,.ledger-delta.neutral .ledger-delta-pct{color:var(--ledger-faint)}
.ledger-ftr{display:flex;justify-content:space-between;align-items:center;padding:13px 20px;background:var(--ledger-ftr-bg)}
.ledger-handle{font-size:13px;font-weight:700}
.ledger-date{font-size:13px;color:var(--ledger-faint)}
"""

ERA_ACCENT_COLORS: dict[str, str] = {
    "taylor swift": "#5b8db8",
    "fearless": "#c4a255",
    "speak now": "#7c3aed",
    "red": "#b91c1c",
    "1989": "#4fb8e8",
    "reputation": "#555555",
    "lover": "#d978a0",
    "folklore": "#8b9eb7",
    "evermore": "#a07850",
    "midnights": "#1a1a3e",
    "the tortured poets department": "#d4c3a3",
    "the life of a showgirl": "#e2712c",
    # Taylor's Version re-records share their original era's color.
    "fearless (taylor's version)": "#c4a255",
    "speak now (taylor's version)": "#7c3aed",
    "red (taylor's version)": "#b91c1c",
    "1989 (taylor's version)": "#4fb8e8",
}


def era_accent_color(album_name: str | None) -> str | None:
    """Canonical per-era brand color (mirrors tsm-frontend's anniversaries.js
    theme accents), used to tint the ledger rank numeral by the row's album."""
    if not album_name:
        return None
    return ERA_ACCENT_COLORS.get(album_name.strip().lower())


_LEDGER_THEME_TOKENS = {
    "dark": {
        "ledger-bg": "#131417",
        "ledger-cols-bg": "#0e0f11",
        "ledger-ftr-bg": "#0e0f11",
        "ledger-row-border": "#232428",
        "ledger-text": "#eef0f2",
        "ledger-muted": "#9aa0ab",
        "ledger-faint": "#5a5e66",
        "ledger-col-label": "#ffffff",
        "ledger-pos": "#6fcf9a",
        "ledger-neg": "#e08a7d",
        "mast-title-color": "#fff",
        "mast-sub-color": "rgba(255,255,255,.85)",
        "mast-word-color": "rgba(255,255,255,.5)",
        "mast-overlay-rgb": "6,8,7",
    },
    "light": {
        "ledger-bg": "#ffffff",
        "ledger-cols-bg": "#f4f6f8",
        "ledger-ftr-bg": "#f4f6f8",
        "ledger-row-border": "#e6e9ee",
        "ledger-text": "#1a1d24",
        "ledger-muted": "#667085",
        "ledger-faint": "#98a2b3",
        "ledger-col-label": "#667085",
        "ledger-pos": "#067647",
        "ledger-neg": "#b42318",
        "mast-title-color": "#12141a",
        "mast-sub-color": "rgba(18,20,26,.72)",
        "mast-word-color": "rgba(18,20,26,.14)",
        "mast-overlay-rgb": "250,250,251",
    },
}


def masthead_theme_for_date(target_date) -> str:
    """Owner rule (2026-08-26): masthead / ledger cards render the **light**
    theme on weekday posts (Mon-Fri) and the **dark** theme on weekend posts
    (Sat/Sun). Per-era overrides (e.g. Best Day Since "Holiday Collection"
    stays light year-round) are applied by the caller *after* this.

    Accepts a ``date``/``datetime``, a ``YYYY-MM-DD`` string, or ``None``.
    ``None`` / unparseable -> ``"dark"`` (the safe default)."""
    if target_date is None:
        return "dark"
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    if isinstance(target_date, str):
        try:
            target_date = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        except ValueError:
            return "dark"
    try:
        weekday = target_date.weekday()
    except AttributeError:
        return "dark"
    return "dark" if weekday >= 5 else "light"


def ledger_name_with_best_day(name_html: str, marker_label: str | None) -> str:
    """Wrap a ledger entity name with the '* ... since MM/DD/YYYY' marker used on
    the album update image. ``marker_label`` is best_day_since.best_day_marker_text
    output ("November 26th, 2025" / "of the year" / "of the month"); "of ..."
    labels render without the "since" prefix. Empty label -> name unchanged."""
    if not marker_label:
        return name_html
    text = str(marker_label)
    prefix = "" if text.startswith("of ") else "since "
    return (
        f'<span class="best-day-star">&#9733;</span> {name_html}'
        f'<span class="best-day-note"> &middot; {prefix}{_html.escape(text)}</span>'
    )


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
    logo_svg: str = SPOTIFY_SVG,
    masthead_word: str | None = None,
    masthead_theme: str = "dark",
) -> str:
    """Build a complete glassmorphism table image HTML document.

    col_heads: list of (label, right_aligned) tuples.
    masthead_word: if set (e.g. "SONGS", "ERAS"), renders the header as an
    "editorial masthead" — a real header photo with a big ghost wordmark
    overlaid on the right — instead of the classic centered logo/title header,
    and switches the table body to the matching dark/light "ledger" style
    instead of the classic glassmorphism table.
    masthead_theme: "dark" (default) or "light" — only used when masthead_word
    is set.
    """
    theme = _LEDGER_THEME_TOKENS.get(masthead_theme, _LEDGER_THEME_TOKENS["dark"])
    header_img = None if header_background else pick_header_image(headers_dir)
    handle_color = handle_color_override or "#1db954"
    if header_background:
        hdr_style = f'style="background:{header_background};"'
    elif header_img:
        handle_color = get_dominant_color(header_img)
        img_url = header_img.as_posix()
        overlay = (
            f"linear-gradient(100deg,rgba({theme['mast-overlay-rgb']},.82) 0%,"
            f"rgba({theme['mast-overlay-rgb']},.48) 45%,rgba({theme['mast-overlay-rgb']},.6) 100%)"
            if masthead_word
            else "linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45))"
        )
        hdr_style = (
            f'style="background-image: {overlay},'
            f"url('file:///{img_url}'); background-size:cover; background-position:center;\""
        )
    else:
        hdr_style = 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"'

    css_vars = (
        f":root{{--body-w:{body_width}px;--grid-cols:{grid_cols};"
        f"--art-size:{art_size}px;--col-gap:{col_gap}px}}"
    )

    if masthead_word:
        font_link = (
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            '<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@900&display=swap" rel="stylesheet">'
        )
        hdr_html = f"""  <div class="hdr masthead" {hdr_style}>
    <div class="mast-left">
      <div class="mast-logo-badge">{logo_svg}</div>
      <div>
        <div class="hdr-title">{title}</div>
        <div class="hdr-sub">{subtitle}</div>
      </div>
    </div>
    <div class="mast-word">{masthead_word}</div>
    <div class="mast-rule" style="background:{handle_color}"></div>
  </div>"""

        col_heads_html = '<div class="ledger-cols">\n'
        for label, right in col_heads:
            cls = ' class="right"' if right else ""
            col_heads_html += f"  <span{cls}>{label}</span>\n"
        col_heads_html += "</div>"

        ledger_tokens = "".join(f"--{k}:{v};" for k, v in theme.items() if k != "mast-overlay-rgb")
        ledger_vars = f":root{{{ledger_tokens}}}"
        ledger_body_bg = f"body{{background:{theme['ledger-bg']};}}"

        footer_html = f"""  <div class="ledger-ftr">
    <span class="ledger-handle" style="color:{handle_color}">{handle}</span>
    <span class="ledger-date">{date_str}</span>
  </div>"""

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
{font_link}
<style>{css_vars}{ledger_vars}{STREAMS_TABLE_CSS}{ledger_body_bg}{extra_css}</style></head>
<body>
<div class="container">
{hdr_html}
  {col_heads_html}
  {rows_html}
{footer_html}
</div>
</body></html>"""

    col_heads_html = '<div class="col-heads">\n'
    for label, right in col_heads:
        cls = ' class="right"' if right else ""
        col_heads_html += f"  <span{cls}>{label}</span>\n"
    col_heads_html += "</div>"

    font_link = ""
    hdr_html = f"""  <div class="hdr" {hdr_style}>
    {logo_svg}
    <div>
      <div class="hdr-title">{title}</div>
      <div class="hdr-sub">{subtitle}</div>
    </div>
  </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
{font_link}
<style>{css_vars}{STREAMS_TABLE_CSS}{extra_css}</style></head>
<body>
<div class="container">
{hdr_html}
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
    export_frame: bool = False,
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
            if export_frame:
                add_export_frame(out_path, device_scale_factor=2)
            browser.close()
    finally:
        if tmp_path.exists() and not keep_html:
            tmp_path.unlink()
    return out_path
