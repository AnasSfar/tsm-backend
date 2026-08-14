"""Shared "best rank since" walk-back primitive.

Mirrors collectors/spotify/streams/best_day_since.py's walk-back algorithm
(compute_best_day_since / passes_filters / sort_key), but for CHART RANK
instead of daily streams. Lower rank number = better, so the comparison
direction is flipped: walk a track's rank history backward from today looking
for the last day with an equal-or-BETTER (equal-or-lower) rank, instead of an
equal-or-higher streams value.

Used by scripts/generate_home_highlights.py (Spotify Charts, inline) and
collectors/apple_music/best_rank_since.py (Apple Music Global chart).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class RankPoint:
    day: date
    rank: int | None


def compute_rank_since(
    points: list[RankPoint],
    target_date: date,
    *,
    release_date: date | None = None,
    history_start_date: date | None = None,
) -> dict | None:
    """Walk `points` backward from `target_date` looking for the last day with
    an equal-or-better (equal-or-lower) rank than today.

    Returns None if there is no rank recorded for target_date, or if today's
    rank isn't actually an improvement over yesterday's data.

    If no qualifying prior point is found at all, this only returns a
    kind="best_ever" row when `release_date is not None and
    history_start_date is not None and release_date >= history_start_date` —
    i.e. only when the caller can vouch that history for this track is
    complete back to its release. Otherwise it returns None rather than risk
    a false "ever" claim on data with gaps (callers that can never youch for
    this — e.g. Apple Music's short local history — should leave both
    `release_date` and `history_start_date` as None, which structurally
    disables the best_ever case).
    """
    point_by_date = {point.day: point for point in points}
    current = point_by_date.get(target_date)
    if current is None or current.rank is None or current.rank <= 0:
        return None

    previous_day = point_by_date.get(target_date - timedelta(days=1))
    if previous_day is None or previous_day.rank is None:
        return None

    previous_points = [
        point for point in points
        if point.day < target_date and point.rank is not None
    ]
    if not previous_points:
        return None

    last_at_or_better: RankPoint | None = None
    for point in reversed(previous_points):
        if point.rank is not None and point.rank <= current.rank:
            last_at_or_better = point
            break

    if last_at_or_better is None:
        if (
            release_date is not None
            and history_start_date is not None
            and release_date >= history_start_date
        ):
            return {
                "date": target_date.isoformat(),
                "rank": current.rank,
                "previous_day_rank": previous_day.rank,
                "kind": "best_ever",
                "best_rank_since": None,
                "previous_at_or_better_date": None,
                "previous_at_or_better_rank": None,
                "days_since": None,
                "first_available_date": previous_points[0].day.isoformat(),
            }
        return None

    best_since = last_at_or_better.day + timedelta(days=1)
    if best_since >= target_date:
        return None

    return {
        "date": target_date.isoformat(),
        "rank": current.rank,
        "previous_day_rank": previous_day.rank,
        "kind": "since",
        "best_rank_since": best_since.isoformat(),
        "previous_at_or_better_date": last_at_or_better.day.isoformat(),
        "previous_at_or_better_rank": last_at_or_better.rank,
        "days_since": (target_date - best_since).days + 1,
        "first_available_date": points[0].day.isoformat() if points else None,
    }


def passes_filters(row: dict, *, min_days: int) -> bool:
    if row["kind"] == "best_ever":
        return True
    return (row.get("days_since") or 0) >= min_days


def sort_key(row: dict) -> tuple[int, int, int]:
    is_best_ever = 1 if row["kind"] == "best_ever" else 0
    days_since = row.get("days_since") or 0
    return (is_best_ever, days_since, -row["rank"])
