"""Estimate a video's cumulative viewCount at an instant nobody read it.

Used when a reading was missed (PC asleep, network down, late run): a missed
first-week mark (core/first_week.py) or a missed/late daily snapshot
(core/gap_fill.py). Owner's choice (2026-10-04): an estimate to the unit beats
a hole, YouTube figures differ between trackers anyway. Estimated values are
flagged in the data (`estimated` column / state), not in public posts.

Model:
1. Anchors = every REAL reading of the video: publication (0 views), the
   first-week marks, each daily-run snapshot (``snapshot_at`` + ``total_views``,
   rows not flagged ``estimated``).
2. The view rate is ``exp(-k·age) × weekday factor``: k (hourly decay) is
   fitted on the intervals between consecutive anchors (least squares on
   log rate, weighted by length, release spike excluded); the weekday factors
   (America/New_York day) come from every exact daily of the catalogue.
3. The missing instant sits between two anchors whose exact gain is known: that
   gain is split along the curve. The estimate always lies between the two real
   readings, and the sum of the estimated days equals the exact gain.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import CSV_PATH, TOOLS_JSON_DIR

# Intervals starting earlier than this after publication are the release
# spike (rate still climbing): left out of the decay fit.
SPIKE_HOURS = 6.0
MIN_INTERVAL_HOURS = 3.0
MAX_DECAY_PER_HOUR = 0.1
STEP = timedelta(hours=1)
NY = ZoneInfo("America/New_York")
FACTOR_WEEKS = 8


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _int(value) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def real_anchor(row: dict) -> tuple[datetime, int] | None:
    """(snapshot_at, total_views) of a CSV row, None when its total is an
    estimate (core/gap_fill.py: estimated == "total") or unusable."""
    if (row.get("estimated") or "").strip() == "total":
        return None
    at, views = _parse(row.get("snapshot_at")), _int(row.get("total_views"))
    return (at, views) if at is not None and views is not None else None


def csv_anchors(video_id: str, csv_path: Path = CSV_PATH) -> list[tuple[datetime, int]]:
    """Real (snapshot_at, total_views) readings of this video in the daily CSV."""
    out: list[tuple[datetime, int]] = []
    if not csv_path.exists():
        return out
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("video_id") == video_id and (anchor := real_anchor(row)):
                out.append(anchor)
    return out


def registry_anchors(tools_json_dir: Path = TOOLS_JSON_DIR) -> dict[str, list[tuple[datetime, int]]]:
    """Exact readings taken outside the daily run: first-day +24h captures
    (first_day/releases) and first-week marks (first_week/*.json)."""
    out: dict[str, list[tuple[datetime, int]]] = defaultdict(list)

    def add(vid: str, reading: dict) -> None:
        at, views = _parse(reading.get("captured_at")), _int(reading.get("views"))
        if vid and at is not None and views is not None:
            out[vid].append((at, views))

    for path in sorted((tools_json_dir / "first_day" / "releases").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for member in record.get("members") or []:
            add(str(member.get("video_id") or ""), member.get("capture") or {})
    for path in sorted((tools_json_dir / "first_week").glob("*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for mark in (state.get("marks") or {}).values():
            add(str(state.get("video_id") or ""), mark)
    return out


def weekday_factors(rows: list[dict]) -> dict[int, float]:
    """NY weekday (0=Mon) -> relative daily volume, from exact one-day
    daily_views of the last FACTOR_WEEKS weeks (all videos summed)."""
    by_date: dict[str, int] = defaultdict(int)
    for row in rows:
        views = _int(row.get("daily_views"))
        if views is None or views < 0 or (row.get("estimated") or "").strip():
            continue
        by_date[row.get("date") or ""] += views
    dates = sorted(d for d in by_date if d)[-FACTOR_WEEKS * 7:]
    if len(dates) < 14:
        return {}
    # Ratio of each day to its centred 7-day mean removes the trend.
    ratios: dict[int, list[float]] = defaultdict(list)
    for i in range(3, len(dates) - 3):
        window = [by_date[d] for d in dates[i - 3:i + 4]]
        mean = sum(window) / 7
        if mean > 0:
            ratios[date.fromisoformat(dates[i]).weekday()].append(by_date[dates[i]] / mean)
    if len(ratios) < 7:
        return {}
    factors = {wd: sorted(v)[len(v) // 2] for wd, v in ratios.items()}
    norm = sum(factors.values()) / 7
    return {wd: f / norm for wd, f in factors.items()}


def _clean(anchors: list[tuple[datetime, int]]) -> list[tuple[datetime, int]]:
    """Sorted, one reading per instant, cumulative counts never decreasing."""
    out: list[tuple[datetime, int]] = []
    for at, views in sorted(set(anchors)):
        if out and at == out[-1][0]:
            continue
        if out and views < out[-1][1]:
            continue  # viewCount glitch (YouTube recount): ignore the reading
        out.append((at, views))
    return out


class _Curve:
    """Relative view rate over time: exp(-k·age) × weekday factor."""

    def __init__(self, published: datetime, k: float, factors: dict[int, float]):
        self.published, self.k, self.factors = published, k, factors

    def _weekday(self, t: datetime) -> float:
        return self.factors.get(t.astimezone(NY).weekday(), 1.0) if self.factors else 1.0

    def mass(self, a: datetime, b: datetime) -> float:
        """∫ rate between a and b (numeric, STEP-sized slices)."""
        total, t = 0.0, a
        while t < b:
            nxt = min(t + STEP, b)
            mid = t + (nxt - t) / 2
            age_h = (mid - self.published).total_seconds() / 3600
            total += math.exp(-self.k * age_h) * self._weekday(mid) * (nxt - t).total_seconds()
            t = nxt
        return total


def fit_decay(anchors: list[tuple[datetime, int]], published: datetime, factors: dict[int, float]) -> float:
    """Hourly decay rate k (rate ∝ exp(-k·age)); 0 when it can't be fitted."""
    flat = _Curve(published, 0.0, factors)
    points: list[tuple[float, float, float]] = []  # (age midpoint h, log deseasonalized rate, weight)
    for (a, va), (b, vb) in zip(anchors, anchors[1:]):
        hours = (b - a).total_seconds() / 3600
        start_age = (a - published).total_seconds() / 3600
        if hours < MIN_INTERVAL_HOURS or start_age < SPIKE_HOURS or vb <= va:
            continue
        seasonal = flat.mass(a, b) / (b - a).total_seconds()
        points.append((start_age + hours / 2, math.log((vb - va) / hours / seasonal), hours))
    if len(points) < 2:
        return 0.0
    total_w = sum(w for _, _, w in points)
    mean_x = sum(x * w for x, _, w in points) / total_w
    mean_y = sum(y * w for _, y, w in points) / total_w
    var = sum(w * (x - mean_x) ** 2 for x, _, w in points)
    if var <= 0:
        return 0.0
    slope = sum(w * (x - mean_x) * (y - mean_y) for x, y, w in points) / var
    return min(max(-slope, 0.0), MAX_DECAY_PER_HOUR)


def estimate_views_at(
    target: datetime,
    anchors: list[tuple[datetime, int]],
    published: datetime | None,
    factors: dict[int, float] | None = None,
    *,
    decay: float | None = None,
    max_bracket: timedelta | None = None,
) -> dict | None:
    """Estimated cumulative views at ``target``, or None until a real reading
    exists on each side of it. Returns {"views", "low", "high",
    "decay_per_hour", "anchors": [iso_before, iso_after]}. ``decay``: a k
    already fitted on the same anchors (saves refitting per target).
    ``max_bracket``: None when the two real readings are further apart."""
    factors = factors or {}
    points = _clean([*anchors, *([(published, 0)] if published else [])])
    before = [p for p in points if p[0] <= target]
    after = [p for p in points if p[0] >= target]
    if not before or not after:
        return None
    (a, va), (b, vb) = before[-1], after[0]
    if max_bracket is not None and b - a > max_bracket:
        return None
    if decay is not None:
        k = decay
    else:
        k = fit_decay(points, published, factors) if published else 0.0
    if b == a:
        fraction = 0.0
    else:
        curve = _Curve(published or a, k, factors)
        whole = curve.mass(a, b)
        fraction = curve.mass(a, target) / whole if whole > 0 else (target - a) / (b - a)
    return {
        "views": round(va + (vb - va) * fraction),
        "low": va,
        "high": vb,
        "decay_per_hour": round(k, 5),
        "anchors": [a.isoformat(), b.isoformat()],
    }
