#!/usr/bin/env python3
"""Score Spotify stream best-day-since candidates.

This script is intentionally read-only: it reuses ``best_day_since.py`` to find
exact best-day-since rows, then ranks them by a richer "post interest" score.

Examples:
  python tools/scripts/score_best_day_since.py
  python tools/scripts/score_best_day_since.py 2026-08-26 --limit 20
  python tools/scripts/score_best_day_since.py 2026-08-26 --json
  python tools/scripts/score_best_day_since.py 2026-08-26 --output best_day_scores.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
STREAMS_DIR = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(STREAMS_DIR))

import best_day_since  # noqa: E402
from core.data_paths import update_streams_dir  # noqa: E402


DEFAULT_LIMIT = 25
DEFAULT_MIN_DAYS = best_day_since.DEFAULT_MIN_DAYS
DEFAULT_MIN_DAILY_STREAMS = 0
RECENT_POST_WINDOW_DAYS = 14
DEFAULT_EARLY_MIN_SCORE = 58.0
ERA_CLUSTER_MIN_ROWS = 3
BEST_EVER_BONUS = 8.0
BIGGEST_DAY_OF_YEAR_BONUS = 18.0
BIGGEST_DAY_OF_MONTH_BONUS = 2.0
COMBINED_TRACK_BONUS = 1.0
EARLY_YEAR_RECORD_THRESHOLD_DISCOUNT = -5.0


@dataclass(frozen=True)
class ScoreWeights:
    age: float = 0.08
    daily_abs_gain: float = 0.20
    daily_pct_gain: float = 0.16
    weekly_pct_gain: float = 0.20
    rarity: float = 0.18
    grower: float = 0.18


WEIGHTS = ScoreWeights()
EARLY_WEIGHTS = ScoreWeights(
    age=0.08,
    daily_abs_gain=0.18,
    daily_pct_gain=0.18,
    weekly_pct_gain=0.18,
    rarity=0.18,
    grower=0.20,
)


def _point_by_day(points: list[best_day_since.Point]) -> dict[date, best_day_since.Point]:
    return {point.day: point for point in best_day_since.fill_missing_dailies(points)}


def _daily_on(points_by_day: dict[date, best_day_since.Point], day: date) -> int | None:
    point = points_by_day.get(day)
    if point is None or point.daily is None:
        return None
    return point.daily


def _window_dailies(
    points_by_day: dict[date, best_day_since.Point],
    *,
    start: date,
    end: date,
) -> list[int]:
    values: list[int] = []
    current = start
    while current <= end:
        daily = _daily_on(points_by_day, current)
        if daily is not None:
            values.append(daily)
        current += timedelta(days=1)
    return values


def _pct_change(today: float, baseline: float | None) -> float | None:
    if baseline is None or baseline <= 0:
        return None
    return (today - baseline) / baseline * 100.0


def _log_age_score(days_since: int | None) -> float:
    if days_since is None:
        return 1.0
    return min(1.0, math.log1p(max(days_since, 0)) / math.log1p(3650))


def _percentile_scores(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    unique = sorted(set(values.values()))
    if len(unique) == 1:
        return {key: 1.0 for key in values}
    max_index = len(unique) - 1
    ranks = {value: index / max_index for index, value in enumerate(unique)}
    return {key: ranks[value] for key, value in values.items()}


def _positive_days(values: Iterable[int]) -> int:
    return sum(1 for value in values if value > 0)


def _streak_score(points_by_day: dict[date, best_day_since.Point], target: date) -> float:
    # Last 4 day-over-day moves ending today. Missing comparison days make the
    # streak incomplete rather than guessed.
    moves: list[int] = []
    for offset in range(3, -1, -1):
        day = target - timedelta(days=offset)
        today = _daily_on(points_by_day, day)
        yesterday = _daily_on(points_by_day, day - timedelta(days=1))
        if today is None or yesterday is None:
            continue
        moves.append(today - yesterday)
    if not moves:
        return 0.0
    return _positive_days(moves) / len(moves)


def _acceleration_score(points_by_day: dict[date, best_day_since.Point], target: date) -> float:
    today = _daily_on(points_by_day, target)
    yesterday = _daily_on(points_by_day, target - timedelta(days=1))
    two_days_ago = _daily_on(points_by_day, target - timedelta(days=2))
    if today is None or yesterday is None or two_days_ago is None:
        return 0.0
    latest_gain = today - yesterday
    previous_gain = yesterday - two_days_ago
    if latest_gain <= 0:
        return 0.0
    if previous_gain <= 0:
        return 1.0
    return max(0.0, min(1.0, (latest_gain - previous_gain) / max(latest_gain, previous_gain)))


def _avg(values: list[int]) -> float | None:
    return statistics.fmean(values) if values else None


def _album_key(album: str | None) -> str:
    text = (album or "").strip().casefold()
    if not text:
        return ""
    aliases = {
        "fearless (taylor's version)": "fearless",
        "speak now (taylor's version)": "speak now",
        "red (taylor's version)": "red",
        "1989 (taylor's version)": "1989",
        "the tortured poets department: the anthology": "the tortured poets department",
        "midnights (the til dawn edition)": "midnights",
        "midnights (3am edition)": "midnights",
        "folklore: the long pond studio sessions": "folklore",
    }
    if text in aliases:
        return aliases[text]
    text = text.replace("(taylor's version)", "")
    for token in (
        "(deluxe edition)",
        "(deluxe)",
        "(standard edition)",
        "(expanded edition)",
        "(the anthology)",
        "(3am edition)",
        "(the til dawn edition)",
    ):
        text = text.replace(token, "")
    return " ".join(text.split())


def _holiday_track(row: dict) -> bool:
    text = f"{row.get('title') or ''} {row.get('album') or ''}".casefold()
    return any(token in text for token in ("christmas", "santa", "holiday", "new year's day"))


def _expected_daily(
    points_by_day: dict[date, best_day_since.Point],
    target: date,
    *,
    holiday: bool,
) -> tuple[float | None, dict[str, float]]:
    same_weekday: list[int] = []
    for weeks_back in range(1, 9):
        daily = _daily_on(points_by_day, target - timedelta(days=7 * weeks_back))
        if daily is not None:
            same_weekday.append(daily)

    recent = _window_dailies(
        points_by_day,
        start=target - timedelta(days=14),
        end=target - timedelta(days=1),
    )
    parts: dict[str, float] = {}
    weekday_avg = _avg(same_weekday)
    recent_avg = _avg(recent)
    if weekday_avg is not None:
        parts["same_weekday_avg"] = weekday_avg
    if recent_avg is not None:
        parts["recent_14d_avg"] = recent_avg

    expected_parts: list[tuple[float, float]] = []
    if weekday_avg is not None:
        expected_parts.append((weekday_avg, 0.55))
    if recent_avg is not None:
        expected_parts.append((recent_avg, 0.45))

    if holiday:
        last_year = _window_dailies(
            points_by_day,
            start=target - timedelta(days=371),
            end=target - timedelta(days=357),
        )
        last_year_avg = _avg(last_year)
        if last_year_avg is not None:
            parts["same_season_last_year_avg"] = last_year_avg
            expected_parts.append((last_year_avg, 0.65))

    if not expected_parts:
        return None, parts
    total_weight = sum(weight for _value, weight in expected_parts)
    expected = sum(value * weight for value, weight in expected_parts) / total_weight
    return expected, parts


def _rarity_value(points_by_day: dict[date, best_day_since.Point], target: date, today: int) -> float | None:
    trailing = _window_dailies(
        points_by_day,
        start=target - timedelta(days=90),
        end=target - timedelta(days=1),
    )
    if len(trailing) < 14:
        return None
    median = statistics.median(trailing)
    if median <= 0:
        return None
    deviations = [abs(value - median) for value in trailing]
    mad = statistics.median(deviations)
    if mad <= 0:
        return (today - median) / median
    return (today - median) / (1.4826 * mad)


def _grower_value(
    points_by_day: dict[date, best_day_since.Point],
    target: date,
    daily_pct_gain: float | None,
    weekly_pct_gain: float | None,
) -> float:
    recent_7 = _window_dailies(
        points_by_day,
        start=target - timedelta(days=6),
        end=target,
    )
    previous_7 = _window_dailies(
        points_by_day,
        start=target - timedelta(days=13),
        end=target - timedelta(days=7),
    )
    recent_avg = _avg(recent_7)
    previous_avg = _avg(previous_7)
    avg_pct = _pct_change(recent_avg, previous_avg) if recent_avg is not None and previous_avg else None
    streak = _streak_score(points_by_day, target)
    acceleration = _acceleration_score(points_by_day, target)

    pct_parts = [
        max(0.0, daily_pct_gain or 0.0),
        max(0.0, weekly_pct_gain or 0.0),
        max(0.0, avg_pct or 0.0),
    ]
    pct_signal = sum(min(value / 50.0, 1.0) for value in pct_parts) / len(pct_parts)
    return 0.40 * min(max(weekly_pct_gain or 0.0, 0.0) / 50.0, 1.0) + 0.25 * pct_signal + 0.20 * streak + 0.15 * acceleration


def _positive_move_count(points_by_day: dict[date, best_day_since.Point], target: date, days: int) -> int:
    count = 0
    for offset in range(days - 1, -1, -1):
        day = target - timedelta(days=offset)
        today = _daily_on(points_by_day, day)
        yesterday = _daily_on(points_by_day, day - timedelta(days=1))
        if today is not None and yesterday is not None and today > yesterday:
            count += 1
    return count


def _record_bonus(row: dict) -> float:
    bonus = 0.0
    if row.get("kind") == "best_ever":
        bonus += BEST_EVER_BONUS
    if row.get("is_biggest_day_of_year"):
        bonus += BIGGEST_DAY_OF_YEAR_BONUS
    if row.get("is_biggest_day_of_month"):
        bonus += BIGGEST_DAY_OF_MONTH_BONUS
    if row.get("combined"):
        bonus += COMBINED_TRACK_BONUS
    return bonus


def _volume_score(value: int | float | None, cap: int = 1_000_000) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(cap))


def _gain_score(value: int | float | None, cap: int) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(cap))


def _pct_score(value: float | None, cap: float = 50.0) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(1.0, value / cap)


def _rarity_score(value: float | None) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(1.0, value / 4.0)


def _surprise_impact_bonus(metrics: dict) -> float:
    """Extra lift for a day that is both big in raw streams and surprising.

    A rare spike on a tiny song and a high-volume routine day are both already
    rewarded elsewhere. This bonus is for the overlap: a large daily total that
    is also unusually high for the song's own history.
    """
    daily = int(metrics.get("daily_streams") or 0)
    rarity = float(metrics.get("rarity") or 0.0)
    daily_pct = float(metrics.get("daily_pct_gain") or 0.0)
    weekly_pct = float(metrics.get("weekly_pct_gain") or 0.0)
    if daily < 30_000 or rarity <= 0:
        return 0.0

    volume = _volume_score(daily)
    surprise = _rarity_score(rarity)
    momentum = max(_pct_score(daily_pct), _pct_score(weekly_pct))
    # Up to +18: big enough to materially reorder the top candidates, but
    # still bounded so one metric cannot overpower exact best-day filters.
    return 18.0 * volume * surprise * (0.70 + 0.30 * momentum)


def _comeback_bonus(row: dict, metrics: dict) -> float:
    days_since = int(row.get("days_since") or 0)
    expected_pct = float(metrics.get("expected_pct_gain") or 0.0)
    positive_days = int(metrics.get("positive_move_days_7") or 0)
    if days_since < 90 or expected_pct < 12.0 or positive_days < 4:
        return 0.0
    age = min(1.0, days_since / 365.0)
    trend = min(1.0, positive_days / 7.0)
    surprise = min(1.0, max(expected_pct, 0.0) / 50.0)
    return 10.0 * (0.35 * age + 0.35 * trend + 0.30 * surprise)


def _seasonality_penalty(row: dict, metrics: dict, target: date) -> float:
    if not _holiday_track(row):
        return 0.0
    if target.month not in {11, 12, 1}:
        return 0.0
    expected_pct = float(metrics.get("expected_pct_gain") or 0.0)
    rarity = float(metrics.get("rarity") or 0.0)
    if expected_pct >= 25.0:
        return 0.0
    # Holiday tracks naturally surge in season. Penalize routine seasonal lift,
    # especially when rarity is inflated by off-season lows.
    return min(8.0, 3.0 + max(rarity, 0.0))


def _posted_track_ids_for_date(target: date) -> set[str]:
    lock_dir = update_streams_dir(target.isoformat()) / "best_day_since_track_locks"
    if not lock_dir.exists():
        return set()
    return {path.stem for path in lock_dir.glob("*.lock")}


def _freshness_penalty(row: dict, target: date, tracks_by_id: dict[str, best_day_since.Track]) -> float:
    track_id = row["track_id"]
    era = _album_key(row.get("album"))
    penalty = 0.0
    for offset in range(1, RECENT_POST_WINDOW_DAYS + 1):
        day = target - timedelta(days=offset)
        posted_ids = _posted_track_ids_for_date(day)
        if track_id in posted_ids:
            penalty = max(penalty, 7.0)
        if era:
            for posted_id in posted_ids:
                posted_track = tracks_by_id.get(posted_id)
                if posted_track and _album_key(posted_track.album) == era:
                    penalty = max(penalty, 2.5)
    return penalty


def _explanations(row: dict, metrics: dict, adjustments: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    if row.get("is_biggest_day_of_year"):
        reasons.append("biggest day of the year")
    if row.get("days_since") and row["days_since"] >= 180:
        reasons.append(f"first best-day marker in {row['days_since']} days")
    if metrics.get("expected_pct_gain") is not None and metrics["expected_pct_gain"] >= 20:
        reasons.append(f"{metrics['expected_pct_gain']:.1f}% above expected baseline")
    if metrics.get("rarity") and metrics["rarity"] >= 3:
        reasons.append("strong surprise vs recent history")
    if metrics.get("positive_move_days_7", 0) >= 4:
        reasons.append(f"{metrics['positive_move_days_7']}/7 recent daily moves positive")
    if adjustments.get("comeback_bonus", 0) > 0:
        reasons.append("meaningful comeback pattern")
    if adjustments.get("seasonality_penalty", 0) < 0:
        reasons.append("seasonal holiday lift discounted")
    if adjustments.get("freshness_penalty", 0) < 0:
        reasons.append("recently represented in best-day posts")
    return reasons


def _apply_era_context(items: list[dict]) -> None:
    by_era: dict[str, list[dict]] = {}
    for item in items:
        era = item.get("era_key") or _album_key(item.get("album"))
        if not era:
            continue
        by_era.setdefault(era, []).append(item)

    for era, era_items in by_era.items():
        if len(era_items) < ERA_CLUSTER_MIN_ROWS:
            for item in era_items:
                item["era_candidate_count"] = len(era_items)
            continue
        era_items.sort(key=lambda item: (item["score"], item["daily_streams"]), reverse=True)
        for index, item in enumerate(era_items):
            item["era_candidate_count"] = len(era_items)
            if index == 0:
                adjustment = 4.0
                item["explanations"].append(f"best representative of {len(era_items)} same-era records")
            else:
                adjustment = -min(6.0, 2.0 * index)
                item["explanations"].append(f"same era already has a stronger candidate ({era})")
            item["score"] = round(float(item["score"]) + adjustment, 3)
            item["bonus"] = round(float(item["bonus"]) + adjustment, 3)
            adjustments = dict(item.get("score_adjustments") or {})
            adjustments["era_context_adjustment"] = round(adjustment, 3)
            item["score_adjustments"] = adjustments


def dynamic_early_min_score(candidate: dict, base_min_score: float = DEFAULT_EARLY_MIN_SCORE) -> tuple[float, dict[str, float]]:
    """Return the early-post threshold adjusted to the candidate's scale.

    Low-volume records need a stronger score to avoid noisy posts; very large
    days, highly surprising days, and strong yearly records get a lower bar.
    """
    threshold = float(base_min_score)
    adjustments: dict[str, float] = {}
    daily = int(candidate.get("daily_streams") or 0)
    rarity = float(candidate.get("rarity") or 0.0)
    daily_pct = float(candidate.get("daily_pct_gain") or 0.0)
    weekly_pct = float(candidate.get("weekly_pct_gain") or 0.0)

    if daily < 50_000:
        adjustments["low_volume_penalty"] = 5.0
    elif daily < 80_000:
        adjustments["medium_low_volume_penalty"] = 2.5
    elif daily >= 750_000:
        adjustments["huge_volume_discount"] = -6.0
    elif daily >= 300_000:
        adjustments["high_volume_discount"] = -4.0
    elif daily >= 150_000:
        adjustments["solid_volume_discount"] = -2.0

    max_momentum = max(daily_pct, weekly_pct)

    if rarity >= 5.0 and max_momentum >= 10.0:
        adjustments["extreme_surprise_discount"] = -5.0
    elif rarity >= 3.0 and max_momentum >= 15.0:
        adjustments["strong_surprise_discount"] = -3.0
    elif rarity >= 2.0:
        adjustments["surprise_discount"] = -1.5

    if max_momentum >= 30.0:
        adjustments["strong_momentum_discount"] = -2.0
    elif max_momentum >= 15.0:
        adjustments["momentum_discount"] = -1.0

    if candidate.get("is_biggest_day_of_year"):
        adjustments["year_record_discount"] = EARLY_YEAR_RECORD_THRESHOLD_DISCOUNT

    threshold += sum(adjustments.values())
    return round(max(48.0, min(68.0, threshold)), 3), adjustments


def _score_from_subscores(
    subscores: dict[str, float],
    weights: ScoreWeights,
    row: dict,
    *,
    extra_bonus: float = 0.0,
) -> tuple[float, float, float]:
    base_score = (
        weights.age * subscores["age"]
        + weights.daily_abs_gain * subscores["daily_abs_gain"]
        + weights.daily_pct_gain * subscores["daily_pct_gain"]
        + weights.weekly_pct_gain * subscores["weekly_pct_gain"]
        + weights.rarity * subscores["rarity"]
        + weights.grower * subscores["grower"]
    ) * 100.0
    bonus = _record_bonus(row) + extra_bonus
    return base_score + bonus, base_score, bonus


def _points_for_row(row: dict, history: dict[str, list[best_day_since.Point]]) -> list[best_day_since.Point]:
    track_ids = row.get("combined_track_ids") or [row["track_id"]]
    points_by_track = [history.get(track_id) or [] for track_id in track_ids]
    if len(points_by_track) <= 1:
        return points_by_track[0] if points_by_track else []
    return best_day_since.combine_points(points_by_track)


def _metrics_for_row(
    row: dict,
    history: dict[str, list[best_day_since.Point]],
    target: date,
    *,
    min_daily_streams: int,
) -> tuple[dict | None, list[str]]:
    points = _points_for_row(row, history)
    points_by_day = _point_by_day(points)
    today = _daily_on(points_by_day, target)
    yesterday = _daily_on(points_by_day, target - timedelta(days=1))
    last_week = _daily_on(points_by_day, target - timedelta(days=7))

    reasons: list[str] = []
    if today is None or today <= 0:
        reasons.append("missing_today_daily")
    if yesterday is None or yesterday <= 0:
        reasons.append("missing_yesterday_daily")
    if last_week is None or last_week <= 0:
        reasons.append("missing_last_week_daily")
    if today is not None and today < min_daily_streams:
        reasons.append("below_min_daily_streams")
    if reasons:
        return None, reasons

    assert today is not None
    daily_abs_gain = today - int(yesterday or 0)
    weekly_abs_gain = today - int(last_week or 0)
    daily_pct_gain = _pct_change(today, yesterday)
    weekly_pct_gain = _pct_change(today, last_week)
    rarity = _rarity_value(points_by_day, target, today)
    grower = _grower_value(points_by_day, target, daily_pct_gain, weekly_pct_gain)
    holiday = _holiday_track(row)
    expected_daily, expected_parts = _expected_daily(points_by_day, target, holiday=holiday)
    expected_abs_gain = today - expected_daily if expected_daily is not None else None
    expected_pct_gain = _pct_change(today, expected_daily) if expected_daily is not None else None

    return {
        "row": row,
        "points_by_day": points_by_day,
        "daily_streams": today,
        "daily_abs_gain": daily_abs_gain,
        "weekly_abs_gain": weekly_abs_gain,
        "daily_pct_gain": daily_pct_gain or 0.0,
        "weekly_pct_gain": weekly_pct_gain or 0.0,
        "rarity": rarity or 0.0,
        "grower": grower,
        "age": _log_age_score(row.get("days_since")),
        "expected_daily": expected_daily,
        "expected_parts": expected_parts,
        "expected_abs_gain": expected_abs_gain,
        "expected_pct_gain": expected_pct_gain or 0.0,
        "positive_move_days_7": _positive_move_count(points_by_day, target, 7),
        "holiday_track": holiday,
    }, []


def score_single_best_day_candidate(
    track_id: str,
    target: date,
    *,
    min_days: int = DEFAULT_MIN_DAYS,
    min_daily_streams: int = DEFAULT_MIN_DAILY_STREAMS,
    combined: bool = False,
) -> dict:
    """Score one best-day-since candidate without needing the full day's pool.

    This is the early-post scorer. It uses absolute, capped components instead
    of same-day percentiles, so it is stable while the rest of the collection is
    still running.
    """
    base_tracks = best_day_since.load_tracks(include_extras=False)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    track = base_tracks.get(track_id) or all_tracks.get(track_id)
    if not track:
        return {"track_id": track_id, "status": "skipped", "reasons": ["track_missing"]}

    history = best_day_since.load_history()
    if combined:
        row = best_day_since.compute_best_day_since_combined(
            track,
            best_day_since.combined_tracks_for(all_tracks.get(track_id, track), all_tracks),
            history,
            target,
        )
    else:
        row = best_day_since.compute_best_day_since(track, history.get(track_id) or [], target)

    if not row:
        return {"track_id": track_id, "status": "skipped", "reasons": ["no_best_day_since"]}
    if not best_day_since.passes_filters(row, min_days=min_days):
        return {
            "track_id": track_id,
            "title": row.get("title"),
            "status": "skipped",
            "reasons": ["below_best_day_filter"],
            "days_since": row.get("days_since"),
            "daily_streams": row.get("daily_streams"),
        }

    metrics, reasons = _metrics_for_row(row, history, target, min_daily_streams=min_daily_streams)
    if metrics is None:
        return {
            "track_id": track_id,
            "title": row.get("title"),
            "status": "blocked",
            "reasons": reasons,
            "daily_streams": row.get("daily_streams"),
        }

    subscores = {
        "age": metrics["age"],
        "daily_abs_gain": _gain_score(metrics["daily_abs_gain"], 500_000),
        "daily_pct_gain": _pct_score(metrics["daily_pct_gain"]),
        "weekly_pct_gain": _pct_score(metrics["weekly_pct_gain"]),
        "rarity": _rarity_score(metrics["rarity"]),
        "grower": max(0.0, min(1.0, metrics["grower"])),
    }
    surprise_impact_bonus = _surprise_impact_bonus(metrics)
    comeback_bonus = _comeback_bonus(row, metrics)
    seasonality_penalty = _seasonality_penalty(row, metrics, target)
    freshness_penalty = _freshness_penalty(row, target, all_tracks)
    score_adjustments = {
        "surprise_impact_bonus": surprise_impact_bonus,
        "comeback_bonus": comeback_bonus,
        "seasonality_penalty": -seasonality_penalty,
        "freshness_penalty": -freshness_penalty,
    }
    net_extra_bonus = sum(score_adjustments.values())
    score, base_score, bonus = _score_from_subscores(
        subscores,
        EARLY_WEIGHTS,
        row,
        extra_bonus=net_extra_bonus,
    )
    candidate = {
        "track_id": track_id,
        "title": row["title"],
        "album": row.get("album"),
        "date": target.isoformat(),
        "status": "scored",
        "score": round(score, 3),
        "base_score": round(base_score, 3),
        "bonus": round(bonus, 3),
        "surprise_impact_bonus": round(surprise_impact_bonus, 3),
        "comeback_bonus": round(comeback_bonus, 3),
        "seasonality_penalty": round(seasonality_penalty, 3),
        "freshness_penalty": round(freshness_penalty, 3),
        "score_adjustments": {key: round(value, 3) for key, value in score_adjustments.items()},
        "label": best_day_since.row_label(row),
        "kind": row.get("kind"),
        "best_day_since": row.get("best_day_since"),
        "days_since": row.get("days_since"),
        "daily_streams": metrics["daily_streams"],
        "daily_abs_gain": metrics["daily_abs_gain"],
        "weekly_abs_gain": metrics["weekly_abs_gain"],
        "daily_pct_gain": round(metrics["daily_pct_gain"], 3),
        "weekly_pct_gain": round(metrics["weekly_pct_gain"], 3),
        "rarity": round(metrics["rarity"], 3),
        "grower": round(metrics["grower"], 3),
        "expected_daily": round(metrics["expected_daily"], 3) if metrics.get("expected_daily") is not None else None,
        "expected_abs_gain": round(metrics["expected_abs_gain"], 3) if metrics.get("expected_abs_gain") is not None else None,
        "expected_pct_gain": round(metrics["expected_pct_gain"], 3),
        "positive_move_days_7": metrics["positive_move_days_7"],
        "holiday_track": bool(metrics["holiday_track"]),
        "subscores": {key: round(value, 3) for key, value in subscores.items()},
        "combined": bool(row.get("combined")),
        "combined_track_ids": row.get("combined_track_ids") or [track_id],
        "is_biggest_day_of_year": bool(row.get("is_biggest_day_of_year")),
        "is_biggest_day_of_month": bool(row.get("is_biggest_day_of_month")),
        "era_key": _album_key(row.get("album")),
        "era_candidate_count": 1,
    }
    dynamic_threshold, threshold_adjustments = dynamic_early_min_score(candidate)
    candidate["dynamic_early_min_score"] = dynamic_threshold
    candidate["threshold_adjustments"] = {key: round(value, 3) for key, value in threshold_adjustments.items()}
    candidate["explanations"] = _explanations(row, metrics, score_adjustments)
    return candidate


def _candidate_rows(target: date, *, include_extras: bool, min_days: int) -> tuple[list[dict], list[dict]]:
    base_tracks = best_day_since.load_tracks(include_extras=include_extras)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    history = best_day_since.load_history()
    rows: list[dict] = []
    skipped: list[dict] = []
    seen_track_ids: set[str] = set()

    for track_id, track in base_tracks.items():
        if track_id in seen_track_ids:
            continue
        related = best_day_since.combined_tracks_for(all_tracks.get(track_id, track), all_tracks)
        for related_track in related:
            seen_track_ids.add(related_track.track_id)
        row = best_day_since.compute_best_day_since_combined(track, related, history, target)
        if not row:
            continue
        if not best_day_since.passes_filters(row, min_days=min_days):
            skipped.append({
                "track_id": row["track_id"],
                "title": row["title"],
                "reason": "below_best_day_filter",
                "days_since": row.get("days_since"),
                "daily_streams": row.get("daily_streams"),
            })
            continue
        rows.append(row)

    return rows, skipped


def score_best_day_since(
    target: date,
    *,
    include_extras: bool = False,
    min_days: int = DEFAULT_MIN_DAYS,
    min_daily_streams: int = DEFAULT_MIN_DAILY_STREAMS,
) -> dict:
    history = best_day_since.load_history()
    if not history:
        raise SystemExit(f"No history found: {best_day_since.HISTORY_PATH}")

    raw_rows, skipped = _candidate_rows(target, include_extras=include_extras, min_days=min_days)
    tracks_by_id = best_day_since.load_tracks(include_extras=True)
    metrics_by_id: dict[str, dict] = {}
    blocked: list[dict] = []

    for row in raw_rows:
        metrics, reasons = _metrics_for_row(row, history, target, min_daily_streams=min_daily_streams)
        if reasons:
            blocked.append({
                "track_id": row["track_id"],
                "title": row["title"],
                "reasons": reasons,
                "daily_streams": row.get("daily_streams"),
            })
            continue
        metrics_by_id[row["track_id"]] = metrics

    daily_abs_scores = _percentile_scores({tid: max(0.0, m["daily_abs_gain"]) for tid, m in metrics_by_id.items()})
    daily_pct_scores = _percentile_scores({tid: max(0.0, m["daily_pct_gain"]) for tid, m in metrics_by_id.items()})
    weekly_pct_scores = _percentile_scores({tid: max(0.0, m["weekly_pct_gain"]) for tid, m in metrics_by_id.items()})
    rarity_scores = _percentile_scores({tid: max(0.0, m["rarity"]) for tid, m in metrics_by_id.items()})
    grower_scores = _percentile_scores({tid: max(0.0, m["grower"]) for tid, m in metrics_by_id.items()})

    items: list[dict] = []
    for track_id, metrics in metrics_by_id.items():
        row = metrics["row"]
        subscores = {
            "age": metrics["age"],
            "daily_abs_gain": daily_abs_scores.get(track_id, 0.0),
            "daily_pct_gain": daily_pct_scores.get(track_id, 0.0),
            "weekly_pct_gain": weekly_pct_scores.get(track_id, 0.0),
            "rarity": rarity_scores.get(track_id, 0.0),
            "grower": grower_scores.get(track_id, 0.0),
        }
        surprise_impact_bonus = _surprise_impact_bonus(metrics)
        comeback_bonus = _comeback_bonus(row, metrics)
        seasonality_penalty = _seasonality_penalty(row, metrics, target)
        freshness_penalty = _freshness_penalty(row, target, tracks_by_id)
        score_adjustments = {
            "surprise_impact_bonus": surprise_impact_bonus,
            "comeback_bonus": comeback_bonus,
            "seasonality_penalty": -seasonality_penalty,
            "freshness_penalty": -freshness_penalty,
        }
        net_extra_bonus = sum(score_adjustments.values())
        score, base_score, bonus = _score_from_subscores(
            subscores,
            WEIGHTS,
            row,
            extra_bonus=net_extra_bonus,
        )
        items.append({
            "track_id": track_id,
            "title": row["title"],
            "album": row.get("album"),
            "date": target.isoformat(),
            "score": round(score, 3),
            "base_score": round(base_score, 3),
            "bonus": round(bonus, 3),
            "surprise_impact_bonus": round(surprise_impact_bonus, 3),
            "comeback_bonus": round(comeback_bonus, 3),
            "seasonality_penalty": round(seasonality_penalty, 3),
            "freshness_penalty": round(freshness_penalty, 3),
            "score_adjustments": {key: round(value, 3) for key, value in score_adjustments.items()},
            "label": best_day_since.row_label(row),
            "kind": row.get("kind"),
            "best_day_since": row.get("best_day_since"),
            "days_since": row.get("days_since"),
            "daily_streams": metrics["daily_streams"],
            "daily_abs_gain": metrics["daily_abs_gain"],
            "weekly_abs_gain": metrics["weekly_abs_gain"],
            "daily_pct_gain": round(metrics["daily_pct_gain"], 3),
            "weekly_pct_gain": round(metrics["weekly_pct_gain"], 3),
            "rarity": round(metrics["rarity"], 3),
            "grower": round(metrics["grower"], 3),
            "expected_daily": round(metrics["expected_daily"], 3) if metrics.get("expected_daily") is not None else None,
            "expected_abs_gain": round(metrics["expected_abs_gain"], 3) if metrics.get("expected_abs_gain") is not None else None,
            "expected_pct_gain": round(metrics["expected_pct_gain"], 3),
            "positive_move_days_7": metrics["positive_move_days_7"],
            "holiday_track": bool(metrics["holiday_track"]),
            "era_key": _album_key(row.get("album")),
            "explanations": _explanations(row, metrics, score_adjustments),
            "subscores": {key: round(value, 3) for key, value in subscores.items()},
            "combined": bool(row.get("combined")),
            "combined_track_ids": row.get("combined_track_ids") or [track_id],
            "is_biggest_day_of_year": bool(row.get("is_biggest_day_of_year")),
            "is_biggest_day_of_month": bool(row.get("is_biggest_day_of_month")),
        })

    _apply_era_context(items)

    items.sort(
        key=lambda item: (
            item["score"],
            -float(item.get("freshness_penalty") or 0.0),
            item.get("era_candidate_count") == 1,
            item["days_since"] or 0,
            item["daily_streams"],
        ),
        reverse=True,
    )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": target.isoformat(),
        "include_extras": include_extras,
        "min_days": min_days,
        "min_daily_streams": min_daily_streams,
        "weights": WEIGHTS.__dict__,
        "count": len(items),
        "blocked_count": len(blocked),
        "skipped_count": len(skipped),
        "items": items,
        "blocked": blocked,
        "skipped": skipped,
    }


def _format_int(value: int | None) -> str:
    return "?" if value is None else f"{value:,}"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:+.1f}%"


def _print_table(items: list[dict], *, limit: int, explain: bool = False) -> None:
    for index, item in enumerate(items[: max(limit, 0)], 1):
        print(
            f"{index:>2}. {item['score']:>6.2f} | "
            f"{item['title']} | "
            f"{_format_int(item['daily_streams'])} | "
            f"{_format_pct(item['daily_pct_gain'])} d/d | "
            f"{_format_pct(item['weekly_pct_gain'])} w/w | "
            f"{item['label']}"
        )
        if explain and item.get("explanations"):
            print(f"    why: {'; '.join(item['explanations'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score best-day-since Spotify streams candidates.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD (default: latest date in history)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Rows to print (default: {DEFAULT_LIMIT})")
    parser.add_argument("--min-days", type=int, default=DEFAULT_MIN_DAYS, help=f"Minimum best-day gap (default: {DEFAULT_MIN_DAYS})")
    parser.add_argument("--min-daily-streams", type=int, default=DEFAULT_MIN_DAILY_STREAMS, help="Minimum current daily streams")
    parser.add_argument("--include-extras", action="store_true", help="Include chart_extra/songs.json extras")
    parser.add_argument("--explain", action="store_true", help="Print explanation lines under scored rows")
    parser.add_argument("--single-track", help="Score one track id with the early single-candidate scorer")
    parser.add_argument(
        "--early-min-score",
        type=float,
        default=DEFAULT_EARLY_MIN_SCORE,
        help=f"Minimum score used to label a single-track early candidate as qualified (default: {DEFAULT_EARLY_MIN_SCORE})",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON payload")
    parser.add_argument("--output", help="Write full JSON payload to this path")
    args = parser.parse_args()

    history = best_day_since.load_history()
    if args.date:
        target = date.fromisoformat(args.date)
    else:
        latest = best_day_since.latest_history_date(history)
        if latest is None:
            raise SystemExit("No dated history rows found.")
        target = latest

    if args.single_track:
        payload = score_single_best_day_candidate(
            args.single_track,
            target,
            min_days=args.min_days,
            min_daily_streams=args.min_daily_streams,
        )
        if payload.get("status") == "scored":
            dynamic_threshold, threshold_adjustments = dynamic_early_min_score(payload, args.early_min_score)
            payload["qualified"] = (payload.get("score") or 0) >= dynamic_threshold
            payload["early_min_score"] = args.early_min_score
            payload["dynamic_early_min_score"] = dynamic_threshold
            payload["threshold_adjustments"] = {
                key: round(value, 3)
                for key, value in threshold_adjustments.items()
            }
        else:
            payload["qualified"] = False
            payload["early_min_score"] = args.early_min_score
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Wrote {output_path}")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        if payload.get("status") != "scored":
            print(f"{args.single_track}: {payload.get('status')} ({', '.join(payload.get('reasons') or [])})")
            return
        verdict = "QUALIFIES" if payload["qualified"] else "below threshold"
        print(
            f"{payload['score']:.2f} / {payload['dynamic_early_min_score']:.2f} | {verdict} | {payload['title']} | "
            f"{_format_int(payload['daily_streams'])} | "
            f"{_format_pct(payload['daily_pct_gain'])} d/d | "
            f"{_format_pct(payload['weekly_pct_gain'])} w/w | "
            f"{payload['label']}"
        )
        return

    payload = score_best_day_since(
        target,
        include_extras=args.include_extras,
        min_days=args.min_days,
        min_daily_streams=args.min_daily_streams,
    )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {output_path}")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(
        f"Best-day-since scores for {payload['date']} "
        f"({payload['count']} scored, {payload['blocked_count']} blocked, {payload['skipped_count']} skipped)"
    )
    _print_table(payload["items"], limit=args.limit, explain=args.explain)


if __name__ == "__main__":
    main()
