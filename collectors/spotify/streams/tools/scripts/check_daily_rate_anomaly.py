#!/usr/bin/env python3
"""Read-only sanity check: flag tracks whose freshly-written daily for a date
looks like it secretly bakes in more than one day of real streams (Spotify's
own backend catching up silently, distinct from a calendar gap in our CSV).

Unlike a calendar-gap check (comparing row dates), this compares the SIZE of
the delta to the track's own recent rate, so it also catches a "gap_days=1"
row whose live total already reflects 2+ days of listening.

Never writes/modifies anything. Meant to run right after update_streams.py
writes a date's rows, before finalize exports/posts, so an anomaly can be
reviewed manually first (data-rules #1: false data is worse than missing).

Usage:
  python check_daily_rate_anomaly.py 2026-09-17
  python check_daily_rate_anomaly.py 2026-09-17 --ratio 1.6
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parents[2]))  # collectors/spotify/

import history_store  # noqa: E402

RATE_WINDOW_DAYS = 14
DEFAULT_RATIO = 1.6  # flag if daily > this x the track's recent median


def _median_rate(rows_by_track: dict[str, list[tuple[date, int]]], track_id: str, before: date) -> float | None:
    values = [
        daily for d, daily in rows_by_track.get(track_id, [])
        if before - timedelta(days=RATE_WINDOW_DAYS) <= d < before
    ]
    if not values:
        return None
    return statistics.median(values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Flag possible multi-day-in-one deltas for a date.")
    parser.add_argument("date", help="Date to check, YYYY-MM-DD.")
    parser.add_argument("--ratio", type=float, default=DEFAULT_RATIO,
                         help=f"Flag threshold as x the recent median daily (default {DEFAULT_RATIO}).")
    args = parser.parse_args()
    target = date.fromisoformat(args.date)

    labels = {}
    for section in history_store.load_album_sections_flat():
        album = section.get("album", "")
        for track in section.get("tracks", []):
            tid = history_store.extract_track_id(track.get("url") or track.get("spotify_url") or "")
            if tid and tid not in labels:
                name = track.get("title_clean") or track.get("title") or ""
                labels[tid] = f"{name} ({album})" if album else name

    rows_by_track: dict[str, list[tuple[date, int]]] = {}
    target_daily: dict[str, int] = {}
    for r in history_store.load_history_rows():
        tid = r.get("track_id")
        if not tid:
            continue
        raw = (r.get("daily_streams") or "").strip()
        if not raw:
            continue
        try:
            d = date.fromisoformat((r.get("date") or "").strip())
            daily = int(raw)
        except Exception:
            continue
        if d == target:
            target_daily[tid] = daily
        else:
            rows_by_track.setdefault(tid, []).append((d, daily))

    if not target_daily:
        print(f"No daily rows written yet for {args.date}. Nothing to check.")
        return

    flagged = []
    for tid, daily in target_daily.items():
        rate = _median_rate(rows_by_track, tid, target)
        if not rate or rate <= 0:
            continue
        ratio = daily / rate
        if ratio >= args.ratio:
            flagged.append((tid, labels.get(tid, tid), daily, rate, ratio))

    print(f"Checked {len(target_daily)} track(s) with a daily for {args.date}.")
    if not flagged:
        print("No anomaly: every daily is within the normal range of its recent rate.")
        return

    flagged.sort(key=lambda x: -x[4])
    print(f"\n{len(flagged)} track(s) look like they might bake in more than one day "
          f"(daily >= {args.ratio}x their {RATE_WINDOW_DAYS}-day median rate) — review before posting/exporting:")
    for tid, name, daily, rate, ratio in flagged:
        print(f"  - {name}: daily={daily:,} vs median_rate={rate:,.0f} -> {ratio:.2f}x")


if __name__ == "__main__":
    main()
