#!/usr/bin/env python3
"""Backtest of the milestone forecast (forecast_milestones.py) on real stream history.

From a start date every 30 days, the production model projects each song >= 100M and is compared
with what really happened:
  - streams gained over 30/90/180/365/540/730 days: median absolute error and median bias
    (+ = the model was optimistic), split into "tune" (start dates before --split) and "check"
    (after) so a setting tuned on old data is judged on data it has not seen;
  - date error on the next 12 milestones of each song, by how far away they were (a milestone
    predicted but not reached by the end of the history counts too, as a lower bound).
Model settings can be overridden to compare variants (--damping, --drift-base, ...).
Read-only: never writes data.

  python dev/forecast_backtest.py --history db/streams_history.csv [--split 2024-07-01]

History CSV columns: date, track_id, streams, daily_streams (db/streams_history.csv format).
Used 2026-10-16 to choose the damped trend + long-run drift model (see forecast_milestones.py).
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from datetime import date, timedelta
from multiprocessing import Pool
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "collectors" / "spotify" / "streams" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import forecast_milestones as fm  # noqa: E402

HORIZONS = (30, 90, 180, 365, 540, 730)
DATE_BUCKETS = ((0, 30, "< 1 month"), (30, 90, "1-3 months"), (90, 180, "3-6 months"),
                (180, 365, "6-12 months"), (365, 99999, "> 1 year"))
MIN_TOTAL = fm.MILESTONE_STEP
MIN_ACTUAL_GAIN = 1_000_000
OVERRIDES = ("TREND_DAMPING", "DRIFT_BASE", "DRIFT_PER_TURNOVER", "DRIFT_MAX")


def _load(path: Path) -> dict[str, list[tuple[str, int, int]]]:
    rows: dict[str, list[tuple[str, int, int]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("streams") and r.get("daily_streams"):
                rows.setdefault(r["track_id"], []).append((r["date"], int(r["streams"]), int(r["daily_streams"])))
    return rows


def _work(args: tuple) -> list[tuple]:
    track_rows, cutoffs, overrides = args
    for name, value in overrides.items():
        setattr(fm, name, value)
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
        start, decay = max(est["projected_next_daily"], 1), est["decay_factor"]
        wf = fm.estimate_weekday_factors(series)
        total = hist[-1][1]
        cum, gains = 0.0, {}
        for day, daily in fm.projected_dailies(total, cut.isoformat(), start, decay, wf, max_days=max(HORIZONS)):
            cum += daily
            gains[day] = cum
        for h in HORIZONS:
            later = by_date.get((cut + timedelta(days=h)).isoformat())
            if later and later[1] - total > MIN_ACTUAL_GAIN and gains.get(h):
                out.append(("gain", cut, h, math.log(gains[h] / (later[1] - total))))
        # Milestone dates: the first 12 milestones above the total, whatever the model predicts.
        # A milestone predicted before the end of the history but never reached counts too (error =
        # at least the gap to the end), else only reached milestones are scored, which favours
        # optimistic models.
        future = [r for r in track_rows if r[0] > cut.isoformat()]
        end = date.fromisoformat(track_rows[-1][0])
        preds = {m["milestone"]: date.fromisoformat(m["expected_date"])
                 for m in fm.project_all_milestones(total, cut.isoformat(), start, decay, wf)}
        first = fm.next_milestone(total)
        for k in range(12):
            target = first + k * fm.MILESTONE_STEP
            real = next((date.fromisoformat(r[0]) for r in future if r[1] >= target), None)
            pred = preds.get(target, cut + timedelta(days=fm.LONG_MAX_DAYS))
            if real:
                out.append(("date", cut, (real - cut).days, (pred - real).days))
            elif pred <= end:
                out.append(("date", cut, (pred - cut).days, -(end - pred).days))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", type=Path, required=True)
    ap.add_argument("--split", default="2024-07-01", help="start dates before = tune, after = check")
    ap.add_argument("--first-cutoff", default="2023-01-15")
    ap.add_argument("--workers", type=int, default=4)
    for name in OVERRIDES:
        ap.add_argument("--" + name.lower().replace("_", "-"), type=float, default=None)
    args = ap.parse_args()
    overrides = {n: getattr(args, n.lower()) for n in OVERRIDES if getattr(args, n.lower()) is not None}

    rows = {k: v for k, v in _load(args.history).items() if max(r[1] for r in v) >= MIN_TOTAL}
    last = date.fromisoformat(max(r[0] for rs in rows.values() for r in rs))
    first = date.fromisoformat(args.first_cutoff)
    cutoffs = [first + timedelta(days=30 * i) for i in range((last - first).days // 30)]
    split = date.fromisoformat(args.split)

    with Pool(args.workers) as pool:
        res = [r for chunk in pool.map(_work, [(rs, cutoffs, overrides) for rs in rows.values()], chunksize=2)
               for r in chunk]

    print(f"history ends {last}; {len(cutoffs)} start dates {cutoffs[0]} → {cutoffs[-1]}; split {split}")
    print("model:", {n: overrides.get(n, getattr(fm, n)) for n in OVERRIDES})
    print("\nstreams gained: median abs error % / median bias % (+ = optimistic)")
    for part, cond in (("tune ", lambda c: c < split), ("check", lambda c: c >= split)):
        cells = []
        for h in HORIZONS:
            e = [x for kind, c, hh, x in res if kind == "gain" and hh == h and cond(c)]
            cells.append(f"{h}d {statistics.median(map(abs, e)) * 100:4.1f}/{statistics.median(e) * 100:+5.1f}"
                         if e else f"{h}d -")
        print(f"  {part} | " + " | ".join(cells))
    print("\nmilestone dates: reached (or predicted) in | n | median abs error / median bias (+ = late) | 80% within")
    for part, cond in (("tune ", lambda c: c < split), ("check", lambda c: c >= split)):
        for lo, hi, label in DATE_BUCKETS:
            e = [x for kind, c, rd, x in res if kind == "date" and lo <= rd < hi and cond(c)]
            if e:
                a = sorted(abs(x) for x in e)
                print(f"  {part} {label:12} | {len(e):5} | {statistics.median(a):4.0f} d / {statistics.median(e):+4.0f} d"
                      f" | ±{a[int(len(a) * 0.8)]:.0f} d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
