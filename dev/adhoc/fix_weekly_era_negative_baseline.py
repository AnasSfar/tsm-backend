from __future__ import annotations

import csv
import shutil
from pathlib import Path


TRACK_IDS = {
    "3wx2iW1rJGgdNln2I64HNh",
    "2jt90bYlYDg1lXYWES34LJ",
    "6LZaxlycSWrJZ4Volb25qx",
    "4i6cwNY6oIUU2XZxPIw82Y",
    "6XDBA3QWX51lDJ0oZbaJJN",
    "45R112Jz5hQeKgITXgSXzs",
    "7zMcNqs55Mxer82bvZFkpg",
}

ACCIDENTALLY_REPAIRED_IDS = {
    "6NUy8xajng5zMSGJRKniAG",
    "5k9RjjuYyOMCLL81U8FaHT",
    "2NGGvVcfb2m2NbVaXHC3gb",
    "0GxW5K0qzrq7L1jwSY5OmY",
    "42pHJPLlDjfvdAUb7aeDXb",
    "3lXekiVK1ZPZbKconsys73",
    "5sbsEKN2PIwKe0l03qoeXn",
    "60oZARTE1kCh5ntBfeO1XB",
    "0BSmbCIY36iw04azzJ3S0S",
    "2l74Cv256ElApUd119a9ib",
    "4511PLndxxMcob8Qq5KHNw",
    "3T7zAF5sfNq0blGbz21TTa",
    "1e380XUelS0g4AyrUfHKHL",
    "2GZNZLMvT3gjCEPd6NNvvQ",
}

TARGET_DATE = "2026-08-18"
BASELINE_DATE = "2026-08-16"
REASON = "manual_trusted"
BACKUP_SUFFIX = ".bak.20260827-weekly-era-negative-baseline"

PATHS = [
    Path("db/streams_history.csv"),
    Path("snapshots/spotify_streams/2026/08/2026-08-25/streams_history.csv"),
]


def _parse_int(value: str | None) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def repair(path: Path) -> int:
    if not path.exists():
        return 0

    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    baseline_streams = {
        row["track_id"]: _parse_int(row.get("streams"))
        for row in rows
        if row.get("date") == BASELINE_DATE
    }

    changed = 0
    for row in rows:
        if row.get("date") != TARGET_DATE:
            continue
        track_id = row.get("track_id")
        if track_id in ACCIDENTALLY_REPAIRED_IDS and row.get("estimated_reason") == REASON:
            row["daily_streams"] = ""
            row["estimated_reason"] = ""
            changed += 1
            continue
        if track_id not in TRACK_IDS:
            continue
        baseline_total = baseline_streams.get(track_id)
        current_total = _parse_int(row.get("streams"))
        current_daily = _parse_int(row.get("daily_streams"))
        existing_reason = str(row.get("estimated_reason") or "").strip()
        if baseline_total is None or current_total is None:
            continue
        repaired_daily = current_total - baseline_total
        if repaired_daily < 0:
            continue
        if current_daily == repaired_daily and existing_reason == REASON:
            continue
        if current_daily is not None and current_daily >= 0 and not existing_reason.startswith("collection_incident_"):
            continue
        row["daily_streams"] = str(repaired_daily)
        row["estimated_reason"] = REASON
        changed += 1

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return changed


def main() -> None:
    for path in PATHS:
        print(f"{path}: {repair(path)} repaired row(s)")


if __name__ == "__main__":
    main()
