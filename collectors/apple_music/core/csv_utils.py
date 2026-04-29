from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .filters import rank_key



def read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))



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
    existing = [row for row in read_csv_rows(csv_path) if row.get("date") != today]
    write_csv_rows(csv_path, fieldnames, [*existing, *new_rows])


def rewrite_for_snapshot(
    csv_path: Path,
    fieldnames: list[str],
    scraped_at: str,
    new_rows: list[dict],
) -> None:
    """Append a new snapshot, removing any existing rows with the same scraped_at (idempotent).
    Skips write if the new data is identical to the most recent existing snapshot."""
    existing = read_csv_rows(csv_path)

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

    filtered = [r for r in existing if r.get("scraped_at") != scraped_at]
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
    rows = read_csv_rows(csv_path)
    if not rows:
        return {}

    all_keys = sorted(
        {_snapshot_key(r) for r in rows if _snapshot_key(r) and _snapshot_key(r) != today},
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
