"""Importance of each finalize post on one shared 0-100 scale (decision 2026-10-10).

Owner: "sometimes milestones have to go first, sometimes something else" — the
paced order is no longer fixed by post type. Every paced post gets an
importance score, comparable across types:

- album update card: score_album_update score as is (typical 30-50, >= 70 =
  already urgent before this module existed);
- best-day-since song card: score_best_day_since score (gate 58, exceptional 90);
- song milestone (every 100M): grows with the size of the milestone (log
  scale), + round-number bonus (every 1B, every 500M) + all-time rank bonus;
- album milestone (every 1B): same idea on the album scale;
- song overtake: passed-song weight, relationship, rank threshold, daily
  competitiveness and a seven-day repetition penalty; gated at 25, capped at two.

Calibrated on the real events of 2026-09-29 -> 10-09 (see
previews_and_sims/post-priority-order/): Cruel Summer 3.5B #1 ~ 87,
folklore 12B #3 ~ 92, ME! 1B ~ 78, Love Story 1.2B ~ 63, Opalite 900M ~ 57,
How You Get The Girl 200M ~ 37; overtake into #20 ~ 75, Patient Zero #280 ~ 30.

`order_by_importance` merges the per-type queues: highest head first, never the
same type twice in a row while another type is waiting. Each type keeps its own
internal order (the album queue keeps its product rules: top 2, Showgirl/TTPD...).
"""
from __future__ import annotations

import math
from typing import Any

# Paced posts at or above this go to finalize's urgent phase (live, right
# after the urgent albums, before top eras / top songs).
URGENT_UNIT_SCORE_MIN = 80.0

WEEKEND_GAINERS_IMPORTANCE = 45.0
DEFAULT_ALBUM_IMPORTANCE = 35.0


def _rank_bonus(rank: Any, steps: tuple[tuple[int, float], ...]) -> float:
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return 0.0
    for limit, bonus in steps:
        if r <= limit:
            return bonus
    return 0.0


def song_milestone_importance(milestone: int, rank: Any) -> float:
    milestone = max(1, int(milestone))
    score = 30.0 + 25.0 * math.log10(milestone / 100_000_000)
    if milestone % 1_000_000_000 == 0:
        score += 20.0
    elif milestone % 500_000_000 == 0:
        score += 8.0
    score += _rank_bonus(rank, ((1, 10.0), (10, 10.0), (25, 6.0), (50, 3.0)))
    return round(min(100.0, score), 1)


def album_milestone_importance(milestone: int, rank: Any) -> float:
    billions = max(1.0, int(milestone) / 1_000_000_000)
    score = 55.0 + 20.0 * math.log10(billions)
    if billions % 5 == 0:
        score += 8.0
    score += _rank_bonus(rank, ((3, 10.0), (10, 5.0)))
    return round(min(100.0, score), 1)


def milestone_importance(event: dict, rank: Any = None) -> float:
    """event from post_stream_milestones (song or `album:` event)."""
    if str(event.get("track_id") or "").startswith("album:"):
        return album_milestone_importance(int(event["milestone"]), event.get("rank"))
    return song_milestone_importance(int(event["milestone"]), rank)


# Calibrated on Oct 3?12: 21 events / 18 grouped posts when replayed
# with a fresh post history, then applying the seven-day repetition penalty.
OVERTAKE_SCORE_MIN = 25.0
OVERTAKE_DAILY_POST_LIMIT = 2


