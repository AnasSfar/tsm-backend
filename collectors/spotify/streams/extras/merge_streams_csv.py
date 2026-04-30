"""
One-shot migration: merge streams_history_full.csv into streams_history.csv.

Strategy (same as export_for_web.py load_raw_history):
  - Load streams_history_full.csv first (lower priority)
  - Load streams_history.csv second (overwrites duplicates)
  - Write merged result sorted by (date, track_id) back to streams_history.csv
  - Rename streams_history_full.csv to streams_history_full.csv.bak

Run once from repo root:
  python collectors/spotify/streams/extras/merge_streams_csv.py
"""

import csv
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
DB_DIR = REPO_ROOT / "db"
HISTORY_CSV = DB_DIR / "streams_history.csv"
FULL_CSV = DB_DIR / "streams_history_full.csv"
BAK_CSV = DB_DIR / "streams_history_full.csv.bak"

FIELDNAMES = ["date", "track_id", "streams", "daily_streams"]


def read_csv(path: Path, merged: dict) -> int:
    count = 0
    with path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            date = (row.get("date") or "").strip()
            track_id = (row.get("track_id") or "").strip()
            if not date or not track_id:
                continue
            merged[(date, track_id)] = {
                "date": date,
                "track_id": track_id,
                "streams": (row.get("streams") or "").strip(),
                "daily_streams": (row.get("daily_streams") or "").strip(),
            }
            count += 1
    return count


def main():
    merged: dict[tuple[str, str], dict] = {}

    if not FULL_CSV.exists():
        print(f"[skip] {FULL_CSV.name} not found — nothing to merge.")
        return

    n_full = read_csv(FULL_CSV, merged)
    print(f"Loaded {n_full:,} rows from {FULL_CSV.name}")

    n_base = read_csv(HISTORY_CSV, merged)
    print(f"Loaded {n_base:,} rows from {HISTORY_CSV.name} (overwrites duplicates)")

    rows = sorted(merged.values(), key=lambda r: (r["date"], r["track_id"]))
    print(f"Writing {len(rows):,} merged rows to {HISTORY_CSV.name} ...")

    with HISTORY_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    shutil.move(str(FULL_CSV), str(BAK_CSV))
    print(f"Renamed {FULL_CSV.name} → {BAK_CSV.name}")
    print("Done. Verify the output, then delete the .bak file.")


if __name__ == "__main__":
    main()
