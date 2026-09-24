#!/usr/bin/env python3
"""post_new_release_progression.py — hourly chart-movement posts for tracks
still inside their release window (default 72h), across Apple Music Global +
key countries and iTunes Store key countries.

Why this exists (2026-09-24, The Encore / 4 new tracks releasing 2026-09-25):
a brand-new track has no previous-day snapshot, so the normal day-over-day
movement markers used everywhere else in Apple Music (`core/csv_utils.py
::load_previous_ranks`, always "vs yesterday" on purpose) can't show anything
useful on release day. This script keeps its OWN small "last seen rank"
state per (track, chart, region) instead, so the delta is always "vs the
last time we checked" — which for a brand-new release is the only baseline
that exists. It naturally goes quiet again once every debut track ages out
of the window (data-rules: never post/estimate a comparison we don't have).

Run as a 3rd step in run_apple_music.bat, after both run_apple_music.py and
run_itunes.py, so this cycle's CSVs from both are already on disk. Safe to
run every cycle even outside a release window: it's a no-op when no catalog
track's release_date falls inside APPLE_MUSIC_DEBUT_WINDOW_HOURS.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Tweet text below includes emoji (U+1F3A7 etc.) outside Windows' default
# console codepage (cp1252) -> printing it when stdout/stderr are redirected
# to a file (run_apple_music.bat) raises UnicodeEncodeError and kills the
# whole script. Force UTF-8 on both streams before any such print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.discography import iter_catalog_tracks, song_key_candidates, song_name_key  # noqa: E402
from core.card_theme import country_label  # noqa: E402
from collectors.spotify.core.data_paths import (  # noqa: E402
    apple_music_daily_csv,
    itunes_daily_csv,
)
from collectors.comp.discography import (  # noqa: E402
    _norm as _album_norm,
    build_cover_map,
    display_title_for_album,
)

COVERS_PATH = REPO_ROOT / "db" / "discography" / "covers.json"
STATE_PATH = HERE / "tools" / "json" / "new_release_progression_state.json"
LOCKS_DIR = HERE / "tools" / "locks" / "new_release_progression"
OUT_DIR = HERE / "tools" / "cards" / "new_release_progression"

DEBUT_WINDOW_HOURS = float(os.getenv("APPLE_MUSIC_DEBUT_WINDOW_HOURS", "72"))
KEY_COUNTRIES = [
    c.strip().lower()
    for c in os.getenv("APPLE_MUSIC_DEBUT_KEY_COUNTRIES", "us,gb,fr,ca,au").split(",")
    if c.strip()
]
TWITTER_SESSION = (
    REPO_ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "twitter_session.json"
)
POST_PRIORITY = int(os.getenv("APPLE_MUSIC_DEBUT_POST_PRIORITY", "1"))
# Runs inside the hourly .bat: waiting the default 30 min for the shared
# @swiftiescharts slot (x4 tracks) would push the cycle past the next trigger.
POST_SLOT_TIMEOUT = int(os.getenv("APPLE_MUSIC_DEBUT_POST_SLOT_TIMEOUT", "180"))
FRONTEND_PUBLIC = REPO_ROOT.parent / "tsm-frontend" / "frontend" / "public"

# Apple Music (streaming) and iTunes (purchases) are separate charts with
# separate site pages — decision 2026-09-24: one card + one post per
# PLATFORM per track per cycle, never both mixed in the same table.
PLATFORMS = {
    # No per-platform color: the card accent comes from the cover (owner
    # 2026-09-24, see _cover_accent).
    "apple_music": {"label": "Apple Music", "site_source": "applemusic",
                    "footer": "Apple Music Charts"},
    "itunes": {"label": "iTunes", "site_source": "itunes",
               "footer": "iTunes Store Charts"},
}


def _album_cover_url(album: str, fallback: str) -> str:
    """covers.json album cover first (holds the current edition's art, e.g.
    The Encore cover for "The Life of a Showgirl"), per-track image_url as
    fallback — per-track URLs in the catalog still point at the original
    standard-edition artwork."""
    try:
        cover = build_cover_map(COVERS_PATH).get(_album_norm(album), "")
    except Exception:
        cover = ""
    return cover if str(cover).startswith("http") else fallback


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    parser.add_argument("--window-hours", type=float, default=DEBUT_WINDOW_HOURS)
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print what would post, write nothing.")
    return parser.parse_args()


def _now_paris() -> datetime:
    try:
        return datetime.now(ZoneInfo("Europe/Paris"))
    except Exception:
        return datetime.now(timezone.utc)


def _parse_release_date(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def active_debut_tracks(now: datetime, window_hours: float) -> list[dict]:
    """Catalog tracks whose release_date is within the last `window_hours` of
    `now` (never in the future — an announced-but-unreleased section must
    stay silent, same rule as the "announced" banner on album update cards)."""
    active: list[dict] = []
    seen_keys: set[str] = set()
    for track in iter_catalog_tracks():
        released = _parse_release_date(track.get("release_date"))
        if released is None or released > now:
            continue
        hours_since = (now - released).total_seconds() / 3600.0
        if hours_since > window_hours:
            continue
        title = str(track.get("title") or track.get("base_title") or "").strip()
        if not title:
            continue
        key = song_name_key(track.get("base_title") or title)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        active.append(
            {
                "title": title,
                "key": key,
                "image_url": _album_cover_url(str(track.get("album") or ""), str(track.get("image_url") or "")),
                "album": str(track.get("album") or ""),
                "release_date": released,
            }
        )
    return active


def _read_today_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _latest_cycle_rows(rows: list[dict]) -> tuple[str, list[dict]]:
    """Rows from the most recent scraped_at present in `rows` (this cycle,
    whatever its exact timestamp — Apple Music and iTunes round independently
    so their scraped_at values don't always match to the second)."""
    if not rows:
        return "", []
    latest = max(str(r.get("scraped_at") or "") for r in rows)
    return latest, [r for r in rows if str(r.get("scraped_at") or "") == latest]


def _row_lookup(rows: list[dict], country: str | None = None) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for row in rows:
        if country is not None and str(row.get("country") or "").lower() != country:
            continue
        for key in song_key_candidates(row.get("song_name")):
            lookup.setdefault(key, row)
    return lookup


def build_sources(today: str) -> list[dict]:
    sources: list[dict] = [
        {
            "source_key": "am_global",
            "platform": "apple_music",
            "region": "global",
            "chart_label": "Global",
            "path": apple_music_daily_csv(today, "apple_music_global.csv"),
            "path_for": lambda d: apple_music_daily_csv(d, "apple_music_global.csv"),
            "row_filter": lambda r: str(r.get("chart_type") or "") == "global",
            "country": None,
        }
    ]
    for country in KEY_COUNTRIES:
        sources.append(
            {
                "source_key": f"am_{country}",
                "platform": "apple_music",
                "region": country,
                "chart_label": country_label(country),
                "path": apple_music_daily_csv(today, "apple_music_country_charts.csv"),
                "path_for": lambda d: apple_music_daily_csv(d, "apple_music_country_charts.csv"),
                "row_filter": None,
                "country": country,
            }
        )
    for country in KEY_COUNTRIES:
        sources.append(
            {
                "source_key": f"itunes_{country}",
                "platform": "itunes",
                "region": country,
                "chart_label": country_label(country),
                "path": itunes_daily_csv(today, "itunes_top_songs.csv"),
                "path_for": lambda d: itunes_daily_csv(d, "itunes_top_songs.csv"),
                "row_filter": None,
                "country": country,
            }
        )
    return sources


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _safe_slug(*parts: str) -> str:
    import re

    text = "_".join(parts)
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return text or "track"


def _file_data_uri(path: Path, mime: str) -> str:
    import base64

    try:
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return ""


def _remote_data_uri(url: str) -> str:
    try:
        from collectors.comp.img_fetch import fetch_data_uri

        return fetch_data_uri(url) or ""
    except Exception:
        return ""


_GLOBE_SVG = (
    '<svg class="region-flag region-flag-globe" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="9" />'
    '<path d="M3 12h18M12 3c2.5 2.5 3.8 5.7 3.8 9S14.5 18.5 12 21c-2.5-2.5-3.8-5.7-3.8-9S9.5 5.5 12 3z" /></svg>'
)


def _cover_accent(img_data_uri: str) -> str:
    """Accent sampled from the song/album cover — same readable-on-light
    extraction as the Spotify Charts cards (comp.chart_card._cover_palette:
    dominant saturated cover color, clamped so text stays legible on a light
    card). Falls back to its own default if the cover can't be decoded."""
    import base64

    try:
        from collectors.comp.chart_card import _cover_palette

        raw = base64.b64decode(img_data_uri.split(",", 1)[1]) if img_data_uri.startswith("data:") else b""
        return _cover_palette(raw)["accent"]
    except Exception:
        return "#f05e33"


def _region_flag_html(region: str) -> str:
    """Same as the site's components/RegionFlag.jsx: globe glyph for Global,
    flagcdn SVG otherwise (uk -> gb)."""
    key = (region or "").strip().lower()
    if key in {"global", "worldwide", "world", "ww"}:
        return _GLOBE_SVG
    iso = {"uk": "gb", "il": "ps"}.get(key, key)
    if len(iso) != 2:
        return ""
    uri = _remote_data_uri(f"https://flagcdn.com/{iso}.svg")
    return f'<img class="region-flag" src="{uri}" alt="" />' if uri else ""


def _format_clock(value: str) -> str:
    """Site's formatCollectorClock in "en" (e.g. "2:00 AM CEST"), rendered in
    Europe/Paris since a posted PNG has no visitor timezone."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Europe/Paris"))
        dt = dt.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        return ""
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'} {dt.tzname() or ''}".strip()


def _format_share_date(value: str) -> str:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return str(value or "")[:10]
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _rank_delta_html(placement: dict) -> str:
    """Site's PlacementTables cell: "(▲ 3)" / "(▼ 2)" / "(NEW)", nothing when
    unchanged (the site shows no marker for delta 0)."""
    if placement.get("is_new"):
        return '<span class="oct-rank-delta rank-new"> (NEW)</span>'
    delta = placement.get("delta")
    if not delta:
        return ""
    cls = "rank-up" if delta > 0 else "rank-down"
    arrow = "\u25b2" if delta > 0 else "\u25bc"
    return f'<span class="oct-rank-delta {cls}"> ({arrow} {abs(delta)})</span>'


def _peak_badge_html(placement: dict) -> str:
    if placement.get("is_new"):
        return '<span class="new-peak">NEW</span>'
    if placement.get("new_peak"):
        return '<span class="new-peak">NEW PEAK</span>'
    return ""


def _split_columns(placements: list[dict]) -> list[list[dict]]:
    """Port of splitPlacementsIntoColumns (pages/AppleMusic.jsx)."""
    n = len(placements)
    count = 4 if n >= 12 else 2 if n >= 6 else 1
    per = -(-n // count) if n else 1
    cols = [placements[i:i + per] for i in range(0, n, per)]
    return [c for c in cols if c]


def _placements_tables_html(placements: list[dict], first_col_header: str) -> str:
    tables = []
    for col in _split_columns(placements):
        rows = "".join(
            "<tr>"
            f'<td class="oct-country"><span class="oct-country-cell">{_region_flag_html(p["region"])}'
            f'{_html_escape(p["label"])}</span></td>'
            f'<td class="oct-rank">#{p["rank"]}{_rank_delta_html(p)}</td>'
            f'<td class="oct-peak">#{p["peak"]}'
            f'{_peak_badge_html(p)}</td>'
            "</tr>"
            for p in col
        )
        tables.append(
            '<table class="overall-country-table"><thead><tr>'
            f'<th class="oct-country">{first_col_header}</th><th class="oct-rank">Ranking</th><th class="oct-peak">Peak</th>'
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    return "".join(tables), len(tables)


def _html_escape(value: object) -> str:
    import html as html_lib

    return html_lib.escape(str(value or ""))


def _build_progression_card_html(
    *, track: dict, placements: list[dict], scraped_at: str, platform: str,
) -> str:
    """Card = the site's own share image for this track (2026-09-24, owner:
    "match the frontend's actual design"): a port of the `.overall-song-block`
    rendered by pages/AppleMusic.jsx (AppleMusicOverallBlock) / pages/ITunes.jsx
    as captured by ShareImageButton (`.is-share-image-capturing`): header
    (cover + title + album), platform brand row (logo + "Apple Music" /
    "iTunes") + "vs <previous cycle time>", Chart|Country / Ranking tables
    split in columns like splitPlacementsIntoColumns, share footer with
    @swiftiescharts (owner: replaces the site's "THE TAYLOR SWIFT MUSEUM : A
    Taylor Swift fan project" line) + the Swifties Charts logo mask (/logo.png recolored to --text). CSS values are
    copied from styles/Charts.css, calendar.css, ITunes.css, RegionFlag.css
    and the default :root tokens (variables.css) — keep in sync if the site
    card changes. Deltas are "since the last update" (this script's state)."""
    conf = PLATFORMS[platform]
    img_uri = _remote_data_uri(track.get("image_url") or "") or (track.get("image_url") or "")
    accent = _cover_accent(img_uri)
    cover_html = (
        f'<img class="overall-song-cover" src="{_html_escape(img_uri)}" alt="" />' if img_uri else ""
    )
    title = _html_escape(track["title"])
    album = _html_escape(display_title_for_album(track.get("album") or ""))
    logo_mask = _file_data_uri(FRONTEND_PUBLIC / "logo.png", "image/png")
    if platform == "apple_music":
        brand_logo = _file_data_uri(FRONTEND_PUBLIC / "icons" / "apple-music-logo.webp", "image/webp")
    else:
        # Official iTunes logo (Wikimedia Commons ITunes_logo.svg), kept next to
        # this script — the site has no iTunes logo asset (owner 2026-09-24:
        # iTunes header must mirror the Apple Music brand row).
        brand_logo = _file_data_uri(HERE / "itunes_logo.svg", "image/svg+xml")
    prev_times = [p["prev_scraped_at"] for p in placements if p.get("prev_scraped_at")]
    compared = _format_clock(max(prev_times)) if prev_times else ""
    compared_html = f'<span class="am-group-brand-compared-to">vs {_html_escape(compared)}</span>' if compared else ""
    # Header right (owner 2026-09-24): logo + platform name, then date + hour
    # of this cycle, then ALWAYS the 4 tallies (even at 0) — "X #1", "X top
    # 10", "X top 50", "X charting" — pill style from the site's iTunes
    # .itunes-overall-stat.
    ranks = [p["rank"] for p in placements]
    tallies = [
        (sum(1 for r in ranks if r == 1), "#1", True),
        (sum(1 for r in ranks if r <= 10), "top 10", False),
        (sum(1 for r in ranks if r <= 50), "top 50", False),
        (len(ranks), "charting", False),
    ]
    pills_html = "".join(
        f'<span class="itunes-overall-stat{" itunes-overall-stat-no1" if hl and n else ""}">{n} {label}</span>'
        for n, label, hl in tallies
    )
    when_html = (
        f'<span class="am-group-brand-when">{_html_escape(_format_share_date(scraped_at))}'
        f' &middot; {_html_escape(_format_clock(scraped_at))}</span>'
    )
    side_html = (
        '<div class="am-group-section-brand"><span class="am-group-brand-row">'
        f'<img src="{brand_logo}" class="am-group-brand-logo" alt="" /><span>{_html_escape(conf["label"])}</span></span>'
        f'{when_html}{compared_html}<div class="itunes-overall-stats">{pills_html}</div></div>'
    )

    if platform == "apple_music":
        # Site order: Global first, then countries, by rank.
        ordered = sorted(placements, key=lambda p: (0 if p["region"] == "global" else 1, p["rank"], p["label"]))
        first_col = "Chart"
        footer_date = f'{conf["footer"]} - {_format_share_date(scraped_at)} - {_html_escape(_format_clock(scraped_at))}'
    else:
        ordered = sorted(placements, key=lambda p: (p["rank"], p["label"]))
        first_col = "Country"
        footer_date = f'{conf["footer"]} - {_format_share_date(scraped_at)}'

    tables_html, col_count = _placements_tables_html(ordered, first_col)
    card_width = {1: 780, 2: 860}.get(col_count, 1280)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<style>
  :root {{
    --text:#101828; --muted:#6b7280; --line:rgba(16,24,40,.09);
    --surface-3:rgba(255,255,255,.99); --accent:{accent};
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #f9fcfa; font-family: Inter, system-ui, "Segoe UI", sans-serif; color: var(--text); }}
  .overall-song-block {{
    width: {card_width}px;
    background: #f9fcfa;
    border-radius: 1.1rem;
    border: 1px solid var(--line);
    box-shadow: 0 2px 16px rgba(0,0,0,0.07);
    padding: 1.1rem 1.2rem 0.9rem 1.2rem;
    display: flex; flex-direction: column;
  }}
  .overall-song-header {{
    display: flex; align-items: center; gap: 0.75rem;
    margin-bottom: 0.9rem; padding-bottom: 0.75rem;
    border-bottom: 1px solid var(--line); width: 100%;
  }}
  .overall-song-header-link {{ display: flex; align-items: center; gap: 1rem; flex: 1; min-width: 0; }}
  .overall-song-cover {{
    width: 52px; height: 52px; border-radius: 0.6rem; object-fit: cover;
    flex-shrink: 0; box-shadow: 0 1px 6px rgba(0,0,0,0.1);
  }}
  .overall-song-meta {{ min-width: 0; }}
  .overall-song-title {{
    font-size: 1.05rem; font-weight: 700; color: var(--text); margin-bottom: 0.15em;
    line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .overall-song-artist {{ font-size: 0.92rem; color: var(--muted); line-height: 1.2; }}
  .am-group-section-brand {{ display: flex; flex-direction: column; align-items: flex-end; gap: 2px; margin-left: auto; }}
  .am-group-brand-row {{
    display: flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 800;
    text-transform: uppercase; letter-spacing: .05em; color: var(--muted); white-space: nowrap;
  }}
  .am-group-brand-logo {{ width: 18px; height: 18px; border-radius: 4px; flex-shrink: 0; }}
  .am-group-brand-compared-to {{ font-size: 9px; font-weight: 600; color: var(--muted); opacity: .75; white-space: nowrap; }}
  .am-group-brand-when {{ font-size: 11px; font-weight: 700; color: var(--text); white-space: nowrap; }}
  .itunes-overall-stats {{ display: flex; flex-wrap: nowrap; align-items: center; justify-content: flex-end; gap: 6px; margin-top: 4px; }}
  .itunes-overall-stat {{
    font-size: 11px; font-weight: 700; color: var(--text); background: #eef1ef;
    border-radius: 999px; padding: 2px 9px; white-space: nowrap;
  }}
  .itunes-overall-stat-no1 {{ color: #fff; background: var(--accent); }}
  .am-overall-table-grid {{ display: grid; grid-template-columns: repeat({col_count}, minmax(0, 1fr)); gap: 0 1.2rem; }}
  .overall-country-table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  .overall-country-table thead tr {{ border-bottom: 1px solid var(--line); }}
  .overall-country-table th {{
    text-align: right; font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.04em; color: var(--muted); padding: 0 0.5rem 0.4rem 0.5rem;
  }}
  .overall-country-table th.oct-country {{ text-align: left; }}
  .overall-country-table td {{ padding: 0.35rem 0.5rem; vertical-align: middle; white-space: nowrap; text-align: right; }}
  .overall-country-table tbody tr:nth-child(even) {{ background: var(--surface-3); }}
  .oct-country {{ font-weight: 600; color: var(--accent); text-align: left !important; min-width: 64px; }}
  .oct-country-cell {{ display: inline-flex; align-items: center; gap: 7px; }}
  .oct-rank {{ font-weight: 700; color: var(--text); }}
  .oct-peak {{ font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; width: 150px; }}
  .new-peak {{
    margin-left: 6px; padding: 1px 7px; border-radius: 999px; background: var(--accent); color: #fff;
    font-size: 0.68rem; font-weight: 800; letter-spacing: .04em; vertical-align: 1px;
  }}
  th.oct-peak {{ color: var(--muted); }}
  .oct-rank-delta {{ font-size: 0.85em; font-weight: 400; }}
  .oct-rank-delta.rank-up {{ color: #2a9d5c; }}
  .oct-rank-delta.rank-down {{ color: #e05c3a; }}
  .oct-rank-delta.rank-new {{ color: var(--muted); font-weight: 700; }}
  .region-flag {{
    display: inline-block; width: 20px; height: 15px; flex-shrink: 0; object-fit: cover;
    border-radius: 2px; border: 1px solid rgba(16,24,40,.07);
    box-shadow: 0 1px 2px rgba(0,0,0,0.12); vertical-align: -2px;
  }}
  .region-flag-globe {{ width: 15px; height: 15px; color: var(--muted); border: none; box-shadow: none; border-radius: 0; }}
  .am-share-card-footer {{
    margin-top: 16px; padding-top: 12px; border-top: 1px solid rgba(16,24,40,.07);
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: 0.02em; text-transform: uppercase;
  }}
  .am-share-card-date {{ flex: 0 0 auto; color: var(--text); }}
  .am-share-card-brand {{ display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 0; text-align: right; }}
  .brand-logo {{
    display: inline-block; background-color: var(--text);
    -webkit-mask-image: url("{logo_mask}"); mask-image: url("{logo_mask}");
    -webkit-mask-repeat: no-repeat; mask-repeat: no-repeat;
    -webkit-mask-position: center; mask-position: center;
    -webkit-mask-size: contain; mask-size: contain;
  }}
  .am-share-card-brand-logo {{ width: 26px; height: 26px; flex: 0 0 auto; }}
</style>
</head>
<body>
<div class="overall-song-block" id="card">
  <div class="overall-song-header">
    <div class="overall-song-header-link">
      {cover_html}
      <div class="overall-song-meta">
        <div class="overall-song-title">{title}</div>
        <div class="overall-song-artist">{album}</div>
      </div>
    </div>
    {side_html}
  </div>
  <div class="am-overall-table-grid">{tables_html}</div>
  <div class="am-share-card-footer">
    <div class="am-share-card-date">{footer_date}</div>
    <div class="am-share-card-brand">
      <span>@swiftiescharts</span>
      <span class="brand-logo am-share-card-brand-logo" role="img" aria-label="Swifties Charts"></span>
      <span>thetsmuseum.app</span>
    </div>
  </div>
</div>
</body>
</html>"""


def _render_card_png(html_content: str, out_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": 1400, "height": 400}, device_scale_factor=3)
        page.set_content(html_content, wait_until="load")
        card = page.locator("#card")
        card.wait_for(state="visible", timeout=5000)
        card.screenshot(path=str(out_path))
        browser.close()


def _peak_since_release(
    track: dict, source: dict, cache: dict[Path, list[dict]], today: str, exclude_scraped_at: str = "",
) -> int | None:
    """Best (lowest) rank this track has had on this chart across EVERY
    collected cycle since its release day (all rows of each daily CSV, not
    just the latest cycle) — real history only, never inferred. Rows of
    `exclude_scraped_at` (the current cycle) are skipped, so the result is
    the peak BEFORE this cycle (used to detect a NEW PEAK)."""
    released = track.get("release_date")
    try:
        start = released.astimezone(ZoneInfo("Europe/Paris")).date() if released else None
    except Exception:
        start = None
    end = datetime.strptime(today, "%Y-%m-%d").date()
    if start is None or start > end:
        start = end
    keys = set(song_key_candidates(track["title"]))
    best: int | None = None
    day = start
    while day <= end:
        path = source["path_for"](day.strftime("%Y-%m-%d"))
        if path not in cache:
            cache[path] = _read_today_rows(path)
        for row in cache[path]:
            if source["row_filter"] and not source["row_filter"](row):
                continue
            if exclude_scraped_at and str(row.get("scraped_at") or "") == exclude_scraped_at:
                continue
            if source["country"] is not None and str(row.get("country") or "").lower() != source["country"]:
                continue
            if not keys.intersection(song_key_candidates(row.get("song_name"))):
                continue
            try:
                r = int(str(row.get("rank") or "").strip())
            except ValueError:
                continue
            if best is None or r < best:
                best = r
        day += timedelta(days=1)
    return best


def _collect_track_placements(track: dict, sources: list[dict], cache: dict[Path, list[dict]], state: dict, today: str) -> list[dict]:
    """One entry per chart/source this track currently appears on, each
    carrying its own delta vs this script's state (not the CSV's own
    `previous_rank`, which means "vs yesterday" for Global and "vs last
    cycle" for country/iTunes — inconsistent for what we need here)."""
    placements: list[dict] = []
    for source in sources:
        path = source["path"]
        if path not in cache:
            cache[path] = _read_today_rows(path)
        rows = cache[path]
        if source["row_filter"]:
            rows = [r for r in rows if source["row_filter"](r)]
        scraped_at, latest_rows = _latest_cycle_rows(rows)
        if not scraped_at:
            continue
        lookup = _row_lookup(latest_rows, country=source["country"])

        row = None
        for key in song_key_candidates(track["title"]):
            if key in lookup:
                row = lookup[key]
                break
        if row is None:
            continue
        try:
            rank = int(str(row.get("rank") or "").strip())
        except ValueError:
            continue

        state_key = f"{track['key']}|{source['source_key']}"
        prev = state.get(state_key)
        prev_rank = prev.get("rank") if prev else None
        prev_scraped_at = prev.get("scraped_at") if prev else None
        is_new_observation = prev_scraped_at != scraped_at
        delta = (prev_rank - rank) if prev_rank is not None else None
        # Peak before this cycle (real CSV history + state), then this cycle.
        prior = []
        csv_peak = _peak_since_release(track, source, cache, today, exclude_scraped_at=scraped_at)
        if csv_peak is not None:
            prior.append(csv_peak)
        if prev and prev.get("peak") and prev_scraped_at != scraped_at:
            prior.append(int(prev["peak"]))
        prior_peak = min(prior) if prior else None
        peak = min([rank] + prior)
        # First run on this chart = no earlier rank anywhere (state or CSV):
        # badge "NEW", never "NEW PEAK" (owner 2026-09-24). NEW PEAK only when
        # a later run beats the existing peak.
        is_first_run = prev_rank is None and prior_peak is None
        new_peak = (not is_first_run) and prior_peak is not None and rank < prior_peak
        placements.append(
            {
                "label": source["chart_label"],
                "platform": source["platform"],
                "region": source["region"],
                "prev_scraped_at": prev_scraped_at if is_new_observation else None,
                "rank": rank,
                "peak": peak,
                "new_peak": new_peak,
                "is_new": is_first_run,
                "delta": delta,
                "moved": is_new_observation and (prev_rank is None or prev_rank != rank),
                "state_key": state_key,
                "scraped_at": scraped_at,
            }
        )
    return placements


def _build_card_and_tweet(*, track: dict, placements: list[dict], max_scraped_at: str, platform: str) -> tuple[str, Path]:
    ordered = sorted(placements, key=lambda p: p["rank"])
    html_content = _build_progression_card_html(
        track=track, placements=placements, scraped_at=max_scraped_at, platform=platform,
    )

    slug = _safe_slug(track["key"], platform, max_scraped_at)
    out_path = OUT_DIR / f"{slug}.png"
    _render_card_png(html_content, out_path)

    platform_label = PLATFORMS[platform]["label"]
    moved = [p for p in ordered if p["moved"]]
    debuts = [p for p in moved if p["is_new"]]
    if debuts and len(debuts) == len(placements):
        headline = f'debuts on {len(placements)} {platform_label} chart{"s" if len(placements) != 1 else ""}'
    elif len(moved) == 1:
        p = moved[0]
        if p["is_new"]:
            headline = f'debuts at #{p["rank"]} on {platform_label} {p["label"]}'
        else:
            direction = "climbs to" if p["delta"] > 0 else "falls to"
            headline = f'{direction} #{p["rank"]} on {platform_label} {p["label"]}'
    else:
        headline = f'moves on {len(moved)} {platform_label} charts'

    tweet = f'🎧 | "{track["title"]}" {headline} — full chart breakdown below.'
    from collectors.twitter.links import amcharts_url

    tweet += f"\n\n{amcharts_url(PLATFORMS[platform]['site_source'])}"
    return tweet, out_path


def _album_snapshot_post(album: str, today: str, state: dict) -> dict | None:
    """Album-filtered Global Apple Music snapshot (generate_snapshot_images
    --album) posted alongside the per-track cards while an album has a track
    in its debut window (owner 2026-09-24). Global only really moves ~once a
    day while this runs hourly -> returns something only when the album's
    Global standings differ from the last ones posted (never the same image
    twice). None = nothing new."""
    import generate_snapshot_images as snap

    try:
        album_name, album_keys = snap._resolve_album_filter(album)
    except ValueError as exc:
        print(f"[new_release_progression] WARN: album snapshot skipped for {album!r}: {exc}")
        return None
    rows, scraped_at = snap.get_region_rows(today, "global", None)
    rows = snap._filter_album(rows, album_keys)
    if not rows or not scraped_at:
        return None
    signature = {str(r.get("song_name") or ""): snap._rank_int(r.get("rank")) for r in rows}
    state_key = f"album_snapshot|{song_name_key(album_name)}|global"
    if (state.get(state_key) or {}).get("ranks") == signature:
        return None
    return {
        "album": album_name,
        "album_keys": album_keys,
        "scraped_at": scraped_at,
        "signature": signature,
        "state_key": state_key,
        "count": len(rows),
    }


def main() -> None:
    args = parse_args()
    now = _now_paris()
    today = now.strftime("%Y-%m-%d")

    active_tracks = active_debut_tracks(now, args.window_hours)
    if not active_tracks:
        print("[new_release_progression] No track currently inside its debut window — nothing to do.")
        return

    print(f"[new_release_progression] {len(active_tracks)} track(s) in window: " + ", ".join(t["title"] for t in active_tracks))

    state = load_state()
    dirty = False

    sources = build_sources(today)
    cache: dict[Path, list[dict]] = {}

    if not args.dry_run and not args.no_post:
        from collectors.spotify.core.twitter import post_with_image

    for track in active_tracks:
        all_placements = _collect_track_placements(track, sources, cache, state, today)
        if not all_placements:
            continue

        for p in all_placements:
            state[p["state_key"]] = {"rank": p["rank"], "peak": p["peak"], "scraped_at": p["scraped_at"], "title": track["title"]}
        dirty = True

        for platform in PLATFORMS:
            placements = [p for p in all_placements if p["platform"] == platform]
            if not placements or not any(p["moved"] for p in placements):
                continue  # nothing new on this platform this cycle (decision 2026-09-24)

            max_scraped_at = max(p["scraped_at"] for p in placements)
            lock_path = LOCKS_DIR / f"{_safe_slug(track['key'], platform, max_scraped_at)}.lock"
            if lock_path.exists():
                continue

            moved_labels = ", ".join(p["label"] for p in placements if p["moved"])
            print(f"[new_release_progression] {track['title']} [{PLATFORMS[platform]['label']}]: moved on {moved_labels}")

            if args.dry_run:
                continue

            tweet, image_path = _build_card_and_tweet(
                track=track, placements=placements, max_scraped_at=max_scraped_at, platform=platform,
            )
            save_state(state)  # persist before posting: a crash mid-post must not reprocess this cycle

            if args.no_post:
                print(f"[new_release_progression] --no-post: {tweet}")
                continue

            LOCKS_DIR.mkdir(parents=True, exist_ok=True)
            try:
                ok = post_with_image(
                    tweet, image_path, TWITTER_SESSION,
                    priority=POST_PRIORITY, skip_if=lambda: lock_path.exists(),
                    slot_timeout=POST_SLOT_TIMEOUT,
                )
            except TimeoutError as exc:
                print(f"[new_release_progression] WARN: X slot busy, skipped: {exc}")
                ok = False
            if ok:
                lock_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
            else:
                print(f"[new_release_progression] WARN: post failed for {track['title']} [{platform}]")

    for album in sorted({t["album"] for t in active_tracks if t["album"]}):
        pending = _album_snapshot_post(album, today, state)
        if not pending:
            continue
        lock_path = LOCKS_DIR / f"{_safe_slug('album_snapshot', pending['album'], pending['scraped_at'])}.lock"
        if lock_path.exists():
            continue
        print(f"[new_release_progression] {pending['album']} [Global album snapshot]: standings changed ({pending['count']} song(s))")
        if args.dry_run:
            continue

        import generate_snapshot_images as snap

        rendered = snap.generate(
            today, "global", None, OUT_DIR, album=(pending["album"], pending["album_keys"]),
        )
        # one PNG per cycle, like the per-track cards (generate() names by album only)
        image_path = rendered.replace(OUT_DIR / f"{_safe_slug('album_snapshot', pending['album'], pending['scraped_at'])}.png")
        # Recorded only once posted (or --no-post): a busy X slot must retry
        # next cycle instead of silently dropping this album card.
        posted_state = {"ranks": pending["signature"], "scraped_at": pending["scraped_at"]}

        from collectors.twitter.links import amcharts_url

        tweet = (
            f'🎧 | "{display_title_for_album(pending["album"])}" songs on the Global Apple Music chart '
            f'({snap._format_snapshot_time(pending["scraped_at"])}).'
            f"\n\n{amcharts_url('applemusic')}"
        )
        if args.no_post:
            print(f"[new_release_progression] --no-post: {tweet}")
            state[pending["state_key"]] = posted_state
            dirty = True
            continue

        LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            ok = post_with_image(
                tweet, image_path, TWITTER_SESSION,
                priority=POST_PRIORITY, skip_if=lambda: lock_path.exists(),
                slot_timeout=POST_SLOT_TIMEOUT,
            )
        except TimeoutError as exc:
            print(f"[new_release_progression] WARN: X slot busy, skipped: {exc}")
            ok = False
        if ok:
            lock_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
            state[pending["state_key"]] = posted_state
            dirty = True
            save_state(state)
        else:
            print(f"[new_release_progression] WARN: album snapshot post failed for {pending['album']}")

    if dirty:
        save_state(state)


if __name__ == "__main__":
    main()
