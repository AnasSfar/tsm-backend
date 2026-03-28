"""Migrate chart history to per-region CSV files.

Sources
-------
FR (2017–2025) : data/YYYY/MM/YYYY-MM-DD/tweet.txt
FR (2026+)     : data/YYYY/MM/YYYY-MM-DD/ts_all_songs.csv
Global (recent): data/YYYY/MM/YYYY-MM-DD/ts_all_songs.csv
Global (2019+) : ts_history.json  (fills dates without a per-day file)

Outputs
-------
website/data/charts_history_fr.csv
website/data/charts_history_global.csv

Columns: date, song_name, rank, streams, previous_rank, peak_rank, total_days

total_days
  - From ts_all_songs.csv  → file value (real Spotify count, float→int)
  - From tweet.txt         → computed as cumulative appearances per song in
                             the migrated dataset (best approximation)
  - From ts_history.json   → computed the same way

Idempotent: dates already in the output CSV are skipped entirely.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parents[2]
_DB_ROOT    = _REPO_ROOT / "db"

FR_DIR      = _SCRIPT_DIR / "fr"
GLOBAL_DIR  = _SCRIPT_DIR / "global"

OUT_FR     = _DB_ROOT / "charts_history_fr.csv"
OUT_GLOBAL = _DB_ROOT / "charts_history_global.csv"

FIELDNAMES = ["date", "song_name", "rank", "streams",
              "previous_rank", "peak_rank", "total_days"]

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

# tweet.txt song line: "- #24 (+2) The Fate of Ophelia | 154 677"
_TWEET_SONG_RE = re.compile(
    r"^-\s*#(\d+)\s*\(([^)]+)\)\s*(.+?)\s*\|\s*([\d\s]+?)(?:\s*\([^)]*\))?\s*$"
)


def _int(v) -> int | str:
    """Return int or '' for missing/invalid values."""
    try:
        return int(float(v)) if v not in (None, "", "None") else ""
    except (ValueError, TypeError):
        return ""


def _discover_day_dirs(data_root: Path) -> list[Path]:
    """Walk data/YYYY/MM/YYYY-MM-DD/ and return sorted list of day directories."""
    dirs = []
    for year_dir in sorted(data_root.iterdir()):
        if not year_dir.is_dir() or not re.match(r"^\d{4}$", year_dir.name):
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if day_dir.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", day_dir.name):
                    dirs.append(day_dir)
    return dirs


def _load_existing_dates(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        return {row["date"] for row in csv.DictReader(f)}


def _compute_total_days(
    rows: list[dict],
) -> list[dict]:
    """Fill total_days for rows that have '' by counting cumulative appearances
    per song up to (and including) each date.

    Rows with an existing numeric total_days are left untouched.
    """
    # Group dates already in the output CSV per song:
    # (rows are already sorted by date at this point)
    cumulative: dict[str, int] = defaultdict(int)
    result = []
    for row in rows:
        song = row["song_name"]
        cumulative[song] += 1
        if row["total_days"] == "":
            row = dict(row)
            row["total_days"] = cumulative[song]
        result.append(row)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# FR parsing
# ──────────────────────────────────────────────────────────────────────────────

def _parse_tweet(path: Path) -> list[dict]:
    """Parse 'Spotify France :' section of a tweet.txt file."""
    entries = []
    in_section = False

    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()

        if re.match(r"Spotify France\s*:", line):
            in_section = True
            continue
        if in_section and re.match(r"Spotify France \(Pop\)", line):
            break
        if not in_section:
            continue

        m = _TWEET_SONG_RE.match(line)
        if not m:
            continue

        rank      = int(m.group(1))
        movement  = m.group(2).strip()
        song_name = m.group(3).strip()
        streams   = int(m.group(4).replace(" ", ""))

        previous_rank: int | str = ""
        if movement not in ("NEW", "RE"):
            try:
                # (+N) → went up N spots → previous_rank = rank + N
                # (-N) → went down N spots → previous_rank = rank + N (N is negative)
                v = int(movement)
                previous_rank = rank + v
                if previous_rank <= 0:
                    previous_rank = ""
            except ValueError:
                pass

        entries.append({
            "song_name":     song_name,
            "rank":          rank,
            "streams":       streams,
            "previous_rank": previous_rank,
            "peak_rank":     "",        # not in tweet.txt
            "total_days":    "",        # will be filled later
        })

    return entries


def _parse_ts_all_songs_csv(path: Path) -> list[dict]:
    """Parse a ts_all_songs.csv file (Taylor Swift rows only)."""
    entries = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if "Taylor Swift" not in row.get("artist_names", ""):
                continue
            song = row.get("track_name", "").strip()
            if not song:
                continue
            entries.append({
                "song_name":     song,
                "rank":          _int(row.get("rank")),
                "streams":       _int(row.get("streams")),
                "previous_rank": _int(row.get("previous_rank")),
                "peak_rank":     _int(row.get("peak_rank")),
                "total_days":    _int(row.get("total_days")),
            })
    return entries


def _collect_fr(existing_dates: set[str]) -> list[dict]:
    data_dir = FR_DIR / "data"
    if not data_dir.exists():
        print("  [FR] data/ directory not found")
        return []

    rows: list[dict] = []
    skipped = 0

    for day_dir in _discover_day_dirs(data_dir):
        date_str = day_dir.name
        if date_str in existing_dates:
            skipped += 1
            continue

        all_csv  = day_dir / "ts_all_songs.csv"
        tweet    = day_dir / "tweet.txt"

        entries = []
        if all_csv.exists():
            entries = _parse_ts_all_songs_csv(all_csv)
        elif tweet.exists():
            entries = _parse_tweet(tweet)

        for e in entries:
            rows.append({"date": date_str, **e})

    print(f"  [FR] {len({r['date'] for r in rows})} new dates "
          f"({len(rows)} rows, skipped {skipped})")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Global parsing
# ──────────────────────────────────────────────────────────────────────────────

def _collect_global(existing_dates: set[str]) -> list[dict]:
    data_dir = GLOBAL_DIR / "data"
    rows: list[dict] = []
    covered_dates: set[str] = set()
    skipped = 0

    # 1. Per-day ts_all_songs.csv (have total_days)
    if data_dir.exists():
        for day_dir in _discover_day_dirs(data_dir):
            date_str = day_dir.name
            if date_str in existing_dates:
                skipped += 1
                continue

            all_csv = day_dir / "ts_all_songs.csv"
            if not all_csv.exists():
                continue

            for e in _parse_ts_all_songs_csv(all_csv):
                rows.append({"date": date_str, **e})
                covered_dates.add(date_str)

    # 2. ts_history.json — fill dates not covered by per-day files
    history_path = GLOBAL_DIR / "ts_history.json"
    if history_path.exists():
        history: dict = json.loads(history_path.read_text(encoding="utf-8"))
        hist_new = 0
        for song_name, dates in history.items():
            for date_str, values in dates.items():
                if date_str in existing_dates or date_str in covered_dates:
                    continue
                rows.append({
                    "date":          date_str,
                    "song_name":     song_name,
                    "rank":          _int(values.get("rank")),
                    "streams":       _int(values.get("streams")),
                    "previous_rank": _int(values.get("previous_rank")),
                    "peak_rank":     _int(values.get("peak_rank")),
                    "total_days":    "",
                })
                hist_new += 1
        print(f"  [Global] {hist_new} rows from ts_history.json")

    print(f"  [Global] {len({r['date'] for r in rows})} new dates "
          f"({len(rows)} rows, skipped {skipped})")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Write
# ──────────────────────────────────────────────────────────────────────────────

def _write_csv(path: Path, new_rows: list[dict]) -> None:
    if not new_rows:
        print(f"  Nothing to add -> {path.name}")
        return

    # Sort new rows by date before computing cumulative total_days
    new_rows.sort(key=lambda r: r["date"])
    new_rows = _compute_total_days(new_rows)

    write_header = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    dates_count = len({r["date"] for r in new_rows})
    print(f"  Added {len(new_rows)} rows for {dates_count} dates -> {path}")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=== FR ===")
    existing_fr = _load_existing_dates(OUT_FR)
    print(f"  Dates already in CSV: {len(existing_fr)}")
    fr_rows = _collect_fr(existing_fr)
    _write_csv(OUT_FR, fr_rows)

    print("\n=== Global ===")
    existing_global = _load_existing_dates(OUT_GLOBAL)
    print(f"  Dates already in CSV: {len(existing_global)}")
    global_rows = _collect_global(existing_global)
    _write_csv(OUT_GLOBAL, global_rows)


if __name__ == "__main__":
    main()
