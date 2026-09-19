"""
Backfill: collapse every past day's multiple "TS Top Songs Global" cycles
into ONE true final ranking per day, instead of keeping whichever cycle
happened to run last.

Since inception (2026-07-30) the composite (`ts_page_all.py`) has run several
times per day (4-5/day early on, up to 30-45/day once the once-a-day gate
stopped working reliably until it was fixed on 2026-09-18). The site export
(`scripts/export_apple_music.py::window_rows`) already collapses each past
day onto its LAST cycle's scraped_at snapshot — but "last cycle of the day"
is an arbitrary pick, not a ranking that reflects the whole day.

This script instead recomputes, for each day, a final ranking that depends
on every cycle collected that day: a song's daily score is the sum of
`_rank_to_score(rank)` (same power-law curve as the live composite) over
every cycle it appeared in that day. A song holding #1 all day accumulates
far more than one that spiked to #1 once and vanished. Songs are matched
across cycles by ISRC (fallback apple_music_id), same identity rule as the
live storefront-merge step.

`previous_rank` only ever depends on the immediately preceding day's FINAL
row (see core/csv_utils.load_previous_ranks) — never on other cycles of the
same day — so recomputing day D's final ranking does not change day D+1's
`rank` values, only D+1's `previous_rank` annotation. Processing days in
chronological order and rewriting each day's file before moving to the next
keeps that chain correct automatically.

Each day's snapshot file is REWRITTEN IN PLACE with only the recomputed
final rows (the raw per-cycle rows are collapsed away). A `.bak` copy of the
original file is kept next to it before any write.

Historical one-off only: since 2026-09-19 the live pipeline no longer needs
this (`ts_page_all.py` writes raw cycles to a separate `*_raw.csv`, and
`finalize_ts_top_songs_daily.py` does this same aggregation automatically,
once/day, for the day that just completed). This script remains for
re-running the backfill if a bug is ever found in the aggregation logic.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collectors.apple_music.core.ts_top_songs_daily import FIELDNAMES, compute_final_rows
from collectors.spotify.core.data_paths import apple_music_charts_dir, apple_music_daily_csv_paths

FILENAME = "apple_music_ts_top_songs_global.csv"


def read_day_rows(day_path: Path) -> list[dict]:
    if not day_path.exists() or day_path.stat().st_size == 0:
        return []
    with day_path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_day_rows(day_path: Path, rows: list[dict], dry_run: bool) -> None:
    if dry_run:
        return
    day_path.parent.mkdir(parents=True, exist_ok=True)
    backup = day_path.with_suffix(day_path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(day_path, backup)
    with day_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=None, help="First day to backfill (YYYY-MM-DD).")
    parser.add_argument("--end", default=None, help="Last day to backfill (YYYY-MM-DD), inclusive.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing.")
    args = parser.parse_args()

    all_paths = apple_music_daily_csv_paths(FILENAME)
    days = sorted({p.parent.name for p in all_paths})
    if args.start:
        days = [d for d in days if d >= args.start]
    if args.end:
        days = [d for d in days if d <= args.end]
    if not days:
        print("No days matched.")
        return

    previous_final_rows: list[dict] = []
    for day in days:
        day_path = apple_music_charts_dir(day) / FILENAME
        cycle_rows = read_day_rows(day_path)
        distinct_cycles = len({r.get("scraped_at") for r in cycle_rows if r.get("scraped_at")})
        if distinct_cycles <= 1:
            print(f"{day}: {distinct_cycles} cycle -> already final, skipping")
            if cycle_rows:
                previous_final_rows = cycle_rows
            continue

        final_rows = compute_final_rows(day, cycle_rows, previous_final_rows)
        print(f"{day}: {distinct_cycles} cycles, {len(cycle_rows)} rows -> {len(final_rows)} final rows")
        if final_rows:
            top5 = ", ".join(f"#{r['rank']} {r['song_name']}" for r in final_rows[:5])
            print(f"  top5: {top5}")

        write_day_rows(day_path, final_rows, args.dry_run)
        previous_final_rows = final_rows if final_rows else previous_final_rows

    if args.dry_run:
        print("\n[dry-run] No files written.")


if __name__ == "__main__":
    main()
