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

from datetime import datetime

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
