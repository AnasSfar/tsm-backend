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
from datetime import date, datetime, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
STREAMS_DIR = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(STREAMS_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

import best_day_since  # noqa: E402
import generate_album_update_image as album_img  # noqa: E402
import score_best_day_since as sbd  # noqa: E402


DEFAULT_LIMIT = 25
DEFAULT_BACKTEST_DAYS = 30
BACKTEST_BIG_GAIN_THRESHOLD = 100_000
BACKTEST_RECORD_HITS_THRESHOLD = 3
PRIMARY_ALBUM_UPDATE_TARGETS = (
    "The Life of a Showgirl",
    "THE TORTURED POETS DEPARTMENT",
)
BOTTOM_ALBUMS_SKIPPED = 2
# Album daily totals are far larger than single-track dailies — scale the
# absolute-gain cap up accordingly.
ALBUM_DAILY_ABS_GAIN_CAP = 5_000_000
MAJORITY_POSITIVE_BONUS_MAX = 6.0
NEGATIVE_MOMENTUM_PENALTY_MAX = 7.0
# Doubled 2026-09-04 (owner call): several tracks clearing a best-day record
# on the same day is a strong "this album deserves the spotlight" signal and
# was underweighted next to the album's own record_bonus (up to 26).
TRACK_RECORDS_BONUS_MAX = 16.0

# score_best_day_since.py's surprise/stature bonuses are tuned to single-song
# daily volumes. Reuse the same 10x ratio already established above for
# ALBUM_DAILY_ABS_GAIN_CAP (500k track cap -> 5M album cap) to scale their
# volume thresholds/caps; the bonus point scale itself (how many score points
# a "big surprise" is worth) is left unchanged.
ALBUM_VOLUME_SCALE = 10
ALBUM_SURPRISE_MIN_DAILY = 30_000 * ALBUM_VOLUME_SCALE
ALBUM_SURPRISE_VOLUME_CAP = 1_000_000 * ALBUM_VOLUME_SCALE
ALBUM_STATURE_MIN_SCALE = 150_000 * ALBUM_VOLUME_SCALE
ALBUM_STATURE_FULL_SCALE = 1_200_000 * ALBUM_VOLUME_SCALE

@dataclass(frozen=True)
class AlbumScoreWeights:
    age: float = 0.06
    daily_abs_gain: float = 0.22
    daily_pct_gain: float = 0.10
    expected_pct_gain: float = 0.12
    weekly_pct_gain: float = 0.18
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
            if not album_img._counts_in_album_total(track):
                continue
            track_id = str(track.get("track_id") or "").strip()
            if track_id and track_id not in seen:
                seen.append(track_id)
    _ALBUM_TRACK_IDS_CACHE[cache_key] = seen
    return seen


def _album_points_by_day(track_ids: list[str], history: dict[str, list[best_day_since.Point]]):
    # The combined day-by-day series only depends on the album's track lineup,
    # not on the target date, so reuse it across score calls in this process.
    cache_key = tuple(track_ids)
    cached = _ALBUM_BY_DAY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    points_by_track = [history.get(track_id) or [] for track_id in track_ids]
    combined = best_day_since.combine_points(points_by_track)
    # Only trust days where every album track already had data, so today's full
    # total is never compared against a day the album was still incomplete.
    starts = [pts[0].day for pts in points_by_track if pts]
    if len(starts) > 1:
        cutoff = max(starts)
        combined = [point for point in combined if point.day >= cutoff]
    result = sbd._point_by_day(combined)
    _ALBUM_BY_DAY_CACHE[cache_key] = result
    return result


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


def _album_surprise_impact_bonus(metrics: dict) -> float:
    """Album-scaled ``score_best_day_since._surprise_impact_bonus``: extra lift
    for a day that is both big in raw album streams and surprising versus the
    album's own recent history (the overlap a routine high-volume day and a
    rare spike on a small album are each already rewarded elsewhere)."""
    daily = int(metrics.get("daily_streams") or 0)
    rarity = float(metrics.get("rarity") or 0.0)
    daily_pct = float(metrics.get("daily_pct_gain") or 0.0)
    weekly_pct = float(metrics.get("weekly_pct_gain") or 0.0)
    if daily < ALBUM_SURPRISE_MIN_DAILY or rarity <= 0:
        return 0.0
    volume = sbd._volume_score(daily, cap=ALBUM_SURPRISE_VOLUME_CAP)
    surprise = sbd._rarity_score(rarity)
    momentum = max(sbd._pct_score(daily_pct), sbd._pct_score(weekly_pct))
    return 18.0 * volume * surprise * (0.70 + 0.30 * momentum)


def _album_stature_bonus(metrics: dict) -> float:
    """Album-scaled ``score_best_day_since._stature_bonus``: a flagship album
    (1989, reputation, Lover...) clearing a months-old best-day marker on an
    otherwise unremarkable day still deserves priority, scaled to the album's
    own streaming size rather than to the size of the day's move."""
    scale = max(
        float(metrics.get("daily_streams") or 0.0),
        float(metrics.get("expected_daily") or 0.0),
    )
    if scale <= ALBUM_STATURE_MIN_SCALE:
        return 0.0
    frac = math.log1p(scale - ALBUM_STATURE_MIN_SCALE) / math.log1p(
        ALBUM_STATURE_FULL_SCALE - ALBUM_STATURE_MIN_SCALE
    )
    return round(sbd.STATURE_BONUS_MAX * min(1.0, frac), 3)


_HISTORY_CACHE: dict[str, dict] | None = None
_RECORD_ROWS_CACHE: dict[str, dict[str, dict]] = {}
_SCORE_CACHE: dict[tuple[str, str, bool], dict] = {}
_ALBUM_TRACK_IDS_CACHE: dict[tuple[str, str], list[str]] = {}
_ALBUM_BY_DAY_CACHE: dict[tuple[str, ...], dict] = {}


def _history() -> dict[str, list[best_day_since.Point]]:
    """Process-local cache: finalize scores albums several times per run."""
    global _HISTORY_CACHE
    if _HISTORY_CACHE is None:
        _HISTORY_CACHE = best_day_since.load_history()
    return _HISTORY_CACHE


def _album_record_track_rows(target: date, *, min_days: int = best_day_since.DEFAULT_MIN_DAYS) -> dict[str, dict]:
    """Best-day rows keyed by track id for individual records on `target`."""
    cache_key = target.isoformat()
    cached = _RECORD_ROWS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    tracks = best_day_since.load_tracks(include_extras=False)
    history = _history()
    hits: dict[str, dict] = {}
    for track_id, track in tracks.items():
        row = best_day_since.compute_best_day_since(track, history.get(track_id) or [], target)
        if not row:
            continue
        if row.get("is_biggest_day_of_year") or best_day_since.passes_filters(row, min_days=min_days):
            hits[track_id] = row
    _RECORD_ROWS_CACHE[cache_key] = hits
    return hits


def _album_record_track_ids(target: date, *, min_days: int = best_day_since.DEFAULT_MIN_DAYS) -> set[str]:
    """Track ids that hit an individual best-day-since record on `target`."""
    return set(_album_record_track_rows(target, min_days=min_days))


def _track_record_quality(row: dict) -> float:
    daily = int(row.get("daily_streams") or 0)
    age = sbd._log_age_score(row.get("days_since"))
    volume = sbd._volume_score(daily, cap=1_000_000)
    pct = 0.0
    previous = row.get("previous_day_daily")
    if previous:
        pct = sbd._pct_score(sbd._pct_change(daily, previous))
    record_flag = 1.0 if row.get("is_biggest_day_of_year") or row.get("kind") == "best_ever" else 0.0
    return max(0.0, min(1.0, 0.35 * age + 0.35 * volume + 0.20 * pct + 0.10 * record_flag))


def _track_records_bonus(record_rows: list[dict]) -> tuple[float, float]:
    if not record_rows:
        return 0.0, 0.0
    weighted_hits = sum(0.35 + 0.65 * _track_record_quality(row) for row in record_rows)
    bonus = TRACK_RECORDS_BONUS_MAX * min(1.0, math.log1p(weighted_hits) / math.log1p(8))
    return bonus, weighted_hits


def _negative_momentum_penalty(
    daily_pct_gain: float,
    weekly_pct_gain: float,
    *,
    has_album_record: bool,
) -> float:
    if has_album_record or daily_pct_gain >= 0 or weekly_pct_gain >= 0:
        return 0.0
    daily_drop = min(1.0, abs(daily_pct_gain) / 5.0)
    weekly_drop = min(1.0, abs(weekly_pct_gain) / 10.0)
    return NEGATIVE_MOMENTUM_PENALTY_MAX * (0.55 * daily_drop + 0.45 * weekly_drop)


def score_album(
    album: str,
    target: date,
    *,
    history: dict[str, list[best_day_since.Point]] | None = None,
    record_track_ids: set[str] | None = None,
    record_track_rows: dict[str, dict] | None = None,
    lightweight: bool = False,
) -> dict:
    """Score one album's daily update for posting order.

    `lightweight=True` skips the per-album best-day-since lookup, used only
    for the age/record/comeback signals.
    """
    cache_key = (album, target.isoformat(), lightweight)
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
    expected_daily, _expected_parts = sbd._expected_daily(by_day, target, holiday=False)
    expected_pct_gain = sbd._pct_change(today, expected_daily) if expected_daily is not None else None
    positive_move_days_7 = sbd._positive_move_count(by_day, target, 7)

    if lightweight:
        album_row = None
    else:
        album_row = best_day_since.compute_album_best_day_since(album, track_ids, history, target)
    days_since = (album_row or {}).get("days_since")
    is_year_record = bool((album_row or {}).get("is_biggest_day_of_year"))
    is_best_ever = (album_row or {}).get("kind") == "best_ever"

    subscores = {
        "age": sbd._log_age_score(days_since),
        "daily_abs_gain": sbd._gain_score(max(0, daily_abs_gain), ALBUM_DAILY_ABS_GAIN_CAP),
        "daily_pct_gain": sbd._pct_score(daily_pct_gain),
        "expected_pct_gain": sbd._pct_score(expected_pct_gain),
        "weekly_pct_gain": sbd._pct_score(weekly_pct_gain),
        "rarity": sbd._rarity_score(rarity),
        "grower": max(0.0, min(1.0, grower)),
    }
    base_score = (
        WEIGHTS.age * subscores["age"]
        + WEIGHTS.daily_abs_gain * subscores["daily_abs_gain"]
        + WEIGHTS.daily_pct_gain * subscores["daily_pct_gain"]
        + WEIGHTS.expected_pct_gain * subscores["expected_pct_gain"]
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

    if record_track_rows is None and record_track_ids is None and not lightweight:
        record_track_rows = _album_record_track_rows(target)
    record_track_ids = (
        set(record_track_rows)
        if record_track_rows is not None
        else (record_track_ids if record_track_ids is not None else set())
    )
    album_record_rows = [
        record_track_rows[track_id]
        for track_id in track_ids
        if record_track_rows is not None and track_id in record_track_rows
    ]
    album_record_hits = (
        len(album_record_rows)
        if record_track_rows is not None
        else sum(1 for track_id in track_ids if track_id in record_track_ids)
    )
    track_records_bonus, track_records_weighted_hits = _track_records_bonus(album_record_rows)
    if album_record_hits and record_track_rows is None:
        track_records_bonus = TRACK_RECORDS_BONUS_MAX * min(1.0, math.log1p(album_record_hits) / math.log1p(8))
        track_records_weighted_hits = float(album_record_hits)

    record_bonus = 0.0
    if is_year_record:
        record_bonus += sbd.BIGGEST_DAY_OF_YEAR_BONUS
    if is_best_ever:
        record_bonus += sbd.BEST_EVER_BONUS
    has_album_record = bool(is_year_record or is_best_ever or days_since is not None)

    metrics_for_bonuses = {
        "daily_streams": today,
        "rarity": rarity,
        "daily_pct_gain": daily_pct_gain,
        "weekly_pct_gain": weekly_pct_gain,
        "expected_daily": expected_daily,
    }
    surprise_impact_bonus = _album_surprise_impact_bonus(metrics_for_bonuses)
    stature_bonus = _album_stature_bonus(metrics_for_bonuses)
    comeback_bonus = 0.0
    if not lightweight:
        comeback_bonus = sbd._comeback_bonus(
            {"days_since": days_since},
            {
                "expected_pct_gain": expected_pct_gain or 0.0,
                "positive_move_days_7": positive_move_days_7,
            },
        )
    negative_momentum_penalty = _negative_momentum_penalty(
        daily_pct_gain,
        weekly_pct_gain,
        has_album_record=has_album_record,
    )

    score = (
        base_score
        + record_bonus
        + majority_bonus
        + track_records_bonus
        + surprise_impact_bonus
        + stature_bonus
        + comeback_bonus
        - negative_momentum_penalty
    )

    explanations: list[str] = []
    if is_year_record:
        explanations.append("album biggest day of the year")
    if is_best_ever:
        explanations.append("album best day ever")
    if daily_pct_gain >= 10:
        explanations.append(f"+{daily_pct_gain:.1f}% album daily vs yesterday")
    if expected_pct_gain is not None and expected_pct_gain >= 20:
        explanations.append(f"{expected_pct_gain:.1f}% above its expected baseline")
    if counted and majority_ratio > 0.5:
        explanations.append(f"{positive}/{counted} tracks up day-over-day")
    if album_record_hits:
        explanations.append(f"{album_record_hits} track best-day record(s) today")
    if negative_momentum_penalty > 0:
        explanations.append("negative daily and weekly momentum discounted")
    if stature_bonus > 0:
        explanations.append("major album clearing a long-standing best day")
    if comeback_bonus > 0:
        explanations.append("album comeback after a quiet stretch")

    result = {
        "album": album,
        "status": "scored",
        "score": round(score, 3),
        "base_score": round(base_score, 3),
        "record_bonus": round(record_bonus, 3),
        "majority_bonus": round(majority_bonus, 3),
        "track_records_bonus": round(track_records_bonus, 3),
        "surprise_impact_bonus": round(surprise_impact_bonus, 3),
        "stature_bonus": round(stature_bonus, 3),
        "comeback_bonus": round(comeback_bonus, 3),
        "negative_momentum_penalty": round(negative_momentum_penalty, 3),
        "subscores": {key: round(value, 3) for key, value in subscores.items()},
        "daily_streams": today,
        "daily_abs_gain": daily_abs_gain,
        "weekly_abs_gain": weekly_abs_gain,
        "daily_pct_gain": round(daily_pct_gain, 3),
        "weekly_pct_gain": round(weekly_pct_gain, 3),
        "rarity": round(rarity, 3),
        "grower": round(grower, 3),
        "expected_daily": round(expected_daily, 3) if expected_daily is not None else None,
        "expected_pct_gain": round(expected_pct_gain, 3) if expected_pct_gain is not None else None,
        "positive_move_days_7": positive_move_days_7,
        "days_since": days_since,
        "is_biggest_day_of_year": is_year_record,
        "is_best_ever": is_best_ever,
        "positive_tracks": positive,
        "counted_tracks": counted,
        "track_record_hits": album_record_hits,
        "track_record_weighted_hits": round(track_records_weighted_hits, 3),
        "track_count": len(track_ids),
        "explanations": explanations,
    }
    _SCORE_CACHE[cache_key] = result
    return result


def score_albums(albums: list[str], target: date) -> list[dict]:
    history = _history()
    record_track_rows = _album_record_track_rows(target)
    items = [
        score_album(
            album,
            target,
            history=history,
            record_track_rows=record_track_rows,
        )
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


def _default_albums() -> list[str]:
    return [
        name for name in album_img.all_album_names()
        if not any(token in name.casefold() for token in ("misc", "standalone"))
    ]


def _postable_albums_for_date(albums: list[str], target: date) -> tuple[list[str], list[str]]:
    postable: list[str] = []
    blocked: list[str] = []
    target_text = target.isoformat()
    for album in albums:
        if album_img.holiday_collection_post_block_reason(album, target_text):
            blocked.append(album)
        else:
            postable.append(album)
    return postable, blocked


def _simulated_post_queue(scored: list[dict]) -> tuple[list[str], list[str], list[str]]:
    ranked = [item["album"] for item in scored]
    if len(ranked) <= 2:
        return ranked, [], []
    primary_cf = {name.casefold() for name in PRIMARY_ALBUM_UPDATE_TARGETS}
    top2 = ranked[:2]
    done_cf = {album.casefold() for album in top2}
    forced = [album for album in ranked if album.casefold() in primary_cf and album.casefold() not in done_cf]
    done_cf |= {album.casefold() for album in forced}
    tail = [album for album in ranked if album.casefold() not in done_cf]
    dropped: list[str] = []
    if len(tail) > BOTTOM_ALBUMS_SKIPPED:
        dropped = tail[-BOTTOM_ALBUMS_SKIPPED:]
        tail = tail[: -BOTTOM_ALBUMS_SKIPPED]
    return top2 + forced + tail, forced, dropped


def backtest_album_scores(days: int, *, end_date: date | None = None) -> dict:
    """Read-only audit over recent album score rankings."""
    history = _history()
    latest = end_date or best_day_since.latest_history_date(history)
    if latest is None:
        raise SystemExit("No dated history rows found.")
    albums = _default_albums()
    runs: list[dict] = []
    for offset in range(max(days, 1) - 1, -1, -1):
        target = latest - timedelta(days=offset)
        postable, blocked_not_postable = _postable_albums_for_date(albums, target)
        scored = score_albums(postable, target)
        scored_items = [item for item in scored if item.get("status") == "scored"]
        queue, forced, dropped = _simulated_post_queue(scored_items)
        top2 = [item["album"] for item in scored_items[:2]]
        top2_cf = {album.casefold() for album in top2}
        top5_cf = {item["album"].casefold() for item in scored_items[:5]}

        warnings: list[dict] = []
        for item in scored_items:
            album_cf = item["album"].casefold()
            if album_cf not in top2_cf and int(item.get("track_record_hits") or 0) >= BACKTEST_RECORD_HITS_THRESHOLD:
                warnings.append({
                    "kind": "multi_record_outside_top2",
                    "album": item["album"],
                    "track_record_hits": item.get("track_record_hits"),
                    "score": item.get("score"),
                })
            if album_cf not in top5_cf and int(item.get("daily_abs_gain") or 0) >= BACKTEST_BIG_GAIN_THRESHOLD:
                warnings.append({
                    "kind": "big_gain_outside_top5",
                    "album": item["album"],
                    "daily_abs_gain": item.get("daily_abs_gain"),
                    "score": item.get("score"),
                })
            if album_cf not in top5_cf and float(item.get("expected_pct_gain") or 0.0) >= 20.0:
                warnings.append({
                    "kind": "expected_spike_outside_top5",
                    "album": item["album"],
                    "expected_pct_gain": item.get("expected_pct_gain"),
                    "score": item.get("score"),
                })

        runs.append({
            "date": target.isoformat(),
            "top2": top2,
            "forced": forced,
            "queue": queue,
            "dropped": dropped,
            "blocked_not_postable": blocked_not_postable,
            "warnings": warnings,
        })
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": len(runs),
        "end_date": latest.isoformat(),
        "runs": runs,
    }


def _print_backtest(payload: dict) -> None:
    print(f"Album score backtest through {payload['end_date']} ({payload['days']} days)")
    total_warnings = 0
    for run in payload["runs"]:
        warnings = run.get("warnings") or []
        total_warnings += len(warnings)
        print(
            f"{run['date']} | top2: {', '.join(run['top2']) or '-'} | "
            f"forced: {', '.join(run['forced']) or '-'} | dropped: {', '.join(run['dropped']) or '-'}"
        )
        if run.get("blocked_not_postable"):
            print(f"    not postable: {', '.join(run['blocked_not_postable'])}")
        for warning in warnings:
            details = ", ".join(
                f"{key}={value}"
                for key, value in warning.items()
                if key not in {"kind", "album"}
            )
            print(f"    warning: {warning['kind']} | {warning['album']} | {details}")
    print(f"Warnings: {total_warnings}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score album daily updates for posting order.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD (default: latest in history)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument(
        "--backtest-days",
        nargs="?",
        const=DEFAULT_BACKTEST_DAYS,
        type=int,
        help=f"Run a read-only ranking audit over N recent days (default: {DEFAULT_BACKTEST_DAYS})",
    )
    parser.add_argument("--backtest-end", help="Backtest end date YYYY-MM-DD (default: latest in history)")
    args = parser.parse_args()

    history = best_day_since.load_history()
    if args.backtest_days is not None:
        end_date = date.fromisoformat(args.backtest_end) if args.backtest_end else None
        payload = backtest_album_scores(args.backtest_days or DEFAULT_BACKTEST_DAYS, end_date=end_date)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_backtest(payload)
        return

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = best_day_since.latest_history_date(history)
        if target is None:
            raise SystemExit("No dated history rows found.")

    albums = _default_albums()
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
