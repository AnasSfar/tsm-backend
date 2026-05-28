#!/usr/bin/env python3
"""
generate_card_images.py — génère des PNG des cards "Overall" pour chaque chanson Taylor Swift.

Reproduit le composant SongBlock.jsx du frontend (light album themes).
Quand une song a beaucoup de pays, le tableau est affiché sur deux colonnes côte à côte.

Lit  : website/site/data/charts_worldwide.json  (by_track + date)
       website/site/data/songs.json              (métadonnées: title, image_url, artist)
Ecrit: collectors/spotify/charts/worldwide/history/YYYY/MM/YYYY-MM-DD/cards/{slug}.png
       collectors/spotify/charts/worldwide/history/YYYY/MM/YYYY-MM-DD/cards/cards_index.json

Usage:
    python generate_card_images.py
    python generate_card_images.py 2026-05-10
    python generate_card_images.py --theme midnights --min-countries 2
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

import os
import tempfile
import time

from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT           = Path(__file__).resolve().parents[6]
WORLDWIDE_JSON = ROOT / "website" / "site" / "data" / "charts_worldwide.json"
SONGS_JSON     = ROOT / "website" / "site" / "data" / "songs.json"
LOGO_PATH        = Path(__file__).parents[7] / "tsm-frontend" / "frontend" / "public" / "icons" / "logo.gif"
TWITTER_SESSION  = Path(__file__).resolve().parents[1] / "json" / "twitter_session.json"

_SPOTIFY_ROOT = ROOT / "collectors" / "spotify"
if str(_SPOTIFY_ROOT) not in sys.path:
    sys.path.insert(0, str(_SPOTIFY_ROOT))
_CORE = _SPOTIFY_ROOT / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))
from core.data_paths import legacy_spotify_chart_dir, spotify_chart_dir  # noqa: E402
from twitter import post_image_thread as _post_image_thread  # noqa: E402
from twitter import post_thread as _post_thread  # noqa: E402

# Shared lock with core/twitter.py — prevents running Playwright while Twitter
# posting scripts are also using a browser (same lock file, same semantics).
_TWITTER_POST_LOCK = Path(tempfile.gettempdir()) / "tsm_twitter_post.lock"
_LOCK_TIMEOUT = 15 * 60  # seconds


def _wait_for_twitter_lock() -> None:
    """Block until no Twitter posting script holds the browser lock."""
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


def _logo_data_uri() -> str:
    try:
        data = base64.b64encode(LOGO_PATH.read_bytes()).decode()
        return f"data:image/gif;base64,{data}"
    except Exception:
        return ""

# ── Theme palette definitions ─────────────────────────────────────────────────
# Each theme maps to CSS color values used in the card HTML.
THEMES: dict[str, dict[str, str]] = {
    # Default site theme (The Life of a Showgirl) — dark mode orange accent
    "showgirl": {
        "bg":         "#fff4ec",
        "card_bg":    "#fffaf6",
        "border":     "#ffd5bd",
        "text":       "#2d211c",
        "muted":      "#8a6658",
        "even_row":   "#fff0e6",
        "region":     "#c64f1d",
        "play_btn":   "#ff6b35",
        "rank_up":    "#2a9d5c",
        "rank_down":  "#cf3f24",
        "stream_pos": "#2a9d5c",
        "stream_neg": "#cf3f24",
    },
    # Plain dark (Spotify green)
    "dark": {
        "bg":         "#f1fbf5",
        "card_bg":    "#ffffff",
        "border":     "#c8ead7",
        "text":       "#102018",
        "muted":      "#64746b",
        "even_row":   "#eaf7ef",
        "region":     "#137c3f",
        "play_btn":   "#1db954",
        "rank_up":    "#2a9d5c",
        "rank_down":  "#cf3f24",
        "stream_pos": "#2a9d5c",
        "stream_neg": "#cf3f24",
    },
    "midnights": {
        "bg":         "#eef2ff",
        "card_bg":    "#f8faff",
        "border":     "#cfd8ff",
        "text":       "#1b2440",
        "muted":      "#687394",
        "even_row":   "#edf2ff",
        "region":     "#4657c7",
        "play_btn":   "#5b6ee1",
        "rank_up":    "#2a9d5c",
        "rank_down":  "#cf3f24",
        "stream_pos": "#2a9d5c",
        "stream_neg": "#cf3f24",
    },
    "ttpd": {
        "bg":         "#f4f1ec",
        "card_bg":    "#fffdf8",
        "border":     "#d8d0c4",
        "text":       "#2c2822",
        "muted":      "#746d63",
        "even_row":   "#f5efe6",
        "region":     "#6f665a",
        "play_btn":   "#9b9387",
        "rank_up":    "#2a9d5c",
        "rank_down":  "#cf3f24",
        "stream_pos": "#2a9d5c",
        "stream_neg": "#cf3f24",
    },
    "lover": {
        "bg":         "#fff0f7",
        "card_bg":    "#fffafd",
        "border":     "#ffcfe4",
        "text":       "#331927",
        "muted":      "#8a6174",
        "even_row":   "#ffe8f2",
        "region":     "#d83c85",
        "play_btn":   "#e8709a",
        "rank_up":    "#4caf7d",
        "rank_down":  "#cf3f24",
        "stream_pos": "#4caf7d",
        "stream_neg": "#cf3f24",
    },
    "fearless": {
        "bg":         "#fff8dc",
        "card_bg":    "#fffdf2",
        "border":     "#f3d982",
        "text":       "#332711",
        "muted":      "#8b7334",
        "even_row":   "#fff3c4",
        "region":     "#a87909",
        "play_btn":   "#d4a017",
        "rank_up":    "#4caf7d",
        "rank_down":  "#cf3f24",
        "stream_pos": "#4caf7d",
        "stream_neg": "#cf3f24",
    },
    "reputation": {
        "bg":         "#f2f2f2",
        "card_bg":    "#ffffff",
        "border":     "#cfcfcf",
        "text":       "#171717",
        "muted":      "#6b6b6b",
        "even_row":   "#ededed",
        "region":     "#343434",
        "play_btn":   "#111111",
        "rank_up":    "#4caf7d",
        "rank_down":  "#cf3f24",
        "stream_pos": "#4caf7d",
        "stream_neg": "#cf3f24",
    },
    "evermore": {
        "bg":         "#f9efe5",
        "card_bg":    "#fffaf4",
        "border":     "#e7c7aa",
        "text":       "#332418",
        "muted":      "#856650",
        "even_row":   "#f7e5d4",
        "region":     "#a45c2a",
        "play_btn":   "#9b6b3d",
        "rank_up":    "#80a040",
        "rank_down":  "#cf3f24",
        "stream_pos": "#80a040",
        "stream_neg": "#cf3f24",
    },
    "folklore": {
        "bg":         "#f2f4f3",
        "card_bg":    "#ffffff",
        "border":     "#d4d9d7",
        "text":       "#202524",
        "muted":      "#697370",
        "even_row":   "#e9eeec",
        "region":     "#5e6a66",
        "play_btn":   "#8f989d",
        "rank_up":    "#6fb07f",
        "rank_down":  "#cf3f24",
        "stream_pos": "#6fb07f",
        "stream_neg": "#cf3f24",
    },
    "1989": {
        "bg":         "#eaf8ff",
        "card_bg":    "#f8fdff",
        "border":     "#bfe8fb",
        "text":       "#152a34",
        "muted":      "#5c7e8d",
        "even_row":   "#dcf3ff",
        "region":     "#1678a7",
        "play_btn":   "#2aa8dc",
        "rank_up":    "#4caf7d",
        "rank_down":  "#cf3f24",
        "stream_pos": "#4caf7d",
        "stream_neg": "#cf3f24",
    },
    "red": {
        "bg":         "#fff1ef",
        "card_bg":    "#fffafa",
        "border":     "#ffc9c2",
        "text":       "#321817",
        "muted":      "#8b5f5a",
        "even_row":   "#ffe5e1",
        "region":     "#b92722",
        "play_btn":   "#b91f2f",
        "rank_up":    "#4caf7d",
        "rank_down":  "#cf3f24",
        "stream_pos": "#4caf7d",
        "stream_neg": "#cf3f24",
    },
    "speak_now": {
        "bg":         "#f8efff",
        "card_bg":    "#fffaff",
        "border":     "#e3c5f5",
        "text":       "#2c1937",
        "muted":      "#7b5b8b",
        "even_row":   "#f3e3fc",
        "region":     "#7a36a2",
        "play_btn":   "#8b4bb3",
        "rank_up":    "#4caf7d",
        "rank_down":  "#cf3f24",
        "stream_pos": "#4caf7d",
        "stream_neg": "#cf3f24",
    },
    "taylor_swift": {
        "bg":         "#edf9f1",
        "card_bg":    "#fbfffc",
        "border":     "#bde7ca",
        "text":       "#16291d",
        "muted":      "#5d8068",
        "even_row":   "#ddf3e5",
        "region":     "#237c47",
        "play_btn":   "#2e8f5f",
        "rank_up":    "#4caf7d",
        "rank_down":  "#cf3f24",
        "stream_pos": "#4caf7d",
        "stream_neg": "#cf3f24",
    },
}


def _album_name(song: dict) -> str:
    return str(song.get("primary_album") or song.get("album") or "").strip()


def _theme_key_for_album(album: str) -> str | None:
    al = album.lower().strip()
    if not al:
        return None
    if "life of a showgirl" in al:
        return "showgirl"
    if "tortured poets" in al:
        return "ttpd"
    if "midnights" in al:
        return "midnights"
    if "evermore" in al:
        return "evermore"
    if "folklore" in al:
        return "folklore"
    if "lover" in al:
        return "lover"
    if "reputation" in al:
        return "reputation"
    if "1989" in al:
        return "1989"
    if "red" in al:
        return "red"
    if "speak now" in al:
        return "speak_now"
    if "fearless" in al:
        return "fearless"
    if "taylor swift" in al or "debut" in al:
        return "taylor_swift"
    return None


def _palette_for_song(song: dict, fallback: dict[str, str]) -> tuple[dict[str, str], str]:
    theme_key = _theme_key_for_album(_album_name(song))
    if theme_key and theme_key in THEMES:
        return THEMES[theme_key], theme_key
    return fallback, "fallback"


def _dominant_album_theme(
    tracks: list[tuple[str, list[dict]]],
    song_meta: dict[str, dict],
    fallback: dict[str, str],
) -> tuple[dict[str, str], str]:
    scores: dict[str, tuple[int, int]] = {}
    for track_id, entries in tracks:
        theme_key = _theme_key_for_album(_album_name(song_meta.get(track_id, {})))
        if not theme_key or theme_key not in THEMES:
            continue
        song_count, appearance_count = scores.get(theme_key, (0, 0))
        scores[theme_key] = (song_count + 1, appearance_count + len(entries))

    if not scores:
        return fallback, "fallback"

    theme_key = max(sorted(scores), key=lambda key: scores[key])
    return THEMES[theme_key], theme_key

# ── Helpers ───────────────────────────────────────────────────────────────────

_img_cache: dict[str, str] = {}


def _url_to_data_uri(url: str) -> str:
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


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig").lstrip("﻿"))


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    return text[:80] or "track"


def _fmt_streams(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}".replace(",", " ")


def _rank_delta_html(entry: dict) -> str:
    if entry.get("out"):
        prev = entry.get("previous_rank")
        suffix = f" (#{prev} yesterday)" if prev else ""
        return f'<span class="oct-rank-delta rank-tag">{html.escape(suffix)}</span>'

    rank_change = entry.get("rank_change")
    prev  = entry.get("previous_rank")
    rank  = entry.get("rank")
    peak  = entry.get("peak_rank")

    delta: int | None
    if rank_change is not None and rank_change != 0:
        delta = rank_change
    elif prev and rank:
        delta = prev - rank
    else:
        delta = None

    if delta is None:
        tag = "NEW" if (peak is None or peak == rank) else "RE"
        return f'<span class="oct-rank-delta rank-tag"> ({html.escape(tag)})</span>'
    if delta > 0:
        return f'<span class="oct-rank-delta rank-up"> (▲{delta})</span>'
    if delta < 0:
        return f'<span class="oct-rank-delta rank-down"> (▼{abs(delta)})</span>'
    return '<span class="oct-rank-delta rank-neutral"> (=)</span>'


def _stream_pct_html(entry: dict) -> str:
    pct = entry.get("stream_change_pct")
    if pct is None:
        return ""
    sign = "+" if pct > 0 else ""
    css  = "positive" if pct > 0 else "negative" if pct < 0 else ""
    return f'<span class="oct-stream-delta {css}"> ({sign}{pct:.1f}%)</span>'


_NAME_OVERRIDES: dict[str, str] = {
    "il": "Occupied Palestine",
    "Israel": "Occupied Palestine",
}

def _country_label(code: str, name: str) -> str:
    if code.lower() in ("global", "glob"):
        return "Global"
    label = name or code.upper()
    return _NAME_OVERRIDES.get(code, _NAME_OVERRIDES.get(label, label))


# ── HTML builder ──────────────────────────────────────────────────────────────

_TABLE_SPLIT_THRESHOLD = 8   # split into 2 columns when entries exceed this
_LOW_COUNTRY_MAX = 2
_LOW_COUNTRY_GROUP_SLUG = "low_country_tracks"


def _rows_html(entries: list[dict]) -> str:
    rows = ""
    for e in entries:
        label = _country_label(e.get("country", ""), e.get("country_name", ""))
        rank = "OUT" if e.get("out") else f"#{e.get('rank', '?')}"
        rows += (
            f"<tr>"
            f'<td class="oct-country">{html.escape(label)}</td>'
            f'<td class="oct-rank">{rank}{_rank_delta_html(e)}</td>'
            f'<td class="oct-streams">{_fmt_streams(e.get("streams"))}{_stream_pct_html(e)}</td>'
            f"</tr>"
        )
    return rows


def _table_html(entries: list[dict]) -> str:
    """Single table."""
    return (
        '<table class="overall-country-table"><thead><tr>'
        '<th class="oct-country">Region</th>'
        '<th class="oct-rank">Ranking</th>'
        '<th class="oct-streams">Streams</th>'
        f"</tr></thead><tbody>{_rows_html(entries)}</tbody></table>"
    )


def _tables_html(entries: list[dict]) -> str:
    """One or two tables depending on entry count."""
    if len(entries) <= _TABLE_SPLIT_THRESHOLD:
        return _table_html(entries)

    mid   = (len(entries) + 1) // 2
    left  = entries[:mid]
    right = entries[mid:]
    tl = _table_html(left)
    tr = _table_html(right)
    return f'<div class="two-col-tables">{tl}{tr}</div>'


_LOGO_URI: str = ""   # loaded once on first call


def _build_card_html(song: dict, entries: list[dict], palette: dict[str, str], chart_date: str = "") -> str:
    global _LOGO_URI
    if not _LOGO_URI:
        _LOGO_URI = _logo_data_uri()
    img_uri = _url_to_data_uri(song.get("image_url", ""))
    title   = html.escape(song.get("title", "Unknown"))
    artist  = html.escape(song.get("primary_artist", "Taylor Swift"))

    # Format date badge: day number + month abbreviation
    try:
        _d = datetime.strptime(chart_date, "%Y-%m-%d")
        date_label = f"{str(_d.day)} {_d.strftime('%b')} {_d.year}"  # "10 May 2026"
    except Exception:
        date_label = chart_date

    # Sort: Global first, then by streams desc
    def _key(e: dict):
        is_global = e.get("country", "").lower() in ("global", "glob")
        is_out = bool(e.get("out"))
        return (0 if is_global else 1, 1 if is_out else 0, -(e.get("streams") or 0), e.get("previous_rank") or 9999)

    sorted_entries = sorted(entries, key=_key)
    two_col = len(sorted_entries) > _TABLE_SPLIT_THRESHOLD
    card_width = 800 if two_col else 480

    cover_html = (
        f'<img class="overall-song-cover" src="{img_uri}" alt="cover" />'
        if img_uri else
        '<div class="overall-song-cover cover-placeholder"></div>'
    )

    p = palette
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: {p['bg']};
    display: flex;
    padding: 16px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .overall-song-block {{
    background: {p['card_bg']};
    border-radius: 1.1rem;
    border: 1px solid {p['border']};
    box-shadow: 0 2px 16px rgba(0,0,0,0.35);
    padding: 1.1rem 1.2rem 0.9rem 1.2rem;
    display: flex;
    flex-direction: column;
    width: {card_width}px;
  }}
  .overall-song-header {{
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.9rem;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid {p['border']};
  }}
  .overall-song-cover {{
    width: 52px;
    height: 52px;
    border-radius: 0.6rem;
    object-fit: cover;
    flex-shrink: 0;
    box-shadow: 0 1px 6px rgba(0,0,0,0.4);
  }}
  .cover-placeholder {{
    background: linear-gradient(135deg, {p['border']}, {p['card_bg']});
  }}
  .overall-song-meta {{ min-width: 0; flex: 1; }}
  .overall-song-title {{
    font-size: 1.05rem;
    font-weight: 700;
    color: {p['text']};
    margin-bottom: 0.15em;
    line-height: 1.2;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .overall-song-artist {{ font-size: 0.92rem; color: {p['muted']}; line-height: 1.2; }}
  .date-badge {{
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    padding: 0 0.75rem;
    height: 32px;
    border-radius: 999px;
    background: {p['play_btn']};
    color: #fff;
    margin-left: auto;
    font-size: 0.82rem;
    font-weight: 700;
    white-space: nowrap;
    letter-spacing: 0.01em;
  }}

  /* Two-column layout */
  .two-col-tables {{
    display: flex;
    gap: 1.2rem;
    align-items: flex-start;
  }}
  .two-col-tables .overall-country-table {{ flex: 1; }}

  .overall-country-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
  }}
  .overall-country-table thead tr {{ border-bottom: 1px solid {p['border']}; }}
  .overall-country-table th {{
    text-align: right;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: {p['muted']};
    padding: 0 0.5rem 0.4rem 0.5rem;
  }}
  .overall-country-table th.oct-country {{ text-align: left; }}
  .overall-country-table td {{
    padding: 0.35rem 0.5rem;
    vertical-align: middle;
    white-space: nowrap;
    text-align: right;
    color: {p['text']};
  }}
  .overall-country-table tbody tr:nth-child(even) {{ background: {p['even_row']}; }}
  .oct-country {{ font-weight: 600; color: {p['region']} !important; text-align: left !important; min-width: 64px; }}
  .oct-rank {{ font-weight: 700; color: {p['text']}; }}
  .oct-rank-delta {{ font-size: 0.85em; font-weight: 400; }}
  .oct-rank-delta.rank-up   {{ color: {p['rank_up']}; }}
  .oct-rank-delta.rank-down {{ color: {p['rank_down']}; }}
  .oct-rank-delta.rank-neutral {{ color: {p['muted']}; }}
  .oct-rank-delta.rank-tag  {{ color: {p['muted']}; }}
  .oct-streams {{ font-weight: 700; color: {p['text']}; font-variant-numeric: tabular-nums; }}
  .oct-stream-delta {{ font-size: 0.85em; font-weight: 400; font-variant-numeric: tabular-nums; }}
  .oct-stream-delta.positive {{ color: {p['stream_pos']}; }}
  .oct-stream-delta.negative {{ color: {p['stream_neg']}; }}
  .card-footer {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 0.65rem;
    padding-top: 0.5rem;
    border-top: 1px solid {p['border']};
    font-size: 0.72rem;
    color: {p['muted']};
    letter-spacing: 0.01em;
    opacity: 0.7;
  }}
  .card-footer-brand {{
    display: flex;
    align-items: center;
    gap: 0.35rem;
  }}
  .card-footer-logo {{
    height: 18px;
    width: auto;
    display: block;
  }}
</style>
</head>
<body>
<div class="overall-song-block" id="card">
  <div class="overall-song-header">
    {cover_html}
    <div class="overall-song-meta">
      <div class="overall-song-title">{title}</div>
      <div class="overall-song-artist">{artist}</div>
    </div>
    <div class="date-badge">{date_label}</div>
  </div>
  {_tables_html(sorted_entries)}
  <div class="card-footer">
    <div class="card-footer-brand">
      <img class="card-footer-logo" src="{_LOGO_URI}" alt="TSM" />
      <span>@tsmuseum13</span>
    </div>
    <span>thetsmuseum.app</span>
  </div>
</div>
</body>
</html>"""


