#!/usr/bin/env python3
"""Smarter, re-runnable gap-fill estimator for chart_extra=true tracks that
are missing real daily data for one or more consecutive days, bounded on
both sides by a real (confirmed) total.

Never applied to chart_extra=false ("main") tracks — those must always wait
for real data, no matter how long the gap.

Unlike a flat day-count split, this:
  - Uses the track's FULL real history (every real daily_streams value ever
    recorded for it), not a fixed lookback window.
  - Computes per-weekday seasonal factors from that full history, so
    weekend dips (or whichever days run slower/faster for that song) are
    reflected in the split instead of assuming every day is identical.
  - Estimates a day-over-day %-growth trend from every real consecutive-day
    pair in the full history (recency-weighted, so recent momentum matters
    more than old momentum, but nothing is thrown away), then projects that
    trend forward from the last known real rate through every day in the
    gap (including the far bookend day itself). Reasoning in % growth
    rather than raw counts matters because a song's daily rate naturally
    decays (or spikes) over time — a flat/linear fit on raw counts would
    weight old, no-longer-representative levels too heavily.
  - The projected shape is then rescaled (not re-guessed) so it sums to
    exactly the known real total delta across the gap — the trend and
    seasonality only decide how that fixed, real amount is distributed
    across days, they never invent extra streams.

Safe to re-run at any time: a gap is only ever estimated once BOTH its
bookend days have real data. If a day currently only has one real bookend
(the other side is still pending), it is left alone. As soon as the
missing bookend arrives, re-running this script re-derives every day in
that gap from scratch using the better anchor — nothing is locked in
prematurely, matching the "estimation d'hier peut changer" requirement.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parents[2]))  # collectors/spotify/

import history_store  # noqa: E402

REASON = "gap_split_estimate"
MIN_POINTS_FOR_SEASONALITY = 14  # below this, weekday factors default to 1.0


def _real_points(rows: list[dict], track_id: str) -> list[tuple[date, int]]:
    """Every (date, daily_streams) this track has a real value for, sorted."""
    out = []
    for r in rows:
        if r.get("track_id") != track_id:
            continue
        daily_raw = (r.get("daily_streams") or "").strip()
        if not daily_raw:
            continue
        try:
            d = date.fromisoformat(r["date"])
            out.append((d, int(daily_raw)))
        except Exception:
            continue
    out.sort()
    return out


def _weekday_seasonal_factors(points: list[tuple[date, int]]) -> dict[int, float]:
    """weekday (0=Mon..6=Sun) -> multiplicative factor, from the FULL history.

    For each point, compares its value against a centered local average
    (the surrounding real points within +/-3 calendar days) to isolate the
    weekly pattern from the underlying trend. Falls back to neutral (1.0)
    factors when there isn't enough history to trust a seasonal signal."""
    if len(points) < MIN_POINTS_FOR_SEASONALITY:
        return {i: 1.0 for i in range(7)}

    by_date = dict(points)
    ratios_by_weekday: dict[int, list[float]] = {i: [] for i in range(7)}
    for d, v in points:
        window = [
            by_date[d + timedelta(days=off)]
            for off in range(-3, 4)
            if (d + timedelta(days=off)) in by_date
        ]
        if len(window) < 3 or v <= 0:
            continue
        local_avg = statistics.mean(window)
        if local_avg <= 0:
            continue
        ratios_by_weekday[d.weekday()].append(v / local_avg)

    factors = {}
    for wd in range(7):
        vals = ratios_by_weekday[wd]
        factors[wd] = statistics.median(vals) if len(vals) >= 3 else 1.0

    # Normalize so the factors average to 1.0 (pure reallocation, no net bias).
    mean_factor = statistics.mean(factors.values())
    if mean_factor > 0:
        factors = {wd: f / mean_factor for wd, f in factors.items()}
    return factors


GROWTH_HALF_LIFE_DAYS = 30.0   # recency weighting for the %-growth trend
GROWTH_CLAMP = (0.5, 2.0)      # sanity bounds on the fitted daily growth factor


def _growth_factor(points: list[tuple[date, int]], seasonal: dict[int, float], anchor: date) -> float:
    """Recency-weighted geometric-mean day-over-day growth factor across the
    track's FULL history of real, consecutive-day pairs (deseasonalized so
    weekday effects don't get mistaken for trend). `anchor` is the gap's
    start date — pairs closer to it in time count for more, but every real
    pair in history contributes something (half-life weighting, nothing is
    discarded outright)."""
    log_terms = []
    weights = []
    for (d0, v0), (d1, v1) in zip(points, points[1:]):
        if (d1 - d0).days != 1 or v0 <= 0 or v1 <= 0:
            continue
        r0 = v0 / seasonal.get(d0.weekday(), 1.0)
        r1 = v1 / seasonal.get(d1.weekday(), 1.0)
        if r0 <= 0 or r1 <= 0:
            continue
        age_days = abs((anchor - d1).days)
        weight = 0.5 ** (age_days / GROWTH_HALF_LIFE_DAYS)
        log_terms.append(weight * math.log(r1 / r0))
        weights.append(weight)

    if not weights or sum(weights) <= 0:
        return 1.0
    avg_log = sum(log_terms) / sum(weights)
    growth = math.exp(avg_log)
    return max(GROWTH_CLAMP[0], min(GROWTH_CLAMP[1], growth))


