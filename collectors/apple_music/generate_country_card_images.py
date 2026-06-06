#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import (  # noqa: E402
    LEGACY_WEBSITE_DATA_DIR,
    WEB_EXPORT_DATA_DIR,
    apple_music_charts_dir,
    first_existing,
    legacy_apple_music_daily_csv,
)

CSV_NAME = "apple_music_country_charts.csv"
GLOBAL_CSV_NAME = "apple_music_global.csv"
APPLEMUSIC_JSON = first_existing(WEB_EXPORT_DATA_DIR / "applemusic.json", LEGACY_WEBSITE_DATA_DIR / "applemusic.json")
SONGS_JSON = first_existing(
    WEB_EXPORT_DATA_DIR / "songs.json",
    LEGACY_WEBSITE_DATA_DIR / "songs.json",
    ROOT / "db" / "discography" / "songs.json",
)
LOGO_PATH = ROOT.parent / "tsm-frontend" / "frontend" / "public" / "icons" / "logo.gif"
OVERALL_DEFAULT_LIMIT = 100

THEMES: dict[str, dict[str, str]] = {
    "showgirl": {
        "bg": "#eefbf8",
        "card_bg": "#fbfffd",
        "border": "#bfe9df",
        "text": "#16322f",
        "muted": "#597b75",
        "even_row": "#e9f8f4",
        "region": "#0f7f7d",
        "play_btn": "#ff6b35",
        "rank_up": "#2a9d5c",
        "rank_down": "#cf3f24",
    },
    "midnights": {
        "bg": "#eef2ff",
        "card_bg": "#f8faff",
        "border": "#cfd8ff",
        "text": "#1b2440",
        "muted": "#687394",
        "even_row": "#edf2ff",
        "region": "#4657c7",
        "play_btn": "#5b6ee1",
        "rank_up": "#2a9d5c",
        "rank_down": "#cf3f24",
    },
    "ttpd": {
        "bg": "#f4f1ec",
        "card_bg": "#fffdf8",
        "border": "#d8d0c4",
        "text": "#2c2822",
        "muted": "#746d63",
        "even_row": "#f5efe6",
        "region": "#6f665a",
        "play_btn": "#9b9387",
        "rank_up": "#2a9d5c",
        "rank_down": "#cf3f24",
    },
    "lover": {
        "bg": "#fff0f7",
        "card_bg": "#fffafd",
        "border": "#ffcfe4",
        "text": "#331927",
        "muted": "#8a6174",
        "even_row": "#ffe8f2",
        "region": "#d83c85",
        "play_btn": "#e8709a",
        "rank_up": "#4caf7d",
        "rank_down": "#cf3f24",
    },
    "fearless": {
        "bg": "#fff8dc",
        "card_bg": "#fffdf2",
        "border": "#f3d982",
        "text": "#332711",
        "muted": "#8b7334",
        "even_row": "#fff3c4",
        "region": "#a87909",
        "play_btn": "#d4a017",
        "rank_up": "#4caf7d",
        "rank_down": "#cf3f24",
    },
    "reputation": {
        "bg": "#f2f2f2",
        "card_bg": "#ffffff",
        "border": "#cfcfcf",
        "text": "#171717",
        "muted": "#6b6b6b",
        "even_row": "#ededed",
        "region": "#343434",
        "play_btn": "#111111",
        "rank_up": "#4caf7d",
        "rank_down": "#cf3f24",
    },
    "evermore": {
        "bg": "#f9efe5",
        "card_bg": "#fffaf4",
        "border": "#e7c7aa",
        "text": "#332418",
        "muted": "#856650",
        "even_row": "#f7e5d4",
        "region": "#a45c2a",
        "play_btn": "#9b6b3d",
        "rank_up": "#80a040",
        "rank_down": "#cf3f24",
    },
    "folklore": {
        "bg": "#f2f4f3",
        "card_bg": "#ffffff",
        "border": "#d4d9d7",
        "text": "#202524",
        "muted": "#697370",
        "even_row": "#e9eeec",
        "region": "#5e6a66",
        "play_btn": "#8f989d",
        "rank_up": "#6fb07f",
        "rank_down": "#cf3f24",
    },
    "1989": {
        "bg": "#eaf8ff",
        "card_bg": "#f8fdff",
        "border": "#bfe8fb",
        "text": "#152a34",
        "muted": "#5c7e8d",
        "even_row": "#dcf3ff",
        "region": "#1678a7",
        "play_btn": "#2aa8dc",
        "rank_up": "#4caf7d",
        "rank_down": "#cf3f24",
    },
    "red": {
        "bg": "#fff1ef",
        "card_bg": "#fffafa",
        "border": "#ffc9c2",
        "text": "#321817",
        "muted": "#8b5f5a",
        "even_row": "#ffe5e1",
        "region": "#b92722",
        "play_btn": "#b91f2f",
        "rank_up": "#4caf7d",
        "rank_down": "#cf3f24",
    },
    "speak_now": {
        "bg": "#f8efff",
        "card_bg": "#fffaff",
        "border": "#e3c5f5",
        "text": "#2c1937",
        "muted": "#7b5b8b",
        "even_row": "#f3e3fc",
        "region": "#7a36a2",
        "play_btn": "#8b4bb3",
        "rank_up": "#4caf7d",
        "rank_down": "#cf3f24",
    },
    "taylor_swift": {
        "bg": "#edf9f1",
        "card_bg": "#fbfffc",
        "border": "#bde7ca",
        "text": "#16291d",
        "muted": "#5d8068",
        "even_row": "#ddf3e5",
        "region": "#237c47",
        "play_btn": "#2e8f5f",
        "rank_up": "#4caf7d",
        "rank_down": "#cf3f24",
    },
    "apple": {
        "bg": "#f7f8fb",
        "card_bg": "#ffffff",
        "border": "#d7dce7",
        "text": "#1f2530",
        "muted": "#667085",
        "even_row": "#f1f4f9",
        "region": "#bf1d47",
        "play_btn": "#fa243c",
        "rank_up": "#248a55",
        "rank_down": "#c4352f",
    },
}

