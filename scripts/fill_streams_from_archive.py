"""
Backfill db/streams_history.csv from the Daily Archive files.

The archive files are title-only. Some files also contain album/total rows after
an empty separator block, and those rows can have the exact same name as a song
("Red (Taylor's Version)", "Speak Now (Taylor's Version)", etc.). This script
only reads the song section before that separator.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


BASE = Path(__file__).parent.parent / "db"
BACKEND_START_DATE = "2026-03-09"

EXTRA_MARKERS = {
    "acoustic",
    "demo",
    "instrumental",
    "karaoke",
    "long pond",
    "remix",
    "stripped",
    "voice memo",
}

LIVE_VERSION_PATTERNS = (
    r"\blive\s*/?\s*\d{4}\b",
    r"\blive\s+(from|at|version|performance|session)\b",
    r"\bfrom .* live\b",
)

ARCHIVE_EXPANSIONS = (
    (r"\btlpss\b", "the long pond studio sessions"),
    (r"\bTV\b", "taylor s version"),
    (r"\bFTV\b", "from the vault"),
    (r"\bLDR\b", "lana del rey"),
    (r"\bMore LDR\b", "more lana del rey"),
    (r"\bWANEGBT\b", "we are never ever getting back together"),
    (r"\btlgad\b", "the last great american dynasty"),
    (r"\bfeat\b", "feat"),
)

TRACK_EXPANSIONS = (
    (r"the long pond studio sessions", "tlpss"),
    (r"taylor'?s version", "tv"),
    (r"from the vault", "ftv"),
)


def normalize(value: str) -> str:
    value = value.lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = re.sub(r"[^a-z0-9 ]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def expand(value: str, replacements: tuple[tuple[str, str], ...]) -> str:
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value


def unique_ordered(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def archive_variants(title: str) -> list[str]:
    return unique_ordered([
        normalize(title),
        normalize(expand(title, ARCHIVE_EXPANSIONS)),
    ])


def track_variants(title: str) -> list[str]:
    return unique_ordered([
        normalize(expand(title, TRACK_EXPANSIONS)),
        normalize(title),
    ])


def matching_variants(value: str) -> list[str]:
    variants = []
    for raw in unique_ordered([
        value,
        expand(value, TRACK_EXPANSIONS),
        expand(value, ARCHIVE_EXPANSIONS),
    ]):
        norm = normalize(raw)
        if not norm:
            continue
        variants.append(norm)
        variants.append(re.sub(r"\bfeat\b.*$", "", norm).strip())
        variants.append(re.sub(r"\bbonus track\b", "", norm).strip())
        variants.append(norm.replace("people s", "peoples"))
        variants.append(norm.replace("aimee", "almee"))
        variants.append(norm.replace("almee", "aimee"))
    return unique_ordered(variants)


def track_archive_variants(track: dict) -> list[str]:
    variants = []
    for key in ("title", "title_clean", "base_title", "song_family"):
        value = (track.get(key) or "").strip()
        if value:
            variants.extend(matching_variants(value))
    return unique_ordered(variants)


def parse_space_number(value: str) -> int:
    value = (
        value
        .replace("\ufeff", "")
        .replace("\u00c2", "")
        .replace("\xa0", "")
        .replace("Ã‚", "")
        .strip()
    )
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else 0


def extract_tracks(data):
    tracks = []
    if isinstance(data, list):
        for item in data:
            tracks.extend(extract_tracks(item))
    elif isinstance(data, dict):
        if "tracks" in data:
            tracks.extend(t for t in data["tracks"] if isinstance(t, dict) and t.get("title"))
        for key in ("sections", "albums"):
            tracks.extend(extract_tracks(data.get(key, [])))
    return tracks


def is_normal_track(track: dict) -> bool:
    searchable = " ".join(
        str(track.get(key, "") or "")
        for key in ("title", "type", "section", "display_section", "edition")
    )
    normalized = normalize(searchable)
    if any(marker in normalized for marker in EXTRA_MARKERS):
        return False
    return not any(re.search(pattern, normalized) for pattern in LIVE_VERSION_PATTERNS)


def build_track_map() -> dict[str, dict]:
    by_id = {}
    for path in [BASE / "discography/songs.json", *sorted((BASE / "discography/albums").glob("*.json"))]:
        with path.open(encoding="utf-8-sig") as handle:
            data = json.load(handle)
        for track in extract_tracks(data):
            if not is_normal_track(track):
                continue
            url = track.get("url", "")
            match = re.search(r"/track/([A-Za-z0-9]+)", url)
            if match:
                canonical_id = match.group(1)
                by_id[canonical_id] = {**track, "track_id": canonical_id}
            for historical_id in track.get("historical_track_ids", []) or []:
                by_id[historical_id] = {**track, "track_id": historical_id}

    with (BASE / "swift_top_100_history.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            track_id = (row.get("track_id") or "").strip()
            title = (row.get("title") or "").strip()
            if track_id and title and track_id in by_id:
                by_id[track_id] = {**by_id[track_id], "title": title}

    return by_id


def parse_archive(filepath: Path) -> tuple[dict[str, dict[str, int]], dict[str, str]]:
    data: dict[str, dict[str, int]] = {}
    norm_to_original: dict[str, str] = {}

    with filepath.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        dates = [h.replace("/", "-") for h in header[1:]]
        consecutive_empty = 0

        for row in reader:
            title = (row[0] if row else "").strip()
            if not title:
                consecutive_empty += 1
                continue

            if consecutive_empty >= 3:
                break
            consecutive_empty = 0

            values = {}
            for index, date in enumerate(dates, start=1):
                if index >= len(row) or not row[index].strip():
                    continue
                try:
                    values[date] = parse_space_number(row[index])
                except ValueError:
                    continue

            for norm in archive_variants(title):
                data.setdefault(norm, {}).update(values)
                norm_to_original.setdefault(norm, title)

    return data, norm_to_original


def load_archive() -> tuple[dict[str, dict[str, int]], dict[str, str]]:
    merged: dict[str, dict[str, int]] = {}
    names: dict[str, str] = {}

    for filepath in sorted(BASE.glob("2026 & 2025 -*Daily Archive*.csv")):
        archive, archive_names = parse_archive(filepath)
        for norm, daily_by_date in archive.items():
            merged.setdefault(norm, {}).update(daily_by_date)
            names.setdefault(norm, archive_names[norm])

    return merged, names


def load_stream_rows() -> list[dict[str, str]]:
    with (BASE / "streams_history.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def int_field(row: dict[str, str], field: str) -> int:
    return int(row.get(field) or 0)


def next_day(day: str) -> str:
    return str(date.fromisoformat(day) + timedelta(days=1))


def main() -> None:
    tracks_by_id = build_track_map()
    archive, archive_names = load_archive()
    original_rows = load_stream_rows()

    existing = defaultdict(dict)
    for row in original_rows:
        existing[row["track_id"]][row["date"]] = row

    id_to_archive_key = {}
    unmatched = []
    candidate_track_ids = sorted(set(existing) | set(tracks_by_id))
    for track_id in candidate_track_ids:
        track = tracks_by_id.get(track_id)
        if not track:
            if track_id in existing:
                unmatched.append((track_id, "missing metadata"))
            continue
        for variant in track_archive_variants(track):
            if variant in archive:
                id_to_archive_key[track_id] = variant
                break
        else:
            unmatched.append((track_id, track["title"]))

    rows_by_key = {
        (row["date"], row["track_id"]): dict(row)
        for row in original_rows
    }
    new_rows = []
    fill_log = []
    updated_blank_daily = 0

    for track_id, archive_key in id_to_archive_key.items():
        archive_dates = dict(archive[archive_key])
        if not archive_dates:
            continue

        known_dates = sorted(existing[track_id])
        if not known_dates:
            continue

        daily_lookup = dict(archive_dates)
        cumulative: dict[str, int] = {}
        for known_date in known_dates:
            row = existing[track_id][known_date]
            cumulative[known_date] = int_field(row, "streams")
            daily = (row.get("daily_streams") or "").strip()
            if daily:
                daily_lookup[known_date] = int(daily)

        ordered_dates = sorted(set(archive_dates) | set(known_dates))

        for index in range(len(ordered_dates) - 2, -1, -1):
            current = ordered_dates[index]
            nxt = ordered_dates[index + 1]
            if next_day(current) != nxt:
                continue
            if nxt in cumulative and nxt in daily_lookup:
                cumulative.setdefault(current, max(0, cumulative[nxt] - daily_lookup[nxt]))

        for index in range(1, len(ordered_dates)):
            previous = ordered_dates[index - 1]
            current = ordered_dates[index]
            if next_day(previous) != current:
                continue
            if previous in cumulative and current in daily_lookup:
                cumulative.setdefault(current, cumulative[previous] + daily_lookup[current])

        filled = 0
        for archive_date in sorted(archive_dates):
            key = (archive_date, track_id)
            if key in rows_by_key:
                row = rows_by_key[key]
                if not (row.get("daily_streams") or "").strip():
                    row["daily_streams"] = str(archive_dates[archive_date])
                    updated_blank_daily += 1
                continue
            if archive_date not in cumulative:
                continue

            row = {
                "date": archive_date,
                "track_id": track_id,
                "streams": str(cumulative[archive_date]),
                "daily_streams": str(archive_dates[archive_date]),
            }
            rows_by_key[key] = row
            new_rows.append(row)
            filled += 1

        if filled:
            fill_log.append((filled, tracks_by_id[track_id]["title"], archive_names[archive_key]))

    all_rows = list(rows_by_key.values())
    all_rows.sort(key=lambda row: (row["date"], row["track_id"]))

    output_path = BASE / "streams_history.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "track_id", "streams", "daily_streams"])
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"[tracks] metadata kept: {len(tracks_by_id)} normal tracks")
    print(f"[archive] keys kept before album sections: {len(archive)}")
    print(f"[match] matched: {len(id_to_archive_key)} / {len(candidate_track_ids)} track IDs")
    print(f"[match] unmatched: {len(unmatched)}")
    print(f"[write] preserved original rows: {len(original_rows)}")
    print(f"[write] added archive rows: {len(new_rows)}")
    print(f"[write] filled blank daily_streams cells: {updated_blank_daily}")
    print(f"[write] total rows: {len(all_rows)}")
    print("[top fills]")
    for filled, title, archive_title in sorted(fill_log, reverse=True)[:20]:
        print(f"  {filled:4d} rows: {title!r} <- {archive_title!r}")


if __name__ == "__main__":
    main()
