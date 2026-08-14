#!/usr/bin/env python3
"""Compute a "best rank since" record for Apple Music's Global chart.

Mirrors collectors/spotify/streams/best_day_since.py's walk-back concept via
the shared collectors/spotify/core/rank_since.py primitive, applied to
db/apple_music_global.csv (unioned with its per-day snapshot copies under
snapshots/apple_music_charts/, via export_apple_music.py's read_csv_rows()).

Apple Music's local history only goes back a few months (collection started
2026-06-05 on this dev machine; production VPS cron since 2026-07-30) — this
module NEVER emits a kind="best_ever" record (always calls compute_rank_since
with release_date=None, history_start_date=None), so it only ever reports
"since <date>" records, never an unqualified "ever" claim. See the
collector-apple-music skill's "jamais de NEW pour une chanson deja sortie"
rule for the same honesty principle applied here.

CLI:
    python collectors/apple_music/best_rank_since.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.rank_since import RankPoint, compute_rank_since, passes_filters, sort_key  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from export_apple_music import GLOBAL_CSV, clean_str, read_csv_rows, to_int  # noqa: E402

RANK_SINCE_MIN_DAYS = 14


def _daily_rank_series(rows: list[dict]) -> tuple[dict[str, list[RankPoint]], dict[str, dict]]:
    """Collapse multiple same-day `scraped_at` snapshots to the LAST one per
    (apple_music_id, date) — mirrors export_apple_music.py's window_rows()
    "keep the last snapshot of each past day" rule (that function is a
    closure over module-level state and isn't importable, so this
    re-implements just that one rule rather than the whole function)."""
    latest_by_key: dict[tuple[str, str], tuple[str, dict]] = {}
    for row in rows:
        am_id = clean_str(row.get("apple_music_id"))
        if not am_id:
            continue
        scraped_at = clean_str(row.get("scraped_at")) or clean_str(row.get("date"))
        day = scraped_at[:10]
        if not day:
            continue
        key = (am_id, day)
        current = latest_by_key.get(key)
        if current is None or scraped_at >= current[0]:
            latest_by_key[key] = (scraped_at, row)

    series: dict[str, list[RankPoint]] = {}
    meta_by_id: dict[str, dict] = {}
    for (am_id, day), (scraped_at, row) in latest_by_key.items():
        try:
            day_date = date.fromisoformat(day)
        except ValueError:
            continue
        series.setdefault(am_id, []).append(RankPoint(day=day_date, rank=to_int(row.get("rank"))))
        existing_meta = meta_by_id.get(am_id)
        if existing_meta is None or scraped_at >= existing_meta.get("_scraped_at", ""):
            meta_by_id[am_id] = {
                "song_name": clean_str(row.get("song_name")),
                "image_url": clean_str(row.get("image_url")),
                "url": clean_str(row.get("url")),
                "artist_name": clean_str(row.get("artist_name")) or "Taylor Swift",
                "_scraped_at": scraped_at,
            }

    for points in series.values():
        points.sort(key=lambda p: p.day)
    return series, meta_by_id


def compute_apple_music_rank_since(*, min_days: int = RANK_SINCE_MIN_DAYS) -> dict | None:
    """Return the single strongest qualifying "best rank since" record for
    today's Apple Music Global chart, or None if nothing qualifies."""
    rows = read_csv_rows(GLOBAL_CSV)
    if not rows:
        return None

    series, meta_by_id = _daily_rank_series(rows)
    if not series:
        return None

    target_date = max((points[-1].day for points in series.values() if points), default=None)
    if target_date is None:
        return None

    candidates: list[dict] = []
    for am_id, points in series.items():
        row = compute_rank_since(points, target_date, release_date=None, history_start_date=None)
        if row and passes_filters(row, min_days=min_days):
            row["apple_music_id"] = am_id
            row.update(meta_by_id.get(am_id, {}))
            row.pop("_scraped_at", None)
            candidates.append(row)

    if not candidates:
        return None
    return max(candidates, key=sort_key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute best-rank-since for Apple Music's Global chart.")
    parser.add_argument("--min-days", type=int, default=RANK_SINCE_MIN_DAYS)
    args = parser.parse_args()

    row = compute_apple_music_rank_since(min_days=args.min_days)
    if not row:
        print("No qualifying best-rank-since record today.")
        return

    if row["kind"] == "since":
        print(f"{row['song_name']} | #{row['rank']} | best rank since {row['best_rank_since']} ({row['days_since']} days)")
    else:
        print(f"{row['song_name']} | #{row['rank']} | new best rank")


if __name__ == "__main__":
    main()
