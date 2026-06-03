#!/usr/bin/env python3
"""Post independent X updates for Spotify debut releases.

Detects tracks whose Spotify API release_date is recent and whose first
positive stream snapshot is the target stats date. Different songs get
standalone posts; multiple versions of the same song share one post.

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
HISTORY_PATH = DB_DIR / "streams_history.csv"
ALBUMS_DIR = DB_DIR / "discography" / "albums"
SONGS_PATH = DB_DIR / "discography" / "songs.json"
TWITTER_SESSION = ROOT.parent / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json"
HANDLE = "@tsmuseum13"
SITE_SETTINGS_KEY = "site_settings.json"
DEFAULT_THEME_MODE = "theme-showgirl"
FRONTEND_THEMES_CSS = REPO_ROOT.parent / "tsm-frontend" / "frontend" / "src" / "styles" / "themes.css"

sys.path.insert(0, str(ROOT.parent))
from core.data_paths import update_streams_dir  # noqa: E402
from core.twitter import post_with_image  # noqa: E402

import generate_streams_image  # noqa: E402
import generate_weekend_streams_image  # noqa: E402


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
            rows[tid] = {"date": day, "streams": streams}
    return rows


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


def _is_recent_release_for_debut(release_date: str | None, target_date: str, *, window_days: int = 3) -> bool:
    if not release_date:
        return False
    try:
        release_day = date.fromisoformat(release_date)
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
.hdr .total-panel{
  min-width:360px;
  max-width:390px;
  overflow:hidden;
}
.hdr .total-value{
  font-size:34px;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:clip;
  font-variant-numeric:tabular-nums;
}
.debut-wrap{padding:18px 20px 0}
.debut-card{
  background:rgba(255,255,255,.78);
  border-top:1px solid rgba(16,24,40,.06);
}
.debut-main{
  display:grid;
  grid-template-columns:152px minmax(0,1fr) 330px;
  gap:18px;
  align-items:center;
  min-height:188px;
  padding:18px 20px;
  overflow:hidden;
}
.debut-cover{
  width:152px;
  height:152px;
  border-radius:9px;
  object-fit:cover;
  box-shadow:0 4px 14px rgba(0,0,0,.16);
}
.debut-cover-ph{
  width:152px;
  height:152px;
  border-radius:9px;
  background:#dde3ea;
}
.debut-kicker{
  font-size:11px;
  font-weight:900;
  text-transform:uppercase;
  letter-spacing:.08em;
  color:#667085;
}
.debut-title{
  margin-top:8px;
  font-size:35px;
  line-height:1.04;
  font-weight:950;
  color:var(--debut-text);
  letter-spacing:0;
}
.debut-sub{
  margin-top:10px;
  font-size:14px;
  color:var(--debut-muted);
  font-weight:750;
}
.debut-num{
  text-align:right;
  font-size:30px;
  line-height:1.02;
  font-weight:950;
  color:var(--debut-text);
  max-width:100%;
  overflow:hidden;
  font-variant-numeric:tabular-nums;
}
.debut-label{
  margin-top:7px;
  text-align:right;
  font-size:12px;
  color:var(--debut-muted);
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.07em;
}
.debut-versions{
  margin-top:7px;
  text-align:right;
  font-size:13px;
  color:var(--debut-accent);
  font-weight:850;
}
.debut-note{
  padding:12px 16px;
  background:rgba(248,250,251,.9);
  border-top:1px solid rgba(16,24,40,.06);
  color:#344054;
  font-size:13px;
  font-weight:700;
}
.version-block{
  border-top:1px solid rgba(16,24,40,.06);
}
.version-head,.version-row{
  display:grid;
  grid-template-columns:52px minmax(0,1fr) 190px 190px;
  column-gap:14px;
  align-items:center;
}
.version-head{
  padding:9px 16px;
  background:rgba(248,250,251,.9);
}
.version-head span{
  font-size:10px;
  font-weight:850;
  text-transform:uppercase;
  letter-spacing:.07em;
  color:#667085;
}
.version-row{
  min-height:46px;
  padding:8px 16px;
  border-top:1px solid rgba(16,24,40,.05);
  background:rgba(255,255,255,.78);
}
.version-row:nth-child(odd){background:rgba(248,250,251,.82)}
.version-name{
  min-width:0;
  font-size:12.5px;
  font-weight:700;
  color:var(--debut-muted);
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.version-rank{
  font-size:13px;
  font-weight:900;
  color:var(--debut-text);
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.version-title{
  min-width:0;
  font-size:13.5px;
  font-weight:900;
  color:var(--debut-text);
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.version-streams{
  text-align:right;
  font-size:13.5px;
  font-weight:900;
  color:#101828;
  font-variant-numeric:tabular-nums;
}
"""


def _cover_data_uri(image_url: str | None) -> str:
    if not image_url:
        return ""
    return generate_streams_image._url_to_data_uri(image_url) or image_url


