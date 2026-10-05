"""Fill missed / late daily snapshots with estimates (owner's choice 2026-10-04).

Every activity day D has a nominal boundary: NY midnight of D+1 plus
BOUNDARY_OFFSET (when an on-time run reads it, ~04:05:40 UTC in summer).
`daily_views` of D = total at boundary(D) − total at boundary(D−1).

- Missing day (run crashed / PC off): its rows are created with the total
  estimated at boundary(D) (core/estimate.py, between the real readings around
  it). The next day's daily then becomes computable again.
- Late run (snapshot more than WINDOW_TOLERANCE away from its boundary): its
  total is replaced by the estimate at boundary(D).

`estimated` column: "total" = this row's total_views (so its daily too) is an
estimate; "daily" = total is real but the previous day's total was estimated.
Rows flagged "total" are never used as anchors for later estimates.
Nothing here touches rows whose day already has an exact on-time reading.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from . import estimate

BOUNDARY_OFFSET = timedelta(minutes=5, seconds=40)
WINDOW_TOLERANCE = timedelta(minutes=5)
# Real readings further apart than this around a boundary: too vague, left alone.
MAX_BRACKET = timedelta(days=8)
_RANK_FIELDS = ("rank", "previous_rank", "rank_change", "total_rank", "previous_total_rank",
                "total_rank_change", "daily_change", "daily_change_pct")


def boundary(day: str) -> datetime:
    nxt = date.fromisoformat(day) + timedelta(days=1)
    midnight = datetime(nxt.year, nxt.month, nxt.day, tzinfo=estimate.NY)
    return midnight.astimezone(timezone.utc) + BOUNDARY_OFFSET


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _days(start: str, end: str) -> list[str]:
    d, last, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def fill(
    rows: list[dict],
    *,
    since: str | None,
    factors: dict[int, float],
    extra_anchors: dict[str, list[tuple[datetime, int]]] | None = None,
    log: Callable[[str], None] = print,
) -> tuple[list[dict], list[str]]:
    """Returns (rows incl. created ones, sorted dates whose rows changed).
    Rows are modified in place; ranks/daily_change must be recomputed by the
    caller for the returned dates."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_date[row.get("date") or ""].append(row)
    stamped = sorted(d for d, rs in by_date.items() if d and any(r.get("snapshot_at") for r in rs))
    if not stamped:
        return rows, []
    # The first timestamped day has no real reading before it: never touched.
    first = (date.fromisoformat(stamped[0]) + timedelta(days=1)).isoformat()
    start = max(since or first, first)
    last = max(by_date)

    anchors: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    published: dict[str, datetime | None] = {}
    for row in rows:
        vid = row.get("video_id") or ""
        if anchor := estimate.real_anchor(row):
            anchors[vid].append(anchor)
        if row.get("published_at"):
            published[vid] = estimate._parse(row["published_at"])
    for vid, readings in (extra_anchors or {}).items():
        anchors[vid].extend(readings)  # first-day / first-week exact captures

    bad: list[str] = []
    for day in _days(start, last):
        day_rows = by_date.get(day)
        if not day_rows:
            bad.append(day)
            continue
        snap = estimate._parse(day_rows[0].get("snapshot_at"))
        if snap is None or day_rows[0].get("estimated") == "total":
            continue
        if abs(snap - boundary(day)) > WINDOW_TOLERANCE:
            bad.append(day)
    if not bad:
        return rows, []

    decay: dict[str, float] = {}

    def k_for(vid: str) -> float:
        if vid not in decay:
            pub = published.get(vid)
            pts = estimate._clean([*anchors[vid], *([(pub, 0)] if pub else [])])
            decay[vid] = estimate.fit_decay(pts, pub, factors) if pub else 0.0
        return decay[vid]

    created = 0
    for day in bad:
        target = boundary(day)
        existing = {r.get("video_id"): r for r in by_date.get(day, [])}
        later = [d for d in sorted(by_date) if d > day and by_date[d]]
        template = by_date[later[0]] if later else []
        candidates = list(existing.values()) or [dict(r) for r in template]
        estimated_here = 0
        for row in candidates:
            vid = row.get("video_id") or ""
            pub = published.get(vid)
            if pub is not None and pub > target:
                continue  # did not exist yet
            result = estimate.estimate_views_at(
                target, anchors[vid], pub, factors, decay=k_for(vid), max_bracket=MAX_BRACKET,
            )
            if result is None:
                continue
            if vid not in existing:
                for field in _RANK_FIELDS:
                    row[field] = ""
                row["date"] = day
                by_date[day].append(row)
                rows.append(row)
                created += 1
            row["snapshot_at"] = _iso(target)
            row["total_views"] = result["views"]
            row["estimated"] = "total"
            estimated_here += 1
        if existing:
            # Videos of a late day that could not be estimated keep their real
            # reading but lose the daily (window != 24 h).
            for row in existing.values():
                if row.get("estimated") != "total" and row.get("snapshot_at") != _iso(target):
                    row["daily_views"] = ""
        log(f"[gap_fill] {day}: {'manquant' if not existing else 'snapshot hors fenêtre'} — "
            f"{estimated_here} total(s) estimé(s) à {_iso(target)}")

    # Recompute daily_views from the first bad day on (on-time days give the
    # exact same figure as before).
    changed: list[str] = []
    for day in _days(bad[0], last):
        prev_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        prev = {r.get("video_id"): r for r in by_date.get(prev_day, [])}
        day_changed = day in bad
        for row in by_date.get(day, []):
            before = prev.get(row.get("video_id"))
            if before is None or row.get("total_views") in ("", None) or before.get("total_views") in ("", None):
                continue
            if row.get("estimated") != "total" and not _on_time(row, day):
                continue  # late real reading that could not be estimated
            if before.get("estimated") != "total" and not _on_time(before, prev_day):
                continue
            daily = int(float(row["total_views"])) - int(float(before["total_views"]))
            flag = "total" if row.get("estimated") == "total" else ("daily" if before.get("estimated") == "total" else "")
            if str(row.get("daily_views")) != str(daily) or (row.get("estimated") or "") != flag:
                day_changed = True
            row["daily_views"] = daily
            row["estimated"] = flag
            row["period_gain_views"] = row["period_days"] = row["period_label"] = ""
        if day_changed:
            changed.append(day)
    log(f"[gap_fill] {created} ligne(s) créée(s), jours modifiés : {', '.join(changed) or 'aucun'}")
    return rows, changed


def _on_time(row: dict, day: str) -> bool:
    snap = estimate._parse(row.get("snapshot_at"))
    return snap is not None and abs(snap - boundary(day)) <= WINDOW_TOLERANCE
