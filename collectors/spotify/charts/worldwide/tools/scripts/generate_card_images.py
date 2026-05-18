#!/usr/bin/env python3
"""
generate_card_images.py — génère des PNG des cards "Overall" pour chaque chanson Taylor Swift.

Reproduit le composant SongBlock.jsx du frontend (dark theme).
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
from twitter import post_with_image as _post_with_image  # noqa: E402

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
        "bg":         "#0d1117",
        "card_bg":    "#161b22",
        "border":     "#30363d",
        "text":       "#e6edf3",
        "muted":      "#8b949e",
        "even_row":   "#1c2128",
        "region":     "#ff8f5a",
        "play_btn":   "#ff6b35",
        "rank_up":    "#2a9d5c",
        "rank_down":  "#e05c3a",
        "stream_pos": "#2a9d5c",
        "stream_neg": "#e05c3a",
    },
    # Plain dark (Spotify green)
    "dark": {
        "bg":         "#0f1117",
        "card_bg":    "#161b22",
        "border":     "#30363d",
        "text":       "#e6edf3",
        "muted":      "#8b949e",
        "even_row":   "#1c2128",
        "region":     "#c0602a",
        "play_btn":   "#1db954",
        "rank_up":    "#2a9d5c",
        "rank_down":  "#e05c3a",
        "stream_pos": "#2a9d5c",
        "stream_neg": "#e05c3a",
    },
    "midnights": {
        "bg":         "#040710",
        "card_bg":    "#080d18",
        "border":     "#1e2540",
        "text":       "#d4dcf0",
        "muted":      "#7080a8",
        "even_row":   "#0d1420",
        "region":     "#818cf8",
        "play_btn":   "#6366f1",
        "rank_up":    "#2a9d5c",
        "rank_down":  "#e05c3a",
        "stream_pos": "#2a9d5c",
        "stream_neg": "#e05c3a",
    },
    "ttpd": {
        "bg":         "#0d1117",
        "card_bg":    "#161b22",
        "border":     "#30363d",
        "text":       "#e6edf3",
        "muted":      "#8b949e",
        "even_row":   "#1c2128",
        "region":     "#d4cfc9",
        "play_btn":   "#d4cfc9",
        "rank_up":    "#2a9d5c",
        "rank_down":  "#e05c3a",
        "stream_pos": "#2a9d5c",
        "stream_neg": "#e05c3a",
    },
    "lover": {
        "bg":         "#1a0812",
        "card_bg":    "#25101c",
        "border":     "#4a1830",
        "text":       "#f5c0d8",
        "muted":      "#c07090",
        "even_row":   "#2e1424",
        "region":     "#ffb1d8",
        "play_btn":   "#e8709a",
        "rank_up":    "#4caf7d",
        "rank_down":  "#e05c3a",
        "stream_pos": "#4caf7d",
        "stream_neg": "#e05c3a",
    },
    "fearless": {
        "bg":         "#18110a",
        "card_bg":    "#221908",
        "border":     "#3d2e10",
        "text":       "#ffedb0",
        "muted":      "#c4943a",
        "even_row":   "#2a200a",
        "region":     "#f5c444",
        "play_btn":   "#d4a017",
        "rank_up":    "#4caf7d",
        "rank_down":  "#e05c3a",
        "stream_pos": "#4caf7d",
        "stream_neg": "#e05c3a",
    },
    "reputation": {
        "bg":         "#000000",
        "card_bg":    "#0a0a0a",
        "border":     "#222222",
        "text":       "#f5f5f5",
        "muted":      "#999999",
        "even_row":   "#141414",
        "region":     "#aaaaaa",
        "play_btn":   "#ffffff",
        "rank_up":    "#4caf7d",
        "rank_down":  "#e05c3a",
        "stream_pos": "#4caf7d",
        "stream_neg": "#e05c3a",
    },
    "evermore": {
        "bg":         "#120a05",
        "card_bg":    "#1e1009",
        "border":     "#3a1e0a",
        "text":       "#e8c5a0",
        "muted":      "#b08050",
        "even_row":   "#261408",
        "region":     "#c47840",
        "play_btn":   "#9b6b3d",
        "rank_up":    "#80a040",
        "rank_down":  "#e05c3a",
        "stream_pos": "#80a040",
        "stream_neg": "#e05c3a",
    },
}

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


def _rows_html(entries: list[dict]) -> str:
    rows = ""
    for e in entries:
        label = _country_label(e.get("country", ""), e.get("country_name", ""))
        rank  = e.get("rank", "?")
        rows += (
            f"<tr>"
            f'<td class="oct-country">{html.escape(label)}</td>'
            f'<td class="oct-rank">#{rank}{_rank_delta_html(e)}</td>'
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
        return (0 if is_global else 1, -(e.get("streams") or 0))

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


def _load_prev_country_counts(chart_date: str) -> dict[str, int]:
    try:
        prev_date = (datetime.strptime(chart_date, "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        return {}

    prev_path = _worldwide_snapshot_path(prev_date)
    if not prev_path.exists():
        prev_path = legacy_spotify_chart_dir("worldwide", prev_date) / f"ts_worldwide_{prev_date}.json"
    if not prev_path.exists():
        return {}

    try:
        prev_data = json.loads(prev_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    by_track = prev_data.get("by_track", {})
    return {track_id: len(entries) for track_id, entries in by_track.items() if isinstance(entries, list)}


def _country_count_text(count: int, prev_count: int | None) -> str:
    if prev_count is None:
        return "one country" if count == 1 else f"{count} countries"
    diff = count - prev_count
    diff_str = f"+{diff}" if diff >= 0 else str(diff)
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
    country_str = _country_count_text(count, prev_count)
    return f'{emoji} | "{title}" charted in {country_str} on Spotify yesterday ({date_fmt}).\n\n{_OVERALL_URL}'


# ── Main ──────────────────────────────────────────────────────────────────────

_GLOBAL_REGION_CODES = {"global", "glob"}


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
    prev_country_counts = _load_prev_country_counts(chart_date)

    d       = datetime.strptime(chart_date, "%Y-%m-%d").date()
    out_dir = spotify_chart_dir("worldwide", chart_date) / "cards"

    index_path = out_dir / "cards_index.json"
    posted_path = out_dir / "posted_cards.json"
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
    print(f"[INFO] {len(tracks)} tracks, thème={theme!r}, min_countries={min_countries}")

    generated: list[str] = []
    priority_index: dict[str, dict] = {}
    to_post: list[tuple[Path, str]] = []  # (image_path, tweet_text)

    # Load already-posted slugs to avoid re-posting on --force reruns
    posted_path = out_dir / "posted_cards.json"
    already_posted: set[str] = set()
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
            # Cards were generated before posted_cards.json existed — assume all were posted
            try:
                slugs = [Path(f).stem for f in json.loads(index_path.read_text(encoding="utf-8")).get("cards", [])]
                already_posted = set(slugs)
                posted_path.write_text(
                    json.dumps({"date": chart_date, "posted": sorted(already_posted)}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[INFO] posted_cards.json créé depuis cards_index.json ({len(already_posted)} slugs)")
            except Exception:
                pass

    _wait_for_twitter_lock()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--force-color-profile=srgb"])
            page = browser.new_page(viewport={"width": 860, "height": 900}, device_scale_factor=4)

            for i, (track_id, entries) in enumerate(tracks, 1):
                meta      = song_meta.get(track_id, {})
                title_raw = meta.get("title", track_id)
                slug      = _slugify(title_raw)
                out_path  = out_dir / f"{slug}.png"

                print(f"  [{i:3d}/{len(tracks)}] {title_raw[:50]}", end="", flush=True)
                html_content = _build_card_html(meta, entries, palette, chart_date)
                try:
                    page.set_content(html_content, wait_until="domcontentloaded")
                    card = page.locator("#card")
                    card.wait_for(state="visible", timeout=5000)
                    card.screenshot(path=str(out_path))
                    generated.append(out_path.name)
                    priority_index[out_path.name] = _priority_payload(entries)
                    print(f"  → {out_path.name}")
                    if post and slug not in already_posted:
                        prev_count = prev_country_counts.get(track_id)
                        to_post.append((out_path, _build_tweet(meta, entries, chart_date, prev_count)))
                    elif post:
                        print(f"    [SKIP] déjà posté")
                except Exception as e:
                    print(f"  [WARN] échec: {e}")

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

    if post and to_post:
        print(f"[STEP] Publication de {len(to_post)} card(s) sur Twitter...")
        ok = err = 0
        newly_posted: list[str] = []
        first = True
        for img_path, tweet_text in to_post:
            slug = img_path.stem
            if not first:
                time.sleep(120)
            first = False
            if _post_with_image(tweet_text, img_path, TWITTER_SESSION):
                ok += 1
                newly_posted.append(slug)
                # Persist after each success so a crash mid-run doesn't lose progress
                all_posted = sorted(already_posted | set(newly_posted))
                posted_path.write_text(
                    json.dumps({"date": chart_date, "posted": all_posted}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            else:
                err += 1
                print(f"[WARN] Echec post: {img_path.name}")
        print(f"[STEP] Twitter: {ok} postés, {err} échecs")

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