def overtake_event_importance(event: dict) -> float:
    """Editorial score; all numeric inputs must come from verified history."""
    overtaker, passed = event["overtaker"], event["passed"]
    previous = event["previous_overtaker"]
    rank, old_rank = int(overtaker["rank"]), int(previous["rank"])
    tiers = ((10, 32.0), (25, 27.0), (50, 22.0), (100, 16.0), (200, 8.0))
    weight = max(_rank_bonus(event["previous_passed"]["rank"], tiers),
                 min(32.0, int(passed["streams"]) / 1_000_000_000 * 25.0))
    single_a = overtaker.get("is_single") is True
    single_b = passed.get("is_single") is True
    weight += 8.0 if single_b else 0.0
    relationship = 0.0
    family = overtaker.get("song_family")
    tv_a = "taylor's version" in overtaker["title"].casefold()
    tv_b = "taylor's version" in passed["title"].casefold()
    if family and family == passed.get("song_family") and tv_a != tv_b:
        relationship = 18.0
    if single_a and single_b:
        relationship = max(relationship, 12.0)
    if overtaker.get("album") and overtaker["album"] == passed.get("album"):
        relationship = max(relationship, 8.0)
    # Only explicit release dates support the new-song-versus-classic story.
    if event.get("stats_date") and overtaker.get("release_date") and passed.get("release_date"):
        from datetime import date
        target = date.fromisoformat(event["stats_date"])
        age_a = (target - date.fromisoformat(overtaker["release_date"][:10])).days
        age_b = (target - date.fromisoformat(passed["release_date"][:10])).days
        if 0 <= age_a <= 90 and age_b >= 3650:
            relationship = max(relationship, 12.0)
    milestone = any(rank <= boundary < old_rank for boundary in (10, 25, 50, 100))
    daily_a, daily_b = int(overtaker["daily_streams"]), int(passed["daily_streams"])
    closeness = min(daily_a, daily_b) / max(daily_a, daily_b) if max(daily_a, daily_b) > 0 else 0.0
    score = 15.0 + weight + relationship + 20.0 * closeness + (20.0 if milestone else 0.0)
    if event.get("recent_surpass_post") and not milestone:
        score -= 35.0
    return round(max(0.0, min(100.0, score)), 1)


def overtake_unit_importance(events: list[dict]) -> float:
    """A posted unit (ledger group or same-album image) = its best overtake."""
    return max((overtake_event_importance(e) for e in events), default=30.0)


def best_day_importance(row: dict) -> float:
    score = ((row.get("_post_score") or {}).get("score"))
    if score is None:
        # Unconditional (biggest day of the year) rows skip the batch scorer.
        return 85.0
    return round(float(score), 1)


# Album cards may follow each other (there are ~15 a weekday), at a small cost
# so another type with a close score gets its turn. Every other type
# (overtakes, milestones, best-day...) never posts twice in a row while
# another type is still waiting (owner, 2026-10-10).
REPEATABLE_KIND_PENALTY = {"album": 8.0}


def order_by_importance(queues: dict[str, list[tuple[Any, float]]]) -> list[tuple[str, Any, float]]:
    """Merge per-type queues [(item, importance), ...] (each kept in its own
    order) into one list: at each turn the queue with the highest pending
    importance posts. The type that just posted is blocked while another type
    is waiting, except the REPEATABLE_KIND_PENALTY types (albums), which only
    lose a few points. A queue's pending importance is the best score still in
    it (the album queue keeps product rules — Showgirl/TTPD forced after the
    top 2 — so a forced low-score album must not hold back better albums
    queued behind it). Ties keep the order of `queues`."""
    heads = {kind: 0 for kind in queues}
    order = list(queues)
    suffix_best = {
        kind: [max(score for _item, score in items[i:]) for i in range(len(items))]
        for kind, items in queues.items()
    }
    out: list[tuple[str, Any, float]] = []
    last_kind: str | None = None
    while True:
        waiting = [k for k in order if heads[k] < len(queues[k])]
        if not waiting:
            return out
        candidates = [
            k for k in waiting if k != last_kind or k in REPEATABLE_KIND_PENALTY
        ] or waiting

        def pending(k: str) -> float:
            penalty = REPEATABLE_KIND_PENALTY.get(k, 0.0) if k == last_kind else 0.0
            return suffix_best[k][heads[k]] - penalty

        kind = max(candidates, key=lambda k: (pending(k), -order.index(k)))
        item, score = queues[kind][heads[kind]]
        heads[kind] += 1
        out.append((kind, item, score))
        last_kind = kind
