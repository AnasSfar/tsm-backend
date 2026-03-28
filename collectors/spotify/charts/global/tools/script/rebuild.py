#!/usr/bin/env python3
"""Reconstruit ts_history.json a partir des ts_all_songs.csv. Usage : python rebuild.py"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.history import save, update

ROOT = Path(__file__).parent


def rebuild_from_ts_csvs(root: Path) -> dict:
    history = {}
    for csv_file in sorted(root.rglob("ts_all_songs.csv")):
        chart_date = csv_file.parent.name
        df = pd.read_csv(csv_file)
        for _, row in df.iterrows():
            update(
                history,
                str(row["track_name"]),
                chart_date,
                int(row["rank"]),
                row.get("streams"),
                previous_rank=row.get("previous_rank"),
                peak_rank=row.get("peak_rank"),
            )
    return history


def main():
    print(f"Reconstruction de ts_history.json dans {ROOT}")
    history = rebuild_from_ts_csvs(ROOT)
    save(history, ROOT / "ts_history.json")
    total = sum(len(v) for v in history.values())
    print(f"\nTermine - {len(history)} chansons, {total} entrees")


if __name__ == "__main__":
    main()