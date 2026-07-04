#!/usr/bin/env python3
"""Build a Spotify period recap thread for @swiftiescharts.

Examples:
  python dev/adhoc/period_streams_recap.py --period month
  python dev/adhoc/period_streams_recap.py --period month --date 2026-06-15
  python dev/adhoc/period_streams_recap.py --start 2026-06-01 --end 2026-06-30
  python dev/adhoc/period_streams_recap.py --period week --post --yes
"""

from __future__ import annotations

import argparse
import base64
import calendar
import colorsys
import csv
import html
import io
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:
    Image = None


REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DATA = REPO_ROOT / "runtime" / "exports" / "web" / "site" / "data"
WEB_HISTORY = REPO_ROOT / "runtime" / "exports" / "web" / "site" / "history"
DB_DIR = REPO_ROOT / "db"
ARTIFACT_DIR = REPO_ROOT / "dev" / "artifacts" / "period_recaps"
COMP_DIR = REPO_ROOT / "collectors" / "comp"
DEFAULT_IMAGE_DIR = REPO_ROOT / "snapshots" / "recap"
HEADERS_DIR = REPO_ROOT / "db" / "discography" / "headers"
CORE_DIR = REPO_ROOT / "collectors" / "spotify" / "core"
SPOTIFY_SCRIPT_DIR = REPO_ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts"
DEFAULT_SESSION = (
    REPO_ROOT
    / "collectors"
    / "spotify"
    / "charts"
    / "worldwide"
    / "tools"
    / "json"
    / "twitter_session.json"
)
DAILY_ARCHIVE_FILES = [
    DB_DIR / "2026 & 2025 - Daily Archive 2026.csv",
    DB_DIR / "2026 & 2025 - Копія аркуша Daily Archive 2025.csv",
]

if str(SPOTIFY_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SPOTIFY_SCRIPT_DIR))

try:
    from catalog_gap_report import _normalize_title as spotify_normalize_title  # noqa: E402
except Exception:
    def spotify_normalize_title(value: str) -> str:
        ascii_text = unicodedata.normalize("NFKD", value or "")
        ascii_text = "".join(char for char in ascii_text if not unicodedata.combining(char))
        return re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).strip()


ERA_ALIASES = {
    "taylor swift": "Taylor Swift",
    "fearless": "Fearless",
    "fearless (taylor's version)": "Fearless",
    "speak now": "Speak Now",
    "speak now (taylor's version)": "Speak Now",
    "red": "Red",
    "red (taylor's version)": "Red",
    "1989": "1989",
    "1989 (taylor's version)": "1989",
    "reputation": "reputation",
    "lover": "Lover",
    "folklore": "folklore",
    "evermore": "evermore",
    "midnights": "Midnights",
    "the tortured poets department": "THE TORTURED POETS DEPARTMENT",
    "the tortured poets department: the anthology": "THE TORTURED POETS DEPARTMENT",
    "the life of a showgirl": "The Life of a Showgirl",
    "the life of a showgirl (deluxe)": "The Life of a Showgirl",
    "the taylor swift holiday collection": "Taylor Swift",
    "misc": "Misc",
    "other": "Misc",
}

NON_SONG_TITLE_RE = re.compile(
    r"(?:\bcommentary\b|\bkaraoke\b|\binstrumental\b|\btrack by track\b|"
    r"\bvoice memo\b|\bofficial music video\b|\bmusic video\b|"
    r"\binstrumental with\b|\binstrumental w/)",
    re.I,
)


@dataclass
class Range:
    start: date
    end: date
    label: str
    slug: str
    period: str


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def today_local() -> date:
    return date.today()


def previous_month(anchor: date) -> tuple[date, date]:
    year = anchor.year
    month = anchor.month - 1
    if month == 0:
        year -= 1
        month = 12
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def previous_year(anchor: date) -> tuple[date, date]:
    year = anchor.year - 1
    return date(year, 1, 1), date(year, 12, 31)


def previous_week(anchor: date) -> tuple[date, date]:
    this_monday = anchor - timedelta(days=anchor.weekday())
    start = this_monday - timedelta(days=7)
    return start, start + timedelta(days=6)


def current_period(anchor: date, period: str) -> tuple[date, date]:
    if period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        last = calendar.monthrange(anchor.year, anchor.month)[1]
        return date(anchor.year, anchor.month, 1), date(anchor.year, anchor.month, last)
    if period == "year":
        return date(anchor.year, 1, 1), date(anchor.year, 12, 31)
    raise ValueError(f"Unsupported period: {period}")


def build_range(args: argparse.Namespace) -> Range:
    if args.start or args.end:
        if not args.start or not args.end:
            raise SystemExit("--start and --end must be passed together.")
        start = parse_day(args.start)
        end = parse_day(args.end)
        label = f"{start:%b} {start.day} - {end:%b} {end.day}, {end.year}" if start.year == end.year else f"{start} - {end}"
        return Range(start, end, label.replace(" 0", " "), f"{start}_to_{end}", "custom")

    anchor = parse_day(args.date) if args.date else today_local()
    if args.current:
        start, end = current_period(anchor, args.period)
    elif args.period == "week":
        start, end = previous_week(anchor)
    elif args.period == "month":
        start, end = previous_month(anchor)
    elif args.period == "year":
        start, end = previous_year(anchor)
    else:
        raise SystemExit(f"Unsupported period: {args.period}")

    if args.period == "week":
        label = f"week of {start:%b %d, %Y}"
        slug = f"week_{start}"
    elif args.period == "month":
        label = f"{start:%B %Y}"
        slug = f"{start:%Y-%m}"
    else:
        label = f"{start:%Y}"
        slug = f"{start:%Y}"
    return Range(start, end, label, slug, args.period)