COUNTRY_NAMES = {
    "ae": "United Arab Emirates",
    "ar": "Argentina",
    "at": "Austria",
    "au": "Australia",
    "az": "Azerbaijan",
    "be": "Belgium",
    "bg": "Bulgaria",
    "bh": "Bahrain",
    "br": "Brazil",
    "bt": "Bhutan",
    "bz": "Belize",
    "ca": "Canada",
    "ch": "Switzerland",
    "cl": "Chile",
    "cn": "China",
    "co": "Colombia",
    "cr": "Costa Rica",
    "cz": "Czech Republic",
    "de": "Germany",
    "dk": "Denmark",
    "do": "Dominican Republic",
    "ec": "Ecuador",
    "ee": "Estonia",
    "eg": "Egypt",
    "es": "Spain",
    "fi": "Finland",
    "fr": "France",
    "gb": "United Kingdom",
    "gh": "Ghana",
    "gr": "Greece",
    "gt": "Guatemala",
    "hk": "Hong Kong",
    "hn": "Honduras",
    "hr": "Croatia",
    "hu": "Hungary",
    "id": "Indonesia",
    "ie": "Ireland",
    "il": "Occupied Palestine",
    "in": "India",
    "is": "Iceland",
    "it": "Italy",
    "jo": "Jordan",
    "jp": "Japan",
    "ke": "Kenya",
    "kw": "Kuwait",
    "kz": "Kazakhstan",
    "lb": "Lebanon",
    "lt": "Lithuania",
    "lu": "Luxembourg",
    "lv": "Latvia",
    "ma": "Morocco",
    "mk": "North Macedonia",
    "mn": "Mongolia",
    "mo": "Macau",
    "mt": "Malta",
    "mx": "Mexico",
    "my": "Malaysia",
    "ng": "Nigeria",
    "nl": "Netherlands",
    "no": "Norway",
    "nz": "New Zealand",
    "om": "Oman",
    "pa": "Panama",
    "pe": "Peru",
    "ph": "Philippines",
    "pk": "Pakistan",
    "pl": "Poland",
    "pt": "Portugal",
    "py": "Paraguay",
    "qa": "Qatar",
    "ro": "Romania",
    "rs": "Serbia",
    "sa": "Saudi Arabia",
    "se": "Sweden",
    "sg": "Singapore",
    "si": "Slovenia",
    "sk": "Slovakia",
    "sv": "El Salvador",
    "th": "Thailand",
    "tn": "Tunisia",
    "tr": "Turkey",
    "tw": "Taiwan",
    "tz": "Tanzania",
    "ua": "Ukraine",
    "uy": "Uruguay",
    "us": "United States",
    "vn": "Vietnam",
    "za": "South Africa",
}

