#!/usr/bin/env python3
"""Post independent X updates for recent Spotify releases.

Detects tracks whose Spotify API release_date is within the release update
window. Different songs get standalone posts; multiple versions of the same
song share one post.

Usage:
  python post_debut_releases.py 2026-06-01
  python post_debut_releases.py 2026-06-01 --no-post
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
REPO_ROOT = SCRIPT_DIR.parents[4]
DB_DIR = REPO_ROOT / "db"
ALBUMS_DIR = DB_DIR / "discography" / "albums"
SONGS_PATH = DB_DIR / "discography" / "songs.json"
HANDLE = "@swiftiescharts"
SITE_SETTINGS_KEY = "site_settings.json"
DEFAULT_THEME_MODE = "theme-showgirl"
FRONTEND_THEMES_CSS = REPO_ROOT.parent / "tsm-frontend" / "frontend" / "src" / "styles" / "themes.css"

sys.path.insert(0, str(REPO_ROOT / "collectors"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(REPO_ROOT))
from core.data_paths import first_existing_db_history, update_streams_dir  # noqa: E402
from core.twitter import post_image_thread, post_with_image  # noqa: E402
from collectors.comp import tables_image  # noqa: E402
from twitter.links import streams_latest_url  # noqa: E402
from twitter.sessions import default_twitter_session  # noqa: E402
TWITTER_SESSION = default_twitter_session(REPO_ROOT)

import generate_streams_image  # noqa: E402
import generate_weekend_streams_image  # noqa: E402
import spotlight  # noqa: E402

HISTORY_PATH = first_existing_db_history("streams_history.csv")


THEME_FALLBACKS = {
    "theme-showgirl": {"accent": "#ff6b35", "accent_2": "#cc5228", "text": "#172033", "muted": "#647089"},
    "theme-taylor-swift": {"accent": "#5a9e74", "accent_2": "#3d7a57", "text": "#1a3d2b", "muted": "#5a7d6a"},
    "theme-fearless": {"accent": "#d4a017", "accent_2": "#a07810", "text": "#4d3d1e", "muted": "#8a7040"},
    "theme-speak-now": {"accent": "#8a5bb5", "accent_2": "#6d3d9e", "text": "#3d1f5e", "muted": "#7a5a90"},
    "theme-red": {"accent": "#b91c1c", "accent_2": "#991414", "text": "#3d0a0a", "muted": "#8a4545"},
    "theme-1989": {"accent": "#4aace7", "accent_2": "#2d8fc8", "text": "#1a3545", "muted": "#5a8da8"},
    "theme-reputation": {"accent": "#ffffff", "accent_2": "#cccccc", "text": "#eeeeee", "muted": "#888888"},
    "theme-lover": {"accent": "#e8709a", "accent_2": "#c4507a", "text": "#5e2d44", "muted": "#a05075"},
    "theme-folklore": {"accent": "#6b6b6b", "accent_2": "#4a4a4a", "text": "#2a2a2a", "muted": "#6b6b6b"},
    "theme-evermore": {"accent": "#9b6b3d", "accent_2": "#7a5230", "text": "#3e2723", "muted": "#8a6550"},
    "theme-midnights": {"accent": "#6366f1", "accent_2": "#818cf8", "text": "#e2e8f0", "muted": "#94a3b8"},
    "theme-ttpd": {"accent": "#d4cfc9", "accent_2": "#b8b0a8", "text": "#e6edf3", "muted": "#8b949e"},
    "theme-tayindependance": {"accent": "#8796d8", "accent_2": "#5f6fb5", "text": "#172033", "muted": "#647089"},
    "theme-taystory": {"accent": "#173f74", "accent_2": "#f2b520", "text": "#173f74", "muted": "#7b604d"},
}


def _track_id(url: str | None) -> str | None:
    if not url or "/track/" not in url:
        return None
    return url.split("/track/", 1)[1].split("?", 1)[0].split("/", 1)[0].strip() or None


VERSION_SUFFIX_PATTERNS = (
    r"\s+-\s+track by track.*$",
    r"\s+-\s+voice memo.*$",
    r"\s+-\s+songwriting voice memo.*$",
    r"\s+-\s+demo.*$",
    r"\s+-\s+commentary.*$",
    r"\s+-\s+karaoke.*$",
    r"\s+-\s+instrumental.*$",
    r"\s+-\s+instrumental\s+(?:with|w/).*$",
    r"\s+-\s+.*\binstrumental\b.*$",
    r"\s+-\s+.*\bacoustic\b.*$",
    r"\s+-\s+.*\blive\b.*$",
    r"\s+-\s+.*\bremix\b.*$",
    r"\s+-\s+.*\bversion\b.*$",
    r"\s+-\s+.*\bedit\b.*$",
    r"\s+-\s+.*\bmix\b.*$",
    r"\s+-\s+from .*$",
)

VERSION_PAREN_PATTERNS = (
    r"\s+\((?:[^)]*\btrack by track\b[^)]*)\)",
    r"\s+\((?:[^)]*\bvoice memo\b[^)]*)\)",
    r"\s+\((?:[^)]*\bdemo\b[^)]*)\)",
    r"\s+\((?:[^)]*\bcommentary\b[^)]*)\)",
    r"\s+\((?:[^)]*\bkaraoke\b[^)]*)\)",
    r"\s+\((?:[^)]*\binstrumental\b[^)]*)\)",
    r"\s+\((?:[^)]*\bacoustic\b[^)]*)\)",
    r"\s+\((?:[^)]*\blive\b[^)]*)\)",
    r"\s+\((?:[^)]*\bremix\b[^)]*)\)",
    r"\s+\((?:[^)]*\bversion\b[^)]*)\)",
    r"\s+\((?:[^)]*\bedit\b[^)]*)\)",
    r"\s+\((?:[^)]*\bmix\b[^)]*)\)",
    r"\s+\((?:from [^)]*)\)",
)

VERSION_BRACKET_PATTERNS = (
    r"\s+\[(?:[^\]]*\btrack by track\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\bvoice memo\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\bdemo\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\bcommentary\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\bkaraoke\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\binstrumental\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\bacoustic\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\blive\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\bremix\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\bversion\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\bedit\b[^\]]*)\]",
    r"\s+\[(?:[^\]]*\bmix\b[^\]]*)\]",
    r"\s+\[(?:from [^\]]*)\]",
)


def _clean_base_title(title: str) -> str:
    original = (title or "").strip()
    value = re.sub(r"\s+", " ", original).strip()
    for pattern in VERSION_PAREN_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.I)
    for pattern in VERSION_BRACKET_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.I)
    for pattern in VERSION_SUFFIX_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" -")
    return value or original


def _family_key(item: dict, track_id: str) -> str:
    value = item.get("base_title") or item.get("title") or item.get("song_family") or track_id
    return re.sub(r"[^a-z0-9]+", "_", _clean_base_title(str(value)).casefold()).strip("_") or track_id


def _version_label(title: str, base_title: str) -> str:
    title = (title or "").strip()
    base = (base_title or "").strip()
    if not title:
        return "Version"

    if base and title.casefold() == base.casefold():
        return "Original"

    checks = (
        ("Taylor's Version", r"taylor'?s version"),
        ("Voice Memo", r"voice memo"),
        ("Karaoke Version", r"karaoke"),
        ("Instrumental", r"instrumental"),
        ("Acoustic Version", r"acoustic"),
        ("Live Version", r"\blive\b"),
        ("Remix", r"remix"),
        ("Demo", r"\bdemo\b"),
        ("Commentary", r"commentary"),
    )
    for label, pattern in checks:
        if re.search(pattern, title, flags=re.I):
            return label

    if base and title.casefold().startswith(base.casefold()):
        suffix = title[len(base):].strip(" -()[]")
        if suffix:
            return suffix

    return title


def _fmt(n: int | None) -> str:
    return f"{int(n or 0):,}"


def _fmt_signed(n: int | None) -> str:
    value = int(n or 0)
    if value > 0:
        return f"+{_fmt(value)}"
    if value < 0:
        return f"-{_fmt(abs(value))}"
    return "0"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "-"
    text = f"{pct:+.1f}%"
    return "+0.0%" if text == "-0.0%" else text


def _date_label(iso_day: str) -> str:
    parsed = date.fromisoformat(iso_day)
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def _normalize_account_id(raw_value: str) -> str:
    value = (raw_value or "").strip()
    value = re.sub(r"^https?://", "", value, flags=re.I).split("/")[0]
    return value[: -len(".r2.cloudflarestorage.com")] if value.endswith(".r2.cloudflarestorage.com") else value


def _active_theme_from_r2() -> str:
    account_id = _normalize_account_id(os.getenv("R2_APP_ACCOUNT_ID") or os.getenv("R2_ACCOUNT_ID") or "")
    access_key = (os.getenv("R2_APP_ACCESS_KEY_ID") or os.getenv("R2_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.getenv("R2_APP_SECRET_ACCESS_KEY") or os.getenv("R2_SECRET_ACCESS_KEY") or "").strip()
    bucket = (os.getenv("R2_APP_BUCKET") or os.getenv("R2_BUCKET") or "").strip()
    if not all([account_id, access_key, secret_key, bucket]):
        return DEFAULT_THEME_MODE

    try:
        import boto3  # noqa: PLC0415

        client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        response = client.get_object(Bucket=bucket, Key=SITE_SETTINGS_KEY)
        payload = json.loads(response["Body"].read().decode("utf-8"))
        theme = str(payload.get("default_theme") or "").strip()
        return theme if theme in THEME_FALLBACKS else DEFAULT_THEME_MODE
    except Exception as exc:
        print(f"[debut_releases] Active theme fallback: {exc}")
        return DEFAULT_THEME_MODE


def _theme_from_css(theme_mode: str) -> dict:
    theme = dict(THEME_FALLBACKS.get(theme_mode) or THEME_FALLBACKS[DEFAULT_THEME_MODE])
    if not FRONTEND_THEMES_CSS.exists():
        return theme

    try:
        css = FRONTEND_THEMES_CSS.read_text(encoding="utf-8")
    except Exception:
        return theme

    match = re.search(rf'body\[data-theme="{re.escape(theme_mode)}"\]\s*\{{(?P<body>.*?)\}}', css, flags=re.S)
    if not match:
        return theme

    key_map = {
        "--accent": "accent",
        "--accent-2": "accent_2",
        "--text": "text",
        "--muted": "muted",
    }
    for css_key, out_key in key_map.items():
        value_match = re.search(rf"{re.escape(css_key)}\s*:\s*(#[0-9a-fA-F]{{6}})", match.group("body"))
        if value_match:
            theme[out_key] = value_match.group(1)
    return theme


def _active_theme() -> dict:
    mode = _active_theme_from_r2()
    theme = _theme_from_css(mode)
    theme["mode"] = mode
    return theme


def _remember_track(
    meta: dict[str, dict],
    track_id: str,
    *,
    title: str,
    album: str,
    release_date: str | None,
    image_url: str | None,
) -> None:
    item = meta.setdefault(
        track_id,
        {
            "title": title,
            "album": album,
            "release_date": None,
            "image_url": None,
            "song_family": None,
            "base_title": None,
        },
    )
    if item.get("title") in (None, ""):
        item["title"] = title
    if item.get("album") in (None, ""):
        item["album"] = album
    if item.get("release_date") in (None, "") and release_date:
        item["release_date"] = release_date
    if item.get("image_url") in (None, "") and image_url:
        item["image_url"] = image_url


def _remember_family(meta: dict[str, dict], track_id: str, track: dict) -> None:
    item = meta.setdefault(track_id, {})
    if item.get("song_family") in (None, "") and track.get("song_family"):
        item["song_family"] = track.get("song_family")
    if item.get("base_title") in (None, "") and track.get("base_title"):
        item["base_title"] = track.get("base_title")


def _load_album_tracks() -> tuple[dict[str, list[str]], dict[str, dict]]:
    album_tracks: dict[str, list[str]] = defaultdict(list)
    meta: dict[str, dict] = {}

    if not ALBUMS_DIR.exists():
        return {}, {}

    for path in sorted(ALBUMS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        album = payload.get("album") if isinstance(payload, dict) else None
        if not album:
            continue
        for section in payload.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for track in section.get("tracks") or []:
                if not isinstance(track, dict):
                    continue
                tid = _track_id(track.get("url") or track.get("spotify_url"))
                title = (track.get("title") or "").strip()
                if not tid or not title:
                    continue
                if tid not in album_tracks[album]:
                    album_tracks[album].append(tid)
                _remember_track(
                    meta,
                    tid,
                    title=title,
                    album=album,
                    release_date=track.get("release_date") or None,
                    image_url=track.get("image_url") or None,
                )
                _remember_family(meta, tid, track)

    return dict(album_tracks), meta


def _load_misc_tracks(meta: dict[str, dict]) -> None:
    if not SONGS_PATH.exists():
        return
    try:
        sections = json.loads(SONGS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return
    for section in sections if isinstance(sections, list) else []:
        album = (section.get("album") or section.get("section") or "").strip()
        for track in section.get("tracks") or []:
            tid = _track_id(track.get("url") or track.get("spotify_url"))
            title = (track.get("title") or "").strip()
            if tid and title:
                _remember_track(
                    meta,
                    tid,
                    title=title,
                    album=album,
                    release_date=track.get("release_date") or None,
                    image_url=track.get("image_url") or None,
                )
                _remember_family(meta, tid, track)


def _load_rows_for_date(target_date: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not HISTORY_PATH.exists():
        return rows

    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = (row.get("track_id") or "").strip()
            day = (row.get("date") or "").strip()
            if not tid or day != target_date:
                continue
            try:
                streams = int((row.get("streams") or "0").strip() or "0")
            except ValueError:
                streams = 0
            try:
                daily_streams = int((row.get("daily_streams") or "0").strip() or "0")
            except ValueError:
                daily_streams = 0
            rows[tid] = {"date": day, "streams": streams, "daily_streams": daily_streams}
    return rows


def _release_day(release_date: str | None) -> date | None:
    if not release_date:
        return None
    try:
        return date.fromisoformat(str(release_date)[:10])
    except Exception:
        return None


def _daily_streams_for_row(row: dict | None, previous_row: dict | None = None) -> int:
    if not row:
        return 0
    daily = int(row.get("daily_streams") or 0)
    if daily > 0:
        return daily
    streams = int(row.get("streams") or 0)
    previous_streams = int((previous_row or {}).get("streams") or 0)
    if streams > 0 and previous_streams > 0 and streams >= previous_streams:
        return streams - previous_streams
    return streams


def _load_first_history_dates() -> dict[str, str]:
    first_dates: dict[str, str] = {}
    if not HISTORY_PATH.exists():
        return first_dates

    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = (row.get("track_id") or "").strip()
            day = (row.get("date") or "").strip()
            if not tid or not day:
                continue
            try:
                streams = int((row.get("streams") or "0").strip() or "0")
            except ValueError:
                streams = 0
            if streams <= 0:
                continue
            if tid not in first_dates or day < first_dates[tid]:
                first_dates[tid] = day
    return first_dates


def _load_latest_positive_rows(track_ids: set[str]) -> dict[str, dict]:
    if not track_ids or not HISTORY_PATH.exists():
        return {}

    latest: dict[str, dict] = {}
    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = (row.get("track_id") or "").strip()
            if tid not in track_ids:
                continue
            day = (row.get("date") or "").strip()
            try:
                streams = int((row.get("streams") or "0").strip() or "0")
            except ValueError:
                streams = 0
            if not day or streams <= 0:
                continue
            if tid not in latest or day > latest[tid]["date"]:
                latest[tid] = {"date": day, "streams": streams}
    return latest


def _is_recent_release_for_debut(release_date: str | None, target_date: str, *, window_days: int = 7) -> bool:
    release_day = _release_day(release_date)
    if release_day is None:
        return False
    try:
        target_day = date.fromisoformat(target_date)
    except Exception:
        return False
    delta = (target_day - release_day).days
    return 0 <= delta <= window_days


def _selector_matches_track(selector: str, track_id: str, item: dict) -> bool:
    needle = (selector or "").strip()
    if not needle:
        return False
    if needle == track_id:
        return True
    folded = needle.casefold()
    title = str(item.get("title") or "").casefold()
    base_title = str(item.get("base_title") or "").casefold()
    cleaned = _clean_base_title(str(item.get("title") or "")).casefold()
    family = str(item.get("song_family") or "").casefold()
    return folded in {title, base_title, cleaned, family}


def _force_song_track_ids(meta: dict[str, dict], selectors: set[str] | None) -> set[str]:
    if not selectors:
        return set()

    selected_family_keys: set[str] = set()
    for selector in selectors:
        for track_id, item in meta.items():
            if _selector_matches_track(selector, track_id, item):
                selected_family_keys.add(_family_key(item, track_id))

    if not selected_family_keys:
        print(f"[debut_releases] --force-song matched no song for: {', '.join(sorted(selectors))}")
        return set()

    return {
        track_id
        for track_id, item in meta.items()
        if _family_key(item, track_id) in selected_family_keys
    }


DEBUT_CSS = """
body{color:var(--debut-text)}
.hdr{
  min-height:96px;
  padding:20px 30px;
  gap:18px;
}
.brand{
  flex:1 1 auto;
}
.hdr .hdr-logo{
  width:48px;
  height:48px;
}
.hdr-title{
  font-size:23px;
}
.hdr-date{
  color:rgba(255,255,255,.86);
  font-size:13px;
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.06em;
}
.debut-wrap{
  padding:22px 26px 0;
}
.debut-card{
  overflow:hidden;
  border-radius:20px;
  background:rgba(255,255,255,.88);
  border:1px solid rgba(16,24,40,.08);
  box-shadow:0 18px 48px rgba(16,24,40,.12);
}
.debut-card .section-title{
  padding:20px 24px;
  background:linear-gradient(90deg,var(--debut-accent-chip),rgba(248,250,251,.96));
}
.debut-card .section-title h2{
  font-size:21px;
}
.debut-card .section-title span{
  padding:7px 11px;
  border-radius:999px;
  background:rgba(255,255,255,.72);
}
.debut-main{
  display:grid;
  grid-template-columns:200px minmax(0,1fr) 390px;
  gap:26px;
  align-items:center;
  min-height:228px;
  padding:28px 28px 30px;
  overflow:hidden;
}
.debut-art{
  width:200px;
  height:200px;
  display:flex;
  align-items:center;
  justify-content:center;
  border-radius:18px;
  background:var(--debut-accent-chip);
  box-shadow:0 16px 34px rgba(16,24,40,.13);
}
.debut-cover,.debut-cover-ph{
  width:180px;
  height:180px;
  border-radius:16px;
}
.debut-cover{
  object-fit:cover;
  box-shadow:0 10px 24px rgba(0,0,0,.22);
}
.debut-cover-ph{
  background:#dde3ea;
}
.debut-kicker{
  display:inline-flex;
  padding:7px 10px;
  border-radius:999px;
  background:var(--debut-accent-chip);
  font-size:11px;
  font-weight:900;
  text-transform:uppercase;
  letter-spacing:.08em;
  color:var(--debut-muted);
}
.debut-title{
  margin-top:14px;
  font-size:34px;
  line-height:1.02;
  font-weight:950;
  color:var(--debut-text);
  letter-spacing:0;
}
.debut-sub{
  margin-top:12px;
  font-size:15px;
  color:var(--debut-muted);
  font-weight:800;
}
.debut-metric{
  display:grid;
  grid-template-columns:1fr;
  gap:12px;
}
.debut-stat{
  min-height:106px;
  padding:15px 16px;
  border-radius:16px;
  background:linear-gradient(180deg,rgba(255,255,255,.98),var(--debut-accent-chip));
  border:1px solid var(--debut-accent-line);
  box-shadow:0 14px 34px rgba(16,24,40,.10);
  text-align:right;
  display:flex;
  flex-direction:column;
  justify-content:center;
}
.debut-num{
  font-size:25px;
  line-height:1.08;
  font-weight:950;
  color:var(--debut-text);
  max-width:100%;
  overflow:hidden;
  font-variant-numeric:tabular-nums;
}
.debut-num.primary{
  font-size:29px;
}
.debut-num.pos{color:#067647}
.debut-num.neg{color:#b42318}
.debut-num.neutral{color:var(--debut-text)}
.debut-stat.wide{
  grid-column:auto;
}
.debut-inline-stats{
  display:flex;
  justify-content:flex-end;
  gap:18px;
  margin-top:10px;
}
.debut-inline-stat{
  text-align:right;
}
.debut-inline-num{
  font-size:17px;
  line-height:1.1;
  font-weight:950;
  font-variant-numeric:tabular-nums;
}
.debut-inline-num.pos{color:#067647}
.debut-inline-num.neg{color:#b42318}
.debut-inline-num.neutral{color:var(--debut-text)}
.debut-inline-label{
  margin-top:4px;
  font-size:9px;
  color:var(--debut-accent);
  font-weight:900;
  text-transform:uppercase;
  letter-spacing:.07em;
}
.debut-label{
  margin-top:7px;
  text-align:right;
  font-size:10px;
  color:var(--debut-accent);
  font-weight:900;
  text-transform:uppercase;
  letter-spacing:.07em;
}
.debut-versions{
  grid-column:1 / -1;
  margin-top:0;
  text-align:right;
  font-size:13px;
  color:var(--debut-muted);
  font-weight:850;
}
.version-block{
  margin:0 28px 24px;
  overflow:hidden;
  border-radius:16px;
  border:1px solid rgba(16,24,40,.07);
}
.version-head,.version-row{
  display:grid;
  grid-template-columns:52px minmax(0,1fr) 190px 190px;
  column-gap:14px;
  align-items:center;
}
.version-head{
  padding:11px 16px;
  background:rgba(248,250,251,.95);
}
.version-head span{
  font-size:10px;
  font-weight:850;
  text-transform:uppercase;
  letter-spacing:.07em;
  color:#667085;
}
.version-row{
  min-height:50px;
  padding:9px 16px;
  border-top:1px solid rgba(16,24,40,.05);
  background:rgba(255,255,255,.86);
}
.version-row:nth-child(odd){background:linear-gradient(90deg,rgba(248,250,251,.92),var(--debut-accent-row))}
.version-name{
  min-width:0;
  font-size:12.5px;
  font-weight:750;
  color:var(--debut-muted);
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.version-rank,.version-title,.version-streams{
  font-weight:900;
  color:var(--debut-text);
}
.version-rank{
  font-size:13px;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.version-title{
  min-width:0;
  font-size:13.5px;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.version-streams{
  text-align:right;
  font-size:13.5px;
  font-variant-numeric:tabular-nums;
}
.ftr{
  margin-top:22px;
}
"""


def _cover_data_uri(image_url: str | None) -> str:
    if not image_url:
        return ""
    return tables_image.url_to_data_uri(image_url) or image_url


def _cover_artwork_assets(image_url: str | None, fallback_accent: str) -> tuple[str, str, str]:
    """Return (data_uri, gradient_css, accent_hex) based on the debut cover."""
    if not image_url:
        return "", "", fallback_accent

    cover_uri, cover_bytes = spotlight._fetch_image(image_url)
    if not cover_uri:
        cover_uri = _cover_data_uri(image_url)
    if not cover_bytes:
        return cover_uri, "", fallback_accent

    gradient, accent = spotlight._cover_palette(cover_bytes)
    return cover_uri, gradient, accent or fallback_accent


def _rgba(hex_color: str, alpha: float) -> str:
    rgb = spotlight._hex_to_rgb(hex_color)
    if rgb is None:
        rgb = spotlight._hex_to_rgb("#1db954") or (29, 185, 84)
    r, g, b = rgb
    return f"rgba({r},{g},{b},{alpha:.3f})"


def _render_html_image(html_text: str, out_path: Path, tmp_name: str) -> Path:
    tmp_html = out_path.parent / tmp_name
    tmp_html.write_text(html_text, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1000, "height": 900}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()
    return out_path


def _build_debut_html(
    target_date: str,
    *,
    title: str,
    daily_streams: int,
    change_streams: int | None,
    change_pct: float | None,
    total_streams: int,
    image_url: str | None,
    versions: list[dict] | None = None,
    version_count: int = 1,
    show_version_details: bool = True,
) -> str:
    date_text = _date_label(target_date)
    theme = _active_theme()
    accent = theme["accent"]
    accent_2 = theme["accent_2"]
    text_color = theme["text"]
    muted_color = theme["muted"]
    cover_uri, cover_gradient, cover_accent = _cover_artwork_assets(image_url, accent)
    if cover_accent:
        accent = cover_accent
    cover_html = (
        f'<img class="debut-cover" src="{html.escape(cover_uri, quote=True)}" />'
        if cover_uri
        else '<div class="debut-cover-ph"></div>'
    )
    version_html = (
        f'<div class="debut-versions">Across {version_count} versions</div>'
        if version_count > 1
        else ""
    )
    change_class = "pos" if (change_streams or 0) > 0 else "neg" if (change_streams or 0) < 0 else "neutral"
    pct_class = "pos" if (change_pct or 0) > 0 else "neg" if (change_pct or 0) < 0 else "neutral"
    version_rows_html = ""
    if show_version_details and versions and len(versions) > 1:
        rows = []
        for idx, version in enumerate(versions, 1):
            row_label = str(version.get("label") or "").strip() or "Version"
            row_title = str(version.get("title") or "").strip() or title
            row_streams = int(version.get("streams") or 0)
            rows.append(
                f"""<div class="version-row">
  <div class="version-rank">#{idx}</div>
  <div class="version-title">{html.escape(row_title)}</div>
  <div class="version-name">{html.escape(row_label)}</div>
  <div class="version-streams">{_fmt(row_streams)}</div>
</div>"""
            )
        version_rows_html = f"""<div class="version-block">
  <div class="version-head">
    <span>Rank</span>
    <span>Track</span>
    <span>Version</span>
    <span class="right">Streams</span>
  </div>
  {''.join(rows)}
</div>"""
    theme_vars = generate_weekend_streams_image._theme_vars_from_color(accent)
    header_style = (
        f'style="background:{cover_gradient};"'
        if cover_gradient
        else f'style="background:linear-gradient(135deg,{accent} 0%,{accent_2} 100%);"'
    )
    extra_vars = (
        f"--debut-accent:{accent};"
        f"--debut-accent-2:{accent_2};"
        f"--debut-accent-wash:{_rgba(accent, 0.18)};"
        f"--debut-accent-chip:{_rgba(accent, 0.12)};"
        f"--debut-accent-row:{_rgba(accent, 0.08)};"
        f"--debut-accent-line:{_rgba(accent, 0.42)};"
        f"--debut-accent-sheen:{_rgba(accent, 0.24)};"
        f"--debut-text:{text_color};"
        f"--debut-muted:{muted_color};"
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{generate_weekend_streams_image.CSS}{DEBUT_CSS}</style></head>
<body style="{theme_vars}{extra_vars}">
<div class="container">
  <div class="hdr" {header_style}>
    <div class="brand">
      {generate_weekend_streams_image.SPOTIFY_SVG}
      <div>
        <div class="hdr-title">Taylor Swift · Spotify Counter</div>
      </div>
    </div>
    <div class="hdr-date">{html.escape(date_text)}</div>
  </div>
  <div class="debut-wrap">
    <div class="section debut-card">
      <div class="section-title">
        <h2>Song Update</h2>
        <span>Song</span>
      </div>
      <div class="debut-main">
        <div class="debut-art">{cover_html}</div>
        <div>
          <div class="debut-kicker">Spotify Counter</div>
          <div class="debut-title">{html.escape(title)}</div>
          <div class="debut-sub">Taylor Swift</div>
        </div>
        <div class="debut-metric">
          <div class="debut-stat wide">
            <div class="debut-num primary">{_fmt(daily_streams)}</div>
            <div class="debut-label">Daily streams</div>
            <div class="debut-inline-stats">
              <div class="debut-inline-stat">
                <div class="debut-inline-num {change_class}">{_fmt_signed(change_streams)}</div>
                <div class="debut-inline-label">Change</div>
              </div>
              <div class="debut-inline-stat">
                <div class="debut-inline-num {pct_class}">{_fmt_pct(change_pct)}</div>
                <div class="debut-inline-label">Vs previous day</div>
              </div>
            </div>
          </div>
          <div class="debut-stat wide">
            <div class="debut-num">{_fmt(total_streams)}</div>
            <div class="debut-label">Total streams</div>
          </div>
          {version_html}
        </div>
      </div>
      {version_rows_html}
    </div>
  </div>
  <div class="ftr">
    <span class="ftr-handle" style="color:{html.escape(accent)}">{HANDLE}</span>
    <span class="ftr-date">{html.escape(date_text)}</span>
  </div>
</div>
</body></html>"""


def _generate_debut_image(
    target_date: str,
    *,
    slug: str,
    title: str,
    kind: str,
    daily_streams: int,
    change_streams: int | None,
    change_pct: float | None,
    total_streams: int,
    image_url: str | None,
    versions: list[dict] | None = None,
    version_count: int = 1,
    show_version_details: bool = True,
) -> Path | None:
    out_dir = update_streams_dir(target_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_slug = "".join(ch if ch.isalnum() else "_" for ch in slug).strip("_").lower()
    out_path = out_dir / f"debut_{safe_slug}.png"
    for stale_path in out_dir.glob(f"debut_{safe_slug}_*.png"):
        try:
            stale_path.unlink()
        except OSError:
            pass
    html_text = _build_debut_html(
        target_date,
        title=title,
        daily_streams=daily_streams,
        change_streams=change_streams,
        change_pct=change_pct,
        total_streams=total_streams,
        image_url=image_url,
        versions=versions,
        version_count=version_count,
        show_version_details=show_version_details,
    )
    return _render_html_image(html_text, out_path, f"_{safe_slug}_debut_tmp.html")


def _build_post_threads(
    target_date: str,
    *,
    force_track_ids: set[str] | None = None,
    force_song_selectors: set[str] | None = None,
) -> list[list[tuple[str, str, Path | None]]]:
    album_tracks, meta = _load_album_tracks()
    _load_misc_tracks(meta)
    day_rows = _load_rows_for_date(target_date)
    previous_date = str(date.fromisoformat(target_date) - timedelta(days=1))
    previous_rows = _load_rows_for_date(previous_date)

    forced_selectors = set(force_song_selectors or set())
    forced_selectors.update(force_track_ids or set())
    forced_ids = _force_song_track_ids(meta, forced_selectors)
    if not forced_ids:
        forced_ids = set(force_track_ids or set())
    if forced_ids:
        latest_rows = _load_latest_positive_rows(forced_ids)
        for tid in forced_ids:
            if tid not in day_rows or int(day_rows.get(tid, {}).get("streams") or 0) <= 0:
                latest = latest_rows.get(tid)
                day_rows[tid] = {
                    "date": target_date,
                    "streams": int(latest["streams"]) if latest else 0,
                }
            if tid in meta:
                meta[tid]["release_date"] = target_date
    debut_ids = {
        tid for tid, item in meta.items()
        if tid in day_rows
        and _is_recent_release_for_debut(item.get("release_date"), target_date)
    }
    if not debut_ids:
        return []

    post_threads: list[list[tuple[str, str, Path | None]]] = []
    date_text = _date_label(target_date)

    groups: dict[str, list[str]] = defaultdict(list)
    for tid in debut_ids:
        item = meta.get(tid, {})
        group_key = _family_key(item, tid)
        groups[str(group_key)].append(tid)

    family_members: dict[str, list[str]] = defaultdict(list)
    for tid, item in meta.items():
        if tid not in day_rows:
            continue
        group_key = str(_family_key(item, tid))
        if group_key not in groups:
            continue
        if _daily_streams_for_row(day_rows.get(tid), previous_rows.get(tid)) <= 0:
            continue
        family_members[group_key].append(tid)

    for group_key, ids in sorted(groups.items(), key=lambda item: item[0].casefold()):
        ids = sorted(
            set(ids).union(family_members.get(group_key, [])),
            key=lambda tid: meta.get(tid, {}).get("title", tid).casefold(),
        )
        primary = meta.get(ids[0], {})
        title = _clean_base_title(primary.get("base_title") or primary.get("title") or ids[0])
        daily_streams = sum(_daily_streams_for_row(day_rows.get(tid), previous_rows.get(tid)) for tid in ids)
        previous_daily_streams = sum(_daily_streams_for_row(previous_rows.get(tid)) for tid in ids)
        total_streams = sum(int(day_rows[tid]["streams"] or 0) for tid in ids)
        change_streams = daily_streams - previous_daily_streams if previous_daily_streams > 0 else None
        change_pct = (change_streams / previous_daily_streams * 100) if change_streams is not None else None
        if daily_streams <= 0:
            print(f"[debut_releases] Skip {title}: daily streams is 0.")
            continue
        image_url = next((meta.get(tid, {}).get("image_url") for tid in ids if meta.get(tid, {}).get("image_url")), None)
        version_count = len(ids)
        versions = [
            {
                "label": _version_label(meta.get(tid, {}).get("title") or tid, title),
                "title": meta.get(tid, {}).get("title") or tid,
                "streams": _daily_streams_for_row(day_rows.get(tid), previous_rows.get(tid)),
            }
            for tid in sorted(
                ids,
                key=lambda tid: (
                    -_daily_streams_for_row(day_rows.get(tid), previous_rows.get(tid)),
                    str(meta.get(tid, {}).get("title") or tid).casefold(),
                ),
            )
        ]
        version_note = f" across {version_count} versions" if version_count > 1 else ""
        movement = ""
        if change_pct is not None:
            direction = "up" if change_streams and change_streams > 0 else "down" if change_streams and change_streams < 0 else "stable at"
            pct_text = _fmt_pct(abs(change_pct) if direction in {"up", "down"} else change_pct)
            if direction == "stable at":
                movement = f", {direction} {pct_text}"
            else:
                movement = f", {direction} {pct_text} vs the previous day"
        show_version_details = version_count > 1
        image_path = _generate_debut_image(
            target_date,
            slug=f"song_{group_key}_details" if show_version_details else f"song_{group_key}_total",
            title=title,
            kind="song",
            daily_streams=daily_streams,
            change_streams=change_streams,
            change_pct=change_pct,
            total_streams=total_streams,
            image_url=image_url,
            versions=versions,
            version_count=version_count,
            show_version_details=show_version_details,
        )
        thread_posts: list[tuple[str, str, Path | None]] = [(
            f"song:{group_key}:details" if show_version_details else f"song:{group_key}:total",
            (
                f'"{title}" received {_fmt(daily_streams)} streams on the Spotify Counter{version_note}{movement} ({date_text}).\n\n'
                f"See full update here : {streams_latest_url()}"
            ),
            image_path,
        )]

        post_threads.append(thread_posts)

    return post_threads


def _build_posts(
    target_date: str,
    *,
    force_track_ids: set[str] | None = None,
    force_song_selectors: set[str] | None = None,
) -> list[tuple[str, str, Path | None]]:
    return [
        post
        for thread in _build_post_threads(
            target_date,
            force_track_ids=force_track_ids,
            force_song_selectors=force_song_selectors,
        )
        for post in thread
    ]


def post_debut_releases(
    target_date: str,
    *,
    no_post: bool = False,
    force_track_ids: set[str] | None = None,
    force_song_selectors: set[str] | None = None,
    snapshot_collected_date: str | None = None,
) -> int:
    snapshot_collected_date = snapshot_collected_date or date.today().isoformat()
    day_dir = update_streams_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    lock_path = day_dir / "debut_releases_posted.json"
    already_posted: set[str] = set()
    if lock_path.exists():
        try:
            already_posted = set(json.loads(lock_path.read_text(encoding="utf-8")).get("posted", []))
        except Exception:
            already_posted = set()

    post_threads = []
    for thread in _build_post_threads(
            target_date,
            force_track_ids=force_track_ids,
            force_song_selectors=force_song_selectors,
    ):
        pending_thread = [
            (slug, text, image_path)
            for slug, text, image_path in thread
            if slug not in already_posted
        ]
        if pending_thread:
            post_threads.append(pending_thread)

    posts = [post for thread in post_threads for post in thread]
    if not posts:
        print(f"[debut_releases] No new debut release posts for {target_date}.")
        return 0

    for slug, text, image_path in posts:
        print(f"[debut_releases] {slug}: {text}")
        print(
            f"[debut_releases] snapshot_date={target_date} | "
            f"snapshot_collected_date={snapshot_collected_date}"
        )
        if image_path:
            print(f"[debut_releases] Image: {image_path}")

    if no_post:
        print("[debut_releases] Twitter posts skipped (--no-post).")
        return 0

    if not TWITTER_SESSION.exists():
        print(f"[debut_releases] Twitter session not found: {TWITTER_SESSION}")
        return 1

    posted = set(already_posted)
    for thread in post_threads:
        missing_image = next((slug for slug, _text, image_path in thread if image_path is None), None)
        if missing_image is not None:
            print(f"[debut_releases] Missing debut image for {missing_image}; aborting post.")
            return 1

        if len(thread) > 1:
            ok = post_image_thread([(text, image_path) for _slug, text, image_path in thread if image_path], TWITTER_SESSION)
        else:
            slug, text, image_path = thread[0]
            ok = bool(image_path and post_with_image(text, image_path, TWITTER_SESSION))

        if not ok:
            print(f"[debut_releases] Failed to post debut thread: {', '.join(slug for slug, _text, _image_path in thread)}.")
            return 1

        posted.update(slug for slug, _text, _image_path in thread)
        lock_path.write_text(
            json.dumps(
                {
                    "snapshot_date": target_date,
                    "snapshot_collected_date": snapshot_collected_date,
                    "posted": sorted(posted),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    print(f"[debut_releases] Posted {len(posts)} debut release(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument(
        "--snapshot-collected-date",
        default=date.today().isoformat(),
        help="Calendar date when the Spotify snapshot was collected (default: today).",
    )
    parser.add_argument(
        "--force-track-id",
        action="append",
        default=[],
        help="Test mode: force the full song family for this track ID. Can be repeated.",
    )
    parser.add_argument(
        "--force-song",
        action="append",
        default=[],
        help="Test mode: force every version in the same song family. Accepts a track ID or exact song title.",
    )
    args = parser.parse_args()
    return post_debut_releases(
        args.date,
        no_post=args.no_post,
        force_track_ids=set(args.force_track_id or []),
        force_song_selectors=set(args.force_song or []),
        snapshot_collected_date=args.snapshot_collected_date,
    )


if __name__ == "__main__":
    raise SystemExit(main())
