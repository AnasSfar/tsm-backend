from __future__ import annotations

import json
import math
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
import milestone_estimator as estimator  # noqa: E402

SITE_DATA_DIR = WEB_EXPORT_DATA_DIR
HISTORY_DIR = WEB_EXPORT_HISTORY_DIR if WEB_EXPORT_HISTORY_DIR.exists() else LEGACY_WEBSITE_HISTORY_DIR
HISTORY_INDEX_PATH = HISTORY_DIR / "index.json"

SONGS_PATH = first_existing(SITE_DATA_DIR / "songs.json", LEGACY_WEBSITE_DATA_DIR / "songs.json")
OUTPUT_PATH = SITE_DATA_DIR / "expected_milestones.json"

MILESTONE_STEP = 100_000_000
MAX_STATIC_MILESTONE = 5_000_000_000
DEFAULT_MILESTONES = list(range(MILESTONE_STEP, MAX_STATIC_MILESTONE + MILESTONE_STEP, MILESTONE_STEP))

MAX_FORECAST_DAYS = 5 * 365


def format_milestone_label(value: int) -> str:
    if value >= 1_000_000_000:
        billions = value / 1_000_000_000
        if abs(billions - round(billions)) < 1e-9:
            return f"{int(round(billions))}B"
        return f"{billions:.1f}B"
    return f"{int(value / 1_000_000)}M"


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


def get_track_history_series(track_id: str, history_by_date: dict, dates: list[str]) -> list[dict]:
    series = []

    for d in dates:
        row = history_by_date.get(d, {}).get(track_id)
        if not row:
            continue

        streams = row.get("s") if row.get("s") is not None else row.get("streams")
        daily_streams = row.get("d") if row.get("d") is not None else row.get("daily_streams")

        series.append(
            {
                "date": d,
                "date_obj": parse_iso_date(d),
                "streams": streams,
                "daily_streams": daily_streams,
                "estimated": bool(row.get("e") or row.get("estimated")),
                "merged": bool(row.get("m")),
            }
        )

    series.sort(key=lambda row: row["date"])
    return series


def estimate_weekday_factors(series: list[dict]) -> dict[int, float]:
    return estimator.weekday_factors(series)


def estimate_future_daily_streams(series: list[dict], **kwargs) -> dict:
    return estimator.estimate(series, **kwargs)


def next_milestone(current_streams: int, milestones: list[int] | None = None) -> int | None:
    milestones = milestones or DEFAULT_MILESTONES
    for milestone in milestones:
        if current_streams < milestone:
            return milestone
    x = milestones[-1] if milestones else MAX_STATIC_MILESTONE
    while current_streams >= x:
        x += MILESTONE_STEP
    return x


def next_song_milestone_at_recent_pace(
    points: list[dict], *, reached_milestone: int, as_of_date: str,
) -> dict:
    """Explicit 7-day-pace estimate for the next step AFTER this song's event.

    Start at the last verified total, including for anticipatory cards whose
    milestone has not yet been collected. Never seed the projection with an
    invented milestone-day total. This is a labelled projection, not a change
    to the damped-trend model or verified stream history.
    """
    end = parse_iso_date(as_of_date)
    start = end - timedelta(days=7)
    target = next_milestone(reached_milestone)
    result = {"milestone": target, "expected_date": None, "basis": "current_7_day_pace", "as_of_date": as_of_date}
    def pending(reason):
        return {**result, "pending_reason": reason}
    by_date = {}
    for row in points:
        day = str(row.get("date") or "")
        if start.isoformat() <= day <= as_of_date:
            if day in by_date:
                return pending(f"duplicate history row on {day}")
            by_date[day] = row
    rows = []
    for offset in range(8):
        day = (start + timedelta(days=offset)).isoformat()
        row = by_date.get(day)
        if row is None:
            return pending(f"missing exact total on {day}")
        if row.get("estimated") or type(row.get("streams")) is not int or row["streams"] < 0:
            return pending(f"non-exact total on {day}")
        if rows:
            daily = row.get("daily_streams")
            if type(daily) is not int or daily < 0 or row["streams"] - rows[-1]["streams"] != daily:
                return pending(f"missing, negative or inconsistent daily on {day}")
        rows.append(row)
    current_total = rows[-1]["streams"]
    target = next_milestone(max(reached_milestone, current_total))
    result["milestone"] = target
    weekly = sum(row["daily_streams"] for row in rows[1:])
    if weekly <= 0:
        return pending("last seven verified dailies sum to zero")
    # Exact integer ceiling of (remaining / (weekly / 7)).
    numerator = (target - current_total) * 7
    days = (numerator + weekly - 1) // weekly
    try:
        expected = (end + timedelta(days=days)).isoformat()
    except OverflowError:
        return pending("projected date exceeds supported calendar")
    return {**result, "expected_date": expected, "days_from_snapshot": days, "verified_weekly_streams": weekly}