_img_cache: dict[str, str] = {}


def _to_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    return text[:80] or "track"


def _logo_data_uri() -> str:
    try:
        data = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        return f"data:image/gif;base64,{data}"
    except Exception:
        return ""


def _url_to_data_uri(url: str) -> str:
    if not url or not url.startswith("http"):
        return url
    if url in _img_cache:
        return _img_cache[url]
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as resp:
            mime = resp.headers.get_content_type() or "image/jpeg"
            data = base64.b64encode(resp.read()).decode("ascii")
            result = f"data:{mime};base64,{data}"
    except Exception:
        result = url
    _img_cache[url] = result
    return result


def _csv_path(chart_date: str) -> Path:
    return next(
        (
            path
            for path in (
                apple_music_charts_dir(chart_date) / CSV_NAME,
                legacy_apple_music_daily_csv(chart_date, CSV_NAME),
                ROOT / "db" / CSV_NAME,
            )
            if path.exists()
        ),
        apple_music_charts_dir(chart_date) / CSV_NAME,
    )


def _global_csv_path(chart_date: str) -> Path:
    return next(
        (
            path
            for path in (
                apple_music_charts_dir(chart_date) / GLOBAL_CSV_NAME,
                legacy_apple_music_daily_csv(chart_date, GLOBAL_CSV_NAME),
                ROOT / "db" / GLOBAL_CSV_NAME,
            )
            if path.exists()
        ),
        apple_music_charts_dir(chart_date) / GLOBAL_CSV_NAME,
    )


def _read_rows(path: Path, chart_date: str) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = [dict(row) for row in csv.DictReader(fh)]
    return [row for row in rows if str(row.get("date") or "").strip() == chart_date]


def _entry_key(entry: dict) -> str:
    return str(
        entry.get("apple_music_id")
        or entry.get("song_name")
        or entry.get("album_name")
        or entry.get("video_name")
        or ""
    ).strip().lower()


def _song_name_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\[([^\]]+)\]", r"(\1)", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _flatten_song_rows(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        if isinstance(payload.get("songs"), list):
            return [item for item in payload["songs"] if isinstance(item, dict)]
        if isinstance(payload.get("tracks"), list):
            return [item for item in payload["tracks"] if isinstance(item, dict)]
        rows: list[dict] = []
        for value in payload.values():
            rows.extend(_flatten_song_rows(value))
        return rows
    if isinstance(payload, list):
        rows = []
        for item in payload:
            rows.extend(_flatten_song_rows(item))
        return rows
    return []


