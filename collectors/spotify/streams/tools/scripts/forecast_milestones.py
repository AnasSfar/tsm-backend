from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[4]
sys.path.insert(0, str(_REPO_ROOT / "collectors" / "spotify"))
from core.data_paths import (  # noqa: E402
    LEGACY_WEBSITE_DATA_DIR,
    LEGACY_WEBSITE_HISTORY_DIR,
    WEB_EXPORT_DATA_DIR,
    WEB_EXPORT_HISTORY_DIR,
    first_existing,
)

SITE_DATA_DIR = WEB_EXPORT_DATA_DIR
HISTORY_DIR = WEB_EXPORT_HISTORY_DIR if WEB_EXPORT_HISTORY_DIR.exists() else LEGACY_WEBSITE_HISTORY_DIR
HISTORY_INDEX_PATH = HISTORY_DIR / "index.json"

SONGS_PATH = first_existing(SITE_DATA_DIR / "songs.json", LEGACY_WEBSITE_DATA_DIR / "songs.json")
OUTPUT_PATH = SITE_DATA_DIR / "expected_milestones.json"

MILESTONE_STEP = 100_000_000
MAX_STATIC_MILESTONE = 5_000_000_000
DEFAULT_MILESTONES = list(range(MILESTONE_STEP, MAX_STATIC_MILESTONE + MILESTONE_STEP, MILESTONE_STEP))

MAX_FORECAST_DAYS = 5 * 365
RECENT_WINDOW = 120
LONG_WINDOW = 365
MIN_REQUIRED_HISTORY_POINTS = 7
BACKTEST_POINTS = 75
SPIKE_IQR_MULTIPLIER = 2.25
EWMA_SHORT_ALPHA = 0.28
EWMA_MEDIUM_ALPHA = 0.12
EWMA_LONG_ALPHA = 0.045
MIN_DECAY_FACTOR = 0.965
MAX_GROWTH_FACTOR = 1.025
SEASONALITY_STRENGTH = 0.35


