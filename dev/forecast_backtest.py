#!/usr/bin/env python3
"""Backtest of the milestone forecast (forecast_milestones.py) on real stream history.

From several past start dates, project each song's streams over 30/90/180/365 days with the
current model and compare with what really happened. Prints, per decline duration, the median
bias (+ = the model was optimistic) and the median absolute error. Read-only: never writes data.

  python dev/forecast_backtest.py --history db/streams_history.csv [--decline-days 30,45,60,365]

History CSV columns: date, track_id, streams, daily_streams (db/streams_history.csv format).
Used 2026-10-16 to pick LONG_DECAY_DAYS = 45 and the growth cap (see forecast_milestones.py).
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from datetime import date, timedelta
from multiprocessing import Pool
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "collectors" / "spotify" / "streams" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import forecast_milestones as fm  # noqa: E402

HORIZONS = (30, 90, 180, 365)
MIN_TOTAL = fm.MILESTONE_STEP  # songs under 100M are not forecast-worthy
MIN_ACTUAL_GAIN = 1_000_000


def _load(path: Path) -> dict[str, list[tuple[str, int, int]]]:
    rows: dict[str, list[tuple[str, int, int]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("streams") and r.get("daily_streams"):
                rows.setdefault(r["track_id"], []).append((r["date"], int(r["streams"]), int(r["daily_streams"])))
    return rows


def _projected_gain(start: float, decay: float, wf: dict, cut: date, days: int, decline_days: int) -> float:
    return sum(
        fm._daily_on_projection_day(i, start, decay, cut, wf, decay_days=decline_days)
        for i in range(1, days + 1)
    )


def _work(args: tuple) -> list[tuple[int, int, float]]:
    (track_rows, cutoffs, decline_days) = args
    track_rows = sorted(track_rows)
    by_date = {r[0]: r for r in track_rows}
    out = []
    for cut in cutoffs:
        hist = [r for r in track_rows if r[0] <= cut.isoformat()]
        if len(hist) < 60 or hist[-1][0] != cut.isoformat() or hist[-1][1] < MIN_TOTAL:
            continue
        series = [{"date": a, "date_obj": date.fromisoformat(a), "streams": b, "daily_streams": c}
                  for a, b, c in hist[-730:]]
        est = fm.estimate_future_daily_streams(series)
        start, decay = max(est["projected_next_daily"], 1), min(est["decay_factor"], 1.0)
        wf = fm.estimate_weekday_factors(series)
        for h in HORIZONS:
            later = by_date.get((cut + timedelta(days=h)).isoformat())
            if not later:
                continue
            actual = later[1] - hist[-1][1]
            if actual <= MIN_ACTUAL_GAIN:
                continue
            for dd in decline_days:
                out.append((dd, h, _projected_gain(start, decay, wf, cut, h, dd) / actual - 1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", type=Path, required=True)
    ap.add_argument("--decline-days", default=f"30,{fm.LONG_DECAY_DAYS},60,90,365")
    ap.add_argument("--cutoffs", type=int, default=12, help="number of start dates, 61 days apart")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    rows = _load(args.history)
    last = max(r[0] for rs in rows.values() for r in rs)
    first_cut = date.fromisoformat(last) - timedelta(days=61 * (args.cutoffs - 1) + max(HORIZONS) // 2)
    cutoffs = [first_cut + timedelta(days=61 * i) for i in range(args.cutoffs)]
    decline_days = [int(x) for x in args.decline_days.split(",")]

    with Pool(args.workers) as pool:
        results = [r for chunk in pool.map(_work, [(rs, cutoffs, decline_days) for rs in rows.values()], chunksize=4)
                   for r in chunk]

    print(f"history ends {last}; start dates {cutoffs[0]} → {cutoffs[-1]}")
    print("median bias (+ = optimistic) / median absolute error / pairs")
    for dd in decline_days:
        cells = []
        for h in HORIZONS:
            errs = [e for d, hh, e in results if d == dd and hh == h]
            if errs:
                cells.append(f"{h:>3}d {statistics.median(errs) * 100:+5.1f}% / "
                             f"{statistics.median(map(abs, errs)) * 100:4.1f}% ({len(errs)})")
        print(f"decline {dd:>3} d | " + " | ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
