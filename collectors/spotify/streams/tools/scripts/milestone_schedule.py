"""Milestone stress scenarios used only to decide when to schedule a post.

Use the recent worst day and observed weekly declines, not a probability
score. Every pessimistic scenario must reach the milestone on the target
day; a recent-best-day scenario must not reach it the day before. Scenario
counts never enter verified history, public cards or stream exports.
"""
from __future__ import annotations

import math
import json
import os
from fractions import Fraction
from datetime import date, timedelta
from pathlib import Path
from contextlib import contextmanager

HISTORY_DAYS = 28
MIN_HISTORY_DAYS = 14
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
            if not isinstance(event, dict) or event.get("status") not in {"submitting", "scheduled", "uncertain", "schedule_unconfirmed"}:
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
            print(f"[milestone_schedule] X SCHEDULING UNCONFIRMED {key} (stored status: {event['status']}); this is not the milestone assessment. Check scheduled posts before retrying")
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
    start = end - timedelta(days=HISTORY_DAYS)
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


def crossing_window(rows: list[dict], milestone: int, horizon: int) -> dict:
    """Crossing must survive the worst recent day and observed weekly declines.

    Weekly trend compares the last seven days with the preceding seven. Also
    stress with the worst same-weekday change seen in those last seven days.
    Growth never raises a pessimistic scenario. Reapply the decline at each
    forecast step as a stress assumption, not a measured future daily change.
    """
    if horizon not in (1, 2):
        raise PendingForecast("only next-day/two-day anticipation is supported")
    if not rows or rows[-1]["streams"] >= milestone:
        raise PendingForecast("milestone already reached or current total missing")
    recent = rows[-HISTORY_DAYS:]
    if len(recent) < MIN_HISTORY_DAYS:
        raise PendingForecast(f"need {MIN_HISTORY_DAYS} consecutive exact days, have {len(recent)}")
    for index, row in enumerate(recent):
        if row.get("estimated") or any(not isinstance(row.get(field), int) or row[field] < 0 for field in ("streams", "daily_streams")):
            raise PendingForecast(f"non-exact or negative scenario input: {row.get('date')}")
        if index:
            prior = recent[index - 1]
            if date.fromisoformat(row["date"]) - date.fromisoformat(prior["date"]) != timedelta(days=1):
                raise PendingForecast(f"non-consecutive scenario history: {prior['date']}/{row['date']}")
            if row["streams"] - prior["streams"] != row["daily_streams"]:
                raise PendingForecast(f"inconsistent scenario history: {row['date']}")
    values = [row["daily_streams"] for row in recent]
    previous_week, latest_week = sum(values[-14:-7]), sum(values[-7:])
    if previous_week <= 0 or any(value <= 0 for value in values[-14:-7]):
        raise PendingForecast("weekly comparison contains zero daily; decline ratio undefined")
    weekly_ratio = Fraction(latest_week, previous_week)
    same_weekday_ratios = [Fraction(values[i], values[i - 7]) for i in range(len(values) - 7, len(values))]
    worst_weekly_ratio = min(same_weekday_ratios)
    decline = min(Fraction(1), weekly_ratio, worst_weekly_ratio)
    worst = min(values)
    # Use exact rational arithmetic, rounding the conservative cumulative gain
    # down only once. No float rounding can make a just-short milestone pass.
    scenarios = {
        "recent_worst_day": Fraction(worst * horizon),
        "last_daily_weekly_decline": sum(Fraction(values[-1]) * decline ** step for step in range(1, horizon + 1)),
        "weekly_average_decline": sum(Fraction(latest_week, 7) * decline ** step for step in range(1, horizon + 1)),
        "worst_day_weekly_decline": sum(Fraction(worst) * decline ** step for step in range(1, horizon + 1)),
    }
    gains = {name: math.floor(gain) for name, gain in scenarios.items()}
    gap = milestone - rows[-1]["streams"]
    lower = min(gains.values())
    prior_upper = max(values) * (horizon - 1)
    eligible = prior_upper < gap <= lower
    reason = "all_pessimistic_scenarios_cross_on_target_day"
    if gap > lower:
        reason = "pessimistic_scenario_short_of_milestone"
    elif gap <= prior_upper:
        reason = "could_cross_before_target_day"
    return {
        "eligible": eligible,
        "assessment": "robust_crossing" if eligible else "pending",
        "assessment_reason": reason,
        "target_date": (date.fromisoformat(rows[-1]["date"]) + timedelta(days=horizon)).isoformat(),
        "base_date": rows[-1]["date"],
        "milestone": milestone,
        "remaining": gap,
        "lower_gain": lower,
        "previous_day_upper_gain": prior_upper,
        "scenario_gains": gains,
        "scenario_passed": {name: gain >= gap for name, gain in gains.items()},
        "observed": {
            "history_days": len(values),
            "history_start": recent[0]["date"],
            "history_end": recent[-1]["date"],
            "recent_worst_daily": worst,
            "recent_best_daily": max(values),
            "last_daily": values[-1],
            "latest_week_total": latest_week,
            "previous_week_total": previous_week,
            "weekly_change_pct": float((weekly_ratio - 1) * 100),
            "worst_recent_weekly_change_pct": float((worst_weekly_ratio - 1) * 100),
            "stress_decline_factor": float(decline),
        },
        "method": "recent_worst_day_and_weekly_decline_scenarios_v2",
    }