def _daily_on_projection_day(
    day_index: int,
    start_daily: float,
    decay_factor: float,
    start_date: date,
    weekday_factors: dict[int, float] | None,
    scenario_multiplier: float = 1.0,
    trend_damping: float = estimator.DEFAULT_DAMPING,
    annual_seasonality: dict | None = None,
) -> float:
    base = estimator.projected_daily(day_index, start_daily, decay_factor, trend_damping)
    if weekday_factors:
        projected_date = start_date + timedelta(days=day_index)
        base *= weekday_factors.get(projected_date.weekday(), 1.0)
    base *= estimator.annual.multiplier(start_date + timedelta(days=day_index), annual_seasonality)
    return max(0.0, base * scenario_multiplier)


def project_milestone_date(
    current_streams: int,
    last_date: str,
    start_daily: int,
    decay_factor: float,
    milestone: int,
    weekday_factors: dict[int, float] | None = None,
    scenario_multiplier: float = 1.0,
    *,
    trend_damping: float = estimator.DEFAULT_DAMPING,
    annual_seasonality: dict | None = None,
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
            trend_damping,
            annual_seasonality,
        )
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
    *,
    trend_damping: float = estimator.DEFAULT_DAMPING,
    annual_seasonality: dict | None = None,
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
            trend_damping=trend_damping,
            annual_seasonality=annual_seasonality,
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
            "model_version": estimator.MODEL_VERSION,
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
            print(f"[forecast] pending {track_id}: no history through {latest_history_date}")
            continue

        last_row = series[-1]
        current_streams = last_row["streams"]
        if type(current_streams) is not int or current_streams < 0:
            print(f"[forecast] pending {track_id}: non-exact total on {last_row['date']}")
            continue

        next_target = next_milestone(current_streams)
        if next_target is None:
            continue

        exact_series, pending_reason = estimator.exact_recent_series(series, latest_history_date)
        projection_inputs = estimate_future_daily_streams(exact_series, track_id=track_id, seasonal_history=series,
            release_date=song.get("release_date")) if not pending_reason else {}
        pending_reason = pending_reason or projection_inputs.get("pending_reason")
        last_track_date = last_row["date"]
        if pending_reason:
            print(f"[forecast] pending {track_id} ({song.get('title')}): {pending_reason}")
            projected_daily = base_daily = None
            decay_factor = trend_damping = None
            projection, scenarios = None, {}
        else:
            projected_daily = projection_inputs["projected_next_daily"]
            decay_factor = projection_inputs["decay_factor"]
            trend_damping = projection_inputs["trend_damping"]
            base_daily = projection_inputs["base_daily"]
            weekday_factors = {int(k): v for k, v in projection_inputs["forecast_model"]["weekday_factors"].items()}
            projection = project_milestone_date(
                current_streams=current_streams, last_date=last_track_date,
                start_daily=projected_daily, decay_factor=decay_factor,
                milestone=next_target, weekday_factors=weekday_factors,
                trend_damping=trend_damping,
                annual_seasonality=projection_inputs.get("annual_seasonality"),
            )
            scenarios = scenario_dates(
                current_streams=current_streams, last_date=last_track_date,
                start_daily=projected_daily, decay_factor=decay_factor,
                milestone=next_target, weekday_factors=weekday_factors,
                confidence=projection_inputs["confidence"], trend_damping=trend_damping,
                annual_seasonality=projection_inputs.get("annual_seasonality"),
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
                "latest_daily_streams": last_row["daily_streams"] if type(last_row["daily_streams"]) is int else None,
                "latest_track_history_date": last_track_date,
                "days_stale": max(0, days_stale),
                "estimated_base_daily": base_daily,
                "estimated_next_daily": projected_daily,
                "estimated_decay_factor": decay_factor,
                "estimated_trend_damping": trend_damping,
                "pending_reason": pending_reason,
                "estimated_trend_per_day": round((decay_factor - 1.0) * base_daily, 2) if decay_factor is not None else None,
                "next_milestone": next_target,
                "next_milestone_label": format_milestone_label(next_target),
                "progress": progress,
                "forecast": projection,
                "scenario_dates": scenarios,
                "confidence": projection_inputs.get("confidence", {"score": None, "band": "low"}),
                "backtest": projection_inputs.get("backtest", {}),
                "forecast_model": projection_inputs.get("forecast_model", {}),
                "forecast_note": estimator.projection_note(projection_inputs, last_track_date, projection["expected_date"]) if projection else None,
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
        "model_version": estimator.MODEL_VERSION,
        "model_notes": [
            "Forecasts are estimates, separate from verified stream totals and daily history.",
            "Fits recent full-week levels and damped trends; release declines and growth do not compound forever.",
            "Explicit verified calendar effects are removed before trend fitting and reapplied only on their calendar dates.",
            "Selects models by rolling-origin cumulative-stream error at 7, 28, 90 and 180 days where available.",
            "Missing, stale, estimated, merged or inconsistent recent data leaves dates pending.",
            "Scenario dates and confidence bands are diagnostics, not calibrated prediction intervals.",
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
