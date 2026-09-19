"""
Finalizes ONE day's "TS Top Songs Global" ranking from that day's raw 2h
collection cycles (`ts_page_all.py` writes those to `*_raw.csv`, never read
by the export/site).

Run once/day by `run_apple_music.py`, right after `ts_page_all.py`, targeting
YESTERDAY relative to the current run date (a day is only "complete" once
its last cycle, 22:00, has run). Idempotent: if the target day's canonical
CSV already has rows, this is a no-op unless `--force` is passed — so
calling it on every 2h cycle is safe, it only ever does real work once, on
the first cycle of the new day.

See `core/ts_top_songs_daily.py` for the aggregation itself (shared with the
one-off historical `backfill_ts_top_songs_daily_final.py`).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collectors.apple_music.core.csv_utils import read_csv_rows, write_csv_rows
from collectors.apple_music.core.ts_top_songs_daily import FIELDNAMES, compute_final_rows
from collectors.spotify.core.data_paths import apple_music_charts_dir

FILENAME = "apple_music_ts_top_songs_global.csv"
RAW_FILENAME = "apple_music_ts_top_songs_global_raw.csv"


def _read_day_rows(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    return read_csv_rows(path)


def _previous_final_rows(target_day: str) -> list[dict]:
    """Latest finalized day strictly before target_day, within 30 days."""
    all_rows = read_csv_rows(
        apple_music_charts_dir(target_day) / FILENAME,
        include_daily_history=True,
        history_days=30,
    )
    by_day: dict[str, list[dict]] = {}
    for row in all_rows:
        day = (row.get("date") or row.get("scraped_at") or "")[:10]
        if day and day < target_day:
            by_day.setdefault(day, []).append(row)
    if not by_day:
        return []
    latest_day = max(by_day)
    return by_day[latest_day]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    parser.add_argument("--target-date", dest="target_date", default=None, help="Override: finalize this day instead of yesterday relative to --date.")
    parser.add_argument("--force", action="store_true", help="Recompute even if the day already has a finalized canonical row set.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.target_date:
        target_day = args.target_date
    else:
        run_day = datetime.strptime(args.run_date, "%Y-%m-%d").date()
        target_day = (run_day - timedelta(days=1)).isoformat()

    canonical_path = apple_music_charts_dir(target_day) / FILENAME
    raw_path = apple_music_charts_dir(target_day) / RAW_FILENAME

    if not args.force and _read_day_rows(canonical_path):
        print(f"[Apple Music TS Global finalize] {target_day}: already finalized, skipping")
        return

    cycle_rows = _read_day_rows(raw_path)
    if not cycle_rows:
        print(f"[Apple Music TS Global finalize] {target_day}: no raw cycles found, nothing to finalize")
        return

    previous_final_rows = _previous_final_rows(target_day)
    final_rows = compute_final_rows(target_day, cycle_rows, previous_final_rows)

    distinct_cycles = len({r.get("scraped_at") for r in cycle_rows if r.get("scraped_at")})
    print(
        f"[Apple Music TS Global finalize] {target_day}: {distinct_cycles} cycle(s), "
        f"{len(cycle_rows)} raw row(s) -> {len(final_rows)} final row(s)"
    )
    if final_rows:
        top5 = ", ".join(f"#{r['rank']} {r['song_name']}" for r in final_rows[:5])
        print(f"  top5: {top5}")

    write_csv_rows(canonical_path, FIELDNAMES, final_rows)
    print(f"[Apple Music TS Global finalize] Wrote {len(final_rows)} row(s) -> {canonical_path}")


if __name__ == "__main__":
    main()
