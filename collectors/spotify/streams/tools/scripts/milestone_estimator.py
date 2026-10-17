"""Forecast trajectories, never measured totals or a replacement for missing data.

Recent full-week levels separate weekday noise from trend. A multiplicative
damped trend lets a release drop or resurgence taper rather than compounding
forever (https://otexts.com/fpp3/holt.html). Model selection evaluates cumulative
streams at rolling origins, not just next-day error. No future observations are
used to fit the model at an origin.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
import milestone_seasonality as annual

MODEL_VERSION = "weekly_damped_trend_seasonal_v4"
MIN_EXACT_DAYS = 14
HORIZONS = (7, 28, 90, 180)
HALF_LIVES = (7, 14, 28, 56)
DEFAULT_DAMPING = 0.5 ** (1 / 14)


def exact_recent_series(series: list[dict], as_of: str) -> tuple[list[dict], str | None]:
    """Use a contiguous exact suffix; a defect in its last 14 days blocks it.

    Older discontinuities end the training window, rather than joining unrelated
    dates into a fake daily sequence. One preceding exact total verifies each
    daily in the suffix. A zero daily is kept, never inferred from equal totals.
    """
    rows = sorted((r for r in series if r["date"] <= as_of), key=lambda r: r["date"])
    if not rows or rows[-1]["date"] != as_of:
        return [], f"missing exact history on {as_of}"
    seen = set()
    for row in rows:
        if row["date"] in seen:
            return [], f"duplicate history on {row['date']}"
        seen.add(row["date"])
    valid = []
    reason = "insufficient consecutive exact history"
    for row in reversed(rows):
        day = row["date"]
        if row.get("estimated") or row.get("merged"):
            reason = f"estimated or merged history on {day}"
            break
        if type(row.get("streams")) is not int or row["streams"] < 0:
            reason = f"non-exact total on {day}"
            break
        if valid:
            newer = valid[-1]
            if date.fromisoformat(newer["date"]) - date.fromisoformat(day) != timedelta(days=1):
                reason = f"missing history before {newer['date']} (previous row {day})"
                break
            daily = newer.get("daily_streams")
            if type(daily) is not int or daily < 0 or newer["streams"] - row["streams"] != daily:
                reason = f"missing, negative or inconsistent daily on {newer['date']}"
                break
        valid.append(row)
    # The earliest row is a baseline total, not a verified daily observation.
    daily_rows = list(reversed(valid))[1:]
    if len(daily_rows) < MIN_EXACT_DAYS:
        return [], f"{reason}; {len(daily_rows)}/{MIN_EXACT_DAYS} verified consecutive dailies"
    return daily_rows, None


def weekday_factors(series: list[dict]) -> dict[int, float]:
    """Estimate weekdays within full weeks so a release decline isn't seasonality."""
    ratios = {i: [] for i in range(7)}
    recent = series[-91:]
    for end in range(len(recent), 6, -7):
        week = recent[end - 7:end]
        level = sum(r["daily_streams"] for r in week) / 7
        if level <= 0:
            continue
        for r in week:
            ratios[r["date_obj"].weekday()].append(r["daily_streams"] / level)
    factors = {i: statistics.median(v) if v else 1.0 for i, v in ratios.items()}
    average = sum(factors.values()) / 7
    return {i: value / average for i, value in factors.items()} if average > 0 else {i: 1.0 for i in range(7)}


def _candidate_names() -> list[str]:
    return ["level_7", "level_28"] + [f"damped_{window}_{half}" for window in (28, 56) for half in HALF_LIVES]


def fit_candidate(series: list[dict], name: str) -> dict:
    values = [r["daily_streams"] for r in series]
    level = sum(values[-7:]) / 7
    if name.startswith("level_"):
        window = min(int(name.split("_")[1]), len(values))
        level = sum(values[-window:]) / window
        factor, damping = 1.0, 0.0
    else:
        _, window, half = name.split("_")
        weeks = []
        for end in range(len(values), max(6, len(values) - int(window)), -7):
            weeks.append(sum(values[end - 7:end]) / 7)
        weeks.reverse()
        # Pairwise log slopes are robust to one unusual week. Zero weeks stay
        # in the observed level; no logarithm or positive floor is invented.
        slopes = [math.log(b / a) / (7 * (j - i))
                  for i, a in enumerate(weeks) if a > 0
                  for j, b in enumerate(weeks) if j > i and b > 0]
        factor = math.exp(statistics.median(slopes)) if slopes else 1.0
        damping = 0.5 ** (1 / int(half))
    return {"name": name, "base_daily": level, "start_daily": level * factor,
            "decay_factor": factor, "trend_damping": damping}


