"""Explicit, evidence-backed calendar effects for forecast trajectories only.

Multiplicative adjustment removes an observed seasonal effect before fitting
the recent trajectory and reapplies it on the appropriate future dates:
https://otexts.com/fpp3/components.html . No measured row is changed.
"""
from __future__ import annotations

import calendar
import statistics
from datetime import date, timedelta

# A song title/family is never an automatic seasonality rule.
TRACK_RULES = {"3hUxzQpSfdDqwM3ZTFQY0K": {"month": 8, "label": "August", "first_year": 2021}}


def verified_seasons(history: list[dict], rule: dict, as_of: str) -> dict:
    rows = {}
    duplicates = set()
    for row in history:
        if row["date"] > as_of:
            continue
        if row["date"] in rows:
            duplicates.add(row["date"])
        rows[row["date"]] = row
    month = rule["month"]
    valid, excluded = [], []
    for year in range(rule["first_year"], date.fromisoformat(as_of).year + 1):
        end = date(year, month, calendar.monthrange(year, month)[1])
        if end.isoformat() > as_of:
            continue  # The unfinished season is never used to train itself.
        start = date(year, month - 1, 1)
        baseline = start - timedelta(days=1)
        reason = None
        sums = {month - 1: 0, month: 0}
        counts = {month - 1: 0, month: 0}
        previous = None
        day = baseline
        while day <= end:
            key = day.isoformat()
            row = rows.get(key)
            if key in duplicates:
                reason = f"duplicate history on {key}"
            elif not row or type(row.get("streams")) is not int or row["streams"] < 0:
                reason = f"missing exact total on {key}"
            elif row.get("estimated") or row.get("merged"):
                reason = f"estimated or merged history on {key}"
            elif previous is not None:
                daily = row.get("daily_streams")
                if type(daily) is not int or daily < 0 or row["streams"] - previous != daily:
                    reason = f"missing, negative or inconsistent daily on {key}"
                else:
                    sums[day.month] += daily
                    counts[day.month] += 1
            if reason:
                break
            previous = row["streams"]
            day += timedelta(days=1)
        if not reason and sums[month - 1] <= 0:
            reason = f"zero verified baseline month in {year}"
        if reason:
            excluded.append({"year": year, "available_after": end.isoformat(), "reason": reason})
        else:
            baseline_mean = sums[month - 1] / counts[month - 1]
            mean = sums[month] / counts[month]
            valid.append({"year": year, "available_after": end.isoformat(),
                          "baseline_streams": sums[month - 1], "season_streams": sums[month],
                          "baseline_days": counts[month - 1], "season_days": counts[month],
                          "ratio": mean / baseline_mean})
    return {"rule": rule, "seasons": valid, "excluded_seasons": excluded}


def profile_at(evidence: dict | None, as_of: str) -> dict | None:
    if not evidence:
        return None
    seasons = [s for s in evidence["seasons"] if s["available_after"] <= as_of]
    results = []
    for i, held_out in enumerate(seasons):
        past = seasons[max(0, i - 3):i]
        if not past:
            continue
        factor = statistics.median(s["ratio"] for s in past)
        actual = held_out["ratio"]
        results.append({"year": held_out["year"], "training_years": [s["year"] for s in past],
                        "predicted_ratio": factor, "observed_ratio": actual,
                        "seasonal_smape": 2 * abs(factor - actual) / (factor + actual) if factor + actual else 0.0,
                        "unadjusted_smape": 2 * abs(1 - actual) / (1 + actual)})
    reason = None
    if len(seasons) < 2 or not results:
        reason = "need two complete verified seasons and a held-out seasonal check"
    elif statistics.mean(r["seasonal_smape"] for r in results) >= statistics.mean(r["unadjusted_smape"] for r in results):
        reason = "seasonal adjustment did not improve held-out seasonal-month totals"
    selected = seasons[-3:]
    factor = statistics.median(s["ratio"] for s in selected) if selected else None
    if factor is not None and factor <= 1:
        reason = "verified seasons do not support a recurring boost"
    return {"enabled": reason is None, "pending_reason": reason,
            "month": evidence["rule"]["month"], "label": evidence["rule"]["label"],
            "factor": factor if reason is None else None,
            "training_years": [s["year"] for s in selected], "seasons": seasons,
            "validation": results,
            "excluded_seasons": [s for s in evidence["excluded_seasons"] if s["available_after"] <= as_of]}


def multiplier(day: date, profile: dict | None) -> float:
    return profile["factor"] if profile and profile.get("enabled") and day.month == profile["month"] else 1.0


def adjusted(series: list[dict], profile: dict | None) -> list[dict]:
    if not profile or not profile.get("enabled"):
        return series
    return [{**row, "daily_streams": row["daily_streams"] / multiplier(row["date_obj"], profile)} for row in series]


def projection_note(profile: dict | None, as_of: str, expected_date: str) -> dict | None:
    if not profile or not profile.get("enabled"):
        return None
    start, end = date.fromisoformat(as_of) + timedelta(days=1), date.fromisoformat(expected_date)
    includes = any(max(start, date(year, profile["month"], 1)) <=
                   min(end, date(year, profile["month"], calendar.monthrange(year, profile["month"])[1]))
                   for year in range(start.year, end.year + 1))
    return {"kind": "annual_boost" if includes else "before_annual_boost",
            "text": f"{profile['label']} boost included" if includes else f"Before next {profile['label']} boost",
            "evidence": {"training_years": profile["training_years"], "factor": profile["factor"]}}
