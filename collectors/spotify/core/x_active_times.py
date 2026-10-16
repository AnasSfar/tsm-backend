"""Audience activity of the X account by weekday/hour (decision 2026-10-07).

Source: X analytics @swiftiescharts, "Active times" heatmap, last 28 days, read
2026-10-07 (Paris time). Levels 1 (least engaged) .. 5 (most engaged), 24 hours
(00h..23h) per weekday. Update LEVELS from a new screenshot when the audience
shifts (kept here, not in a .json: *.json is gitignored in this repo).

Used by streams finalize (post_pacing) to space its programmed posts: a
weight-1.0 hour is the reference rhythm, busier hours pack posts closer, dead
hours (3h-10h) stretch them out.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

AUDIENCE_TIMEZONE = ZoneInfo("Europe/Paris")
ACTIVITY_SOURCE_DATE = "2026-10-07"

LEVEL_WEIGHTS = {1: 0.5, 2: 0.75, 3: 1.0, 4: 1.15, 5: 1.3}

#           00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23
LEVELS = {
    0: [3, 2, 2, 1, 2, 1, 1, 1, 1, 1, 2, 1, 1, 1, 2, 2, 5, 3, 2, 2, 3, 1, 3, 3],  # mon
    1: [3, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 3, 5, 3, 3, 3, 2, 3, 3, 3],  # tue
    2: [3, 3, 2, 2, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1, 2, 3, 5, 3, 3, 3, 2, 3, 3, 3],  # wed
    3: [5, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 2, 2, 1, 2, 2, 5, 3, 2, 2, 1, 2, 3, 3],  # thu
    4: [3, 3, 3, 2, 2, 2, 2, 2, 1, 1, 1, 3, 2, 2, 3, 3, 5, 4, 3, 3, 2, 2, 4, 2],  # fri
    5: [3, 3, 2, 1, 2, 2, 3, 2, 2, 1, 2, 3, 2, 3, 2, 4, 5, 4, 4, 4, 5, 3, 2, 2],  # sat
    6: [3, 2, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 5, 3, 3, 2, 3, 2, 3, 3],  # sun
}


def engagement_weight(at: datetime) -> float:
    """Relative audience activity at this local (Paris) time, > 0."""
    try:
        return LEVEL_WEIGHTS[LEVELS[at.weekday()][at.hour]]
    except (KeyError, IndexError):
        return 1.0


def best_engagement_slot(
    target_date: date,
    *,
    now: datetime,
    busy: list[datetime],
    min_gap_seconds: int,
    min_lead_seconds: int = 180,
) -> datetime | None:
    """Best free minute of the target audience day, using the recorded heatmap.

    Prefer the highest activity, then a sustained peak, then the middle of
    the hour. Equal windows use the later one, avoiding unnecessarily early
    milestone announcements when activity is identical. No interpolation
    of streams or claim about intraday milestone crossing is involved.
    Naive busy times are host-local, like the existing X schedule registry.
    """
    if now.tzinfo is None:
        raise ValueError("engagement planning requires a timezone-aware now")
    for delta in (-1, 0, 1):
        weekday = (target_date + timedelta(days=delta)).weekday()
        levels = LEVELS.get(weekday)
        if not isinstance(levels, list) or len(levels) != 24 or any(level not in LEVEL_WEIGHTS for level in levels):
            raise ValueError(f"missing or invalid audience heatmap for weekday {weekday}; scheduling pending")
    start = datetime.combine(target_date, time.min, AUDIENCE_TIMEZONE).astimezone(timezone.utc)
    end = datetime.combine(target_date + timedelta(days=1), time.min, AUDIENCE_TIMEZONE).astimezone(timezone.utc)
    earliest = now.astimezone(timezone.utc) + timedelta(seconds=min_lead_seconds)
    busy_utc = [value.astimezone(timezone.utc) for value in busy]
    best, best_score = None, None
    at = start
    while at < end:
        if at >= earliest and all(abs((at - other).total_seconds()) >= min_gap_seconds for other in busy_utc):
            local = at.astimezone(AUDIENCE_TIMEZONE)
            activity = engagement_weight(local)
            if activity <= 0:
                raise ValueError(f"invalid audience activity at {local.isoformat()}")
            center = local.replace(minute=30).astimezone(timezone.utc)
            shoulders = sum(engagement_weight((center + timedelta(hours=offset)).astimezone(AUDIENCE_TIMEZONE)) for offset in (-1, 1))
            score = (activity, shoulders, -abs(local.minute - 30), at.timestamp())
            if best_score is None or score > best_score:
                best, best_score = local, score
        at += timedelta(minutes=1)
    return best