def format_milestone_label(value: int) -> str:
    if value >= 1_000_000_000:
        billions = value / 1_000_000_000
        if abs(billions - round(billions)) < 1e-9:
            return f"{int(round(billions))}B"
        return f"{billions:.1f}B"
    return f"{int(value / 1_000_000)}M"


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_history_bundle() -> dict:
    if not HISTORY_INDEX_PATH.exists():
        return {"dates": [], "by_date": {}}

    index_data = load_json(HISTORY_INDEX_PATH)
    dates = sorted(index_data.get("dates", []))
    by_date = {}

    for d in dates:
        day_path = HISTORY_DIR / f"{d}.json"
        if not day_path.exists():
            continue
        by_date[d] = load_json(day_path) or {}

    return {"dates": dates, "by_date": by_date}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * clamp(pct, 0.0, 1.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def remove_spikes(values: list[float]) -> list[float]:
    if len(values) < 8:
        return values
    q1 = percentile(values, 0.25)
    q3 = percentile(values, 0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return values
    lower = max(1.0, q1 - SPIKE_IQR_MULTIPLIER * iqr)
    upper = q3 + SPIKE_IQR_MULTIPLIER * iqr
    cleaned = [v for v in values if lower <= v <= upper]
    return cleaned if len(cleaned) >= max(4, len(values) // 3) else values


def winsorize(values: list[float]) -> list[float]:
    if len(values) < 8:
        return values[:]
    q1 = percentile(values, 0.25)
    q3 = percentile(values, 0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return values[:]
    lower = max(1.0, q1 - SPIKE_IQR_MULTIPLIER * iqr)
    upper = q3 + SPIKE_IQR_MULTIPLIER * iqr
    return [clamp(v, lower, upper) for v in values]


def ewma(values: list[float], alpha: float) -> float:
    if not values:
        return 0.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1.0 - alpha) * result
    return result


def smape(actual: float, predicted: float) -> float:
    actual = max(0.0, actual)
    predicted = max(0.0, predicted)
    denom = actual + predicted
    if denom <= 0:
        return 0.0
    return abs(predicted - actual) * 2.0 / denom


def linear_regression_slope(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denom = sum((x - x_mean) ** 2 for x in xs)
    if denom <= 0:
        return 0.0
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denom


def log_trend_factor(values: list[float], max_abs_daily_change: float = 0.018) -> float:
    cleaned = [v for v in winsorize(values) if v > 0]
    if len(cleaned) < 5:
        return 1.0
    xs = list(range(len(cleaned)))
    ys = [math.log(v) for v in cleaned]
    slope = linear_regression_slope([float(x) for x in xs], ys)
    slope = clamp(slope, -max_abs_daily_change, max_abs_daily_change)
    return clamp(math.exp(slope), MIN_DECAY_FACTOR, MAX_GROWTH_FACTOR)


def weighted_average(items: list[tuple[float, float]]) -> float:
    total_weight = sum(max(0.0, weight) for _, weight in items)
    if total_weight <= 0:
        return 0.0
    return sum(value * max(0.0, weight) for value, weight in items) / total_weight


def get_track_history_series(track_id: str, history_by_date: dict, dates: list[str]) -> list[dict]:
    series = []

    for d in dates:
        row = history_by_date.get(d, {}).get(track_id)
        if not row:
            continue

        streams = safe_int(row.get("s") if row.get("s") is not None else row.get("streams"))
        daily_streams = row.get("d") if row.get("d") is not None else row.get("daily_streams")

        if daily_streams in (None, ""):
            continue

        daily_streams = safe_int(daily_streams)
        if streams <= 0 or daily_streams <= 0:
            continue

        series.append(
            {
                "date": d,
                "date_obj": parse_iso_date(d),
                "streams": streams,
                "daily_streams": daily_streams,
            }
        )

    series.sort(key=lambda row: row["date"])
    return series


def daily_values(series: list[dict]) -> list[float]:
    return [safe_float(row["daily_streams"]) for row in series if safe_float(row["daily_streams"]) > 0]


def recent_values(series: list[dict], window: int) -> list[float]:
    return daily_values(series[-window:])


def estimate_weekday_factors(series: list[dict]) -> dict[int, float]:
    recent = series[-min(len(series), LONG_WINDOW):]
    values = [safe_float(row["daily_streams"]) for row in recent if safe_float(row["daily_streams"]) > 0]
    baseline = median(remove_spikes(values)) or median(values) or 1.0
    raw: dict[int, list[float]] = {i: [] for i in range(7)}
    for row in recent:
        daily = safe_float(row["daily_streams"])
        if daily > 0:
            raw[row["date_obj"].weekday()].append(daily / baseline)

    factors = {}
    for weekday, weekday_values in raw.items():
        if len(weekday_values) < 3:
            factors[weekday] = 1.0
        else:
            observed = clamp(median(remove_spikes(weekday_values)), 0.78, 1.22)
            factors[weekday] = 1.0 + (observed - 1.0) * SEASONALITY_STRENGTH

    avg = sum(factors.values()) / 7.0
    if avg > 0:
        factors = {weekday: factor / avg for weekday, factor in factors.items()}
    return factors


def candidate_next_daily_estimates(series: list[dict]) -> dict[str, float]:
    values = daily_values(series)
    if not values:
        return {}

    last = values[-1]
    short = winsorize(values[-14:])
    medium = winsorize(values[-45:])
    recent = winsorize(values[-RECENT_WINDOW:])
    long = winsorize(values[-LONG_WINDOW:])

    estimates = {
        "last_observed": last,
        "median_14": median(short),
        "median_45": median(medium),
        "ewma_short": ewma(short, EWMA_SHORT_ALPHA),
        "ewma_medium": ewma(medium, EWMA_MEDIUM_ALPHA),
        "ewma_long": ewma(long, EWMA_LONG_ALPHA),
    }

    for name, window in (("trend_30", 30), ("trend_90", 90), ("trend_365", 365), ("trend_full", len(values))):
        source = values[-window:] if window < len(values) else values
        factor = log_trend_factor(source)
        estimates[name] = median(winsorize(source[-14:])) * factor

    cleaned_recent = remove_spikes(recent)
    estimates["recent_blend"] = weighted_average(
        [
            (ewma(short, EWMA_SHORT_ALPHA), 0.34),
            (ewma(medium, EWMA_MEDIUM_ALPHA), 0.30),
            (median(cleaned_recent), 0.22),
            (last, 0.14),
        ]
    )

    floor = max(1.0, percentile(winsorize(values[-LONG_WINDOW:]), 0.05) * 0.45)
    cap = max(last, percentile(winsorize(values[-LONG_WINDOW:]), 0.95) * 1.75)
    return {name: clamp(value, floor, cap) for name, value in estimates.items() if value > 0}


def backtest_candidate_weights(series: list[dict]) -> tuple[dict[str, float], dict]:
    if len(series) < 21:
        return {}, {"points": 0, "mean_smape": None, "best_model": None}

    errors: dict[str, list[float]] = {}
    start = max(MIN_REQUIRED_HISTORY_POINTS, len(series) - BACKTEST_POINTS)
    for idx in range(start, len(series)):
        train = series[:idx]
        actual = safe_float(series[idx]["daily_streams"])
        if actual <= 0:
            continue
        for name, predicted in candidate_next_daily_estimates(train).items():
            errors.setdefault(name, []).append(smape(actual, predicted))

    if not errors:
        return {}, {"points": 0, "mean_smape": None, "best_model": None}

    model_errors = {
        name: sum(vals) / len(vals)
        for name, vals in errors.items()
        if vals
    }
    best_model = min(model_errors, key=model_errors.get)
    weights = {
        name: 1.0 / ((err + 0.025) ** 2)
        for name, err in model_errors.items()
    }
    total = sum(weights.values())
    if total > 0:
        weights = {name: weight / total for name, weight in weights.items()}

    weighted_error = sum(model_errors[name] * weights.get(name, 0.0) for name in model_errors)
    return weights, {
        "points": max(len(vals) for vals in errors.values()),
        "mean_smape": round(weighted_error, 4),
        "best_model": best_model,
        "model_errors": {name: round(err, 4) for name, err in sorted(model_errors.items())},
        "model_weights": {name: round(weight, 4) for name, weight in sorted(weights.items())},
    }


def estimate_decay_factor(series: list[dict]) -> float:
    values = daily_values(series)
    if len(values) < 7:
        return 1.0

    windows: list[tuple[float, float]] = []
    for window, weight in ((21, 0.28), (45, 0.27), (90, 0.23), (180, 0.14), (365, 0.08)):
        source = values[-window:] if len(values) > window else values
        if len(source) >= 7:
            windows.append((log_trend_factor(source), weight))

    if not windows:
        return 1.0

    decay = weighted_average(windows)
    long_term = log_trend_factor(values[-LONG_WINDOW:] if len(values) > LONG_WINDOW else values)
    # Dampen extreme short-term moves toward the long-term trend.
    return clamp(decay * 0.72 + long_term * 0.28, MIN_DECAY_FACTOR, MAX_GROWTH_FACTOR)


def estimate_confidence(series: list[dict], backtest: dict, decay_factor: float) -> dict:
    values = daily_values(series)
    history_days = 0
    if series:
        history_days = max(1, (series[-1]["date_obj"] - series[0]["date_obj"]).days + 1)

    smape_value = backtest.get("mean_smape")
    error_score = 0.45 if smape_value is None else clamp(1.0 - float(smape_value) / 0.55, 0.0, 1.0)
    history_score = clamp(math.log1p(len(values)) / math.log1p(365), 0.0, 1.0)
    span_score = clamp(math.log1p(history_days) / math.log1p(730), 0.0, 1.0)

    recent = values[-min(len(values), 60):]
    if len(recent) >= 7:
        avg = sum(recent) / len(recent)
        volatility = statistics.pstdev(recent) / avg if avg > 0 else 1.0
    else:
        volatility = 0.75
    volatility_score = clamp(1.0 - volatility / 0.65, 0.0, 1.0)
    trend_score = clamp(1.0 - abs(decay_factor - 1.0) / 0.035, 0.0, 1.0)

    score = (
        error_score * 0.36
        + history_score * 0.20
        + span_score * 0.16
        + volatility_score * 0.18
        + trend_score * 0.10
    )
    if score >= 0.74:
        band = "high"
    elif score >= 0.52:
        band = "medium"
    else:
        band = "low"

    return {
        "score": round(score, 3),
        "band": band,
        "history_points": len(values),
        "history_days": history_days,
        "recent_volatility": round(volatility, 4),
    }


def estimate_future_daily_streams(series: list[dict]) -> dict:
    if not series:
        return {
            "base_daily": 0,
            "decay_factor": 1.0,
            "projected_next_daily": 0,
            "forecast_model": {},
            "confidence": {"score": 0.0, "band": "low"},
            "backtest": {"points": 0, "mean_smape": None, "best_model": None},
        }

    candidates = candidate_next_daily_estimates(series)
    weights, backtest = backtest_candidate_weights(series)
    if not weights:
        weights = {
            "ewma_short": 0.35,
            "ewma_medium": 0.30,
            "median_14": 0.20,
            "last_observed": 0.15,
        }

    weighted_candidates = [
        (candidates[name], weight)
        for name, weight in weights.items()
        if name in candidates and candidates[name] > 0
    ]
    if not weighted_candidates:
        weighted_candidates = [(safe_float(series[-1]["daily_streams"]), 1.0)]

    base_daily = max(1.0, weighted_average(weighted_candidates))
    decay_factor = estimate_decay_factor(series)
    projected_next = max(1.0, base_daily * decay_factor)
    confidence = estimate_confidence(series, backtest, decay_factor)

    return {
        "base_daily": int(round(base_daily)),
        "decay_factor": decay_factor,
        "projected_next_daily": int(round(projected_next)),
        "forecast_model": {
            "name": "ensemble_backtested_logtrend_seasonal",
            "candidate_estimates": {name: int(round(value)) for name, value in sorted(candidates.items())},
            "selected_weights": {name: round(weight, 4) for name, weight in sorted(weights.items())},
            "weekday_factors": {str(k): round(v, 4) for k, v in estimate_weekday_factors(series).items()},
            "windows": {
                "recent_window": RECENT_WINDOW,
                "long_window": LONG_WINDOW,
                "max_forecast_days": MAX_FORECAST_DAYS,
            },
        },
        "confidence": confidence,
        "backtest": backtest,
    }


def next_milestone(current_streams: int, milestones: list[int] | None = None) -> int | None:
    milestones = milestones or DEFAULT_MILESTONES
    for milestone in milestones:
        if current_streams < milestone:
            return milestone
    x = milestones[-1] if milestones else MAX_STATIC_MILESTONE
    while current_streams >= x:
        x += MILESTONE_STEP
    return x


def _daily_on_projection_day(
    day_index: int,
    start_daily: float,
    decay_factor: float,
    start_date: date,
    weekday_factors: dict[int, float] | None,
    scenario_multiplier: float = 1.0,
) -> float:
    base = start_daily * (decay_factor ** (day_index - 1))
    if weekday_factors:
        projected_date = start_date + timedelta(days=day_index)
        base *= weekday_factors.get(projected_date.weekday(), 1.0)
    return max(0.0, base * scenario_multiplier)


def project_milestone_date(
    current_streams: int,
    last_date: str,
    start_daily: int,
    decay_factor: float,
    milestone: int,
    weekday_factors: dict[int, float] | None = None,
    scenario_multiplier: float = 1.0,
) -> dict | None:
    if current_streams >= milestone:
        return None

    remaining = milestone - current_streams
    if remaining <= 0 or start_daily <= 0:
        return None

    current_date = parse_iso_date(last_date)
    projected_streams = float(current_streams)

    for day_index in range(1, MAX_FORECAST_DAYS + 1):
        daily = _daily_on_projection_day(
            day_index,
            float(start_daily),
            decay_factor,
            current_date,
            weekday_factors,
            scenario_multiplier,
        )
        if daily < 1.0:
            return None

        projected_streams += daily
        if projected_streams >= milestone:
            eta_date = current_date + timedelta(days=day_index)
            return {
                "expected_date": eta_date.isoformat(),
                "days_left": day_index,
                "projected_streams_on_hit": int(round(projected_streams)),
                "average_daily_needed": int(math.ceil(remaining / day_index)),
            }

    return None


def scenario_dates(
    current_streams: int,
    last_date: str,
    start_daily: int,
    decay_factor: float,
    milestone: int,
    weekday_factors: dict[int, float],
    confidence: dict,
) -> dict:
    score = float(confidence.get("score") or 0.0)
    spread = clamp(0.22 - score * 0.12, 0.08, 0.22)
    scenarios = {
        "optimistic": 1.0 + spread,
        "expected": 1.0,
        "conservative": max(0.55, 1.0 - spread),
    }
    return {
        name: project_milestone_date(
            current_streams=current_streams,
            last_date=last_date,
            start_daily=start_daily,
            decay_factor=decay_factor,
            milestone=milestone,
            weekday_factors=weekday_factors,
            scenario_multiplier=multiplier,
        )
        for name, multiplier in scenarios.items()
    }


def compute_progress(current_streams: int, target: int) -> dict:
    target = max(1, target)
    progress_ratio = clamp(current_streams / target, 0.0, 1.0)
    return {
        "previous_reference": max(0, target - MILESTONE_STEP),
        "target": target,
        "remaining": max(0, target - current_streams),
        "progress_ratio": progress_ratio,
        "progress_percent": round(progress_ratio * 100, 2),
    }


def build_forecasts() -> dict:
    songs_data = load_json(SONGS_PATH)
    history_data = load_history_bundle()

    songs = songs_data.get("songs", [])
    dates = history_data.get("dates", [])
    history_by_date = history_data.get("by_date", {})

    if not songs or not dates:
        return {
            "generated_at": datetime.now().isoformat(),
            "latest_history_date": None,
            "model_version": "ensemble_backtested_logtrend_seasonal_v2",
            "forecasts": [],
        }

    latest_history_date = dates[-1]
    forecasts = []

    for song in songs:
        track_id = song.get("track_id")
        if not track_id:
            continue

        series = get_track_history_series(track_id, history_by_date, dates)
        if not series:
            continue

        last_row = series[-1]
        current_streams = safe_int(last_row["streams"])
        if current_streams <= 0:
            continue

        next_target = next_milestone(current_streams)
        if next_target is None:
            continue

        projection_inputs = estimate_future_daily_streams(series)
        if len(series) < MIN_REQUIRED_HISTORY_POINTS:
            projected_daily = max(safe_int(last_row["daily_streams"]), 1)
            decay_factor = 1.0
            base_daily = projected_daily
        else:
            projected_daily = max(projection_inputs["projected_next_daily"], 1)
            decay_factor = projection_inputs["decay_factor"]
            base_daily = max(projection_inputs["base_daily"], 1)

        last_track_date = last_row["date"]
        weekday_factors = estimate_weekday_factors(series)
        projection = project_milestone_date(
            current_streams=current_streams,
            last_date=last_track_date,
            start_daily=projected_daily,
            decay_factor=decay_factor,
            milestone=next_target,
            weekday_factors=weekday_factors,
        )
        scenarios = scenario_dates(
            current_streams=current_streams,
            last_date=last_track_date,
            start_daily=projected_daily,
            decay_factor=decay_factor,
            milestone=next_target,
            weekday_factors=weekday_factors,
            confidence=projection_inputs["confidence"],
        )

        progress = compute_progress(current_streams, next_target)
        days_stale = (parse_iso_date(latest_history_date) - parse_iso_date(last_track_date)).days

        forecasts.append(
            {
                "track_id": track_id,
                "title": song.get("title"),
                "title_clean": song.get("title_clean") or song.get("title"),
                "image_url": song.get("image_url"),
                "primary_album": song.get("primary_album"),
                "primary_artist": song.get("primary_artist"),
                "spotify_url": song.get("spotify_url"),
                "current_streams": current_streams,
                "latest_daily_streams": safe_int(last_row["daily_streams"]),
                "latest_track_history_date": last_track_date,
                "days_stale": max(0, days_stale),
                "estimated_base_daily": base_daily,
                "estimated_next_daily": projected_daily,
                "estimated_decay_factor": round(decay_factor, 6),
                "estimated_trend_per_day": round((decay_factor - 1.0) * base_daily, 2),
                "next_milestone": next_target,
                "next_milestone_label": format_milestone_label(next_target),
                "progress": progress,
                "forecast": projection,
                "scenario_dates": scenarios,
                "confidence": projection_inputs["confidence"],
                "backtest": projection_inputs["backtest"],
                "forecast_model": projection_inputs["forecast_model"],
            }
        )

    sortable = []
    unsortable = []

    for item in forecasts:
        if item["forecast"] and item["forecast"].get("expected_date"):
            sortable.append(item)
        else:
            unsortable.append(item)

    sortable.sort(
        key=lambda x: (
            x["forecast"]["expected_date"],
            x["forecast"]["days_left"],
            -(x["confidence"]["score"] or 0),
            -(x["current_streams"] or 0),
        )
    )

    unsortable.sort(
        key=lambda x: (
            -(x["progress"]["progress_percent"] or 0),
            -(x["confidence"]["score"] or 0),
            -(x["current_streams"] or 0),
        )
    )

    return {
        "generated_at": datetime.now().isoformat(),
        "latest_history_date": latest_history_date,
        "model_version": "ensemble_backtested_logtrend_seasonal_v2",
        "model_notes": [
            "Uses full available song history, not only the latest average.",
            "Blends multiple candidate models weighted by recent backtest error.",
            "Projects daily streams with log-trend decay/growth and weekday seasonality.",
            "Includes confidence, scenario dates, and staleness diagnostics.",
        ],
        "forecasts": sortable + unsortable,
    }


def main() -> None:
    output = build_forecasts()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    print(f"Written: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
