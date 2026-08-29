#!/usr/bin/env python3
"""Score album daily updates to decide the posting order (who goes first).

Mirrors ``score_best_day_since.py``: the same subscore shape (age, daily
absolute gain, daily % gain, weekly % gain, rarity, grower) plus record
bonuses, aggregated to album level from per-track history. Read-only. Used
only to ORDER the album update cards, never to gate which albums post — every
non-Misc album still gets its card every weekday.

Examples:
  python tools/scripts/score_album_update.py
  python tools/scripts/score_album_update.py 2026-08-27 --limit 20
  python tools/scripts/score_album_update.py 2026-08-27 --json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
STREAMS_DIR = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(STREAMS_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

import best_day_since  # noqa: E402
import generate_album_update_image as album_img  # noqa: E402
import score_best_day_since as sbd  # noqa: E402


DEFAULT_LIMIT = 25
# Album daily totals are far larger than single-track dailies — scale the
# absolute-gain cap up accordingly.
ALBUM_DAILY_ABS_GAIN_CAP = 5_000_000
MAJORITY_POSITIVE_BONUS_MAX = 6.0
TRACK_RECORDS_BONUS_MAX = 8.0


@dataclass(frozen=True)
class AlbumScoreWeights:
    age: float = 0.06
    daily_abs_gain: float = 0.24
    daily_pct_gain: float = 0.18
    weekly_pct_gain: float = 0.20
    rarity: float = 0.14
    grower: float = 0.18


WEIGHTS = AlbumScoreWeights()


def _album_track_ids(album: str, target_date: str) -> list[str]:
    """Standard-edition track ids for the album, as its update card shows them."""
    cache_key = (album, target_date)
    cached = _ALBUM_TRACK_IDS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        sections, _canonical = album_img.load_album_sections(album, target_date)
    except Exception:
        sections = []
    seen: list[str] = []
    for section in sections:
        for track in section.get("tracks", []):
            track_id = str(track.get("track_id") or "").strip()
            if track_id and track_id not in seen:
                seen.append(track_id)
    _ALBUM_TRACK_IDS_CACHE[cache_key] = seen
    return seen


def _album_points_by_day(track_ids: list[str], history: dict[str, list[best_day_since.Point]]):
    points_by_track = [history.get(track_id) or [] for track_id in track_ids]
    combined = best_day_since.combine_points(points_by_track)
    # Only trust days where every album track already had data, so today's full
    # total is never compared against a day the album was still incomplete.
    starts = [pts[0].day for pts in points_by_track if pts]
    if len(starts) > 1:
        cutoff = max(starts)
        combined = [point for point in combined if point.day >= cutoff]
    return sbd._point_by_day(combined)


def _majority_positive_ratio(
    track_ids: list[str],
    history: dict[str, list[best_day_since.Point]],
    target: date,
) -> tuple[int, int]:
    counted = 0
    positive = 0
    for track_id in track_ids:
        by_day = sbd._point_by_day(history.get(track_id) or [])
        today = sbd._daily_on(by_day, target)
        yesterday = sbd._daily_on(by_day, target - timedelta(days=1))
        if today is None or yesterday is None:
            continue
        counted += 1
        if today > yesterday:
            positive += 1
    return positive, counted


_HISTORY_CACHE: dict[str, dict] | None = None
_RECORD_IDS_CACHE: dict[str, set[str]] = {}
_SCORE_CACHE: dict[tuple[str, str], dict] = {}
_ALBUM_TRACK_IDS_CACHE: dict[tuple[str, str], list[str]] = {}


def _history() -> dict[str, list[best_day_since.Point]]:
    """Process-local cache: finalize scores albums several times per run."""
    global _HISTORY_CACHE
    if _HISTORY_CACHE is None:
        _HISTORY_CACHE = best_day_since.load_history()
    return _HISTORY_CACHE


def _album_record_track_ids(target: date, *, min_days: int = best_day_since.DEFAULT_MIN_DAYS) -> set[str]:
    """Track ids that hit an individual best-day-since record on `target`."""
    cache_key = target.isoformat()
    cached = _RECORD_IDS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    tracks = best_day_since.load_tracks(include_extras=False)
    history = _history()
    hits: set[str] = set()
    for track_id, track in tracks.items():
        row = best_day_since.compute_best_day_since(track, history.get(track_id) or [], target)
        if not row:
            continue
        if row.get("is_biggest_day_of_year") or best_day_since.passes_filters(row, min_days=min_days):
            hits.add(track_id)
    _RECORD_IDS_CACHE[cache_key] = hits
    return hits


def score_album(
    album: str,
    target: date,
    *,
    history: dict[str, list[best_day_since.Point]] | None = None,
    record_track_ids: set[str] | None = None,
) -> dict:
    cache_key = (album, target.isoformat())
    if cache_key in _SCORE_CACHE:
        return _SCORE_CACHE[cache_key]
    history = history if history is not None else _history()
    track_ids = _album_track_ids(album, target.isoformat())
    if not track_ids:
        result = {"album": album, "status": "skipped", "reasons": ["no_tracks"], "score": 0.0}
        _SCORE_CACHE[cache_key] = result
        return result

    by_day = _album_points_by_day(track_ids, history)
    today = sbd._daily_on(by_day, target)
    yesterday = sbd._daily_on(by_day, target - timedelta(days=1))
    last_week = sbd._daily_on(by_day, target - timedelta(days=7))
    if today is None or today <= 0 or yesterday is None or yesterday <= 0:
        result = {"album": album, "status": "blocked", "reasons": ["missing_daily"], "score": 0.0}
        _SCORE_CACHE[cache_key] = result
        return result

    daily_abs_gain = today - yesterday
    weekly_abs_gain = today - int(last_week) if last_week else None
    daily_pct_gain = sbd._pct_change(today, yesterday) or 0.0
    weekly_pct_gain = sbd._pct_change(today, last_week) or 0.0
    rarity = sbd._rarity_value(by_day, target, today) or 0.0
    grower = sbd._grower_value(by_day, target, daily_pct_gain, weekly_pct_gain)

    album_row = best_day_since.compute_album_best_day_since(album, track_ids, history, target)
    days_since = (album_row or {}).get("days_since")
    is_year_record = bool((album_row or {}).get("is_biggest_day_of_year"))
    is_best_ever = (album_row or {}).get("kind") == "best_ever"

    subscores = {
        "age": sbd._log_age_score(days_since),
        "daily_abs_gain": sbd._gain_score(max(0, daily_abs_gain), ALBUM_DAILY_ABS_GAIN_CAP),
        "daily_pct_gain": sbd._pct_score(daily_pct_gain),
        "weekly_pct_gain": sbd._pct_score(weekly_pct_gain),
        "rarity": sbd._rarity_score(rarity),
        "grower": max(0.0, min(1.0, grower)),
    }
    base_score = (
        WEIGHTS.age * subscores["age"]
        + WEIGHTS.daily_abs_gain * subscores["daily_abs_gain"]
        + WEIGHTS.daily_pct_gain * subscores["daily_pct_gain"]
        + WEIGHTS.weekly_pct_gain * subscores["weekly_pct_gain"]
        + WEIGHTS.rarity * subscores["rarity"]
        + WEIGHTS.grower * subscores["grower"]
    ) * 100.0

    positive, counted = _majority_positive_ratio(track_ids, history, target)
    majority_ratio = positive / counted if counted else 0.0
    majority_bonus = (
        MAJORITY_POSITIVE_BONUS_MAX * min(1.0, max(0.0, (majority_ratio - 0.5) * 2.0))
        if counted
        else 0.0
    )

    record_track_ids = record_track_ids if record_track_ids is not None else set()
    album_record_hits = sum(1 for track_id in track_ids if track_id in record_track_ids)
    track_records_bonus = (
        TRACK_RECORDS_BONUS_MAX * min(1.0, math.log1p(album_record_hits) / math.log1p(8))
        if album_record_hits
        else 0.0
    )

    record_bonus = 0.0
    if is_year_record:
        record_bonus += sbd.BIGGEST_DAY_OF_YEAR_BONUS
    if is_best_ever:
        record_bonus += sbd.BEST_EVER_BONUS

    score = base_score + record_bonus + majority_bonus + track_records_bonus

    explanations: list[str] = []
    if is_year_record:
        explanations.append("album biggest day of the year")
    if is_best_ever:
        explanations.append("album best day ever")
    if daily_pct_gain >= 10:
        explanations.append(f"+{daily_pct_gain:.1f}% album daily vs yesterday")
    if counted and majority_ratio > 0.5:
        explanations.append(f"{positive}/{counted} tracks up day-over-day")
    if album_record_hits:
        explanations.append(f"{album_record_hits} track best-day record(s) today")

    result = {
        "album": album,
        "status": "scored",
        "score": round(score, 3),
        "base_score": round(base_score, 3),
        "record_bonus": round(record_bonus, 3),
        "majority_bonus": round(majority_bonus, 3),
        "track_records_bonus": round(track_records_bonus, 3),
        "subscores": {key: round(value, 3) for key, value in subscores.items()},
        "daily_streams": today,
        "daily_abs_gain": daily_abs_gain,
        "weekly_abs_gain": weekly_abs_gain,
        "daily_pct_gain": round(daily_pct_gain, 3),
        "weekly_pct_gain": round(weekly_pct_gain, 3),
        "rarity": round(rarity, 3),
        "grower": round(grower, 3),
        "days_since": days_since,
        "is_biggest_day_of_year": is_year_record,
        "is_best_ever": is_best_ever,
        "positive_tracks": positive,
        "counted_tracks": counted,
        "track_record_hits": album_record_hits,
        "track_count": len(track_ids),
        "explanations": explanations,
    }
    _SCORE_CACHE[cache_key] = result
    return result


def score_albums(albums: list[str], target: date) -> list[dict]:
    history = _history()
    record_track_ids = _album_record_track_ids(target)
    items = [
        score_album(album, target, history=history, record_track_ids=record_track_ids)
        for album in albums
    ]
    items.sort(key=_sort_key, reverse=True)
    return items


def _sort_key(item: dict) -> tuple:
    return (
        1 if item.get("status") == "scored" else 0,
        float(item.get("score") or 0.0),
        int(item.get("daily_abs_gain") or 0),
        int(item.get("daily_streams") or 0),
    )


def rank_albums(albums: list[str], target_date: str) -> list[str]:
    """Album names ordered best-first for posting. Ties keep the caller's input
    order (stable sort); anything the scorer drops is appended at the end.
    Never raises — ordering must not be able to break posting."""
    try:
        target = date.fromisoformat(target_date)
    except ValueError:
        return list(albums)
    try:
        scored = score_albums(list(albums), target)
    except Exception as exc:  # never let ordering break posting
        print(f"[score_album_update] ranking failed, keeping input order: {exc}")
        return list(albums)
    ranked = [item["album"] for item in scored]
    for album in albums:  # safety: keep any album the scorer dropped
        if album not in ranked:
            ranked.append(album)
    return ranked


def main() -> None:
    parser = argparse.ArgumentParser(description="Score album daily updates for posting order.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD (default: latest in history)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--explain", action="store_true")
    args = parser.parse_args()

    history = best_day_since.load_history()
    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = best_day_since.latest_history_date(history)
        if target is None:
            raise SystemExit("No dated history rows found.")

    albums = [
        name for name in album_img.all_album_names()
        if not any(token in name.casefold() for token in ("misc", "standalone"))
    ]
    items = score_albums(albums, target)

    if args.json:
        print(json.dumps({"date": target.isoformat(), "items": items}, ensure_ascii=False, indent=2))
        return

    print(f"Album update scores for {target.isoformat()} ({len(items)} albums)")
    for index, item in enumerate(items[: max(args.limit, 0)], 1):
        print(
            f"{index:>2}. {item.get('score', 0):>7.2f} | {item['album']} | "
            f"{item.get('daily_streams') or 0:,} daily | "
            f"{(item.get('daily_pct_gain') or 0):+.1f}% d/d | "
            f"{item.get('status')}"
        )
        if args.explain and item.get("explanations"):
            print(f"    why: {'; '.join(item['explanations'])}")


if __name__ == "__main__":
    main()
