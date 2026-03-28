"""Backfill website/data/history.csv from website/site/history/*.json files.

Each per-date JSON has the shape:
  { track_id: { streams, daily_streams, ... }, ... }

Rows already present in history.csv (same date) are skipped.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

_SCRIPT_DIR      = Path(__file__).resolve().parent
_REPO_ROOT       = _SCRIPT_DIR.parents[2]
_DB_ROOT         = _REPO_ROOT / "db"
ROOT             = _REPO_ROOT / "website"
SITE_HISTORY_DIR = ROOT / "site" / "history"
HISTORY_CSV_PATH = _DB_ROOT / "streams_history.csv"

FIELDNAMES = ["date", "track_id", "streams", "daily_streams"]


def _load_existing_dates() -> set[str]:
    if not HISTORY_CSV_PATH.exists():
        return set()
    with open(HISTORY_CSV_PATH, newline="", encoding="utf-8") as f:
        return {row["date"] for row in csv.DictReader(f)}


def main() -> None:
    existing_dates = _load_existing_dates()
    print(f"Dates already in CSV : {len(existing_dates)}")

    new_rows: list[dict] = []
    skipped = 0

    for path in sorted(SITE_HISTORY_DIR.glob("*.json")):
        date_str = path.stem
        if date_str == "index":
            continue
        if date_str in existing_dates:
            skipped += 1
            continue

        day_data: dict = json.loads(path.read_text(encoding="utf-8"))
        for track_id, values in day_data.items():
            new_rows.append({
                "date":          date_str,
                "track_id":      track_id,
                "streams":       values.get("streams", ""),
                "daily_streams": values.get("daily_streams", ""),
            })

    if not new_rows:
        print(f"Nothing to add (skipped {skipped} dates already present).")
        return

    write_header = not HISTORY_CSV_PATH.exists()
    HISTORY_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    dates_added = len({r["date"] for r in new_rows})
    print(
        f"Added {len(new_rows)} rows for {dates_added} new date(s) "
        f"(skipped {skipped} already present)."
    )
    print(f"→ {HISTORY_CSV_PATH}")


if __name__ == "__main__":
    main()