def _load_song_album_lookup() -> dict[str, str]:
    if not SONGS_JSON.exists():
        return {}
    try:
        payload = json.loads(SONGS_JSON.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    lookup: dict[str, str] = {}
    for row in _flatten_song_rows(payload):
        title = _song_name_key(row.get("title") or row.get("song_name") or row.get("title_clean"))
        album = str(row.get("primary_album") or row.get("album") or "").strip()
        if title and album and title not in lookup:
            lookup[title] = album
    return lookup


def _load_global_meta(chart_date: str) -> dict[str, dict]:
    path = _global_csv_path(chart_date)
    meta: dict[str, dict] = {}
    if path.exists():
        for row in _read_rows(path, chart_date):
            track_id = str(row.get("apple_music_id") or "").strip()
            if not track_id:
                continue
            rank = _to_int(row.get("rank"))
            meta[track_id] = {
                "song_name": str(row.get("song_name") or "").strip(),
                "album_name": str(row.get("album_name") or "").strip(),
                "apple_music_id": track_id,
                "rank": rank,
                "previous_rank": _to_int(row.get("previous_rank")),
                "image_url": str(row.get("image_url") or "").strip(),
                "url": str(row.get("url") or "").strip(),
                "artist_name": str(row.get("artist_name") or "Taylor Swift").strip() or "Taylor Swift",
            }
    if meta or not APPLEMUSIC_JSON.exists():
        return meta

    try:
        data = json.loads(APPLEMUSIC_JSON.read_text(encoding="utf-8-sig"))
    except Exception:
        return meta
    global_chart = data.get("global_chart") if isinstance(data, dict) else None
    if not isinstance(global_chart, dict):
        return meta
    global_date = str(global_chart.get("date") or "").split("T", 1)[0]
    if global_date and global_date != chart_date:
        return meta
    for entry in global_chart.get("entries") or []:
        track_id = str(entry.get("apple_music_id") or "").strip()
        if not track_id:
            continue
        meta[track_id] = {
            "song_name": str(entry.get("song_name") or "").strip(),
            "album_name": str(entry.get("album_name") or "").strip(),
            "apple_music_id": track_id,
            "rank": _to_int(entry.get("rank")),
            "previous_rank": _to_int(entry.get("previous_rank")),
            "image_url": str(entry.get("image_url") or "").strip(),
            "url": str(entry.get("url") or "").strip(),
            "artist_name": str(entry.get("artist_name") or "Taylor Swift").strip() or "Taylor Swift",
        }
    return meta


def _row_to_entry(row: dict, *, kind: str, label: str, album_lookup: dict[str, str]) -> dict:
    song_name = str(row.get("song_name") or "").strip()
    album_name = str(row.get("album_name") or "").strip() or album_lookup.get(_song_name_key(song_name), "")
    return {
        "country": str(row.get("country") or "").strip().lower(),
        "kind": kind,
        "label": label,
        "song_name": song_name,
        "album_name": album_name,
        "artist_name": str(row.get("artist_name") or "Taylor Swift").strip() or "Taylor Swift",
        "apple_music_id": str(row.get("apple_music_id") or song_name).strip(),
        "rank": _to_int(row.get("rank")),
        "previous_rank": _to_int(row.get("previous_rank")),
        "image_url": str(row.get("image_url") or "").strip(),
        "url": str(row.get("url") or "").strip(),
    }


def _track_groups(rows: list[dict], global_meta: dict[str, dict], album_lookup: dict[str, str]) -> dict[str, dict]:
    grouped: dict[str, dict] = {}

    def remember(entry: dict, placement: dict) -> None:
        key = _entry_key(entry)
        if not key:
            return
        if key not in grouped:
            grouped[key] = {
                "key": key,
                "entry": entry,
                "album_name": entry.get("album_name") or album_lookup.get(_song_name_key(entry.get("song_name")), ""),
                "placements": [],
                "best_rank": entry.get("rank") or 9999,
            }
        item = grouped[key]
        existing_idx = next(
            (
                idx for idx, current in enumerate(item["placements"])
                if current.get("kind") == placement.get("kind") and current.get("label") == placement.get("label")
            ),
            None,
        )
        if existing_idx is None:
            item["placements"].append(placement)
        else:
            current_rank = item["placements"][existing_idx].get("rank") or 9999
            next_rank = placement.get("rank") or 9999
            if next_rank <= current_rank:
                item["placements"][existing_idx] = placement
        rank = entry.get("rank") or 9999
        if rank < item["best_rank"]:
            item["best_rank"] = rank
            item["entry"] = entry
            item["album_name"] = entry.get("album_name") or item.get("album_name") or ""

    for meta in global_meta.values():
        entry = {
            "country": "global",
            "kind": "global",
            "label": "Global",
            "song_name": meta.get("song_name") or "",
            "album_name": meta.get("album_name") or album_lookup.get(_song_name_key(meta.get("song_name")), ""),
            "artist_name": meta.get("artist_name") or "Taylor Swift",
            "apple_music_id": meta.get("apple_music_id") or "",
            "rank": meta.get("rank"),
            "previous_rank": meta.get("previous_rank"),
            "image_url": meta.get("image_url") or "",
            "url": meta.get("url") or "",
        }
        remember(entry, entry)

    for row in rows:
        country = str(row.get("country") or "").strip().lower()
        if not country:
            continue
        entry = _row_to_entry(row, kind="country", label=_country_label(country), album_lookup=album_lookup)
        remember(entry, entry)
    for item in grouped.values():
        item["placements"].sort(key=lambda e: (0 if e.get("kind") == "global" else 1, e.get("rank") or 9999, e.get("label") or ""))
    return grouped


def _theme_key_for_album(album: str) -> str:
    value = album.lower().strip()
    if "life of a showgirl" in value:
        return "showgirl"
    if "tortured poets" in value:
        return "ttpd"
    if "midnights" in value:
        return "midnights"
    if "evermore" in value:
        return "evermore"
    if "folklore" in value:
        return "folklore"
    if "lover" in value:
        return "lover"
    if "reputation" in value:
        return "reputation"
    if "1989" in value:
        return "1989"
    if "red" in value:
        return "red"
    if "speak now" in value:
        return "speak_now"
    if "fearless" in value:
        return "fearless"
    if "taylor swift" in value or "debut" in value:
        return "taylor_swift"
    return "apple"


def _palette_for_album(album: str) -> tuple[dict[str, str], str]:
    theme = _theme_key_for_album(album)
    return THEMES.get(theme, THEMES["apple"]), theme


def _country_label(code: str) -> str:
    return COUNTRY_NAMES.get(code.lower(), code.upper())


def _rank_delta_html(entry: dict) -> str:
    rank = entry.get("rank")
    prev = entry.get("previous_rank")
    if not rank or not prev:
        return '<span class="delta neutral"> (NEW)</span>'
    delta = prev - rank
    if delta > 0:
        return f'<span class="delta up"> (+{delta})</span>'
    if delta < 0:
        return f'<span class="delta down"> (-{abs(delta)})</span>'
    return '<span class="delta neutral"> (=)</span>'


def _table_html(entries: list[dict]) -> str:
    rows = ""
    for entry in entries:
        rows += (
            "<tr>"
            f'<td class="country">{html.escape(entry.get("label") or _country_label(entry["country"]))}</td>'
            f'<td class="rank">#{entry.get("rank") or "?"}{_rank_delta_html(entry)}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Region</th><th>Ranking</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _tables_html(entries: list[dict]) -> str:
    if len(entries) < 6:
        return _table_html(entries)
    column_count = 3 if len(entries) >= 12 else 2
    rows_per_column = (len(entries) + column_count - 1) // column_count
    tables = [
        _table_html(entries[idx:idx + rows_per_column])
        for idx in range(0, len(entries), rows_per_column)
    ]
    return f'<div class="multi-col">{"".join(tables)}</div>'


def _build_card_html(item: dict, chart_date: str) -> str:
    placements = item.get("placements") or []
    first = item.get("entry") or (placements[0] if placements else {})
    title = html.escape(first.get("song_name") or "Unknown")
    artist = html.escape(first.get("artist_name") or "Taylor Swift")
    album = str(item.get("album_name") or first.get("album_name") or "").strip()
    img_uri = _url_to_data_uri(first.get("image_url") or "")
    logo_uri = _logo_data_uri()
    width = 980 if len(placements) >= 12 else 800 if len(placements) >= 6 else 500
    try:
        date_label = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        date_label = chart_date
    cover = (
        f'<img class="cover" src="{html.escape(img_uri)}" alt="cover" />'
        if img_uri else
        '<div class="cover cover-placeholder"></div>'
    )
    p, _theme = _palette_for_album(album)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: {p["bg"]};
    display: flex;
    padding: 16px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: {p["text"]};
  }}
  .card {{
    width: {width}px;
    background: {p["card_bg"]};
    border: 1px solid {p["border"]};
    border-radius: 18px;
    box-shadow: 0 2px 18px rgba(31,37,48,0.18);
    padding: 18px 20px 14px;
  }}
  .head {{
    display: flex;
    align-items: center;
    gap: 14px;
    padding-bottom: 14px;
    border-bottom: 1px solid {p["border"]};
  }}
  .cover {{
    width: 76px;
    height: 76px;
    flex: 0 0 76px;
    border-radius: 12px;
    object-fit: cover;
    box-shadow: 0 2px 10px rgba(31,37,48,0.22);
  }}
  .cover-placeholder {{ background: {p["border"]}; }}
  .kicker {{
    color: {p["region"]};
    font-size: 13px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0;
  }}
  .title {{
    margin-top: 3px;
    font-size: 26px;
    line-height: 1.08;
    font-weight: 850;
    overflow-wrap: anywhere;
  }}
  .artist {{
    margin-top: 4px;
    color: {p["muted"]};
    font-size: 16px;
    font-weight: 650;
  }}
  .date {{
    margin-left: auto;
    align-self: flex-start;
    flex: 0 0 auto;
    border-radius: 999px;
    background: {p["play_btn"]};
    color: white;
    padding: 7px 11px;
    font-size: 13px;
    font-weight: 850;
  }}
  .meta {{
    display: flex;
    justify-content: space-between;
    gap: 12px;
    color: {p["muted"]};
    font-size: 15px;
    font-weight: 720;
    padding: 12px 0 8px;
  }}
  .multi-col {{
    display: grid;
    grid-template-columns: repeat({3 if len(placements) >= 12 else 2}, 1fr);
    gap: 14px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }}
  th, td {{
    border-bottom: 1px solid {p["border"]};
    padding: 8px 8px;
    font-size: 15px;
    line-height: 1.16;
    vertical-align: middle;
  }}
  th {{
    color: {p["muted"]};
    font-size: 12px;
    font-weight: 850;
    text-align: left;
    text-transform: uppercase;
  }}
  tr:nth-child(even) td {{ background: {p["even_row"]}; }}
  .country {{
    color: {p["region"]};
    font-weight: 800;
    overflow-wrap: anywhere;
  }}
  .rank {{
    width: 116px;
    text-align: right;
    font-weight: 850;
    white-space: nowrap;
  }}
  .delta {{
    font-weight: 750;
    font-size: 13px;
  }}
  .delta.up {{ color: {p["rank_up"]}; }}
  .delta.down {{ color: {p["rank_down"]}; }}
  .delta.neutral {{ color: {p["muted"]}; }}
  .footer {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid {p["border"]};
    color: {p["muted"]};
    font-size: 13px;
    font-weight: 700;
  }}
  .brand {{
    display: flex;
    align-items: center;
    gap: 7px;
  }}
  .logo {{ height: 22px; width: auto; }}
