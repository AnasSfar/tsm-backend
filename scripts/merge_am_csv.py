#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "db"

PAIRS = [
    ("applemusic_country_charts.csv", "apple_music_country_charts.csv"),
    ("applemusic_genre_charts.csv", "apple_music_genre_charts.csv"),
    ("applemusic_ts.csv", "apple_music_ts_top_songs.csv"),
    ("applemusicglobal_.csv", "apple_music_global.csv"),
]


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def all_fieldnames(rows_a: list[dict], rows_b: list[dict]) -> list[str]:
    seen: list[str] = []
    for rows in (rows_a, rows_b):
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.append(key)
    return seen


def normalize_row(row: dict, fieldnames: list[str]) -> dict:
    return {field: row.get(field, "") for field in fieldnames}


def row_key(row: dict) -> tuple:
    preferred = [
        "date",
        "country",
        "genre_id",
        "genre_name",
        "song_name",
        "rank",
        "apple_music_id",
        "url",
    ]
    if any(k in row for k in preferred):
        return tuple((k, row.get(k, "")) for k in preferred if k in row)

    return tuple(sorted(row.items()))


def merge_pair(old_name: str, new_name: str) -> None:
    old_path = DB_DIR / old_name
    new_path = DB_DIR / new_name

    old_rows = read_csv(old_path)
    new_rows = read_csv(new_path)

    if not old_rows and not new_rows:
        print(f"[skip] {old_name} + {new_name} not found / empty")
        return

    fieldnames = all_fieldnames(old_rows, new_rows)
    merged: list[dict] = []
    seen: set[tuple] = set()

    # ancien d'abord, puis nouveau pour garder aussi les colonnes modernes
    for row in old_rows + new_rows:
        normalized = normalize_row(row, fieldnames)
        key = row_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)

    with new_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged)

    print(
        f"[merged] {old_name} + {new_name} -> {new_name} | "
        f"old={len(old_rows)} new={len(new_rows)} merged={len(merged)}"
    )


def main() -> None:
    for old_name, new_name in PAIRS:
        merge_pair(old_name, new_name)


if __name__ == "__main__":
    main()