def projected_daily(day: int, start: float, factor: float, damping: float) -> float:
    """The *trend* decays toward zero; the daily level doesn't decay forever.

    Day 1 = start. Later log changes are log(factor)*phi, *phi^2, ...,
    with a finite sum for both growing and declining trends.
    """
    if not 0 <= damping < 1 or factor <= 0:
        raise ValueError("forecast requires a positive trend factor and 0 <= damping < 1")
    exponent = damping * (-math.expm1((day - 1) * math.log(damping))) / (1 - damping) if damping else 0.0
    return start * math.exp(math.log(factor) * exponent)


def _path(model: dict, series: list[dict], length: int, seasonality: dict | None = None) -> list[float]:
    factors = weekday_factors(series)
    end = series[-1]["date_obj"]
    return [projected_daily(h, model["start_daily"], model["decay_factor"], model["trend_damping"])
            * factors[(end + timedelta(days=h)).weekday()]
            * annual.multiplier(end + timedelta(days=h), seasonality) for h in range(1, length + 1)]


def estimate(series: list[dict], *, track_id: str | None = None, seasonal_history: list[dict] | None = None,
             release_date: str | None = None) -> dict:
    if len(series) < MIN_EXACT_DAYS:
        return {"pending_reason": f"need {MIN_EXACT_DAYS} exact consecutive daily observations"}
    # Most recent year; no gaps have been compressed into observation indices.
    rule = annual.TRACK_RULES.get(track_id)
    evidence = annual.verified_seasons(seasonal_history or series, rule, series[-1]["date"]) if rule else None
    series = series[-365:]
    names = _candidate_names()
    errors = {name: {h: [] for h in HORIZONS} for name in names}
    # Up to 16 distinct rolling origins; each sees only its own past. Including
    # a long horizon must not drown out the shorter-horizon cumulative checks.
    origins = list(range(MIN_EXACT_DAYS, len(series) - min(HORIZONS) + 1, 14))[-16:]
    for origin in origins:
        train = series[:origin]
        # Every origin learns its calendar effect only from seasons completed
        # by that origin. Latest-year evidence must not leak into backtests.
        seasonality = annual.profile_at(evidence, train[-1]["date"])
        adjusted_train = annual.adjusted(train, seasonality)
        horizons = [h for h in HORIZONS if origin + h <= len(series)]
        if not horizons:
            continue
        actual = {h: sum(r["daily_streams"] for r in series[origin:origin + h]) for h in horizons}
        for name in names:
            model = fit_candidate(adjusted_train, name)
            path = _path(model, adjusted_train, max(horizons), seasonality)
            for h in horizons:
                predicted = sum(path[:h])
                denom = actual[h] + predicted
                errors[name][h].append(2 * abs(predicted - actual[h]) / denom if denom else 0.0)
    scores = {name: statistics.mean(statistics.mean(v) for v in by_h.values() if v)
              for name, by_h in errors.items() if any(by_h.values())}
    # A new release with two exact weeks has an observed recent pace, but no
    # evidence yet for choosing a lifecycle trend. Use that explicit level
    # model, report zero held-out cases, and never manufacture a decay rate.
    best = min(scores, key=scores.get) if scores else "level_7"
    seasonality = annual.profile_at(evidence, series[-1]["date"])
    adjusted_series = annual.adjusted(series, seasonality)
    model = fit_candidate(adjusted_series, best)
    if model["start_daily"] <= 0:
        return {"pending_reason": "recent verified streaming level is zero"}
    counts = {str(h): len(errors[best][h]) for h in HORIZONS if errors[best][h]}
    backtest = {"points": sum(counts.values()), "mean_smape": round(scores[best], 4) if scores else None,
                "best_model": best, "horizon_points": counts,
                "horizon_smape": {str(h): round(statistics.mean(v), 4) for h, v in errors[best].items() if v},
                "model_errors": {name: round(score, 4) for name, score in scores.items()}}
    # This is a diagnostic band, not a probability that an exact date is right.
    band = "low" if not counts or max(map(int, counts)) < 90 else "medium"
    note = observed_note(series, model, bool(scores), release_date)
    return {"base_daily": round(model["base_daily"]), "decay_factor": model["decay_factor"],
            "trend_damping": model["trend_damping"], "projected_next_daily": round(model["start_daily"]),
            "annual_seasonality": seasonality, "forecast_note": note,
            "forecast_model": {"name": MODEL_VERSION, "selected_model": best,
                               "trend_damping": model["trend_damping"],
                               "weekday_factors": {str(k): v for k, v in weekday_factors(adjusted_series).items()},
                               "annual_seasonality": seasonality,
                               "training_start": series[0]["date"], "training_end": series[-1]["date"],
                               "selection_basis": "rolling_origin_cumulative_streams" if scores else "observed_7_day_pace_no_holdout_yet",
                               "validation_note": None if scores else "new release: insufficient history for held-out validation; no trend extrapolated",
                               "backtest_horizons": list(HORIZONS)},
            "confidence": {"score": None, "band": band, "kind": "diagnostic_not_probability",
                           "history_points": len(series)}, "backtest": backtest}