</style>
</head>
<body>
<div class="card" id="card">
  <div class="head">
    {cover}
    <div>
      <div class="kicker">Apple Music Overall Countries</div>
      <div class="title">{title}</div>
      <div class="artist">{artist}</div>
    </div>
    <div class="date">{html.escape(date_label)}</div>
  </div>
  <div class="meta">
    <span>{sum(1 for placement in placements if placement.get("kind") == "country")} charting region{'s' if sum(1 for placement in placements if placement.get("kind") == "country") != 1 else ''}</span>
    <span>{html.escape(album or "Global Top 100")}</span>
  </div>
  {_tables_html(placements)}
  <div class="footer">
    <div class="brand"><img class="logo" src="{logo_uri}" alt="TSM" /><span>@tsmuseum13</span></div>
    <span>thetsmuseum.app</span>
  </div>
</div>
</body>
</html>"""


def _priority(item: dict) -> tuple:
    placements = item.get("placements") or []
    country_count = sum(1 for placement in placements if placement.get("kind") == "country")
    best_rank = item.get("best_rank") or 9999
    title = (item.get("entry") or {}).get("song_name") or item.get("key") or ""
    return (-country_count, -len(placements), best_rank, str(title).lower())


def generate(chart_date: str, *, min_countries: int = 1, limit: int = OVERALL_DEFAULT_LIMIT, force: bool = False) -> int:
    csv_path = _csv_path(chart_date)
    if not csv_path.exists():
        print(f"[ERROR] Missing Apple Music country charts CSV: {csv_path}")
        return 1

    out_dir = csv_path.parent / "country_cards"
    index_path = out_dir / "cards_index.json"
    if index_path.exists() and not force:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        print(f"[SKIP] Apple Music cards already generated for {chart_date} ({len(data.get('cards', []))} images)")
        return 0

    global_meta = _load_global_meta(chart_date)
    album_lookup = _load_song_album_lookup()

    rows = _read_rows(csv_path, chart_date)
    grouped = _track_groups(rows, global_meta, album_lookup)
    tracks = [
        item
        for item in grouped.values()
        if sum(1 for placement in item.get("placements", []) if placement.get("kind") == "country") >= min_countries
    ]
    tracks.sort(key=_priority)
    if limit > 0:
        tracks = tracks[:limit]

    out_dir.mkdir(parents=True, exist_ok=True)
    if force:
        for old_png in out_dir.glob("*.png"):
            old_png.unlink()
    generated: list[str] = []
    priority: dict[str, dict] = {}
    used_slugs: set[str] = set()

    print(
        f"[INFO] Apple Music overall cards: {len(tracks)} tracks "
        f"({len(global_meta)} Global placement song ids), min_countries={min_countries}, limit={limit}, source={csv_path}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": 900, "height": 1400}, device_scale_factor=3)
        for idx, item in enumerate(tracks, 1):
            entry = item.get("entry") or {}
            title = entry.get("song_name") or item.get("key") or "track"
            slug = _slugify(title)
            if slug in used_slugs:
                slug = f"{slug}_{_slugify(item.get('key') or '')[-12:]}"
            used_slugs.add(slug)
            out_path = out_dir / f"{slug}.png"
            print(f"  [{idx:2d}/{len(tracks)}] {title[:52]}", end="", flush=True)
            try:
                page.set_content(_build_card_html(item, chart_date), wait_until="domcontentloaded")
                card = page.locator("#card")
                card.wait_for(state="visible", timeout=5000)
                card.screenshot(path=str(out_path))
                generated.append(out_path.name)
                _palette, theme = _palette_for_album(str(item.get("album_name") or ""))
                placements = item.get("placements") or []
                priority[out_path.name] = {
                    "countries": sum(1 for placement in placements if placement.get("kind") == "country"),
                    "placements": len(placements),
                    "best_rank": item.get("best_rank"),
                    "global_rank": next((placement.get("rank") for placement in placements if placement.get("kind") == "global"), None),
                    "album_name": item.get("album_name"),
                    "theme": theme,
                    "apple_music_id": entry.get("apple_music_id"),
                }
                print(f" -> {out_path.name}")
            except Exception as exc:
                print(f" [WARN] failed: {exc}")
        browser.close()

    index_path.write_text(
        json.dumps(
            {
                "date": chart_date,
                "source": str(csv_path),
                "global_source": str(_global_csv_path(chart_date)),
                "min_countries": min_countries,
                "limit": limit,
                "filter": "apple_music_frontend_overall",
                "cards": generated,
                "priority": priority,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[DONE] {len(generated)} Apple Music card image(s) -> {out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Apple Music country chart card PNGs.")
    parser.add_argument("date_pos", nargs="?", metavar="YYYY-MM-DD")
    parser.add_argument("--date", metavar="YYYY-MM-DD")
    parser.add_argument("--min-countries", type=int, default=1)
    parser.add_argument("--limit", type=int, default=OVERALL_DEFAULT_LIMIT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    raw_date = args.date or args.date_pos or date.today().isoformat()
    try:
        chart_date = datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
    except ValueError:
        print(f"[ERROR] Invalid date: {raw_date!r}")
        return 1
    return generate(chart_date, min_countries=max(1, args.min_countries), limit=args.limit, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