def safe_replace_year(day: date, year: int) -> date:
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def month_range(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def is_full_month(start: date, end: date) -> bool:
    return start.day == 1 and end == month_range(start.year, start.month)[1]


def is_full_year(start: date, end: date) -> bool:
    return start == date(start.year, 1, 1) and end == date(start.year, 12, 31)


def previous_period_range(rng: Range) -> Range:
    if rng.period == "year" or is_full_year(rng.start, rng.end):
        return range_from_dates(date(rng.start.year - 1, 1, 1), date(rng.start.year - 1, 12, 31), "year")
    if rng.period == "month" or is_full_month(rng.start, rng.end):
        prev_start, prev_end = previous_month(rng.start)
        return range_from_dates(prev_start, prev_end, "month")
    if rng.period == "week":
        return range_from_dates(rng.start - timedelta(days=7), rng.end - timedelta(days=7), "week")
    span = (rng.end - rng.start).days + 1
    prev_end = rng.start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    return range_from_dates(prev_start, prev_end, "custom")


def range_from_dates(start: date, end: date, period: str) -> Range:
    if period == "month":
        label = f"{start:%B %Y}"
        slug = f"{start:%Y-%m}"
    elif period == "year":
        label = f"{start:%Y}"
        slug = f"{start:%Y}"
    elif period == "week":
        label = f"week of {start:%b %d, %Y}"
        slug = f"week_{start}"
    else:
        label = f"{start:%b} {start.day} - {end:%b} {end.day}, {end.year}" if start.year == end.year else f"{start} - {end}"
        slug = f"{start}_to_{end}"
    return Range(start, end, label.replace(" 0", " "), slug, period)


def comparison_ranges(rng: Range) -> list[tuple[str, Range]]:
    prev = previous_period_range(rng)
    yoy = range_from_dates(
        safe_replace_year(rng.start, rng.start.year - 1),
        safe_replace_year(rng.end, rng.end.year - 1),
        "year" if is_full_year(rng.start, rng.end) else "month" if is_full_month(rng.start, rng.end) else rng.period,
    )

    ranges = [("prev", prev)]
    if yoy.start != prev.start or yoy.end != prev.end:
        ranges.append(("yoy", yoy))
    return ranges


def iter_days(start: date, end: date) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        clean = str(value).replace(",", "").replace("\xa0", "").replace("Â", "").replace(" ", "").strip()
        return int(float(clean))
    except Exception:
        return None


def fmt_int(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{int(round(value)):,}"


def compact_label(rng: Range) -> str:
    if rng.period == "month":
        return f"{rng.start:%b %Y}"
    if rng.period == "year":
        return f"{rng.start:%Y}"
    if rng.period == "week":
        return f"{rng.start:%b} {rng.start.day}"
    return rng.label


def fmt_stream_delta(current: int | None, previous: int | None) -> tuple[str, str]:
    if current is None or previous is None:
        return "-", "neutral"
    delta = int(current) - int(previous)
    cls = "pos" if delta >= 0 else "neg"
    if previous <= 0:
        return (f"+{fmt_int(delta)}" if delta >= 0 else f"-{fmt_int(abs(delta))}"), cls
    pct = delta / previous * 100
    sign = "+" if delta >= 0 else "-"
    return f"{sign}{fmt_int(abs(delta))}<span class=\"metric-sub\">{sign}{abs(pct):.1f}%</span>", cls


def norm_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("_", " ").split())


def loose_song_key(value: Any) -> str:
    raw = str(value or "").casefold()
    raw = raw.replace("’", "'").replace("`", "'")
    raw = re.sub(r"\s*\([^)]*\)", "", raw)
    raw = re.sub(r"\s*-\s*(?:from .+|single|ep)$", "", raw)
    raw = raw.replace("'", "")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return " ".join(raw.split())


def archive_title_for_match(value: Any) -> str:
    raw = str(value or "").replace("’", "'").replace("`", "'")
    raw = re.sub(r"\b10\s*mv\b", "10 minute version", raw, flags=re.I)
    raw = re.sub(r"\btv\b", "taylor's version", raw, flags=re.I)
    raw = re.sub(r"\bftv\b", "from the vault", raw, flags=re.I)
    return raw


def song_exact_key(value: Any) -> str:
    return spotify_normalize_title(archive_title_for_match(value))


def song_base_key(value: Any) -> str:
    raw = archive_title_for_match(value)
    raw = re.sub(r"\s*\([^)]*\)", "", raw)
    raw = re.sub(r"\s*-\s*(?:from .+|single|ep)$", "", raw, flags=re.I)
    return spotify_normalize_title(raw)


def song_lookup_keys(value: Any) -> list[str]:
    keys: list[str] = []
    for key in (song_exact_key(value), song_base_key(value)):
        if key and key not in keys:
            keys.append(key)
    return keys


def canonical_song_family(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    base = raw
    base = base.split(" - ")[0]
    base = re.sub(r"\s*\((?:taylor['’]s version|from .+?|feat\..+?|ft\..+?)\)", "", base, flags=re.I)
    base = re.sub(r"\s*-\s*(single|ep)$", "", base, flags=re.I)
    return " ".join(base.strip().split())


def canonical_era(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Misc"
    key = norm_key(raw)
    key = key.replace("[deluxe]", "").replace("(deluxe version)", "").replace("(deluxe)", "").strip()
    key = key.replace("(the anthology)", "").strip()
    if key in ERA_ALIASES:
        return ERA_ALIASES[key]
    for alias, era in ERA_ALIASES.items():
        if alias and alias in key:
            return era
    return raw


def is_recap_song(title: Any) -> bool:
    return not NON_SONG_TITLE_RE.search(str(title or ""))


def read_spotify_catalog() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    songs_payload = load_json(WEB_DATA / "songs.json")
    albums_payload = load_json(WEB_DATA / "albums.json")
    songs = {song["track_id"]: song for song in songs_payload.get("songs", []) if song.get("track_id")}
    albums = albums_payload.get("albums", [])
    return songs, albums


def build_song_lookup(songs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for song in songs.values():
        for title in (song.get("base_title"), song.get("title")):
            for key in song_lookup_keys(title):
                if key and key not in lookup:
                    lookup[key] = song
    return lookup


def apply_daily_archive_fallback(
    rng: Range,
    songs: dict[str, dict[str, Any]],
    song_totals: dict[str, int],
    song_best: dict[str, tuple[int, str]],
    archive_meta: dict[str, dict[str, Any]],
) -> None:
    wanted = {day.strftime("%Y/%m/%d"): day.isoformat() for day in iter_days(rng.start, rng.end)}
    if not wanted:
        return
    song_lookup = build_song_lookup(songs)
    seen_keys = set()
    for track_id in song_totals:
        song = songs.get(track_id, {})
        for title in (song.get("base_title"), song.get("title")):
            key = song_exact_key(title)
            if key:
                seen_keys.add(key)

    for path in DAILY_ARCHIVE_FILES:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            date_columns = [column for column in (reader.fieldnames or []) if column in wanted]
            if not date_columns:
                continue
            for row in reader:
                title = (row.get("Title") or "").strip()
                if not is_recap_song(title):
                    continue
                exact_key = song_exact_key(title)
                if not exact_key:
                    continue
                total = 0
                best_streams = 0
                best_day = ""
                for column in date_columns:
                    streams = to_int(row.get(column)) or 0
                    if streams <= 0:
                        continue
                    total += streams
                    if streams > best_streams:
                        best_streams = streams
                        best_day = wanted[column]
                if total <= 0:
                    continue

                catalog_song = {}
                for key in song_lookup_keys(title):
                    catalog_song = song_lookup.get(key, {})
                    if catalog_song:
                        break
                track_id = catalog_song.get("track_id") or f"archive:{exact_key}"
                catalog_exact_keys = {
                    song_exact_key(catalog_title)
                    for catalog_title in (catalog_song.get("base_title"), catalog_song.get("title"))
                    if catalog_title
                }
                versioned_exact_match = (
                    bool(catalog_song)
                    and exact_key in catalog_exact_keys
                    and exact_key != song_base_key(title)
                )
                existing_total = song_totals.get(track_id)
                if existing_total:
                    if versioned_exact_match and total > existing_total * 2:
                        song_totals[track_id] = total
                        song_best[track_id] = (best_streams, best_day)
                        seen_keys.add(exact_key)
                    continue
                if exact_key in seen_keys:
                    continue
                song_totals[track_id] += total
                song_best[track_id] = (best_streams, best_day)
                archive_meta[track_id] = {
                    "title": catalog_song.get("title") or title,
                    "base_title": catalog_song.get("base_title") or title,
                    "primary_album": catalog_song.get("primary_album") or "Other",
                    "image_url": catalog_song.get("image_url") or "",
                }
                seen_keys.add(exact_key)
                for catalog_title in (catalog_song.get("base_title"), catalog_song.get("title")):
                    catalog_key = song_exact_key(catalog_title)
                    if catalog_key:
                        seen_keys.add(catalog_key)


def spotify_period_stats(rng: Range) -> dict[str, Any]:
    songs, albums = read_spotify_catalog()
    song_totals: dict[str, int] = defaultdict(int)
    song_best: dict[str, tuple[int, str]] = {}
    archive_meta: dict[str, dict[str, Any]] = {}
    dates_used = []

    for day in iter_days(rng.start, rng.end):
        path = WEB_HISTORY / f"{day.isoformat()}.json"
        if not path.exists():
            continue
        dates_used.append(day.isoformat())
        data = load_json(path)
        for track_id, values in data.items():
            daily = to_int(values.get("d") if isinstance(values, dict) else None) or 0
            if daily <= 0:
                continue
            song_totals[track_id] += daily
            if daily > song_best.get(track_id, (0, ""))[0]:
                song_best[track_id] = (daily, day.isoformat())

    apply_daily_archive_fallback(rng, songs, song_totals, song_best, archive_meta)

    songs_ranked = []
    for track_id, total in song_totals.items():
        song = songs.get(track_id, {}) or archive_meta.get(track_id, {})
        if not is_recap_song(song.get("title") or track_id):
            continue
        family_title = canonical_song_family(song.get("base_title") or song.get("title") or track_id)
        songs_ranked.append({
            "track_id": track_id,
            "title": song.get("title") or track_id,
            "family_key": norm_key(family_title),
            "compare_key": loose_song_key(family_title or song.get("title") or track_id),
            "family_title": family_title or song.get("title") or track_id,
            "album": song.get("primary_album") or "Other",
            "era": canonical_era(song.get("primary_album") or "Other"),
            "image_url": song.get("image_url") or "",
            "streams": total,
            "best_day_streams": song_best.get(track_id, (0, ""))[0],
            "best_day": song_best.get(track_id, (0, ""))[1],
        })
    songs_ranked.sort(key=lambda item: (-item["streams"], item["title"].casefold()))
    for index, item in enumerate(songs_ranked, 1):
        item["rank"] = index

    combined_by_key: dict[str, dict[str, Any]] = {}
    eras_by_key: dict[str, dict[str, Any]] = {}
    for item in songs_ranked:
        family_key = item["family_key"] or item["track_id"]
        combined = combined_by_key.setdefault(family_key, {
            "family_key": family_key,
            "title": item["family_title"] or item["title"],
            "album": item["album"],
            "era": item["era"],
            "image_url": item.get("image_url") or "",
            "streams": 0,
            "best_day_streams": 0,
            "best_day": "",
            "versions": 0,
        })
        combined["streams"] += item["streams"]
        combined["versions"] += 1
        if item["best_day_streams"] > combined["best_day_streams"]:
            combined["best_day_streams"] = item["best_day_streams"]
            combined["best_day"] = item["best_day"]
        if not combined.get("image_url") and item.get("image_url"):
            combined["image_url"] = item["image_url"]

        era_key = norm_key(item["era"])
        era = eras_by_key.setdefault(era_key, {
            "era_key": era_key,
            "title": item["era"],
            "image_url": item.get("image_url") or "",
            "streams": 0,
            "track_count": 0,
        })
        era["streams"] += item["streams"]
        era["track_count"] += 1
        if not era.get("image_url") and item.get("image_url"):
            era["image_url"] = item["image_url"]

    songs_combined = sorted(combined_by_key.values(), key=lambda item: (-item["streams"], item["title"].casefold()))
    eras_ranked = sorted(eras_by_key.values(), key=lambda item: (-item["streams"], item["title"].casefold()))
    for index, item in enumerate(songs_combined, 1):
        item["rank"] = index
    era_rank = 1
    for item in eras_ranked:
        if is_misc_era(item):
            item["rank"] = None
            continue
        item["rank"] = era_rank
        era_rank += 1

    return {
        "dates_used": dates_used,
        "songs_non_combined": songs_ranked,
        "songs_combined": songs_combined,
        "eras": eras_ranked,
        "songs": songs_ranked,
        "albums": eras_ranked,
    }


def enrich_spotify_comparisons(current: dict[str, Any], comparisons: dict[str, dict[str, Any]]) -> None:
    non_combined_maps = {
        key: {item["track_id"]: item for item in stats.get("songs_non_combined", [])}
        for key, stats in comparisons.items()
    }
    combined_maps = {
        key: {item["family_key"]: item for item in stats.get("songs_combined", [])}
        for key, stats in comparisons.items()
    }
    era_maps = {
        key: {item["era_key"]: item for item in stats.get("eras", [])}
        for key, stats in comparisons.items()
    }
    for item in current.get("songs_non_combined", []):
        item["comparisons"] = {}
        for key, lookup in non_combined_maps.items():
            previous = lookup.get(item["track_id"])
            item["comparisons"][key] = {
                "streams": previous.get("streams") if previous else None,
                "best_day_streams": previous.get("best_day_streams") if previous else None,
                "rank": previous.get("rank") if previous else None,
            }
    for item in current.get("songs_combined", []):
        item["comparisons"] = {}
        for key, lookup in combined_maps.items():
            previous = lookup.get(item["family_key"])
            item["comparisons"][key] = {
                "streams": previous.get("streams") if previous else None,
                "best_day_streams": previous.get("best_day_streams") if previous else None,
                "rank": previous.get("rank") if previous else None,
            }
    for item in current.get("eras", []):
        item["comparisons"] = {}
        for key, lookup in era_maps.items():
            previous = lookup.get(item["era_key"])
            item["comparisons"][key] = {
                "streams": previous.get("streams") if previous else None,
                "rank": previous.get("rank") if previous else None,
            }
    current["songs"] = current.get("songs_non_combined", [])
    current["albums"] = current.get("eras", [])


def short_title(title: str, limit: int = 48) -> str:
    title = str(title or "").strip()
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "..."


def make_rank_lines(items: list[dict[str, Any]], value_key: str, *, top: int, label: str = "") -> list[str]:
    lines = []
    for index, item in enumerate(items[:top], 1):
        value = item.get(value_key)
        value_text = f"{fmt_int(value)}{label}" if isinstance(value, int) else str(value)
        title = item.get("title") or item.get("album") or ""
        lines.append(f"{index}. {short_title(title)} - {value_text}")
    return lines


def is_misc_era(item: dict[str, Any]) -> bool:
    return norm_key(item.get("title") or item.get("era") or "") == "misc"


def eras_for_display(eras: list[dict[str, Any]], top: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    misc = next((item for item in eras if is_misc_era(item)), None)
    ranked = [item for item in eras if not is_misc_era(item)]
    return ranked[:top], misc


def make_era_lines(eras: list[dict[str, Any]], *, top: int) -> list[str]:
    ranked, misc = eras_for_display(eras, top)
    lines = make_rank_lines(ranked, "streams", top=top)
    if misc:
        lines.append(f"Misc - {fmt_int(misc.get('streams'))}")
    return lines


def append_chunked_section(tweets: list[str], title: str, lines: list[str]) -> None:
    current = title
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= 280:
            current = candidate
            continue
        if current:
            tweets.append(current)
        current = f"{title} (cont.)\n{line}" if len(f"{title} (cont.)\n{line}") <= 280 else line
    if current:
        tweets.append(current)


def build_thread(
    rng: Range,
    spotify: dict[str, Any],
    top: int,
) -> list[str]:
    sp_dates = spotify.get("dates_used") or []
    date_note = ""
    if sp_dates and (sp_dates[0] != rng.start.isoformat() or sp_dates[-1] != rng.end.isoformat()):
        date_note = f"\nSpotify data available: {sp_dates[0]} to {sp_dates[-1]}."

    tweets = [
        f"{rng.label} Spotify recap for Taylor Swift.{date_note}",
    ]
    append_chunked_section(
        tweets,
        "Spotify - top songs (non-combined)",
        make_rank_lines(spotify["songs_non_combined"], "streams", top=top),
    )
    append_chunked_section(
        tweets,
        "Spotify - top songs (combined)",
        make_rank_lines(spotify["songs_combined"], "streams", top=top),
    )
    append_chunked_section(
        tweets,
        "Spotify - top eras",
        make_era_lines(spotify["eras"], top=top),
    )

    return tweets


def import_comp_tables():
    repo_collectors = REPO_ROOT / "collectors"
    for path in (REPO_ROOT, repo_collectors):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
    from comp import tables_image  # noqa: E402

    return tables_image


def clear_previous_images(output_dir: Path, rng: Range) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    removed = 0
    for pattern in (f"period_recap_{rng.slug}_*.png", f"period_recap_{rng.slug}_*.html"):
        for path in output_dir.glob(pattern):
            if path.is_file():
                path.unlink()
                removed += 1
    if removed:
        print(f"Deleted {removed} previous recap preview file(s) from {output_dir}")


def recap_period_output_dir(base_dir: Path, rng: Range) -> Path:
    return base_dir / rng.slug


def image_art(tables_image, image_url: str) -> str:
    data_uri = tables_image.url_to_data_uri(image_url or "")
    if data_uri:
        return f'<img class="art" src="{html.escape(data_uri, quote=True)}" />'
    return '<div class="art-ph"></div>'


def clamp_channel(value: float) -> int:
    return max(0, min(255, int(round(value))))


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        return 29, 185, 84
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def adjust_color(hex_color: str, *, saturation: float = 1.0, value: float = 1.0) -> str:
    r, g, b = hex_to_rgb(hex_color)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    s = max(0.0, min(1.0, s * saturation))
    v = max(0.0, min(1.0, v * value))
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return f"#{clamp_channel(r2 * 255):02x}{clamp_channel(g2 * 255):02x}{clamp_channel(b2 * 255):02x}"


def dominant_color_from_data_uri(data_uri: str) -> str:
    if Image is None or not data_uri.startswith("data:image/") or "," not in data_uri:
        return "#1db954"
    try:
        raw = base64.b64decode(data_uri.split(",", 1)[1])
        img = Image.open(io.BytesIO(raw)).convert("RGB").resize((64, 64), Image.LANCZOS)
        pixels = list(img.getdata())
        filtered = [
            (r, g, b) for r, g, b in pixels
            if not (r > 225 and g > 225 and b > 225)
            and not (r < 25 and g < 25 and b < 25)
        ] or pixels
        r = sum(pixel[0] for pixel in filtered) / len(filtered)
        g = sum(pixel[1] for pixel in filtered) / len(filtered)
        b = sum(pixel[2] for pixel in filtered) / len(filtered)
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        s = max(0.38, min(1.0, s * 1.65))
        v = max(0.45, min(0.9, v * 0.95))
        r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
        return f"#{clamp_channel(r2 * 255):02x}{clamp_channel(g2 * 255):02x}{clamp_channel(b2 * 255):02x}"
    except Exception:
        return "#1db954"


def header_background_for_cover(tables_image, image_url: str) -> str:
    data_uri = tables_image.url_to_data_uri(image_url or "")
    base = dominant_color_from_data_uri(data_uri)
    left = adjust_color(base, saturation=1.16, value=1.05)
    right = adjust_color(base, saturation=1.25, value=0.56)
    return f"linear-gradient(135deg,{left} 0%,{right} 100%)"


RECAP_EXTRA_CSS = """
.col-num{line-height:1.08;display:flex;flex-direction:column;align-items:flex-end;justify-content:center}
.metric-main{display:block;font-size:14px;font-weight:700}
.metric-sub{display:block;font-size:10px;font-weight:600;color:#667085;margin-top:3px}
.col-num.pos .metric-main,.col-num.pos{color:#067647}
.col-num.neg .metric-main,.col-num.neg{color:#b42318}
.col-num.neutral .metric-main,.col-num.neutral{color:#667085}
.col-rank-delta{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-size:10px;font-weight:800;text-transform:uppercase;line-height:1.05}
.rank-delta.pos{color:#067647}
.rank-delta.neg{color:#b42318}
.rank-delta.neutral{color:#667085}
"""


def value_cell(value: str | dict[str, str]) -> str:
    if isinstance(value, dict):
        cls = value.get("class", "")
        main = value.get("main", "")
        sub = value.get("sub", "")
        html_value = value.get("html", "")
        if html_value:
            return f'  <div class="col-num {html.escape(cls)}">{html_value}</div>'
        sub_html = f'<span class="metric-sub">{html.escape(sub)}</span>' if sub else ""
        return f'  <div class="col-num {html.escape(cls)}"><span class="metric-main">{html.escape(main)}</span>{sub_html}</div>'
    return f'  <div class="col-num">{html.escape(str(value))}</div>'


def rank_change_parts(current_rank: int | None, previous_rank: int | None) -> tuple[str, str]:
    if current_rank is None:
        return "", ""
    if previous_rank is None:
        return "new", "pos"
    delta = int(previous_rank) - int(current_rank)
    if delta == 0:
        return "=", "neutral"
    sign = "+" if delta > 0 else "-"
    cls = "pos" if delta > 0 else "neg"
    return f"{sign}{abs(delta)}", cls


def rank_delta_cell(item: dict[str, Any], compare_keys: list[str]) -> str:
    parts: list[str] = []
    comparisons = item.get("comparisons") or {}
    show_labels = len(compare_keys) > 1
    labels = {"prev": "P", "yoy": "Y"}
    for key in compare_keys:
        text, cls = rank_change_parts(item.get("rank"), (comparisons.get(key) or {}).get("rank"))
        if not text:
            continue
        prefix = f"{labels.get(key, key[:1].upper())} " if show_labels else ""
        parts.append(f'<span class="rank-delta {html.escape(cls)}">{html.escape(prefix + text)}</span>')
    if not parts:
        parts.append('<span class="rank-delta neutral">-</span>')
    return '<div class="col-rank-delta">' + "".join(parts) + "</div>"


def delta_cell(
    current: int | None,
    previous: int | None,
    *,
    current_rank: int | None = None,
    previous_rank: int | None = None,
) -> dict[str, str]:
    text, cls = fmt_stream_delta(current, previous)
    if "<" in text:
        main, rest = text.split("<span", 1)
        sub = rest.split(">", 1)[1].rsplit("</span>", 1)[0]
        return {
            "class": cls,
            "html": f'<span class="metric-main">{html.escape(main)}</span><span class="metric-sub">{html.escape(sub)}</span>',
        }
    return {"class": cls, "html": f'<span class="metric-main">{html.escape(text)}</span>'}


def metric_row_html(
    tables_image,
    *,
    index: int,
    title: str,
    subtitle: str,
    image_url: str,
    values: list[str | dict[str, str]],
    rank_label: str | None = None,
    rank_delta_html: str = "",
) -> str:
    row_class = "data-row row-gold" if index == 1 else ("data-row row-odd" if index % 2 else "data-row")
    value_html = "\n".join(value_cell(value) for value in values)
    rank_text = rank_label if rank_label is not None else f"#{index}"
    return f"""<div class="{row_class}">
  <div class="col-rank">{html.escape(rank_text)}</div>
  {rank_delta_html or '<div class="col-rank-delta"><span class="rank-delta neutral">-</span></div>'}
  <div class="col-entity">
    {image_art(tables_image, image_url)}
    <div class="entity-info">
      <div class="entity-name">{html.escape(title)}</div>
      <div class="entity-sub">{html.escape(subtitle)}</div>
    </div>
  </div>
{value_html}
</div>"""


def render_recap_table(
    tables_image,
    *,
    output_dir: Path,
    rng: Range,
    key: str,
    title: str,
    subtitle: str,
    date_text: str,
    rows_html: str,
    col_heads: list[tuple[str, bool]],
    grid_cols: str,
    header_background: str,
    keep_html: bool,
) -> Path:
    html_text = tables_image.build_table_html(
        title=title,
        subtitle=subtitle,
        col_heads=col_heads,
        grid_cols=grid_cols,
        rows_html=rows_html,
        handle="@swiftiescharts",
        date_str=date_text,
        headers_dir=HEADERS_DIR,
        body_width=920,
        art_size=48,
        header_background=header_background,
        extra_css=RECAP_EXTRA_CSS,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"period_recap_{rng.slug}_{key}.png"
    html_path = output_dir / f"period_recap_{rng.slug}_{key}.html"
    print(f"[recap-image] Writing HTML: {html_path}", flush=True)
    print(f"[recap-image] Rendering PNG: {out_path}", flush=True)
    return tables_image.render_html_to_png(html_text, out_path, html_path, width=920, keep_html=keep_html)


def render_recap_images(
    rng: Range,
    spotify: dict[str, Any],
    *,
    output_dir: Path,
    top: int,
    keep_html: bool,
    compare_labels: dict[str, str],
) -> list[Path]:
    tables_image = import_comp_tables()
    output_dir = recap_period_output_dir(output_dir, rng)
    clear_previous_images(output_dir, rng)
    date_text = rng.label
    images: list[Path] = []
    compare_keys = [key for key in ("prev", "yoy") if key in compare_labels]
    compare_heads = [(f"Vs {compare_labels[key]}", True) for key in compare_keys]
    stream_grid = "52px 46px minmax(300px,1fr) 130px" + (" " + " ".join("108px" for _ in compare_keys) if compare_keys else "")
    song_grid = "52px 46px minmax(230px,1fr) 120px" + (" " + " ".join("94px" for _ in compare_keys) if compare_keys else "") + " 122px"
    non_combined_header = header_background_for_cover(
        tables_image,
        (spotify.get("songs_non_combined") or [{}])[0].get("image_url") or "",
    )
    combined_header = header_background_for_cover(
        tables_image,
        (spotify.get("songs_combined") or [{}])[0].get("image_url") or "",
    )
    song_rows = "\n".join(
        metric_row_html(
            tables_image,
            index=index,
            title=item["title"],
            subtitle=item.get("album") or "",
            image_url=item.get("image_url") or "",
            values=[
                fmt_int(item["streams"]),
                *[
                    delta_cell(
                        item.get("streams"),
                        (item.get("comparisons") or {}).get(key, {}).get("streams"),
                        current_rank=item.get("rank"),
                        previous_rank=(item.get("comparisons") or {}).get(key, {}).get("rank"),
                    )
                    for key in compare_keys
                ],
                {"main": fmt_int(item["best_day_streams"]), "sub": item["best_day"]},
            ],
            rank_delta_html=rank_delta_cell(item, compare_keys),
        )
        for index, item in enumerate(spotify["songs_non_combined"][:top], 1)
    )
    images.append(render_recap_table(
        tables_image,
        output_dir=output_dir,
        rng=rng,
        key="spotify_songs",
        title=f"Taylor Swift songs in {rng.label} (Not Combined)",
        subtitle="Spotify streams recap",
        date_text=date_text,
        rows_html=song_rows,
        col_heads=[("Pos", False), ("Rank", False), ("Track", False), ("Streams", True), *compare_heads, ("Best day", True)],
        grid_cols=song_grid,
        header_background=non_combined_header,
        keep_html=keep_html,
    ))

    combined_rows = "\n".join(
        metric_row_html(
            tables_image,
            index=index,
            title=item["title"],
            subtitle=item.get("album") or "",
            image_url=item.get("image_url") or "",
            values=[
                fmt_int(item["streams"]),
                *[
                    delta_cell(
                        item.get("streams"),
                        (item.get("comparisons") or {}).get(key, {}).get("streams"),
                        current_rank=item.get("rank"),
                        previous_rank=(item.get("comparisons") or {}).get(key, {}).get("rank"),
                    )
                    for key in compare_keys
                ],
                {"main": fmt_int(item["best_day_streams"]), "sub": item["best_day"]},
            ],
            rank_delta_html=rank_delta_cell(item, compare_keys),
        )
        for index, item in enumerate(spotify["songs_combined"][:top], 1)
    )
    images.append(render_recap_table(
        tables_image,
        output_dir=output_dir,
        rng=rng,
        key="spotify_songs_combined",
        title=f"Taylor Swift songs in {rng.label} (Combined)",
        subtitle="Spotify streams recap",
        date_text=date_text,
        rows_html=combined_rows,
        col_heads=[("Pos", False), ("Rank", False), ("Track", False), ("Streams", True), *compare_heads, ("Best day", True)],
        grid_cols=song_grid,
        header_background=combined_header,
        keep_html=keep_html,
    ))

    era_ranked, era_misc = eras_for_display(spotify["eras"], top)
    era_header = header_background_for_cover(
        tables_image,
        (era_ranked or spotify.get("eras") or [{}])[0].get("image_url") or "",
    )
    era_display_rows = [
        (
            index,
            item,
            None,
        )
        for index, item in enumerate(era_ranked, 1)
    ]
    if era_misc:
        era_display_rows.append((len(era_display_rows) + 1, era_misc, ""))
    era_rows = "\n".join(
        metric_row_html(
            tables_image,
            index=index,
            title=item["title"],
            subtitle=f"{item.get('track_count') or 0} tracks",
            image_url=item.get("image_url") or "",
            values=[
                fmt_int(item["streams"]),
                *[
                    delta_cell(
                        item.get("streams"),
                        (item.get("comparisons") or {}).get(key, {}).get("streams"),
                        current_rank=item.get("rank"),
                        previous_rank=(item.get("comparisons") or {}).get(key, {}).get("rank"),
                    )
                    for key in compare_keys
                ],
            ],
            rank_label=rank_label,
            rank_delta_html=rank_delta_cell(item, compare_keys),
        )
        for index, item, rank_label in era_display_rows
    )
    images.append(render_recap_table(
        tables_image,
        output_dir=output_dir,
        rng=rng,
        key="spotify_eras",
        title=f"Taylor Swift eras in {rng.label}",
        subtitle="Spotify streams recap",
        date_text=date_text,
        rows_html=era_rows,
        col_heads=[("Pos", False), ("Rank", False), ("Era", False), ("Streams", True), *compare_heads],
        grid_cols=stream_grid,
        header_background=era_header,
        keep_html=keep_html,
    ))

    return images


def validate_tweets(tweets: list[str]) -> bool:
    ok = True
    for index, tweet in enumerate(tweets, 1):
        length = len(tweet)
        if length > 280:
            print(f"Post {index} is too long: {length}/280", file=sys.stderr)
            ok = False
    return ok


def write_artifact(rng: Range, payload: dict[str, Any], tweets: list[str], image_paths: list[Path]) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACT_DIR / f"{rng.slug}_recap.json"
    if out.exists():
        out.unlink()
        print(f"Deleted previous preview: {out}")
    out.write_text(
        json.dumps({**payload, "tweets": tweets, "images": [str(path) for path in image_paths]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def stats_has_stream_data(stats: dict[str, Any]) -> bool:
    for section in ("songs_non_combined", "songs_combined", "eras"):
        for item in stats.get(section, []):
            if (item.get("streams") or 0) > 0:
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Build/post a period recap for @swiftiescharts.")
    parser.add_argument("--period", choices=["week", "month", "year"], default="month")
    parser.add_argument("--date", help="Anchor date for period calculations, YYYY-MM-DD.")
    parser.add_argument("--current", action="store_true", help="Use the current period containing --date/today.")
    parser.add_argument("--start", help="Custom start date, YYYY-MM-DD.")
    parser.add_argument("--end", help="Custom end date, YYYY-MM-DD.")
    parser.add_argument("--top", type=int, default=15, help="Number of entries per section.")
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR, help="Directory for generated recap images.")
    parser.add_argument("--no-images", action="store_true", help="Only generate the text thread/artifact.")
    parser.add_argument("--keep-html", action="store_true", default=True, help="Keep generated HTML previews next to PNGs.")
    parser.add_argument("--no-keep-html", action="store_false", dest="keep_html", help="Delete generated HTML previews.")
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION, help="Path to twitter_session.json.")
    parser.add_argument("--post", action="store_true", help="Post the thread to X/Twitter.")
    parser.add_argument("--yes", action="store_true", help="Required together with --post.")
    args = parser.parse_args()

    rng = build_range(args)
    spotify = spotify_period_stats(rng)
    candidate_compare_ranges = comparison_ranges(rng)
    spotify_comparisons: dict[str, dict[str, Any]] = {}
    compare_ranges: list[tuple[str, Range]] = []
    for key, compare_rng in candidate_compare_ranges:
        stats = spotify_period_stats(compare_rng)
        if not stats_has_stream_data(stats):
            continue
        spotify_comparisons[key] = stats
        compare_ranges.append((key, compare_rng))
    compare_labels = {key: compact_label(compare_rng) for key, compare_rng in compare_ranges}
    enrich_spotify_comparisons(spotify, spotify_comparisons)
    top = max(1, args.top)
    tweets = build_thread(rng, spotify, top)
    image_paths: list[Path] = []
    if not args.no_images:
        image_paths = render_recap_images(
            rng,
            spotify,
            output_dir=args.image_dir,
            top=top,
            keep_html=args.keep_html,
            compare_labels=compare_labels,
        )
    artifact = write_artifact(
        rng,
        {
            "range": {"start": rng.start.isoformat(), "end": rng.end.isoformat(), "label": rng.label},
            "comparison_ranges": {
                key: {
                    "start": compare_rng.start.isoformat(),
                    "end": compare_rng.end.isoformat(),
                    "label": compare_rng.label,
                }
                for key, compare_rng in compare_ranges
            },
            "spotify": spotify,
        },
        tweets,
        image_paths,
    )

    print(f"Range: {rng.start} -> {rng.end} ({rng.label})")
    for key, compare_rng in compare_ranges:
        print(f"Compare {key}: {compare_rng.start} -> {compare_rng.end} ({compare_rng.label})")
    print(f"Artifact: {artifact}")
    if image_paths:
        print("Images:")
        for image_path in image_paths:
            print(f"  {image_path}")
    print()
    for index, tweet in enumerate(tweets, 1):
        print(f"--- post {index}/{len(tweets)} ({len(tweet)}/280) ---")
        print(tweet)
        print()

    if not validate_tweets(tweets):
        return 2

    if args.post:
        if not args.yes:
            print("Dry run only. Re-run with --post --yes to publish.")
            return 0
        session = args.session.resolve()
        if not session.exists():
            print(f"Twitter session not found: {session}", file=sys.stderr)
            return 1
        if str(CORE_DIR) not in sys.path:
            sys.path.insert(0, str(CORE_DIR))
        if image_paths:
            from twitter import post_image_thread  # noqa: E402

            spotify_posts = [
                (f"{rng.label} Spotify recap: top songs (non-combined)", image_paths[0]),
                (f"{rng.label} Spotify recap: top songs (combined)", image_paths[1]),
                (f"{rng.label} Spotify recap: top eras", image_paths[2]),
            ]
            return 0 if post_image_thread(spotify_posts, session) else 1

        from twitter import post_thread  # noqa: E402

        return 0 if post_thread(tweets, session) else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