def _render_html_image(html_text: str, out_path: Path, tmp_name: str) -> Path:
    tmp_html = out_path.parent / tmp_name
    tmp_html.write_text(html_text, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
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
    streams: int,
    image_url: str | None,
    versions: list[dict] | None = None,
    version_count: int = 1,
) -> str:
    date_text = _date_label(target_date)
    theme = _active_theme()
    accent = theme["accent"]
    accent_2 = theme["accent_2"]
    text_color = theme["text"]
    muted_color = theme["muted"]
    cover_uri = _cover_data_uri(image_url)
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
    version_rows_html = ""
    if versions and len(versions) > 1:
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
        f'style="background:linear-gradient(135deg,{accent} 0%,{accent_2} 100%);"'
    )
    extra_vars = (
        f"--debut-accent:{accent};"
        f"--debut-accent-2:{accent_2};"
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
        <div class="hdr-title">Taylor Swift · Spotify Debut</div>
        <div class="hdr-sub">{html.escape(date_text)} · First positive counter snapshot</div>
      </div>
    </div>
    <div class="total-panel">
      <div class="total-label">Debut streams</div>
      <div class="total-value">{_fmt(streams)}</div>
    </div>
  </div>
  <div class="debut-wrap">
    <div class="section debut-card">
      <div class="section-title">
        <h2>New Release</h2>
        <span>Song</span>
      </div>
      <div class="debut-main">
        {cover_html}
        <div>
          <div class="debut-kicker">Spotify Counter</div>
          <div class="debut-title">{html.escape(title)}</div>
          <div class="debut-sub">Taylor Swift</div>
        </div>
        <div>
          <div class="debut-num">{_fmt(streams)}</div>
          <div class="debut-label">Streams</div>
          {version_html}
        </div>
      </div>
      {version_rows_html}
      <div class="debut-note">See full update here : thetsmuseum.app/streams/latest</div>
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
    streams: int,
    image_url: str | None,
    versions: list[dict] | None = None,
    version_count: int = 1,
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
        streams=streams,
        image_url=image_url,
        versions=versions,
        version_count=version_count,
    )
    return _render_html_image(html_text, out_path, f"_{safe_slug}_debut_tmp.html")


def _build_posts(
    target_date: str,
    *,
    force_track_ids: set[str] | None = None,
    force_song_selectors: set[str] | None = None,
) -> list[tuple[str, str, Path | None]]:
    album_tracks, meta = _load_album_tracks()
    _load_misc_tracks(meta)
    day_rows = _load_rows_for_date(target_date)
    first_history_dates = _load_first_history_dates()

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
                first_history_dates[tid] = target_date
    debut_ids = {
        tid for tid, item in meta.items()
        if tid in day_rows
        and first_history_dates.get(tid) == target_date
        and _is_recent_release_for_debut(item.get("release_date"), target_date)
    }
    if not debut_ids:
        return []

    posts: list[tuple[str, str, Path | None]] = []
    date_text = _date_label(target_date)

    groups: dict[str, list[str]] = defaultdict(list)
    for tid in debut_ids:
        item = meta.get(tid, {})
        group_key = _family_key(item, tid)
        groups[str(group_key)].append(tid)

    for group_key, ids in sorted(groups.items(), key=lambda item: item[0].casefold()):
        ids = sorted(ids, key=lambda tid: meta.get(tid, {}).get("title", tid).casefold())
        primary = meta.get(ids[0], {})
        title = _clean_base_title(primary.get("base_title") or primary.get("title") or ids[0])
        streams = sum(day_rows[tid]["streams"] for tid in ids)
        if streams <= 0:
            print(f"[debut_releases] Skip {title}: total debut streams is 0.")
            continue
        image_url = next((meta.get(tid, {}).get("image_url") for tid in ids if meta.get(tid, {}).get("image_url")), None)
        version_count = len(ids)
        versions = [
            {
                "label": _version_label(meta.get(tid, {}).get("title") or tid, title),
                "title": meta.get(tid, {}).get("title") or tid,
                "streams": day_rows[tid]["streams"],
            }
            for tid in sorted(
                ids,
                key=lambda tid: (
                    -int(day_rows[tid]["streams"] or 0),
                    str(meta.get(tid, {}).get("title") or tid).casefold(),
                ),
            )
        ]
        image_path = _generate_debut_image(
            target_date,
            slug=f"song_{group_key}",
            title=title,
            kind="song",
            streams=streams,
            image_url=image_url,
            versions=versions,
            version_count=version_count,
        )
        version_note = f" across {version_count} versions" if version_count > 1 else ""
        posts.append((
            f"song:{group_key}",
            (
                f'"{title}" debuted with {_fmt(streams)} streams on the Spotify Counter{version_note} ({date_text}).\n\n'
                "See full update here : https://thetsmuseum.app/streams/latest"
            ),
            image_path,
        ))

    return posts


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

    posts = [
        (slug, text, image_path)
        for slug, text, image_path in _build_posts(
            target_date,
            force_track_ids=force_track_ids,
            force_song_selectors=force_song_selectors,
        )
        if slug not in already_posted
    ]
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
    for slug, text, image_path in posts:
        if image_path is None:
            print(f"[debut_releases] Missing debut image for {slug}; aborting post.")
            return 1
        if not post_with_image(text, image_path, TWITTER_SESSION):
            print(f"[debut_releases] Failed to post {slug}.")
            return 1
        posted.add(slug)
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
