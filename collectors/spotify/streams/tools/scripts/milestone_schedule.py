"""Private, conservative milestone planning; never writes predicted history.

Bounds use rolling, out-of-sample cumulative forecast errors. Each tail is
4%, leaving a nominal 92% band for crossing on one calendar day (union bound).
This is historical calibration, not a guaranteed probability under a new
regime. The public cards must never use these forecasts as exact streams.
"""
from __future__ import annotations

import math
import json
import os
import statistics
from datetime import date, timedelta
from pathlib import Path
from contextlib import contextmanager

TRAIN_DAYS = 28
CALIBRATION_DAYS = 84
MIN_CALIBRATION = 56
TAIL = 0.04
LEDGER_PATH = Path(__file__).resolve().parents[5] / "runtime" / "social" / "stream_milestones_scheduled.json"


@contextmanager
def scheduling_lock():
    """Serialize run and post-only planners; release automatically on a crash."""
    path = LEDGER_PATH.with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SystemExit("[milestone_schedule] PENDING: another milestone planner holds the lock") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def load_ledger() -> dict:
    if not LEDGER_PATH.exists():
        return {"events": {}}
    try:
        payload = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), dict):
            raise ValueError("expected events object")
        for key, event in payload["events"].items():
            if not isinstance(event, dict) or event.get("status") not in {"submitting", "scheduled", "uncertain"}:
                raise ValueError(f"invalid event {key}")
            required = {"target_date", "milestone", "track_id", "kind"}
            if not required <= event.keys() or event["kind"] not in {"song", "album"}:
                raise ValueError(f"incomplete event {key}")
            date.fromisoformat(event["target_date"])
            if not isinstance(event["milestone"], int) or event["milestone"] <= 0:
                raise ValueError(f"invalid milestone {key}")
            if event["kind"] == "album" and not event.get("album"):
                raise ValueError(f"missing album {key}")
        return payload
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit(f"[milestone_schedule] BLOCKED: invalid schedule ledger {LEDGER_PATH}: {exc}") from exc


def save_ledger(payload: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LEDGER_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, LEDGER_PATH)


def reserved_keys() -> set[str]:
    events = load_ledger()["events"]
    for key, event in events.items():
        if event["status"] != "scheduled":
            print(f"[milestone_schedule] BLOCKED duplicate {key}: X scheduling outcome {event['status']}; check scheduled posts before retrying")
    return set(events)


class PendingForecast(ValueError):
    pass


def exact_series(history, track_ids: set[str], stats_date: str) -> list[dict]:
    """A contiguous suffix, complete for every album member, with exact deltas.

    A historical gap stops the suffix; it is never bridged or filled. Duplicate
    dates, including equal duplicate rows, block instead of picking one.
    """
    if not track_ids:
        raise PendingForecast("empty track membership")
    end = date.fromisoformat(stats_date)
    start = end - timedelta(days=TRAIN_DAYS + CALIBRATION_DAYS + 2)
    indexed = {}
    for tid in sorted(track_ids):
        rows = {}
        for point in history.points_by_track.get(tid, []):
            day = str(point["date"])
            if start.isoformat() <= day <= stats_date:
                if day in rows:
                    raise PendingForecast(f"duplicate history row: {tid} {day}")
                rows[day] = point
        indexed[tid] = rows
    result = []
    for offset in range((end - start).days + 1):
        day = (end - timedelta(days=offset)).isoformat()
        previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        total = daily = 0
        for tid, rows in indexed.items():
            cur, prev = rows.get(day), rows.get(previous)
            if not cur or not prev:
                if offset == 0:
                    raise PendingForecast(f"missing current/comparison total: {tid} {day}/{previous}")
                return list(reversed(result))
            if cur.get("estimated") or prev.get("estimated"):
                raise PendingForecast(f"estimated history: {tid} {day}/{previous}")
            value = cur.get("daily_streams")
            if value is None or int(value) < 0 or int(cur["streams"]) - int(prev["streams"]) != int(value):
                raise PendingForecast(f"missing, negative or inconsistent daily: {tid} {day}")
            total += int(cur["streams"])
            daily += int(value)
        result.append({"date": day, "streams": total, "daily_streams": daily})
    return list(reversed(result))


def _predict(rows: list[dict], horizon: int) -> float:
    """Weekday baseline with a recent decline carried forward; no growth boost."""
    values = [r["daily_streams"] for r in rows[-TRAIN_DAYS:]]
    if len(values) != TRAIN_DAYS or min(values) <= 0:
        raise PendingForecast("need 28 positive, consecutive training dailies")
    decline = min(1.0, sum(values[-7:]) / sum(values[-14:-7]))
    baseline = statistics.median(values[-7:])
    overall = statistics.median(values)
    last = date.fromisoformat(rows[-1]["date"])
    prediction = 0.0
    for step in range(1, horizon + 1):
        weekday = (last + timedelta(days=step)).weekday()
        seasonal = statistics.median([
            r["daily_streams"] for r in rows[-TRAIN_DAYS:]
            if date.fromisoformat(r["date"]).weekday() == weekday
        ]) / overall
        prediction += baseline * seasonal * decline ** step
    return prediction


def crossing_window(rows: list[dict], milestone: int, horizon: int) -> dict:
    """Qualify only if the conservative interval crosses on the target day.

    The upper bound for the preceding day must still be below the milestone,
    preventing a likely earlier crossing being scheduled a day late.
    """
    if horizon not in (1, 2):
        raise PendingForecast("only next-day/two-day horizons are calibrated")
    if not rows or rows[-1]["streams"] >= milestone:
        raise PendingForecast("milestone already reached or current total missing")
    errors = {h: [] for h in range(1, horizon + 1)}
    for h in errors:
        for cut in range(max(TRAIN_DAYS, len(rows) - CALIBRATION_DAYS - h + 1), len(rows) - h + 1):
            prediction = _predict(rows[:cut], h)
            actual = sum(r["daily_streams"] for r in rows[cut:cut + h])
            errors[h].append(actual / prediction)
        if len(errors[h]) < MIN_CALIBRATION:
            raise PendingForecast(f"insufficient exact calibration: {len(errors[h])}/{MIN_CALIBRATION} at horizon {h}")
    def bound(h, upper=False):
        if h == 0:
            return 0.0
        ratios = sorted(errors[h])
        # Finite-sample order statistics, without interpolating tail evidence.
        rank = math.ceil((1 - TAIL) * (len(ratios) + 1)) if upper else math.floor(TAIL * (len(ratios) + 1))
        if not 1 <= rank <= len(ratios):
            raise PendingForecast("not enough calibration points for tail bound")
        return _predict(rows, h) * ratios[rank - 1]
    gap = milestone - rows[-1]["streams"]
    lower = bound(horizon)
    prior_upper = bound(horizon - 1, upper=True)
    return {
        "eligible": prior_upper < gap <= lower,
        "target_date": (date.fromisoformat(rows[-1]["date"]) + timedelta(days=horizon)).isoformat(),
        "base_date": rows[-1]["date"],
        "milestone": milestone,
        "remaining": gap,
        "lower_gain": lower,
        "previous_day_upper_gain": prior_upper,
        "calibration_points": {str(h): len(e) for h, e in errors.items()},
        "nominal_coverage": 1 - 2 * TAIL,
        "method": "rolling_weekday_decline_cumulative_errors_v1",
    }
