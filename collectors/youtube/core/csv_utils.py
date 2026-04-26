"""CSV helpers + delta state (last known view counts)."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_rows(
    path: Path,
    rows: Iterable[dict],
    fieldnames: list[str],
) -> None:
    """Append rows to CSV. Writes header only when file is new/empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def date_already_collected(path: Path, date: str) -> bool:
    """Return True if any row with this date already exists in the CSV."""
    rows = read_csv_rows(path)
    return any(r.get("date") == date for r in rows)


# ---------------------------------------------------------------------------
# Delta state — yesterday's total_views per video_id
# ---------------------------------------------------------------------------

def get_last_views(history_path: Path) -> dict[str, int]:
    """Load {video_id: total_views} from the JSON state file."""
    if not history_path.exists():
        return {}
    try:
        with history_path.open(encoding="utf-8") as f:
            data = json.load(f)
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def save_last_views(history_path: Path, data: dict[str, int]) -> None:
    """Persist {video_id: total_views} for next day's delta calculation."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
