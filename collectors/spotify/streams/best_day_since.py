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
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
DB_ROOT = REPO_ROOT / "db"
sys.path.insert(0, str(SCRIPT_DIR.parent))  # collectors/spotify/ for core.*
from core.data_paths import archived_db_file  # noqa: E402

HISTORY_PATH = (
    DB_ROOT / "streams_history.csv"
    if (DB_ROOT / "streams_history.csv").exists()
    else archived_db_file("streams_history.csv")
)
DISCOGRAPHY_DIR = DB_ROOT / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
SONGS_JSON = DISCOGRAPHY_DIR / "songs.json"
DEFAULT_OUTPUT = REPO_ROOT / "website" / "site" / "data" / "best_day_since.json"
HISTORY_START_DATE = date(2025, 1, 1)


@dataclass(frozen=True)
class Track:
    track_id: str
    title: str
    album: str
    spotify_url: str


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
    if not SONGS_JSON.exists():
        return []
    try:
        payload = json.loads(SONGS_JSON.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


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

    for point in points:
        daily = point.daily
        if daily is None and point.total is not None and previous_total is not None:
            diff = point.total - previous_total
            daily = diff if diff >= 0 else None
        filled.append(Point(point.day, point.total, daily))
        if point.total is not None:
            previous_total = point.total

    return filled


def latest_history_date(history: dict[str, list[Point]]) -> date | None:
    latest: date | None = None
    for points in history.values():
        if points:
            point_date = points[-1].day
            latest = point_date if latest is None or point_date > latest else latest
    return latest


def compute_best_day_since(track: Track, points: list[Point], target_date: date) -> dict | None:
    points = fill_missing_dailies(points)
    point_by_date = {point.day: point for point in points}
    current = point_by_date.get(target_date)
    if current is None or current.daily is None or current.daily <= 0:
        return None

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
        if first_available_date > HISTORY_START_DATE:
            kind = "best_ever"
            best_day_since = "ever"
        else:
            kind = "before_history"
            best_day_since = "before 2025"

        return {
            "track_id": track.track_id,
            "title": track.title,
            "album": track.album,
            "spotify_url": track.spotify_url,
            "date": target_date.isoformat(),
            "daily_streams": current.daily,
            "kind": kind,
            "best_day_since": best_day_since,
            "previous_higher_or_equal_date": None,
            "previous_higher_or_equal_daily": None,
            "days_since": None,
            "first_available_date": first_available_date.isoformat(),
        }

    best_since = last_at_or_above.day + timedelta(days=1)
    if best_since >= target_date:
        return None

    return {
        "track_id": track.track_id,
        "title": track.title,
        "album": track.album,
        "spotify_url": track.spotify_url,
        "date": target_date.isoformat(),
        "daily_streams": current.daily,
        "kind": "since",
        "best_day_since": best_since.isoformat(),
        "previous_higher_or_equal_date": last_at_or_above.day.isoformat(),
        "previous_higher_or_equal_daily": last_at_or_above.daily,
        "days_since": (target_date - best_since).days + 1,
        "first_available_date": points[0].day.isoformat() if points else None,
    }


def format_int(value: int | None) -> str:
    return "?" if value is None else f"{value:,}"


def sort_key(row: dict) -> tuple[int, int, int]:
    is_record = 1 if row["kind"] in {"best_ever", "before_history"} else 0
    days_since = row.get("days_since") or 0
    return (is_record, days_since, row["daily_streams"])


def passes_filters(row: dict, *, min_days: int) -> bool:
    if row["kind"] in {"best_ever", "before_history"}:
        return True
    return (row.get("days_since") or 0) >= min_days


def best_day_since_for_track(track_id: str, target_date: str, *, min_days: int = 14) -> dict | None:
    """Return best-day-since data for one album track, excluding extras."""
    track = load_tracks(include_extras=False).get(track_id)
    if not track:
        return None

    points = load_history().get(track_id)
    if not points:
        return None

    row = compute_best_day_since(track, points, date.fromisoformat(target_date))
    if not row or not passes_filters(row, min_days=min_days):
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


def row_label(row: dict) -> str:
    if row["kind"] == "best_ever":
        return "best day ever"
    if row["kind"] == "before_history":
        return "best day since before 2025"
    return f"best day since {format_long_date(row['best_day_since'])}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute best-day-since stream stats.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD (default: latest date in history)")
    parser.add_argument("--limit", type=int, default=50, help="Number of rows to print (default: 50)")
    parser.add_argument("--min-days", type=int, default=14, help="Minimum days since previous higher/equal day (default: 14)")
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
        points = history.get(track_id)
        if not points:
            continue
        row = compute_best_day_since(track, points, target_date)
        if row:
            rows.append(row)

    rows = [row for row in rows if passes_filters(row, min_days=args.min_days)]
    rows.sort(key=sort_key, reverse=True)
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
