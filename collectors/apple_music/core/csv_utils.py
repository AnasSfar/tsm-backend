from __future__ import annotations

import csv
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from .filters import rank_key
from .config import ARCHIVE_DB_DIR
from collectors.spotify.core.data_paths import apple_music_daily_csv, apple_music_daily_csv_paths

# Only the latest snapshot strictly before the current day is ever needed for
# previous ranks; reading the full multi-year daily history is wasted I/O.
PREV_RANK_WINDOW_DAYS = 30

_DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _path_day(path: Path) -> str:
    for parent in (path.parent, path.parent.parent):
        if _DAY_DIR_RE.match(parent.name):
            return parent.name
    return ""


def _daily_csv_paths(csv_path: Path, days: int | None = None) -> list[Path]:
    paths = apple_music_daily_csv_paths(csv_path.name)
    if days is None:
        return paths
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [p for p in paths if _path_day(p) >= cutoff]


def read_csv_rows(
    csv_path: Path,
    *,
    include_daily_history: bool = False,
    history_days: int | None = None,
) -> list[dict[str, str]]:
    paths = [csv_path]
    archive_path = ARCHIVE_DB_DIR / csv_path.name
    if archive_path != csv_path:
        paths.append(archive_path)
    if include_daily_history:
        paths.extend(_daily_csv_paths(csv_path, days=history_days))

    seen_paths: set[Path] = set()
    unique_paths: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        unique_paths.append(path)

    rows: list[dict[str, str]] = []
    for path in unique_paths:
        if not path.exists() or path.stat().st_size == 0:
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows.extend(csv.DictReader(handle))
    return rows



def write_csv_rows(csv_path: Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)



def rewrite_for_date(
    csv_path: Path,
    fieldnames: list[str],
    today: str,
    new_rows: list[dict],
) -> None:
    csv_path = apple_music_daily_csv(today, csv_path.name)
    existing = []
    if csv_path.exists() and csv_path.stat().st_size > 0:
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            existing = [row for row in csv.DictReader(handle) if row.get("date") != today]
    write_csv_rows(csv_path, fieldnames, [*existing, *new_rows])


def rewrite_for_snapshot(
    csv_path: Path,
    fieldnames: list[str],
    scraped_at: str,
    new_rows: list[dict],
) -> None:
    """Append a new snapshot, removing any existing rows with the same scraped_at (idempotent).
    Skips write if the new data is identical to the most recent existing snapshot."""
    existing = read_csv_rows(csv_path, include_daily_history=True, history_days=7)

    # Find the most recent previous snapshot rows
    prev_keys = sorted(
        {_snapshot_key(r) for r in existing if _snapshot_key(r) != scraped_at},
        reverse=True,
    )
    if prev_keys:
        _COMPARE_FIELDS = [f for f in fieldnames if f not in ("scraped_at", "date", "previous_rank")]
        prev_rows = [
            {f: r.get(f, "") for f in _COMPARE_FIELDS}
            for r in existing if _snapshot_key(r) == prev_keys[0]
        ]
        new_comparable = [
            {f: str(r.get(f, "")) for f in _COMPARE_FIELDS}
            for r in new_rows
        ]
        if prev_rows == new_comparable:
            prev_day = prev_keys[0][:10]
            new_day = scraped_at[:10]
            if prev_day == new_day:
                print(f"[skip] snapshot identical to previous ({prev_keys[0]}), not writing")
                return
            # Cross-day identical: weak signal (TS-filtered subsets can genuinely
            # repeat), so keep the data but leave a trace for diagnosis.
            print(
                f"[info] {csv_path.name}: snapshot identical to last known snapshot "
                f"({prev_keys[0]}) — Apple chart may not have refreshed yet; writing anyway"
            )

    current_day = scraped_at[:10]
    csv_path = apple_music_daily_csv(current_day, csv_path.name)
    filtered = [
        r for r in existing
        if (r.get("scraped_at") != scraped_at and (r.get("date") or r.get("scraped_at", "")[:10]) == current_day)
    ]
    write_csv_rows(csv_path, fieldnames, [*filtered, *new_rows])


def _snapshot_key(row: dict) -> str:
    return row.get("scraped_at") or row.get("date", "")


def load_previous_ranks(
    csv_path: Path,
    key_fields: list[str],
    today: str,
    song_field: str = "song_name",
    rank_field: str = "rank",
) -> dict[tuple[str, ...], int]:
    """Ranks from the last snapshot of the most recent *previous day*.

    Same-day earlier snapshots are ignored on purpose: movement markers must
    always mean "vs yesterday", not "vs this morning's rerun".
    """
    rows = read_csv_rows(csv_path, include_daily_history=True, history_days=PREV_RANK_WINDOW_DAYS)
    if not rows:
        return {}

    current_day = (today or "")[:10]
    all_keys = sorted(
        {_snapshot_key(r) for r in rows if _snapshot_key(r) and _snapshot_key(r)[:10] < current_day},
        reverse=True,
    )
    if not all_keys:
        return {}
    latest = all_keys[0]

    previous: dict[tuple[str, ...], int] = {}
    for row in rows:
        if _snapshot_key(row) != latest:
            continue
        try:
            rank = int(row.get(rank_field, ""))
        except (TypeError, ValueError):
            continue
        key = tuple((row.get(field, "") if field != song_field else rank_key(row.get(song_field, ""))) for field in key_fields)
        previous[key] = rank
    return previous
