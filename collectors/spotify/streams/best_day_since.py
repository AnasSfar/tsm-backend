#!/usr/bin/env python3
"""
Compute "best day since" notes from streams_history.csv.

Examples:
  python best_day_since.py
  python best_day_since.py 2026-05-07
  python best_day_since.py 2026-05-07 --limit 25
  python best_day_since.py 2026-05-07 --include-extras --no-write

By default, only album tracks from db/discography/albums/*.json are included.
This excludes songs.json extras.

NOTE: the admin Image Studio (tsm-frontend) has a JS mirror of the core rules
in frontend/src/components/imageTemplates/bestDaySince.js — if the record
logic here changes (thresholds, ignore days, combine guard…), update it too.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
DB_ROOT = REPO_ROOT / "db"
sys.path.insert(0, str(SCRIPT_DIR.parent))  # collectors/spotify/ for core.*
from core.data_paths import WEB_EXPORT_DATA_DIR, first_existing_db_history  # noqa: E402

HISTORY_PATH = first_existing_db_history("streams_history.csv")
DISCOGRAPHY_DIR = DB_ROOT / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
SONGS_JSON = DISCOGRAPHY_DIR / "songs.json"
MISC_JSON = DISCOGRAPHY_DIR / "misc.json"
FEATURES_JSON = DISCOGRAPHY_DIR / "features.json"
DEFAULT_OUTPUT = WEB_EXPORT_DATA_DIR / "best_day_since.json"
HISTORY_START_DATE = date(2025, 1, 1)
DEFAULT_MIN_DAYS = 30
# A "best day since X" whose beaten day X is at most this many days old counts as
# a *recent repeat* record (two comparable big days close together, not on
# consecutive days) - captions then read "has once again earned its best day
# since X" instead of "earned its best day since X".
RECENT_REPEAT_RECORD_DAYS = 60
LIVE_COLLECTION_MIN_DAYS = 30
LIVE_COLLECTION_MIN_PCT_CHANGE = 10.0
YEAR_RECORD_IGNORE_DAYS = 15
MONTH_RECORD_IGNORE_DAYS = 10
MONTH_RECORD_MIN_DAILY_STREAMS = 200_000
MONTH_RECORD_LAST_DAYS = 5


@dataclass(frozen=True)
class Track:
    track_id: str
    title: str
    album: str
    spotify_url: str
    song_family: str = ""
    is_alt_version: bool = False
    release_date: date | None = None


@dataclass(frozen=True)
class Point:
    day: date
    total: int | None
    daily: int | None


def extract_track_id(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"track/([A-Za-z0-9]+)", url)
    return match.group(1) if match else None


def parse_int(value: str | None) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_release_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def load_album_sections() -> list[dict]:
    sections: list[dict] = []
    if not ALBUMS_DIR.exists():
        return sections

    for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        album_name = payload.get("album", "")
        for section in payload.get("sections", []):
            if not isinstance(section, dict):
                continue
            item = dict(section)
            if not item.get("album"):
                item["album"] = album_name
            sections.append(item)

    return sections


def load_song_sections() -> list[dict]:
    sections: list[dict] = []
    for extra_path in (SONGS_JSON, FEATURES_JSON, MISC_JSON):
        if not extra_path.exists():
            continue
        try:
            payload = json.loads(extra_path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(payload, list):
            sections.extend(payload)
    return sections


def is_extra_track(section: dict, item: dict) -> bool:
    for value in (item.get("chart_extra"), section.get("chart_extra")):
        if isinstance(value, bool):
            return value
        if value is not None:
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "y", "on"}:
                return True
            if text in {"0", "false", "no", "n", "off"}:
                return False

    edition = str(item.get("edition") or "").strip().lower()
    track_type = str(item.get("type") or "").strip().lower()
    section_name = str(section.get("name") or section.get("section") or item.get("section") or "").strip().lower()
    display_section = str(item.get("display_section") or "").strip().lower()
    album = str(section.get("album") or item.get("album") or "").strip().lower()

    if edition in {"extras", "extra", "acoustic", "extended", "karaoke", "live", "other editions"}:
        return True
    if track_type in {"remix"}:
        return True
    return (
        re.search(r"extras|kworb|remix|karaoke|live|soundtrack|voice_memos|track_by_track|music_video|acoustic|bonus_versions|misc_standalone|long_pond", section_name)
        or re.search(r"extras|kworb extras|track by track|karaoke|live|soundtrack|long pond|acoustic", display_section)
        or re.search(r"extras|kworb extras|track by track|karaoke|live|soundtrack|long pond|acoustic", album)
    ) is not None


def load_tracks(*, include_extras: bool = False) -> dict[str, Track]:
    sections = load_album_sections()
    if include_extras:
        sections.extend(load_song_sections())

    tracks: dict[str, Track] = {}
    for section in sections:
        album = (section.get("album") or section.get("section") or "").strip()
        for item in section.get("tracks", []):
            if not isinstance(item, dict):
                continue
            if not include_extras and is_extra_track(section, item):
                continue
            url = (item.get("url") or item.get("spotify_url") or "").strip()
            track_id = extract_track_id(url)
            title = (item.get("title") or "").strip()
            if not track_id or not title or track_id in tracks:
                continue
            tracks[track_id] = Track(
                track_id=track_id,
                title=title,
                album=album,
                spotify_url=f"https://open.spotify.com/track/{track_id}",
                song_family=str(item.get("song_family") or "").strip(),
                is_alt_version=is_extra_track(section, item),
                release_date=parse_release_date(item.get("release_date") or section.get("release_date")),
            )

    return tracks


def load_history() -> dict[str, list[Point]]:
    history: dict[str, list[Point]] = {}
    if not HISTORY_PATH.exists():
        return history

    with HISTORY_PATH.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            track_id = (row.get("track_id") or "").strip()
            date_raw = (row.get("date") or "").strip()
            if not track_id or not date_raw:
                continue

            try:
                day = date.fromisoformat(date_raw)
            except ValueError:
                continue

            history.setdefault(track_id, []).append(
                Point(
                    day=day,
                    total=parse_int(row.get("streams")),
                    daily=parse_int(row.get("daily_streams")),
                )
            )

    for points in history.values():
        points.sort(key=lambda p: p.day)

    return history


def fill_missing_dailies(points: list[Point]) -> list[Point]:
    filled: list[Point] = []
    previous_total: int | None = None
    previous_day: date | None = None

    for point in points:
        daily = point.daily
        if (
            daily is None
            and point.total is not None
            and previous_total is not None
            and previous_day is not None
            and point.day == previous_day + timedelta(days=1)
        ):
            diff = point.total - previous_total
            daily = diff if diff >= 0 else None
        filled.append(Point(point.day, point.total, daily))
        if point.total is not None:
            previous_total = point.total
            previous_day = point.day

    return filled


def combine_points(points_by_track: list[list[Point]]) -> list[Point]:
    filled_by_track = [fill_missing_dailies(points) for points in points_by_track if points]
    dates = sorted({point.day for points in filled_by_track for point in points})
    combined: list[Point] = []

    for day in dates:
        total_sum = 0
        total_seen = False
        daily_sum = 0
        daily_seen = False
        for points in filled_by_track:
            point = next((p for p in points if p.day == day), None)
            if point is None:
                continue
            if point.total is not None:
                total_sum += point.total
                total_seen = True
            if point.daily is not None:
                daily_sum += point.daily
                daily_seen = True
        combined.append(
            Point(
                day=day,
                total=total_sum if total_seen else None,
                daily=daily_sum if daily_seen else None,
            )
        )

    return combined


def recent_daily_points(points: list[Point], target_date: date, *, days: int = 14) -> list[Point]:
    """Last ``days`` calendar days ending at ``target_date`` (inclusive), one
    Point per day in ascending order — missing days get an empty Point rather
    than being dropped, so callers (the Chart Sheet bar chart) can render a
    fixed number of columns without reshuffling. Reuses fill_missing_dailies
    so a short single-day gap between two known totals still gets a daily
    figure instead of showing empty."""
    filled = {point.day: point for point in fill_missing_dailies(points)}
    start = target_date - timedelta(days=days - 1)
    return [
        filled.get(day) or Point(day=day, total=None, daily=None)
        for day in (start + timedelta(days=offset) for offset in range(days))
    ]


def _chart_sheet_short_date(value: date) -> str:
    return f"{value.month}/{value.day}"


def _chart_sheet_k_label(value: int) -> str:
    if value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}"
        return f"{text[:-2] if text.endswith('.0') else text}M"
    if value >= 1_000:
        return f"{round(value / 1000)}K"
    return str(value)


def build_chart_sheet_bars(
    points: list[Point],
    target_date: date,
    *,
    days: int = 14,
    historical_date: date | None = None,
    historical_daily: int | None = None,
) -> list[dict]:
    """Bar-column dicts ready for song_card_chart_sheet.render_chart_sheet_card.

    ``historical_date``/``historical_daily`` are the previous-record callback
    (best_day_since row's previous_higher_or_equal_date/_daily) — pass both to
    get a dimmed callback bar + gap marker before the recent run, or leave
    both None for a plain run (Weekend Gainer, or a best_ever row with no
    prior record to reference). Bar heights are scaled proportionally to the
    tallest value in the window, including the historical one when present —
    that callback bar can end up taller than today's, since a "best day
    since X" row means X's own total was at or above today's, not below it."""
    recent = recent_daily_points(points, target_date, days=days)
    values = [point.daily for point in recent if point.daily is not None]
    if historical_daily is not None:
        values.append(historical_daily)
    max_value = max(values) if values else 1

    def _height_pct(value: int) -> float:
        return max(4.0, min(100.0, (value / max_value) * 100))

    bars: list[dict] = []
    if historical_date is not None and historical_daily is not None:
        bars.append({
            "type": "bar",
            "date_label": f"{historical_date.month}/{historical_date.day}/{historical_date.strftime('%y')}",
            "value_label": _chart_sheet_k_label(historical_daily),
            "height_pct": _height_pct(historical_daily),
            "dimmed": True,
        })
        bars.append({"type": "gap"})

    for point in recent:
        if point.daily is None:
            bars.append({
                "type": "bar",
                "date_label": _chart_sheet_short_date(point.day),
                "value_label": "–",
                "height_pct": 4.0,
                "today": point.day == target_date,
            })
            continue
        bars.append({
            "type": "bar",
            "date_label": _chart_sheet_short_date(point.day),
            "value_label": _chart_sheet_k_label(point.daily),
            "height_pct": _height_pct(point.daily),
            "today": point.day == target_date,
        })
    return bars


def combined_tracks_for(track: Track, tracks: dict[str, Track]) -> list[Track]:
    family = (track.song_family or "").strip()
    if not family:
        return [track]
    related = [
        candidate for candidate in tracks.values()
        if candidate.song_family == family and not candidate.is_alt_version
    ]
    return related or [track]


def compute_best_day_since_combined(
    track: Track,
    related_tracks: list[Track],
    history: dict[str, list[Point]],
    target_date: date,
) -> dict | None:
    # Solo streams take priority: only fall back to the family-wide sum (and
    # flag the row as "combined") when the track's own streams alone don't
    # produce a best-day-since result.
    solo_row = compute_best_day_since(track, history.get(track.track_id) or [], target_date)
    if solo_row:
        solo_row["combined"] = False
        solo_row["combined_track_ids"] = [track.track_id]
        solo_row["combined_version_count"] = 1
        return solo_row

    points_by_track = [
        history.get(related.track_id) or []
        for related in related_tracks
    ]
    points = combine_points(points_by_track)

    # Only compare against days where every related track already existed,
    # so a "combined" record is checked against past combined sums — never
    # against an old day when a single version's solo total stood in for
    # the sum because the other versions hadn't been released yet.
    track_start_days = [pts[0].day for pts in points_by_track if pts]
    if len(track_start_days) > 1:
        combined_start = max(track_start_days)
        points = [point for point in points if point.day >= combined_start]

    row = compute_best_day_since(track, points, target_date)
    if not row:
        return None
    related_ids = [related.track_id for related in related_tracks]
    row["combined"] = len(related_ids) > 1
    row["combined_track_ids"] = related_ids
    row["combined_version_count"] = len(related_ids)
    return row


def load_album_track_ids(tracks: dict[str, Track]) -> dict[str, list[str]]:
    """Group standard-edition track IDs (no karaoke/live/remix/acoustic) by album."""
    by_album: dict[str, list[str]] = {}
    for track_id, track in tracks.items():
        if track.is_alt_version or not track.album:
            continue
        by_album.setdefault(track.album, []).append(track_id)
    return by_album


_ERA_KEY_ALIASES = {
    "fearless (taylor's version)": "fearless",
    "speak now (taylor's version)": "speak now",
    "red (taylor's version)": "red",
    "1989 (taylor's version)": "1989",
    "the tortured poets department: the anthology": "the tortured poets department",
    "midnights (the til dawn edition)": "midnights",
    "midnights (3am edition)": "midnights",
    "folklore: the long pond studio sessions": "folklore",
}


def era_key(album: str | None) -> str:
    """Normalize an album name to a stable era key: collapses Taylor's Version,
    deluxe / anniversary / 3am / Til Dawn / anthology editions and karaoke onto
    the base era so "Red" and "Red (Taylor's Version)" group together."""
    text = re.sub(r"\s+", " ", (album or "").strip())
    if not text:
        return ""

    normalized = text.casefold()
    if normalized in _ERA_KEY_ALIASES:
        return _ERA_KEY_ALIASES[normalized]

    text = re.sub(r"\s*\(taylor's version\)", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\s*\((?:deluxe|standard|expanded|bonus|anniversary|karaoke|acoustic|live|tour|edition|"
        r"the anthology|the til dawn edition|3am edition)[^)]*\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*[-–—]\s*(?:deluxe|standard|expanded|bonus|anniversary|karaoke|acoustic|live|tour|edition).*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*:\s*(?:the anthology|the long pond studio sessions|.*edition).*$", "", text, flags=re.IGNORECASE)

    karaoke_match = re.match(r"Taylor Swift Karaoke:\s*(.+)", text, flags=re.IGNORECASE)
    if karaoke_match:
        text = karaoke_match.group(1)

    return re.sub(r"\s+", " ", text.strip()).casefold()


def _era_recap_post_gate_ok(
    row: dict,
    *,
    min_daily_streams: int | None,
    min_pct_change: float | None,
    always_post_after_days: int,
) -> bool:
    """Mirror of ``post_best_day_since_twitter._passes_song_post_gate``: does this
    best-day row clear the bar for an individual song post (and therefore count
    toward an era recap)?"""
    if row.get("is_biggest_day_of_year"):
        return True
    if (row.get("days_since") or 0) > always_post_after_days:
        return True
    daily = int(row.get("daily_streams") or 0)
    if min_daily_streams is not None and daily >= min_daily_streams:
        return True
    previous_day_daily = row.get("previous_day_daily")
    if min_pct_change is not None and previous_day_daily and previous_day_daily > 0:
        if (daily - previous_day_daily) / previous_day_daily * 100 > min_pct_change:
            return True
    return min_daily_streams is None and min_pct_change is None


def era_recap_groups(
    target_date: date,
    *,
    min_songs: int = 5,
    min_days: int = LIVE_COLLECTION_MIN_DAYS,
    min_daily_streams: int | None = None,
    min_pct_change: float | None = LIVE_COLLECTION_MIN_PCT_CHANGE,
    always_post_after_days: int = 60,
    tracks: dict[str, Track] | None = None,
    history: dict[str, list[Point]] | None = None,
    exclude_predicate: Callable[[Track], bool] | None = None,
) -> list[dict]:
    """Group the day's best-day-since song records by era and keep only eras
    where at least ``min_songs`` songs both hit a record and clear the
    individual post gate. Feeds the dedicated per-era "best day recap" card and
    the web export. Records are not consumed here - a song still appears in the
    global recap and (unless suppressed by the era card) can get its own card.
    """
    tracks = tracks if tracks is not None else load_tracks(include_extras=False)
    history = history if history is not None else load_history()
    display_names = {era_key(album): album for album in load_album_track_ids(tracks)}

    buckets: dict[str, list[dict]] = {}
    for track_id, track in tracks.items():
        if exclude_predicate is not None and exclude_predicate(track):
            continue
        key = era_key(track.album)
        if not key:
            continue
        row = compute_best_day_since(track, history.get(track_id) or [], target_date)
        if not row:
            continue
        if not (row.get("is_biggest_day_of_year") or passes_filters(row, min_days=min_days)):
            continue
        if not _era_recap_post_gate_ok(
            row,
            min_daily_streams=min_daily_streams,
            min_pct_change=min_pct_change,
            always_post_after_days=always_post_after_days,
        ):
            continue
        buckets.setdefault(key, []).append(row)

    groups: list[dict] = []
    for key, rows in buckets.items():
        if len(rows) < min_songs:
            continue
        rows.sort(key=sort_key, reverse=True)
        groups.append({
            "era_key": key,
            "album": display_names.get(key, rows[0]["album"]),
            "count": len(rows),
            "track_ids": [row["track_id"] for row in rows],
            "items": rows,
        })
    groups.sort(key=lambda group: (group["count"], group["items"][0]["daily_streams"]), reverse=True)
    return groups


def best_day_marker_text(row: dict | None) -> str | None:
    """Short "since" marker for a best-day-since row, matching the album update
    image: "of the year" / "of the month" (no "since" prefix) or a long date
    like "November 26th, 2025"."""
    if not row or row.get("kind") not in ("since", "best_ever"):
        return None
    if row.get("is_biggest_day_of_year"):
        return "of the year"
    value = row.get("best_day_since")
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}$", value):
        marker_date = date.fromisoformat(value)
        return f"{marker_date.strftime('%B')} {ordinal(marker_date.day)}, {marker_date.year}"
    if row.get("is_biggest_day_of_month"):
        return "of the month"
    return None


def best_day_marker_labels(
    track_ids: "list[str] | set[str]",
    target_date: date,
    *,
    min_days: int = DEFAULT_MIN_DAYS,
    tracks: dict[str, Track] | None = None,
    history: dict[str, list[Point]] | None = None,
) -> dict[str, str]:
    """{track_id: marker text} for tracks that hit a *solo* best-day-since record
    on ``target_date`` (combined-family records are not surfaced - same rule as
    generate_album_update_image._best_day_labels_for_sections). Powers the
    ``* since ...`` note on Top Songs / Top Eras / Gainers cards."""
    tracks = tracks if tracks is not None else load_tracks(include_extras=True)
    history = history if history is not None else load_history()
    labels: dict[str, str] = {}
    for track_id in set(track_ids):
        track = tracks.get(track_id)
        if track is None:
            continue
        row = compute_best_day_since(track, history.get(track_id) or [], target_date)
        if not row or row.get("kind") != "since":
            continue
        if not (row.get("is_biggest_day_of_year") or passes_filters(row, min_days=min_days)):
            continue
        label = best_day_marker_text(row)
        if label:
            labels[track_id] = label
    return labels


def compute_album_best_day_since(
    album: str,
    track_ids: list[str],
    history: dict[str, list[Point]],
    target_date: date,
) -> dict | None:
    points_by_track = [history.get(track_id) or [] for track_id in track_ids]
    points = combine_points(points_by_track)

    # Same guard as compute_best_day_since_combined: only compare against
    # days where every album track already had data, so we're never
    # comparing today's full-album total against a day when the album
    # wasn't complete yet (e.g. before a bonus/deluxe track was added).
    track_start_days = [pts[0].day for pts in points_by_track if pts]
    if len(track_start_days) > 1:
        combined_start = max(track_start_days)
        points = [point for point in points if point.day >= combined_start]

    album_track = Track(track_id="", title=album, album=album, spotify_url="")
    row = compute_best_day_since(album_track, points, target_date)
    if not row:
        return None
    row["album"] = album
    row["track_ids"] = track_ids
    return row


def latest_history_date(history: dict[str, list[Point]]) -> date | None:
    latest: date | None = None
    for points in history.values():
        if points:
            point_date = points[-1].day
            latest = point_date if latest is None or point_date > latest else latest
    return latest


def _has_complete_daily_span(points: list[Point], start: date, end: date) -> bool:
    by_day = {point.day: point for point in points}
    cursor = start
    while cursor <= end:
        point = by_day.get(cursor)
        if point is None or point.daily is None:
            return False
        cursor += timedelta(days=1)
    return True


def _is_biggest_daily_in_period(
    points: list[Point],
    *,
    target_date: date,
    current_daily: int,
    period_start: date,
) -> bool:
    if target_date < period_start:
        return False

    period_points = [
        point
        for point in points
        if period_start <= point.day <= target_date and point.daily is not None
    ]
    if not period_points:
        return False

    first_available = min(point.day for point in period_points)
    if not _has_complete_daily_span(points, first_available, target_date):
        return False

    return current_daily >= max(int(point.daily or 0) for point in period_points)


def _is_in_last_days_of_month(target_date: date, days: int) -> bool:
    last_day = calendar.monthrange(target_date.year, target_date.month)[1]
    return (last_day - target_date.day) < days


def period_record_flags(points: list[Point], target_date: date, current_daily: int) -> dict[str, bool]:
    year_start = date(target_date.year, 1, 1) + timedelta(days=YEAR_RECORD_IGNORE_DAYS)
    month_start = date(target_date.year, target_date.month, 1) + timedelta(days=MONTH_RECORD_IGNORE_DAYS)
    return {
        "is_biggest_day_of_year": _is_biggest_daily_in_period(
            points,
            target_date=target_date,
            current_daily=current_daily,
            period_start=year_start,
        ),
        "is_biggest_day_of_month": (
            current_daily > MONTH_RECORD_MIN_DAILY_STREAMS
            and _is_in_last_days_of_month(target_date, MONTH_RECORD_LAST_DAYS)
            and _is_biggest_daily_in_period(
                points,
                target_date=target_date,
                current_daily=current_daily,
                period_start=month_start,
            )
        ),
    }


def compute_best_day_since(track: Track, points: list[Point], target_date: date) -> dict | None:
    points = fill_missing_dailies(points)
    point_by_date = {point.day: point for point in points}
    current = point_by_date.get(target_date)
    if current is None or current.daily is None or current.daily <= 0:
        return None
    previous_day = point_by_date.get(target_date - timedelta(days=1))
    if previous_day is None or previous_day.total is None:
        return None
    record_flags = period_record_flags(points, target_date, current.daily)

    previous_points = [point for point in points if point.day < target_date and point.daily is not None]
    if not previous_points:
        return None

    last_at_or_above: Point | None = None
    for point in reversed(previous_points):
        if point.daily is not None and point.daily >= current.daily:
            last_at_or_above = point
            break

    if last_at_or_above is None:
        first_available_date = previous_points[0].day if previous_points else target_date
        if track.release_date is not None and track.release_date >= HISTORY_START_DATE:
            kind = "best_ever"
            best_day_since = "ever"
        else:
            return None

        return {
            "track_id": track.track_id,
            "title": track.title,
            "album": track.album,
            "spotify_url": track.spotify_url,
            "date": target_date.isoformat(),
            "daily_streams": current.daily,
            "previous_day_daily": previous_day.daily,
            "kind": kind,
            "best_day_since": best_day_since,
            "previous_higher_or_equal_date": None,
            "previous_higher_or_equal_daily": None,
            "days_since": None,
            "first_available_date": first_available_date.isoformat(),
            **record_flags,
        }

    if last_at_or_above.day >= target_date - timedelta(days=1):
        return None
    best_since = last_at_or_above.day

    return {
        "track_id": track.track_id,
        "title": track.title,
        "album": track.album,
        "spotify_url": track.spotify_url,
        "date": target_date.isoformat(),
        "daily_streams": current.daily,
        "previous_day_daily": previous_day.daily,
        "kind": "since",
        "best_day_since": best_since.isoformat(),
        "previous_higher_or_equal_date": last_at_or_above.day.isoformat(),
        "previous_higher_or_equal_daily": last_at_or_above.daily,
        "days_since": (target_date - best_since).days,
        "first_available_date": points[0].day.isoformat() if points else None,
        **record_flags,
    }


def format_int(value: int | None) -> str:
    return "?" if value is None else f"{value:,}"


def sort_key(row: dict) -> tuple[int, int, int]:
    is_record = 1 if row["kind"] == "best_ever" else 0
    days_since = row.get("days_since") or 0
    return (is_record, days_since, row["daily_streams"])


def passes_filters(row: dict, *, min_days: int, min_pct_change: float | None = None) -> bool:
    if min_pct_change is not None:
        previous_day_daily = row.get("previous_day_daily")
        if previous_day_daily is None or previous_day_daily <= 0:
            return False
        pct_change = (row["daily_streams"] - previous_day_daily) / previous_day_daily * 100
        if pct_change < min_pct_change:
            return False

    if row["kind"] == "before_history":
        return False
    if row["kind"] == "best_ever":
        return True
    return (row.get("days_since") or 0) >= min_days


def best_day_since_for_track(
    track_id: str,
    target_date: str,
    *,
    min_days: int = DEFAULT_MIN_DAYS,
    min_pct_change: float | None = None,
    combined: bool = False,
    keep_year_record: bool = False,
) -> dict | None:
    """Return best-day-since data for one track.

    ``keep_year_record``: a row flagged ``is_biggest_day_of_year`` is returned
    even when it would fail ``passes_filters`` (e.g. its ``days_since`` gap is
    shorter than ``min_days``). Biggest day of the year is always postable
    (owner decision 2026-08-29).
    """
    base_tracks = load_tracks(include_extras=False)
    all_tracks = load_tracks(include_extras=True)
    track = base_tracks.get(track_id) or all_tracks.get(track_id)
    if not track:
        return None

    history = load_history()
    if combined:
        row = compute_best_day_since_combined(
            track,
            combined_tracks_for(all_tracks.get(track_id, track), all_tracks),
            history,
            date.fromisoformat(target_date),
        )
    else:
        points = history.get(track_id)
        if not points:
            return None
        row = compute_best_day_since(track, points, date.fromisoformat(target_date))

    if not row:
        return None
    if (
        keep_year_record
        and row.get("is_biggest_day_of_year")
        and row.get("kind") in ("since", "best_ever")
    ):
        return row
    if not passes_filters(row, min_days=min_days, min_pct_change=min_pct_change):
        return None
    return row


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_long_date(value: str) -> str:
    if value == "ever":
        return "ever"
    if value == "before 2025":
        return "before 2025"
    d = date.fromisoformat(value)
    return d.strftime("%B {S}, %Y").replace("{S}", ordinal(d.day))


def is_recent_repeat_record(
    row: dict,
    *,
    window_days: int = RECENT_REPEAT_RECORD_DAYS,
) -> bool:
    """True when this "best day since" beats a *recent* prior day.

    Used to switch caption wording from "earned its best day since X" to "has
    once again earned its best day since X" - the song/album had a comparable
    big day within ``window_days`` and just did it again (never on consecutive
    days: ``compute_best_day_since`` already rejects a beaten day <= 1 day old).
    ``best_ever`` has no beaten day and is never a repeat.
    """
    if row.get("kind") != "since":
        return False
    days_since = row.get("days_since")
    return days_since is not None and 0 < int(days_since) <= window_days


def row_label(row: dict) -> str:
    if row["kind"] == "best_ever":
        label = "best day ever"
    elif row.get("is_biggest_day_of_year") and row.get("kind") == "since":
        label = f"biggest day of the year and best day since {format_long_date(row['best_day_since'])}"
    elif row.get("is_biggest_day_of_year"):
        label = "biggest day of the year"
    elif row.get("kind") == "since":
        label = f"best day since {format_long_date(row['best_day_since'])}"
    elif row.get("is_biggest_day_of_month"):
        label = "biggest day of the month"
    else:
        label = f"best day since {format_long_date(row['best_day_since'])}"
    # A "combined" row's record is set by the summed streams of every version
    # in the song family (e.g. original + Taylor's Version), not this track
    # alone — callers must say so wherever the label is posted publicly.
    if row.get("combined"):
        label = f"{label} (combined)"
    return label


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute best-day-since stream stats.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD (default: latest date in history)")
    parser.add_argument("--limit", type=int, default=50, help="Number of rows to print (default: 50)")
    parser.add_argument(
        "--min-days",
        type=int,
        default=DEFAULT_MIN_DAYS,
        help=f"Minimum days since previous higher/equal day (default: {DEFAULT_MIN_DAYS})",
    )
    parser.add_argument("--include-extras", action="store_true", help="Include songs.json extras too")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON output path")
    parser.add_argument("--no-write", action="store_true", help="Print only, do not write JSON")
    args = parser.parse_args()

    tracks = load_tracks(include_extras=args.include_extras)
    history = load_history()
    if not history:
        raise SystemExit(f"No history found: {HISTORY_PATH}")

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        latest = latest_history_date(history)
        if latest is None:
            raise SystemExit("No dated history rows found.")
        target_date = latest

    rows = []
    for track_id, track in tracks.items():
        row = compute_best_day_since(track, history.get(track_id) or [], target_date)
        if row:
            rows.append(row)

    rows = [row for row in rows if passes_filters(row, min_days=args.min_days)]
    album_rows = []
    for album, track_ids in load_album_track_ids(tracks).items():
        if len(track_ids) < 2:
            continue
        row = compute_album_best_day_since(album, track_ids, history, target_date)
        if row and row.get("kind") == "since" and passes_filters(row, min_days=args.min_days):
            album_rows.append(row)

    rows.sort(key=sort_key, reverse=True)
    album_rows.sort(key=sort_key, reverse=True)
    limited_rows = rows[: max(args.limit, 0)]
    by_track = {row["track_id"]: row for row in rows}

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": target_date.isoformat(),
        "include_extras": args.include_extras,
        "min_days": args.min_days,
        "count": len(rows),
        "items": rows,
        "by_track": by_track,
        "album_count": len(album_rows),
        "albums": album_rows,
        "by_album": {row["album"]: row for row in album_rows},
    }

    print(f"Best day since for {target_date.isoformat()} ({len(rows)} match(es))")
    for index, row in enumerate(limited_rows, 1):
        label = row_label(row)
        if row.get("days_since"):
            label = f"{label} ({row['days_since']} days)"
        print(f"{index:>2}. {row['title']} | {format_int(row['daily_streams'])} | {label}")

    if not args.no_write:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {output}")


if __name__ == "__main__":
    main()