def estimate_gap(track_id: str, rows: list[dict]) -> list[tuple[date, int, int]]:
    """Returns [(date, total, daily), ...] for every day that either:
      - has no real data for this track and sits strictly between two days
        that DO have real data (gets a fresh total+daily), or
      - is the real bookend day right after such a gap (its total is kept
        exactly as recorded, but its daily is corrected down from "however
        many days piled up since the last real day" to just its own share).
    Empty list if there's no such fully-bounded gap."""
    points = _real_points(rows, track_id)
    if len(points) < 2:
        return []

    totals_by_date: dict[date, int] = {}
    for r in rows:
        if r.get("track_id") != track_id:
            continue
        try:
            d = date.fromisoformat(r["date"])
            totals_by_date[d] = int(r["streams"])
        except Exception:
            continue

    seasonal = _weekday_seasonal_factors(points)
    real_dates = {d for d, _ in points}

    out: list[tuple[date, int, int]] = []
    for (d_start, rate_start), (d_end, _rate_end) in zip(points, points[1:]):
        gap_days = (d_end - d_start).days
        if gap_days <= 1:
            continue  # no missing day in between
        missing_dates = [d_start + timedelta(days=k) for k in range(1, gap_days)]
        if any(d in real_dates for d in missing_dates):
            continue  # shouldn't happen, but don't touch if something's already real

        total_start = totals_by_date.get(d_start)
        total_end = totals_by_date.get(d_end)
        if total_start is None or total_end is None:
            continue
        delta_total = total_end - total_start
        if delta_total < 0:
            continue  # a real decrease across the gap — never invent a story for that

        # Project the deseasonalized rate forward from the last clean real
        # rate (day_start) through every day of the gap, INCLUDING d_end
        # itself — d_end's currently-stored daily is contaminated (it's
        # whatever multi-day total landed there), so it's never used as an
        # anchor, only as a total to redistribute towards.
        growth = _growth_factor(points, seasonal, d_start)
        deseason_start = rate_start / seasonal.get(d_start.weekday(), 1.0)
        span_dates = missing_dates + [d_end]
        projected = [
            deseason_start * (growth ** k) * seasonal.get(span_dates[k - 1].weekday(), 1.0)
            for k in range(1, len(span_dates) + 1)
        ]
        projected = [max(p, 0.0) for p in projected]
        total_projected = sum(projected) or 1.0

        running_total = total_start
        allocated = 0
        for i, d in enumerate(span_dates):
            if i == len(span_dates) - 1:
                share = delta_total - allocated  # last day (d_end) absorbs rounding, guarantees exact sum
            else:
                share = round(delta_total * projected[i] / total_projected)
                share = max(0, min(delta_total - allocated, share))
            allocated += share
            running_total += share
            out.append((d, running_total, share))

    return out


def _track_labels() -> dict[str, str]:
    labels: dict[str, str] = {}
    for section in history_store.load_album_sections_flat():
        album = section.get("album", "")
        for track in section.get("tracks", []):
            tid = history_store.extract_track_id(track.get("url") or track.get("spotify_url") or "")
            if tid and tid not in labels:
                name = track.get("title_clean") or track.get("title") or ""
                labels[tid] = f"{name} ({album})" if album else name
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--track-ids", help="Comma-separated track ids to check. Default: every chart_extra=true track.")
    parser.add_argument("--apply", action="store_true", help="Write the estimates (default: dry-run report only).")
    args = parser.parse_args()

    path = history_store.HISTORY_PATH
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    original_count = len(rows)

    main_ids = history_store.load_album_track_ids()  # chart_extra=false only
    if args.track_ids:
        candidate_ids = [t.strip() for t in args.track_ids.split(",") if t.strip()]
        blocked = [t for t in candidate_ids if t in main_ids]
        if blocked:
            print(f"[refuse] {len(blocked)} of the given track(s) are chart_extra=false — "
                  f"never estimated, skipping them: {blocked}")
        target_ids = [t for t in candidate_ids if t not in main_ids]
    else:
        all_ids = {r["track_id"] for r in rows} - main_ids
        target_ids = sorted(all_ids)

    labels = _track_labels()
    all_estimates: dict[tuple[str, str], tuple[int, int]] = {}  # (date_iso, track_id) -> (total, daily)
    for tid in target_ids:
        for d, total, daily in estimate_gap(tid, rows):
            all_estimates[(d.isoformat(), tid)] = (total, daily)
            name = labels.get(tid, tid)
            print(f"{d.isoformat()} | {name}: total={total} daily=+{daily}")

    print(f"\n{len(all_estimates)} day(s) would be filled across {len(target_ids)} candidate track(s).")
    if not args.apply:
        print("(dry-run — pass --apply to write these.)")
        return

    out_rows = []
    seen = set()
    for r in rows:
        key = (r["date"], r["track_id"])
        if key in all_estimates:
            total, daily = all_estimates[key]
            r = dict(r)
            r["streams"] = str(total)
            r["daily_streams"] = str(daily)
            r["estimated"] = "1"
            r["estimated_reason"] = REASON
            seen.add(key)
        out_rows.append(r)
    for (date_iso, tid), (total, daily) in all_estimates.items():
        if (date_iso, tid) in seen:
            continue
        out_rows.append({
            "date": date_iso, "track_id": tid,
            "streams": str(total), "daily_streams": str(daily),
            "estimated": "1", "estimated_reason": REASON,
        })

    tmp_path = path.with_suffix(".csv.tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)
    tmp_count = sum(1 for _ in tmp_path.open("r", encoding="utf-8-sig")) - 1
    if tmp_count != len(out_rows):
        print(f"[abort] verify failed: tmp has {tmp_count} rows, expected {len(out_rows)}.")
        tmp_path.unlink()
        return
    tmp_path.replace(path)
    print(f"Applied. Master CSV: {original_count} -> {len(out_rows)} rows.")


if __name__ == "__main__":
    main()
