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

    dates = sorted({row.get("date", "") for row in rows if row.get("date") and row.get("date") != today}, reverse=True)
    if not dates:
        return {}
    latest = dates[0]

    previous: dict[tuple[str, ...], int] = {}
    for row in rows:
        if row.get("date") != latest:
            continue
        try:
            rank = int(row.get(rank_field, ""))
        except (TypeError, ValueError):
            continue
        key = tuple((row.get(field, "") if field != song_field else rank_key(row.get(song_field, ""))) for field in key_fields)
        previous[key] = rank
    return previous
