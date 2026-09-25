"""
Live "TS Top Songs Global" ranking for the CURRENT day: every hourly cycle
already collected today (ts_page_all.py's `*_raw.csv`) aggregated with the
exact same `compute_final_rows()` the next-day finalize uses. After the day's
last cycle this IS the final ranking (same inputs, same function); before
that it is an in-progress view, labelled as such by `live_cycles`.

Writes `apple_music_ts_top_songs_global_live.csv` in today's snapshot dir,
fully rewritten each run (derived view, never appended). Read only by
export_apple_music.py (`ts_top_songs_live`); TayBoard/posting/finalize keep
reading the canonical final CSV only — never publish this as a final day.

Runs in the runner right after finalize_ts_top_songs_daily.py, so yesterday's
final (the previous_rank reference, same as the final chart's) exists first.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collectors.apple_music.core.csv_utils import write_csv_rows
from collectors.apple_music.core.ts_top_songs_daily import FIELDNAMES, compute_final_rows
from collectors.apple_music.finalize_ts_top_songs_daily import RAW_FILENAME, _previous_final_rows, _read_day_rows
from collectors.spotify.core.data_paths import apple_music_charts_dir

LIVE_FILENAME = "apple_music_ts_top_songs_global_live.csv"
LIVE_FIELDNAMES = [*FIELDNAMES, "live_cycles", "compared_to_day"]


def build_live_rows(day: str, cycle_rows: list[dict], previous_final_rows: list[dict]) -> list[dict]:
    rows = compute_final_rows(day, cycle_rows, previous_final_rows)
    cycles = len({r.get("scraped_at") for r in cycle_rows if r.get("scraped_at")})
    compared_to = max(((r.get("date") or r.get("scraped_at") or "")[:10] for r in previous_final_rows), default="")
    for row in rows:
        row["live_cycles"] = cycles
        row["compared_to_day"] = compared_to
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None, help="Accepted for runner compatibility; unused.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    day = args.run_date
    day_dir = apple_music_charts_dir(day)

    cycle_rows = _read_day_rows(day_dir / RAW_FILENAME)
    if not cycle_rows:
        print(f"[Apple Music TS Global live] {day}: no raw cycle yet, nothing to write")
        return

    rows = build_live_rows(day, cycle_rows, _previous_final_rows(day))
    write_csv_rows(day_dir / LIVE_FILENAME, LIVE_FIELDNAMES, rows)
    top5 = ", ".join(f"#{r['rank']} {r['song_name']}" for r in rows[:5])
    print(
        f"[Apple Music TS Global live] {day}: {rows[0]['live_cycles'] if rows else 0} cycle(s) so far, "
        f"{len(rows)} song(s), vs final {rows[0]['compared_to_day'] if rows else '-'} -> {day_dir / LIVE_FILENAME}"
    )
    if top5:
        print(f"  top5: {top5}")


if __name__ == "__main__":
    main()