def _date_label(chart_date: str) -> str:
    try:
        d = datetime.strptime(chart_date, "%Y-%m-%d")
        return f"{str(d.day)} {d.strftime('%b')} {d.year}"
    except Exception:
        return chart_date


def _sorted_entries(entries: list[dict]) -> list[dict]:
    def _key(e: dict):
        is_global = e.get("country", "").lower() in ("global", "glob")
        return (0 if is_global else 1, -(e.get("streams") or 0))

    return sorted(entries, key=_key)


def _low_country_regions_html(entries: list[dict]) -> str:
    chips = ""
    for entry in _sorted_entries(entries):
        label = _country_label(entry.get("country", ""), entry.get("country_name", ""))
        rank = entry.get("rank", "?")
        streams = _fmt_streams(entry.get("streams"))
        chips += (
            '<div class="mini-region">'
            f'<span class="mini-region-name">{html.escape(label)}</span>'
            f'<span class="mini-region-rank">#{rank}{_rank_delta_html(entry)}</span>'
            f'<span class="mini-region-streams">{streams}{_stream_pct_html(entry)}</span>'
            "</div>"
        )
    return chips


def _build_low_country_group_html(
    tracks: list[tuple[str, list[dict]]],
    song_meta: dict[str, dict],
    palette: dict[str, str],
    chart_date: str,
) -> str:
    global _LOGO_URI
    if not _LOGO_URI:
        _LOGO_URI = _logo_data_uri()

    p = palette
    rows = ""
    for track_id, entries in tracks:
        song = song_meta.get(track_id, {})
        title = html.escape(song.get("title", track_id))
        artist = html.escape(song.get("primary_artist", "Taylor Swift"))
        img_uri = _url_to_data_uri(song.get("image_url", ""))
        cover_html = (
            f'<img class="mini-cover" src="{img_uri}" alt="cover" />'
            if img_uri else
            '<div class="mini-cover cover-placeholder"></div>'
        )
        rows += f"""
  <div class="mini-song-row">
    {cover_html}
    <div class="mini-song-main">
      <div class="mini-song-title">{title}</div>
      <div class="mini-song-artist">{artist}</div>
    </div>
    <div class="mini-region-list">
      {_low_country_regions_html(entries)}
    </div>
  </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: {p['bg']};
    display: flex;
    padding: 16px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .mini-card {{
    width: 760px;
    background: {p['card_bg']};
    border: 1px solid {p['border']};
    border-radius: 1.1rem;
    box-shadow: 0 2px 16px rgba(0,0,0,0.35);
    padding: 1.05rem 1.15rem 0.85rem 1.15rem;
    color: {p['text']};
  }}
  .mini-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding-bottom: 0.75rem;
    margin-bottom: 0.65rem;
    border-bottom: 1px solid {p['border']};
  }}
  .mini-heading {{
    min-width: 0;
  }}
  .mini-title {{
    font-size: 1.08rem;
    font-weight: 750;
    line-height: 1.2;
  }}
  .mini-subtitle {{
    margin-top: 0.15rem;
    font-size: 0.86rem;
    color: {p['muted']};
  }}
  .date-badge {{
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    padding: 0 0.75rem;
    height: 32px;
    border-radius: 999px;
    background: {p['play_btn']};
    color: #fff;
    font-size: 0.82rem;
    font-weight: 700;
    white-space: nowrap;
  }}
  .mini-song-row {{
    display: grid;
    grid-template-columns: 48px minmax(0, 1fr) minmax(310px, 0.95fr);
    gap: 0.75rem;
    align-items: center;
    padding: 0.62rem 0;
    border-bottom: 1px solid {p['border']};
  }}
  .mini-song-row:last-of-type {{ border-bottom: 0; }}
  .mini-cover {{
    width: 48px;
    height: 48px;
    border-radius: 0.55rem;
    object-fit: cover;
    box-shadow: 0 1px 6px rgba(0,0,0,0.4);
  }}
  .cover-placeholder {{
    background: linear-gradient(135deg, {p['border']}, {p['card_bg']});
  }}
  .mini-song-main {{ min-width: 0; }}
  .mini-song-title {{
    font-size: 0.98rem;
    font-weight: 720;
    line-height: 1.22;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .mini-song-artist {{
    margin-top: 0.12rem;
    font-size: 0.82rem;
    color: {p['muted']};
  }}
  .mini-region-list {{
    display: flex;
    flex-direction: column;
    gap: 0.32rem;
  }}
  .mini-region {{
    display: grid;
    grid-template-columns: minmax(92px, 1fr) 76px 126px;
    gap: 0.45rem;
    align-items: baseline;
    padding: 0.32rem 0.45rem;
    border-radius: 0.45rem;
    background: {p['even_row']};
    font-size: 0.78rem;
  }}
  .mini-region-name {{
    color: {p['region']};
    font-weight: 700;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .mini-region-rank {{
    text-align: right;
    font-weight: 720;
    white-space: nowrap;
  }}
  .mini-region-streams {{
    text-align: right;
    font-weight: 720;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }}
  .oct-rank-delta {{ font-size: 0.82em; font-weight: 400; }}
  .oct-rank-delta.rank-up   {{ color: {p['rank_up']}; }}
  .oct-rank-delta.rank-down {{ color: {p['rank_down']}; }}
  .oct-rank-delta.rank-neutral {{ color: {p['muted']}; }}
  .oct-rank-delta.rank-tag  {{ color: {p['muted']}; }}
  .oct-stream-delta {{ font-size: 0.82em; font-weight: 400; font-variant-numeric: tabular-nums; }}
  .oct-stream-delta.positive {{ color: {p['stream_pos']}; }}
  .oct-stream-delta.negative {{ color: {p['stream_neg']}; }}
  .card-footer {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 0.55rem;
    padding-top: 0.5rem;
    border-top: 1px solid {p['border']};
    font-size: 0.72rem;
    color: {p['muted']};
    letter-spacing: 0.01em;
    opacity: 0.7;
  }}
  .card-footer-brand {{
    display: flex;
    align-items: center;
    gap: 0.35rem;
  }}
  .card-footer-logo {{
    height: 18px;
    width: auto;
    display: block;
  }}
</style>
</head>
<body>
<div class="mini-card" id="card">
  <div class="mini-header">
    <div class="mini-heading">
      <div class="mini-title">Taylor Swift on Spotify Charts</div>
      <div class="mini-subtitle">Songs charting in one or two countries</div>
    </div>
    <div class="date-badge">{_date_label(chart_date)}</div>
  </div>
  {rows}
  <div class="card-footer">
    <div class="card-footer-brand">
      <img class="card-footer-logo" src="{_LOGO_URI}" alt="TSM" />
      <span>@tsmuseum13</span>
    </div>
    <span>thetsmuseum.app</span>
  </div>
</div>
</body>
</html>"""


