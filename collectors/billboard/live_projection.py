"""Day-of-week seasonality + trend/momentum projection helpers.

Used by swift_top_100_live.py to project the remaining days of an
in-progress Friday->Thursday tracking week from the days already actual.
Not used by the official swift_top_100.py weekly run (which only ever
scores fully-elapsed weeks).

Adapts patterns already used elsewhere in this repo rather than inventing a
new methodology:
- weekday factor from trailing history, deseasonalize/reseasonalize pattern:
  collectors/spotify/streams/tools/scripts/gap_estimate.py
  (_weekday_seasonal_factors, _growth_factor, estimate_gap)
- EWMA momentum: collectors/spotify/streams/tools/scripts/forecast_milestones.py
  (ewma, log_trend_factor)
- day-of-week extrapolation + momentum correction ratio clamped to
  [0.55, 1.75]: collectors/billboard/swift_top_100.py
  (_estimate_missing_stream_days)

Dependency-light: stdlib only (statistics, math, datetime), matching the
existing gap_estimate.py/forecast_milestones.py modules this adapts, which
are themselves pure stdlib.
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

# How far back to look for a track's own same-weekday history. ~8 weeks
# mirrors gap_estimate.py's trailing-history approach without going so far
# back that a song's older, no-longer-representative level pollutes today's
# weekday shape.
TRAILING_WEEKS = 8

# Below this many real same-weekday points, a track's own history is too
# thin to trust (e.g. a song that only charted 1-2 weeks ago) -- fall back
# to the caller-supplied catalog-wide/aggregate series instead.
MIN_WEEKDAY_OCCURRENCES = 3

# Recency weight for the EWMA momentum factor. Same order of magnitude as
# gap_estimate.py's GROWTH_HALF_LIFE_DAYS-driven weighting, tuned here as a
# plain smoothing constant since EWMA (not a half-life weighted log-fit) is
# the requested method for this module.
EWMA_ALPHA = 0.35

# Same clamp range as the momentum correction ratio already used in
# swift_top_100.py::_estimate_missing_stream_days (~line 819) -- a single
# day/track should never be projected at less than 55% or more than 175%
# of its deseasonalized baseline, no matter how sharp the recent trend.
MOMENTUM_CLAMP = (0.55, 1.75)


def _parse_series(daily_series: dict[str, float]) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    for date_str, value in (daily_series or {}).items():
        if value is None:
            continue
        try:
            d = date.fromisoformat(date_str)
        except (TypeError, ValueError):
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        out.append((d, v))
    out.sort()
    return out


def _weekday_factor_from_series(daily_series: dict[str, float], target_weekday: int) -> float | None:
    """Return a weekday factor from this series alone, or None if too thin."""
    points = _parse_series(daily_series)
    if not points:
        return None
    latest = points[-1][0]
    window_start = latest - timedelta(weeks=TRAILING_WEEKS)
    window = [(d, v) for d, v in points if d >= window_start and v > 0]
    if len(window) < 3:
        return None
    target_values = [v for d, v in window if d.weekday() == target_weekday]
    if len(target_values) < MIN_WEEKDAY_OCCURRENCES:
        return None
    overall_mean = statistics.mean(v for _, v in window)
    if overall_mean <= 0:
        return None
    return statistics.mean(target_values) / overall_mean


def weekday_seasonal_factor(
    daily_series: dict[str, float],
    target_weekday: int,
    *,
    fallback_series: dict[str, float] | None = None,
) -> float:
    """Per-track weekday multiplier from trailing history (own series first).

    Falls back to a catalog-wide/aggregate series when the track's own
    history has fewer than MIN_WEEKDAY_OCCURRENCES real points on that
    weekday (e.g. a brand-new single). Falls back to a neutral 1.0 when
    neither series has enough signal, matching gap_estimate.py's
    "not enough history -> neutral factor" behavior.
    """
    factor = _weekday_factor_from_series(daily_series, target_weekday)
    if factor is not None:
        return factor
    if fallback_series:
        factor = _weekday_factor_from_series(fallback_series, target_weekday)
        if factor is not None:
            return factor
    return 1.0


def ewma(values: list[float], alpha: float) -> float:
    """Same recurrence as forecast_milestones.py::ewma."""
    if not values:
        return 0.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1.0 - alpha) * result
    return result


def trend_momentum_factor(daily_series: dict[str, float]) -> float:
    """EWMA-based recent growth/decay factor, clamped to [0.55, 1.75].

    Compares an EWMA of the most recent ~14 real values against an EWMA of
    the values from a week (or more) earlier -- a ratio > 1 means the track
    is trending up right now relative to ~1-2 weeks ago, < 1 means it's
    decaying. Too little history (fewer than 4 real points) returns a
    neutral 1.0 rather than reacting to noise.
    """
    points = _parse_series(daily_series)
    values = [v for _, v in points if v > 0]
    if len(values) < 4:
        return 1.0

    recent = values[-14:]
    baseline_window = values[:-7] if len(values) > 7 else values
    if not baseline_window:
        return 1.0

    recent_level = ewma(recent, EWMA_ALPHA)
    baseline_level = ewma(baseline_window, EWMA_ALPHA)
    if baseline_level <= 0:
        return 1.0

    ratio = recent_level / baseline_level
    return max(MOMENTUM_CLAMP[0], min(MOMENTUM_CLAMP[1], ratio))


def project_remaining_days(
    actual_daily: dict[str, float],
    remaining_dates: list[date],
    history_daily: dict[str, float],
) -> dict[str, float]:
    """Project a value for each of `remaining_dates` from actuals-so-far.

    Combines a deseasonalized baseline level (from the days already actual
    this week, or the most recent history day if none yet), a momentum
    factor (from history + actual combined), and each remaining date's own
    weekday factor (reseasonalized) -- same deseasonalize/apply-growth/
    reseasonalize shape as gap_estimate.py's estimate_gap(), generalized
    from "fill up to 2 missing days in the past" to "project N days forward".
    Returns {date_iso: projected_value}; never negative.
    """
    if not remaining_dates:
        return {}

    combined = dict(history_daily or {})
    combined.update(actual_daily or {})
    momentum = trend_momentum_factor(combined)

    baseline_values: list[float] = []
    for date_str, value in (actual_daily or {}).items():
        if value is None:
            continue
        try:
            d = date.fromisoformat(date_str)
            v = float(value)
        except (TypeError, ValueError):
            continue
        wd_factor = weekday_seasonal_factor(history_daily, d.weekday(), fallback_series=combined)
        if wd_factor > 0:
            baseline_values.append(v / wd_factor)

    if baseline_values:
        deseasonalized_baseline = statistics.mean(baseline_values)
    else:
        # No actual data yet this week (as-of == the week's Friday, before
        # any collection has landed) -- anchor on the most recent real
        # history day instead, same bookend logic as gap_estimate.py.
        history_points = _parse_series(history_daily)
        if not history_points:
            return {d.isoformat(): 0.0 for d in remaining_dates}
        last_date, last_value = history_points[-1]
        wd_factor = weekday_seasonal_factor(history_daily, last_date.weekday()) or 1.0
        deseasonalized_baseline = last_value / wd_factor if wd_factor else last_value

    projected: dict[str, float] = {}
    for d in remaining_dates:
        wd_factor = weekday_seasonal_factor(history_daily, d.weekday(), fallback_series=combined)
        value = deseasonalized_baseline * momentum * wd_factor
        projected[d.isoformat()] = max(0.0, value)
    return projected
