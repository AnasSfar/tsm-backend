"""Day-of-week seasonality + damped trend projection helpers.

Used by swift_top_100_live.py to project the remaining days of an
in-progress Friday->Thursday tracking week from the days already actual.
Not used by the official swift_top_100.py weekly run (which only ever
scores fully-elapsed weeks).

Method (rewritten 2026-10-03 after comparing the 2026-10-01 live projection
with the official TayBoard of the same week — the old version anchored on the
MEAN of the week's actual days and multiplied by a level-ratio "momentum",
which badly over-projected release weeks (Patient Zero Spotify +38% on the
2 missing days) and every steady song early in the week (YouTube +20-30%)):

1. Weekday shape from the series' own trailing history, as the median ratio
   of each day to its centered 7-day mean — robust to level shifts (track id
   switches, release spikes) and to trends, unlike a ratio to the window's
   overall mean. Clamped, neutral when too thin.
2. Level = deseasonalized mean of the last LEVEL_DAYS actual days (the most
   recent level, not the week's mean).
3. Trend = log-linear daily growth fitted on the last TREND_POINTS
   deseasonalized actual days (>= 3 points), clamped, then DAMPED (each
   further day adds PHI^h of the growth) so a short spike never compounds.
   Fewer than 3 points -> flat.
4. Release mode: when the series was ~zero before this week (history level
   < RELEASE_RATIO x first actual day), the remaining days follow
   RELEASE_CURVE (median day-N/day-0 ratio of real releases) anchored on the
   last known days — the generic trend fit can't see a day-1 -> day-2 halving
   coming. Opt-in per platform via `release_curve=`: RELEASE_CURVE (Spotify
   streams + surplus), YOUTUBE_RELEASE_CURVE, ITUNES_RELEASE_CURVE. Apple
   Music points (rank-based, very noisy per day) keep the generic method.

Calibration: backtest on every Fri->Thu week since 2026-03 of real
db/streams_history.csv and db/youtube_title_history.csv series (script kept
in the session scratchpad, numbers in CONTEXTE.md "Methode de projection").
Stdlib only.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

# Trailing window used to learn the weekday shape.
TRAILING_WEEKS = 8

# Each weekday needs this many real (fully-surrounded) points in the window,
# otherwise the shape is neutral (1.0 for every day).
MIN_WEEKDAY_OCCURRENCES = 3

# Weekday factor bounds — a real TS catalog weekday swing stays well inside.
WEEKDAY_CLAMP = (0.75, 1.35)

# Level = deseasonalized mean of the last N actual days.
LEVEL_DAYS = 2

# Trend fit window (deseasonalized actual days of the current week).
TREND_POINTS = 4

# Per-day growth bounds before damping.
GROWTH_CLAMP = (0.85, 1.06)

# Damping: day h after the last actual day gets growth^(PHI + PHI^2 + ... + PHI^h).
PHI = 0.6

# Series level before this week below this fraction of the first actual day
# -> treated as a release (new song / new track id) week.
RELEASE_RATIO = 0.25

# Spotify daily streams of a Friday release, as a fraction of release day
# (index = days since release). Medians of the 12 TLOAS tracks (2025-10-03)
# and the 4 Encore tracks (2026-09-25), rounded; index 7-13 from TLOAS only.
RELEASE_CURVE = (1.0, 0.48, 0.35, 0.38, 0.34, 0.32, 0.29,
                 0.28, 0.24, 0.20, 0.21, 0.20, 0.19, 0.18)

# YouTube daily views of a new title (lyric videos) — Cleveland!/Babylon/Pink
# Clouding, Encore 2026-09-25 (the only release in youtube_title_history so
# far); index 6+ extrapolated at ~-9%/day. A music video dropped mid-week
# (Patient Zero, 2026-09-28/30) breaks any curve — the projection then
# re-anchors on the new last days, it can't foresee the drop itself.
YOUTUBE_RELEASE_CURVE = (1.0, 0.95, 0.52, 0.47, 0.35, 0.31, 0.28,
                         0.26, 0.24, 0.22, 0.21, 0.20, 0.19, 0.18)

# iTunes song-chart daily score of a new title (purchases fade, so ranks
# slide) — median of the 4 Encore titles, 2026-09-25; index 7+ extrapolated.
ITUNES_RELEASE_CURVE = (1.0, 0.97, 0.80, 0.67, 0.62, 0.49, 0.37,
                        0.33, 0.30, 0.27, 0.25, 0.23, 0.21, 0.20)


def _parse_series(daily_series: dict[str, float] | None) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    for date_str, value in (daily_series or {}).items():
        if value is None:
            continue
        try:
            out.append((date.fromisoformat(date_str), float(value)))
        except (TypeError, ValueError):
            continue
    out.sort()
    return out


def weekday_factors(history_daily: dict[str, float] | None) -> dict[int, float] | None:
    """Weekday -> multiplier from trailing history, or None if too thin.

    Each point is divided by its own centered 7-day mean (needs all 7
    neighbours), so a level shift or a steady trend inside the window doesn't
    leak into the weekday shape. Median per weekday, renormalized to mean 1,
    clamped to WEEKDAY_CLAMP.
    """
    points = [(d, v) for d, v in _parse_series(history_daily) if v > 0]
    if len(points) < 14:
        return None
    by_day = dict(points)
    window_start = points[-1][0] - timedelta(weeks=TRAILING_WEEKS)
    ratios: dict[int, list[float]] = {i: [] for i in range(7)}
    for d, v in points:
        if d < window_start:
            continue
        neighbours = [by_day.get(d + timedelta(days=j)) for j in range(-3, 4)]
        if any(n is None or n <= 0 for n in neighbours):
            continue
        ratios[d.weekday()].append(v / (sum(neighbours) / 7))
    if any(len(r) < MIN_WEEKDAY_OCCURRENCES for r in ratios.values()):
        return None
    raw = {i: statistics.median(r) for i, r in ratios.items()}
    norm = sum(raw.values()) / 7
    if norm <= 0:
        return None
    lo, hi = WEEKDAY_CLAMP
    return {i: max(lo, min(hi, x / norm)) for i, x in raw.items()}


def _fitted_growth(points: list[tuple[date, float]]) -> tuple[float, float] | None:
    """(daily growth, fitted value at the last point) of a log-linear fit."""
    pos = [(d, v) for d, v in points if v > 0]
    if len(pos) < 3:
        return None
    xs = [(d - pos[0][0]).days for d, _ in pos]
    ys = [math.log(v) for _, v in pos]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return math.exp(slope), math.exp(my + slope * (xs[-1] - mx))


def _release_projection(
    actual: list[tuple[date, float]],
    remaining_dates: list[date],
    release_curve: tuple[float, ...],
) -> dict[str, float]:
    release_day = actual[0][0]
    anchors = actual[-LEVEL_DAYS:]

    def _curve(idx: int) -> float:
        return release_curve[min(idx, len(release_curve) - 1)]

    projected: dict[str, float] = {}
    for d in remaining_dates:
        idx = (d - release_day).days
        estimates = [v * _curve(idx) / _curve((ad - release_day).days) for ad, v in anchors if _curve((ad - release_day).days) > 0]
        projected[d.isoformat()] = max(0.0, statistics.mean(estimates)) if estimates else 0.0
    return projected


def project_remaining_days(
    actual_daily: dict[str, float],
    remaining_dates: list[date],
    history_daily: dict[str, float],
    *,
    release_curve: tuple[float, ...] | None = None,
) -> dict[str, float]:
    """Project a value for each of `remaining_dates` from actuals-so-far.

    `actual_daily`: this week's real days. `history_daily`: the same series'
    trailing days before the week (for the weekday shape / release check).
    `release_curve`: opt-in release-week decay (see module docstring).
    Returns {date_iso: projected_value}; never negative.
    """
    if not remaining_dates:
        return {}

    factors = weekday_factors(history_daily) or {i: 1.0 for i in range(7)}
    actual = [(d, v) for d, v in _parse_series(actual_daily) if v >= 0]
    history = _parse_series(history_daily)
    history_level = statistics.mean(v for _, v in history[-7:]) if history else 0.0

    if actual:
        first_value = next((v for _, v in actual if v > 0), 0.0)
        is_release = first_value > 0 and history_level < RELEASE_RATIO * first_value
        if is_release and release_curve:
            actual_nonzero = [(d, v) for d, v in actual if v > 0]
            return _release_projection(actual_nonzero, remaining_dates, release_curve)

        deseasonalized = [(d, v / factors[d.weekday()]) for d, v in actual]
        last_date = deseasonalized[-1][0]
        level = statistics.mean(v for _, v in deseasonalized[-LEVEL_DAYS:])
        growth = 1.0
        fit = _fitted_growth(deseasonalized[-TREND_POINTS:])
        if fit is not None:
            growth, fitted_last = fit
            level = 0.5 * level + 0.5 * fitted_last
        lo, hi = GROWTH_CLAMP
        if is_release:
            hi = min(hi, 1.0)  # a just-released series doesn't keep growing
        growth = max(lo, min(hi, growth))
    else:
        # No actual data yet this week — anchor on the last history days.
        if not history:
            return {d.isoformat(): 0.0 for d in remaining_dates}
        last_date = history[-1][0]
        level = statistics.mean(v / factors[d.weekday()] for d, v in history[-LEVEL_DAYS:])
        growth = 1.0

    projected: dict[str, float] = {}
    for d in remaining_dates:
        horizon = max(0, (d - last_date).days)
        damped_steps = sum(PHI ** j for j in range(1, horizon + 1))
        projected[d.isoformat()] = max(0.0, level * (growth ** damped_steps) * factors[d.weekday()])
    return projected