# ── Tweet builder ─────────────────────────────────────────────────────────────

_ALBUM_EMOJI: list[tuple[str, str]] = [
    ("the life of a showgirl", "❤️‍🔥"),
    ("the tortured poets department", "🤍"),
    ("midnights", "💙"),
    ("evermore", "🤎"),
    ("folklore", "🩶"),
    ("lover", "🩷"),
    ("reputation", "🖤"),
    ("1989", "🩵"),
    ("red", "❤️"),
    ("speak now", "💜"),
    ("fearless", "💛"),
    ("taylor swift", "💚"),
]

_OVERALL_URL = "🔗 See full update here : https://thetsmuseum.app/charts?region=overall&view=today"


def _album_emoji(album: str) -> str:
    al = album.lower().strip()
    for key, emoji in _ALBUM_EMOJI:
        if al.startswith(key) or key in al:
            return emoji
    return "🎵"


def _worldwide_snapshot_path(chart_date: str) -> Path:
    return spotify_chart_dir("worldwide", chart_date) / f"ts_worldwide_{chart_date}.json"


def _previous_snapshot_path(chart_date: str) -> Path | None:
    try:
        prev_date = (datetime.strptime(chart_date, "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        return None

    prev_path = _worldwide_snapshot_path(prev_date)
    if not prev_path.exists():
        prev_path = legacy_spotify_chart_dir("worldwide", prev_date) / f"ts_worldwide_{prev_date}.json"
    if not prev_path.exists():
        return None
    return prev_path


def _load_prev_country_counts(chart_date: str) -> dict[str, int]:
    prev_path = _previous_snapshot_path(chart_date)
    if not prev_path:
        return {}

    try:
        prev_data = json.loads(prev_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    by_track = prev_data.get("by_track", {})
    return {track_id: len(entries) for track_id, entries in by_track.items() if isinstance(entries, list)}


def _load_prev_by_track(chart_date: str) -> dict[str, list[dict]]:
    prev_path = _previous_snapshot_path(chart_date)
    if not prev_path:
        return {}
    try:
        prev_data = json.loads(prev_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    by_track = prev_data.get("by_track", {})
    return {track_id: entries for track_id, entries in by_track.items() if isinstance(entries, list)}


def _with_out_regions(entries: list[dict], prev_entries: list[dict]) -> list[dict]:
    current_countries = {str(entry.get("country") or "").lower() for entry in entries}
    enriched = list(entries)
    for prev in prev_entries:
        country = str(prev.get("country") or "").lower()
        if not country or country in current_countries:
            continue
        out_entry = dict(prev)
        out_entry["out"] = True
        out_entry["rank"] = None
        out_entry["previous_rank"] = prev.get("rank")
        out_entry["rank_change"] = None
        out_entry["streams"] = None
        out_entry["stream_change"] = None
        out_entry["stream_change_pct"] = None
        enriched.append(out_entry)
    return enriched


def _country_count_text(count: int, prev_count: int | None) -> str:
    if prev_count is None:
        return "one country" if count == 1 else f"{count} countries"
    diff = count - prev_count
    diff_str = "=" if diff == 0 else f"+{diff}" if diff > 0 else str(diff)
    return f"{count} ({diff_str}) countries"


def _build_tweet(song: dict, entries: list[dict], chart_date: str, prev_count: int | None = None) -> str:
    title    = song.get("title", "Unknown")
    album    = song.get("primary_album", "")
    emoji    = _album_emoji(album)
    count    = len(entries)
    try:
        date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        date_fmt = chart_date
    if count == 1:
        entry = entries[0]
        country = _country_label(str(entry.get("country") or ""), str(entry.get("country_name") or ""))
        rank = entry.get("rank", "?")
        streams = _fmt_streams(entry.get("streams"))
        return (
            f'{emoji} | "{title}" charted on Spotify in {country} at #{rank} '
            f"with {streams} streams yesterday ({date_fmt}).\n\n{_OVERALL_URL}"
        )
    country_str = _country_count_text(count, prev_count)
    return f'{emoji} | "{title}" charted in {country_str} on Spotify yesterday ({date_fmt}).\n\n{_OVERALL_URL}'


def _build_reentry_tweet(song: dict, entries: list[dict], chart_date: str) -> str:
    title = song.get("title", "Unknown")
    count = len(entries)
    country_word = "country" if count == 1 else "countries"
    try:
        date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        date_fmt = chart_date
    if count == 1:
        entry = entries[0]
        country = _country_label(str(entry.get("country") or ""), str(entry.get("country_name") or ""))
        rank = entry.get("rank", "?")
        streams = _fmt_streams(entry.get("streams"))
        return (
            f'"{title}" re-entered the Spotify Charts.\n\n'
            f"Charted on Spotify in {country} at #{rank} with {streams} streams "
            f"yesterday ({date_fmt}).\n\n{_OVERALL_URL}"
        )
    return (
        f'"{title}" re-entered the Spotify Charts in {count} {country_word} '
        f"yesterday ({date_fmt}).\n\n{_OVERALL_URL}"
    )


def _build_low_country_group_tweet(
    tracks: list[tuple[str, list[dict]]],
    song_meta: dict[str, dict],
    chart_date: str,
) -> str:
    try:
        date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        date_fmt = chart_date

    titles = [str(song_meta.get(track_id, {}).get("title") or track_id) for track_id, _ in tracks]
    if len(titles) <= 3:
        title_text = ", ".join(f'"{title}"' for title in titles)
    else:
        title_text = ", ".join(f'"{title}"' for title in titles[:3]) + f", and {len(titles) - 3} more"

    return (
        f"🎵 | {len(tracks)} Taylor Swift songs charted in one or two countries "
        f"on Spotify yesterday ({date_fmt}): {title_text}.\n\n{_OVERALL_URL}"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

_GLOBAL_REGION_CODES = {"global", "glob"}


def _best_entry(entries: list[dict], key: str, *, reverse: bool = True) -> dict | None:
    valid = [e for e in entries if e.get(key) is not None]
    regional = [e for e in valid if str(e.get("country") or "").lower() not in _GLOBAL_REGION_CODES]
    pool = regional or valid
    if not pool:
        return None
    return sorted(pool, key=lambda e: e.get(key) or 0, reverse=reverse)[0]


def _summary_rows(
    tracks: list[tuple[str, list[dict]]],
    song_meta: dict[str, dict],
) -> list[dict]:
    rows: list[dict] = []
    for track_id, entries in tracks:
        title = str(song_meta.get(track_id, {}).get("title") or track_id)
        peak_streams = _best_entry(entries, "streams", reverse=True)
        peak_rank = _best_entry(entries, "rank", reverse=False)
        rows.append(
            {
                "song": title,
                "countries": len(entries),
                "peak_streams": peak_streams,
                "peak_rank": peak_rank,
                "top10": sum(1 for e in entries if (e.get("rank") or 9999) <= 10),
                "top50": sum(1 for e in entries if (e.get("rank") or 9999) <= 50),
                "top100": sum(1 for e in entries if (e.get("rank") or 9999) <= 100),
            }
        )
    return rows


def _summary_cell_entry(entry: dict | None, *, streams: bool = False) -> str:
    if not entry:
        return "-"
    country = _country_label(str(entry.get("country") or ""), str(entry.get("country_name") or ""))
    if streams:
        return f"{country} - {_fmt_streams(entry.get('streams'))}"
    return f"{country} - #{entry.get('rank', '?')}"


def _build_summary_html(
    tracks: list[tuple[str, list[dict]]],
    song_meta: dict[str, dict],
    palette: dict[str, str],
    chart_date: str,
) -> str:
    global _LOGO_URI
    if not _LOGO_URI:
        _LOGO_URI = _logo_data_uri()

    try:
        date_label = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        date_label = chart_date

    rows_html = ""
    for row in _summary_rows(tracks, song_meta):
        rows_html += (
            "<tr>"
            f"<td class='song'>{html.escape(row['song'])}</td>"
            f"<td>{row['countries']}</td>"
            f"<td class='wide'>{html.escape(_summary_cell_entry(row['peak_streams'], streams=True))}</td>"
            f"<td class='wide'>{html.escape(_summary_cell_entry(row['peak_rank']))}</td>"
            f"<td>{row['top10']}</td>"
            f"<td>{row['top50']}</td>"
            f"<td>{row['top100']}</td>"
            "</tr>"
        )

    p = palette
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: {p['bg']};
    padding: 22px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: {p['text']};
  }}
  .summary {{
    width: 1320px;
    background: {p['card_bg']};
    border: 1px solid {p['border']};
    border-radius: 18px;
    box-shadow: 0 2px 18px rgba(0,0,0,0.22);
    padding: 22px 24px 16px;
  }}
  .header {{
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 18px;
    padding-bottom: 16px;
    border-bottom: 1px solid {p['border']};
  }}
  .title {{
    font-size: 32px;
    font-weight: 800;
    line-height: 1.08;
  }}
  .subtitle {{
    margin-top: 6px;
    color: {p['muted']};
    font-size: 18px;
    font-weight: 650;
  }}
  .date {{
    flex-shrink: 0;
    border-radius: 999px;
    background: {p['play_btn']};
    color: #fff;
    padding: 9px 16px;
    font-size: 18px;
    font-weight: 800;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 16px;
    table-layout: fixed;
  }}
  th, td {{
    border-bottom: 1px solid {p['border']};
    padding: 9px 10px;
    font-size: 16px;
    line-height: 1.18;
    text-align: center;
    vertical-align: middle;
  }}
  th {{
    color: {p['muted']};
    font-size: 13px;
    font-weight: 800;
    text-transform: uppercase;
  }}
  tr:nth-child(even) td {{ background: {p['even_row']}; }}
  .song {{
    width: 280px;
    text-align: left;
    color: {p['region']};
    font-weight: 800;
  }}
  .wide {{
    width: 245px;
    text-align: left;
    font-weight: 720;
  }}
  .footer {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid {p['border']};
    color: {p['muted']};
    font-size: 15px;
    opacity: 0.78;
  }}
  .brand {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .logo {{ height: 24px; width: auto; }}
</style>
</head>
<body>
<div class="summary" id="card">
  <div class="header">
    <div>
      <div class="title">Taylor Swift on Spotify Charts</div>
      <div class="subtitle">Worldwide recap by song</div>
    </div>
    <div class="date">{html.escape(date_label)}</div>
  </div>
  <table>
    <thead>
      <tr>
        <th class="song">Song</th>
        <th>Countries</th>
        <th class="wide">Peak streams</th>
        <th class="wide">Peak rank</th>
        <th>Top 10</th>
        <th>Top 50</th>
        <th>Top 100</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <div class="footer">
    <div class="brand">
      <img class="logo" src="{_LOGO_URI}" alt="TSM" />
      <span>@tsmuseum13</span>
    </div>
    <span>thetsmuseum.app</span>
  </div>
</div>
</body>
</html>"""


def _summary_tweet(chart_date: str, track_count: int, prev_track_count: int | None = None) -> str:
    try:
        date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        date_fmt = chart_date
    if prev_track_count is None:
        delta_text = ""
    else:
        delta = track_count - prev_track_count
        delta_text = " (=)" if delta == 0 else f" ({delta:+d})"
    return (
        f"🧵 Taylor Swift charted {track_count} songs{delta_text} "
        f"on the Spotify Charts yesterday ({date_fmt}).\n\n{_OVERALL_URL}"
    )


def _global_entry(entries: list[dict]) -> dict | None:
    for entry in entries:
        if str(entry.get("country") or "").lower() in _GLOBAL_REGION_CODES:
            return entry
    return None


def _is_re_entry(entry: dict | None) -> bool:
    if not entry or entry.get("previous_rank") is not None:
        return False
    rank = entry.get("rank")
    peak = entry.get("peak_rank")
    return peak is not None and peak != rank


def _card_priority(track_id: str, entries: list[dict], song: dict) -> tuple:
    global_entry = _global_entry(entries)
    global_rank = global_entry.get("rank") if global_entry else None
    best_rank = min((e.get("rank") or 9999 for e in entries), default=9999)
    total_streams = sum(e.get("streams") or 0 for e in entries)
    title = str(song.get("title") or track_id).lower()
    return (
        0 if global_entry else 1,
        0 if _is_re_entry(global_entry) else 1,
        global_rank or 9999,
        -len(entries),
        best_rank,
        -total_streams,
        title,
    )


def _priority_payload(entries: list[dict]) -> dict:
    global_entry = _global_entry(entries)
    if not global_entry:
        return {"level": 1, "reason": "regional_only"}
    status = "re_entry" if _is_re_entry(global_entry) else "active"
    return {
        "level": 0,
        "reason": f"global_chart_{status}",
        "global_rank": global_entry.get("rank"),
        "global_previous_rank": global_entry.get("previous_rank"),
    }


def generate(chart_date: str, *, theme: str = "showgirl", min_countries: int = 3, force: bool = False, post: bool = False) -> int:
    palette = THEMES.get(theme)
    if palette is None:
        print(f"[ERROR] Thème inconnu: {theme!r}. Choix: {', '.join(THEMES)}")
        return 1

    if not WORLDWIDE_JSON.exists():
        print(f"[ERROR] Fichier introuvable: {WORLDWIDE_JSON}")
        return 1
    if not SONGS_JSON.exists():
        print(f"[ERROR] Fichier introuvable: {SONGS_JSON}")
        return 1

    data      = _load_json(WORLDWIDE_JSON)
    file_date = data.get("date", "")
    by_track  = data.get("by_track", {})

    if file_date != chart_date:
        print(f"[WARN] charts_worldwide.json contient {file_date!r}, attendu {chart_date!r}")

    songs_raw  = _load_json(SONGS_JSON)
    songs_list = songs_raw.get("songs", songs_raw) if isinstance(songs_raw, dict) else songs_raw
    song_meta: dict[str, dict] = {s["track_id"]: s for s in songs_list if "track_id" in s}
    has_prev_snapshot = _previous_snapshot_path(chart_date) is not None
    prev_by_track = _load_prev_by_track(chart_date)
    prev_country_counts = _load_prev_country_counts(chart_date)

    d       = datetime.strptime(chart_date, "%Y-%m-%d").date()
    out_dir = spotify_chart_dir("worldwide", chart_date) / "cards"

    index_path = out_dir / "cards_index.json"
    posted_path = out_dir / "posted_cards.json"
    posted_reentries_path = out_dir / "posted_reentries.json"
    if index_path.exists() and not force:
        try:
            if post:
                # Existing images may still have missing posted_cards entries.
                # Continue so pending cards can be posted instead of returning early.
                raise RuntimeError("post requested")
            existing = json.loads(index_path.read_text(encoding="utf-8"))
            n = len(existing.get("cards", []))
            print(f"[SKIP] Cards déjà générées pour {chart_date} ({n} images) — utilise --force pour refaire")
            return 0
        except Exception:
            pass

    out_dir.mkdir(parents=True, exist_ok=True)

    tracks = [(tid, entries) for tid, entries in by_track.items() if len(entries) >= min_countries]
    tracks.sort(key=lambda item: _card_priority(item[0], item[1], song_meta.get(item[0], {})))
    low_country_tracks = [
        (tid, entries)
        for tid, entries in by_track.items()
        if min_countries > 1 and 1 <= len(entries) < min_countries and len(entries) <= _LOW_COUNTRY_MAX
    ]
    low_country_tracks.sort(key=lambda item: _card_priority(item[0], item[1], song_meta.get(item[0], {})))
    print(
        f"[INFO] {len(tracks)} tracks, {len(low_country_tracks)} low-country tracks, "
        f"thème={theme!r}, min_countries={min_countries}"
    )

    generated: list[str] = []
    priority_index: dict[str, dict] = {}
    to_post: list[tuple[Path, str]] = []  # (image_path, tweet_text)
    reentries_to_post: list[tuple[str, str]] = []  # (slug, tweet_text)

    # Load already-posted slugs to avoid re-posting on --force reruns
    posted_path = out_dir / "posted_cards.json"
    already_posted: set[str] = set()
    already_posted_reentries: set[str] = set()
    if post:
        if posted_path.exists():
            try:
                data = json.loads(posted_path.read_text(encoding="utf-8"))
                # Support both {"posted": [...slugs...]} and legacy {"cards": [...filenames...]}
                if "posted" in data:
                    already_posted = set(data["posted"])
                else:
                    already_posted = {Path(f).stem for f in data.get("cards", [])}
            except Exception:
                pass
        elif index_path.exists():
            print("[INFO] cards_index.json existe mais posted_cards.json est absent; publication des cards non verrouillées")
        if posted_reentries_path.exists():
            try:
                data = json.loads(posted_reentries_path.read_text(encoding="utf-8"))
                already_posted_reentries = set(data.get("posted", []))
            except Exception:
                pass

    _wait_for_twitter_lock()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--force-color-profile=srgb"])
            page = browser.new_page(viewport={"width": 1400, "height": 2200}, device_scale_factor=3)

            summary_slug = "worldwide_summary"
            summary_path = out_dir / f"{summary_slug}.png"
            print(f"  [summary] {len(tracks)} tracks", end="", flush=True)
            try:
                page.set_content(
                    _build_summary_html(tracks, song_meta, palette, chart_date),
                    wait_until="domcontentloaded",
                )
                card = page.locator("#card")
                card.wait_for(state="visible", timeout=5000)
                card.screenshot(path=str(summary_path))
                generated.append(summary_path.name)
                priority_index[summary_path.name] = {
                    "level": -1,
                    "reason": "thread_summary",
                    "track_count": len(tracks),
                }
                print(f"  -> {summary_path.name}")
                if post and summary_slug not in already_posted:
                    to_post.append((summary_path, _summary_tweet(chart_date, len(tracks), len(prev_by_track) if has_prev_snapshot else None)))
                elif post:
                    print("    [SKIP] deja poste")
            except Exception as e:
                print(f"  [WARN] echec summary: {e}")

            page.set_viewport_size({"width": 860, "height": 900})

            for i, (track_id, entries) in enumerate(tracks, 1):
                meta      = song_meta.get(track_id, {})
                title_raw = meta.get("title", track_id)
                slug      = _slugify(title_raw)
                out_path  = out_dir / f"{slug}.png"
                card_palette, card_theme = _palette_for_song(meta, palette)

                print(f"  [{i:3d}/{len(tracks)}] {title_raw[:50]}", end="", flush=True)
                card_entries = _with_out_regions(entries, prev_by_track.get(track_id, []))
                html_content = _build_card_html(meta, card_entries, card_palette, chart_date)
                try:
                    page.set_content(html_content, wait_until="domcontentloaded")
                    card = page.locator("#card")
                    card.wait_for(state="visible", timeout=5000)
                    card.screenshot(path=str(out_path))
                    generated.append(out_path.name)
                    priority = _priority_payload(entries)
                    priority["theme"] = card_theme
                    priority_index[out_path.name] = priority
                    print(f"  → {out_path.name}")
                    prev_count = prev_country_counts.get(track_id)
                    if post and has_prev_snapshot and (prev_count or 0) == 0 and slug not in already_posted_reentries:
                        reentries_to_post.append((slug, _build_reentry_tweet(meta, entries, chart_date)))
                    if post and slug not in already_posted:
                        to_post.append((out_path, _build_tweet(meta, entries, chart_date, prev_count)))
                    elif post:
                        print(f"    [SKIP] déjà posté")
                except Exception as e:
                    print(f"  [WARN] échec: {e}")

            if len(low_country_tracks) > 1:
                out_path = out_dir / f"{_LOW_COUNTRY_GROUP_SLUG}.png"
                group_palette, group_theme = _dominant_album_theme(low_country_tracks, song_meta, palette)
                print(f"  [group] {len(low_country_tracks)} low-country tracks", end="", flush=True)
                html_content = _build_low_country_group_html(
                    low_country_tracks,
                    song_meta,
                    group_palette,
                    chart_date,
                )
                try:
                    page.set_content(html_content, wait_until="domcontentloaded")
                    card = page.locator("#card")
                    card.wait_for(state="visible", timeout=5000)
                    card.screenshot(path=str(out_path))
                    generated.append(out_path.name)
                    priority_index[out_path.name] = {
                        "level": 2,
                        "reason": "low_country_group",
                        "track_count": len(low_country_tracks),
                        "max_countries": _LOW_COUNTRY_MAX,
                        "theme": group_theme,
                    }
                    print(f"  â†’ {out_path.name}")
                    if post and _LOW_COUNTRY_GROUP_SLUG not in already_posted:
                        to_post.append(
                            (
                                out_path,
                                _build_low_country_group_tweet(low_country_tracks, song_meta, chart_date),
                            )
                        )
                    elif post:
                        print("    [SKIP] dÃ©jÃ  postÃ©")
                except Exception as e:
                    print(f"  [WARN] Ã©chec group: {e}")

            browser.close()
    finally:
        _release_twitter_lock()

    index_path = out_dir / "cards_index.json"
    index_path.write_text(
        json.dumps(
            {"date": chart_date, "theme": theme, "cards": generated, "priority": priority_index},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[DONE] {len(generated)} images → {out_dir}")

    if post and reentries_to_post:
        print(f"[STEP] Publication de {len(reentries_to_post)} re-entry post(s) prioritaires...")
        newly_posted_reentries: list[str] = []
        for slug, tweet_text in reentries_to_post:
            if _post_thread([tweet_text], TWITTER_SESSION):
                newly_posted_reentries.append(slug)
                all_reentries = sorted(already_posted_reentries | set(newly_posted_reentries))
                posted_reentries_path.write_text(
                    json.dumps({"date": chart_date, "posted": all_reentries}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            else:
                print(f"[WARN] Echec post re-entry: {slug}")
                return 1

    if post and to_post:
        print(f"[STEP] Publication d'un thread de {len(to_post)} card(s) sur Twitter...")
        thread_posts = [(tweet_text, img_path) for img_path, tweet_text in to_post]
        if _post_image_thread(thread_posts, TWITTER_SESSION):
            all_posted = sorted(already_posted | {img_path.stem for img_path, _ in to_post})
            posted_path.write_text(
                json.dumps({"date": chart_date, "posted": all_posted}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[STEP] Twitter: thread publie ({len(to_post)} posts)")
        else:
            print("[WARN] Echec publication thread cards")
            return 1
        return 0

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Génère les images PNG des cards worldwide.")
    parser.add_argument("date_pos", nargs="?", metavar="YYYY-MM-DD")
    parser.add_argument("--date", metavar="YYYY-MM-DD")
    parser.add_argument("--theme", default="showgirl",
                        choices=list(THEMES),
                        help=f"Palette de couleurs (défaut: showgirl). Choix: {', '.join(THEMES)}")
    parser.add_argument("--min-countries", type=int, default=3,
                        help="Nombre minimum de pays pour inclure un track (défaut: 3)")
    parser.add_argument("--force", action="store_true",
                        help="Régénère les images même si elles existent déjà pour cette date")
    parser.add_argument("--post", action="store_true",
                        help="Poste chaque card sur Twitter après génération")
    args = parser.parse_args()

    raw_date = args.date or args.date_pos or str(date.today() - timedelta(days=1))
    try:
        chart_date = datetime.strptime(raw_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        print(f"[ERROR] Date invalide: {raw_date!r}")
        return 1

    return generate(chart_date, theme=args.theme, min_countries=args.min_countries, force=args.force, post=args.post)


if __name__ == "__main__":
    raise SystemExit(main())
