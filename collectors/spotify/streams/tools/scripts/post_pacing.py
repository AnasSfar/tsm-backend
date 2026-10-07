"""X schedule plan for the paced finalize posts (decision 2026-10-07).

Replaces the flat 60 s -> 6 min curve of 2026-10-04 (posts still looked ~2 min
apart). Each paced post gets a gap that:
- grows logarithmically with its rank k in the paced phase:
  BASE x (1 + GROWTH x ln(1 + k)) -> 3, 6, 8, 9, 10, 11... min at reference activity;
- is measured in AUDIENCE time, not clock time: the gap is consumed at the
  speed of core.x_active_times.engagement_weight (X analytics heatmap), so
  posts pack closer around 16h / evenings and stretch through 3h-10h — the
  spread depends on the hour the streams became available;
- fits the number of posts left: when the whole remaining plan would end after
  the window deadline (data availability + WINDOW_HOURS), every gap is scaled
  down (never under MIN_GAP_SECONDS);
- never collides with a post already programmed on the account by another
  update/run (core.twitter schedule registry, COLLISION gap), and stays a
  whole minute (X schedules per minute).
The plan is recomputed before every step from the last real slot, so a step
posting more or fewer posts than expected only shifts what follows.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta
from typing import Callable

from core import twitter as twitter_core
from core.x_active_times import engagement_weight

BASE_GAP_SECONDS = float(os.getenv("FINALIZE_SCHEDULE_BASE_SECONDS", "180"))
GROWTH = float(os.getenv("FINALIZE_SCHEDULE_GROWTH", "1.5"))
MIN_GAP_SECONDS = int(os.getenv("FINALIZE_SCHEDULE_MIN_GAP_SECONDS", "180"))
MAX_GAP_SECONDS = int(os.getenv("FINALIZE_SCHEDULE_MAX_GAP_SECONDS", str(45 * 60)))
WINDOW_HOURS = float(os.getenv("FINALIZE_SCHEDULE_WINDOW_HOURS", "8"))
MIN_LEAD_SECONDS = int(os.getenv("FINALIZE_SCHEDULE_MIN_LEAD_SECONDS", "150"))


def weighted_gap_seconds(k: int) -> float:
    """Gap before the paced post of rank k, in audience-weighted seconds."""
    return BASE_GAP_SECONDS * (1.0 + GROWTH * math.log1p(max(0, k)))


def window_deadline(start: datetime) -> datetime:
    return start + timedelta(hours=WINDOW_HOURS)


def _advance(t: datetime, weighted_s: float, weight_fn: Callable[[datetime], float]) -> datetime:
    """Clock time after spending weighted_s of audience time from t."""
    remaining = weighted_s
    while remaining > 1e-6:
        w = weight_fn(t)
        step = min(60.0, remaining / w)
        t += timedelta(seconds=step)
        remaining -= step * w
    return t


def plan_slots(
    *,
    start: datetime,
    first_index: int,
    count: int,
    deadline: datetime,
    busy: list[datetime],
    now: datetime | None = None,
    weight_fn: Callable[[datetime], float] = engagement_weight,
) -> tuple[list[datetime], float]:
    """Slots for the next `count` paced posts (ranks first_index...), chained
    from `start` (previous slot, or now). Returns (slots, scale): scale < 1
    when the gaps were compressed to fit the deadline."""
    now = now or datetime.now()
    count = max(1, int(count))
    earliest = now + timedelta(seconds=MIN_LEAD_SECONDS)

    def chain(scale: float) -> list[datetime]:
        out: list[datetime] = []
        prev = max(start, now)
        for i in range(count):
            t = _advance(prev, scale * weighted_gap_seconds(first_index + i), weight_fn)
            t = min(max(t, prev + timedelta(seconds=MIN_GAP_SECONDS)), prev + timedelta(seconds=MAX_GAP_SECONDS))
            t = max(t, earliest)
            t = twitter_core.free_schedule_slot(t, busy)
            out.append(t)
            prev = t
        return out

    natural = chain(1.0)
    if natural[-1] <= deadline:
        return natural, 1.0
    floor = chain(0.0)
    if floor[-1] > deadline:
        # Even at the minimum gap the posts don't fit: post them all anyway
        # (never drop a post), as tight as allowed.
        return floor, 0.0
    lo, hi = 0.0, 1.0
    for _ in range(20):
        mid = (lo + hi) / 2
        if chain(mid)[-1] <= deadline:
            lo = mid
        else:
            hi = mid
    return chain(lo), lo
