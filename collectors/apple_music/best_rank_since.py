#!/usr/bin/env python3
"""Compute a "best rank since" record for Apple Music's Global chart.

Mirrors collectors/spotify/streams/best_day_since.py's walk-back concept via
the shared collectors/spotify/core/rank_since.py primitive, applied to
db/apple_music_global.csv (unioned with its per-day snapshot copies under
snapshots/apple_music_charts/, via export_apple_music.py's read_csv_rows()).

Apple Music's local history only goes back a few months (collection started
2026-06-05 on this dev machine; production VPS cron since 2026-07-30) — this
module never emits kind="best_ever" from its own history (always calls
compute_rank_since with release_date=None, history_start_date=None).
Exception (2026-09-25): songs listed in db/apple_music_global_alltime_peaks.json
(owner reference: all-time Global peak + best rank 2026-01-01 -> 06-01) can get
"best_ever" or "since 2026-01-01" when they beat our whole history — see
_reference_record. See the
collector-apple-music skill's "jamais de NEW pour une chanson deja sortie"
rule for the same honesty principle applied here.

CLI:
    python collectors/apple_music/best_rank_since.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.rank_since import RankPoint, compute_rank_since, passes_filters, sort_key  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from export_apple_music import GLOBAL_CSV, clean_str, read_csv_rows, to_int  # noqa: E402

RANK_SINCE_MIN_DAYS = 14

# Owner-provided Global reference (2026-09-25) for songs released before our
# history: all-time `peak` + `best_2026_jan1_jun1` (Kworb top 200). Used only
# when a song beats EVERY point of our own history (compute_rank_since then
# returns None: we can't see before our first day).
REFERENCE_PATH = ROOT / "db" / "apple_music_global_alltime_peaks.json"
REFERENCE_2026_START = date(2026, 1, 1)
KWORB_DEPTH = 200  # "outside top 200" in the reference = worse than #200


def _load_reference() -> dict[str, dict]:
    import json

    try:
        payload = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {clean_str(s.get("song_name")).casefold(): s for s in payload.get("songs") or [] if s.get("song_name")}


def _reference_record(points: list[RankPoint], target_date: date, ref: dict) -> dict | None:
    """Record backed by the reference when today's rank beats our whole
    history:
    - better than the all-time peak -> kind="best_ever" (verifiable: the
      reference covers the song's whole run before our history);
    - better than its best rank from 2026-01-01 to 2026-06-01 -> "since
      2026-01-01" (our own history covers the rest of 2026);
    - otherwise the last equal-or-better day is somewhere before our history
      with no known date -> None, as before (never guess a date)."""
    by_day = {p.day: p for p in points}
    current = by_day.get(target_date)
    previous_day = by_day.get(target_date - timedelta(days=1))
    if current is None or not current.rank or current.rank <= 0 or previous_day is None or previous_day.rank is None:
        return None
    previous = [p for p in points if p.day < target_date and p.rank is not None]
    if not previous or any(p.rank <= current.rank for p in previous):
        return None  # compute_rank_since already had the answer
    if min(p.day for p in previous) > date(2026, 6, 1):
        return None  # reference window and our history don't join up
    base = {
        "date": target_date.isoformat(),
        "rank": current.rank,
        "previous_day_rank": previous_day.rank,
        "first_available_date": points[0].day.isoformat(),
        "source_note": "owner reference db/apple_music_global_alltime_peaks.json",
    }
    peak = ref.get("peak")
    if isinstance(peak, int) and current.rank < peak:
        return {**base, "kind": "best_ever", "best_rank_since": None, "previous_at_or_better_date": None,
                "previous_at_or_better_rank": None, "days_since": None}
    best_2026 = ref.get("best_2026_jan1_jun1")
    if not isinstance(best_2026, int):
        best_2026 = KWORB_DEPTH + 1 if ref.get("outside_top200_2026_jan1_jun1") else None
    if best_2026 is not None and current.rank < best_2026:
        return {**base, "kind": "since", "best_rank_since": REFERENCE_2026_START.isoformat(),
                "previous_at_or_better_date": None, "previous_at_or_better_rank": None,
                "days_since": (target_date - REFERENCE_2026_START).days + 1}
    return None


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

    reference = _load_reference()
    candidates: list[dict] = []
    for am_id, points in series.items():
        row = compute_rank_since(points, target_date, release_date=None, history_start_date=None)
        if row is None:
            ref = reference.get(clean_str(meta_by_id.get(am_id, {}).get("song_name")).casefold())
            if ref:
                row = _reference_record(points, target_date, ref)
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