def observed_note(series: list[dict], model: dict, validated: bool, release_date: str | None) -> dict | None:
    """Short image copy backed by actual inputs; no invented song narratives."""
    if not validated:
        return {"kind": "limited_history", "text": "Limited history · recent pace used", "evidence": {"holdout_points": 0}}
    weeks = [sum(r["daily_streams"] for r in series[-offset-7:len(series)-offset if offset else None])
             for offset in (0, 7, 14) if len(series) >= offset + 7]
    if len(weeks) < 3 or min(weeks) <= 0:
        return None
    recent, prior, older = weeks
    evidence = {"recent_week_streams": recent, "prior_week_streams": prior, "earlier_week_streams": older}
    if release_date:
        try:
            release = date.fromisoformat(release_date[:10])
        except ValueError:
            print(f"[forecast_note] release note pending: invalid release_date {release_date!r}")
        else:
            age = (series[-1]["date_obj"] - release).days
            debut = [r for r in series if release <= r["date_obj"] < release + timedelta(days=7)]
            if 0 <= age <= 90 and len(debut) == 7 and recent < prior * .95 and prior < older:
                debut_streams = sum(r["daily_streams"] for r in debut)
                if debut_streams > prior:
                    return {"kind": "release_easing", "text": "Release-week surge easing",
                            "evidence": {**evidence, "release_age_days": age, "release_week_streams": debut_streams}}
    if model["decay_factor"] > 1 and recent > prior * 1.05 and prior > older:
        return {"kind": "recent_growth", "text": "Recent streaming growth", "evidence": evidence}
    if model["decay_factor"] < 1 and recent < prior * .95 and prior < older:
        return {"kind": "recent_decline", "text": "Recent weekly decline", "evidence": evidence}
    if model["name"].startswith("level_") and max(weeks) <= min(weeks) * 1.05:
        return {"kind": "steady_pace", "text": "Recent pace holding steady", "evidence": evidence}
    if len(series) >= 28 and model["name"] == "level_28" and abs(model["base_daily"] - series[-1]["daily_streams"]) >= model["base_daily"] * .10:
        return {"kind": "four_week_pace", "text": "Based on recent four-week pace",
                "evidence": {"observed_28_day_streams": sum(r["daily_streams"] for r in series[-28:]),
                             "latest_daily_streams": series[-1]["daily_streams"]}}
    return None


def projection_note(estimate: dict, as_of: str, expected_date: str) -> dict | None:
    return annual.projection_note(estimate.get("annual_seasonality"), as_of, expected_date) or estimate.get("forecast_note")
