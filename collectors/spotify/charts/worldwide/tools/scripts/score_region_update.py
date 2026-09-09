#!/usr/bin/env python3
"""Score Spotify Charts regional snapshots for posting priority.

Read-only scorer for a single region inside a worldwide Taylor Swift snapshot.
It keeps an exact raw score plus diagnostic and ranking fields:

- ``score``: signed raw score from chart events, rank moves, and stream changes.
- ``normalized_score``: raw score divided by charting song count.
- ``adjusted_score``: raw score after optional market weighting and repeat penalty.

Examples:
  python collectors/spotify/charts/worldwide/tools/scripts/score_region_update.py 2026-09-06
  python collectors/spotify/charts/worldwide/tools/scripts/score_region_update.py 2026-09-06 --region br --json
  python collectors/spotify/charts/worldwide/tools/scripts/score_region_update.py 2026-09-06 --backtest-days 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[5]
SPOTIFY_DIR = ROOT / "collectors" / "spotify"
if str(SPOTIFY_DIR) not in sys.path:
    sys.path.insert(0, str(SPOTIFY_DIR))

from core.data_paths import legacy_spotify_chart_dir, spotify_chart_dir  # noqa: E402


NEW_ENTRY_BASE = 40.0
NEW_ENTRY_RANK_FACTOR = 0.6
RE_ENTRY_BASE = 80.0
RE_ENTRY_RANK_FACTOR = 0.8
MAIN_STORE_RE_ENTRY_BONUS = 220.0
MAIN_STORE_RE_ENTRY_RANK_FACTOR = 1.2
DROPOUT_BASE = 70.0
DROPOUT_RANK_FACTOR = 0.5
STREAM_PCT_CAP = 50.0
STREAM_PCT_WEIGHT = 1.2
WEEKLY_STREAM_PCT_CAP = 50.0
WEEKLY_STREAM_PCT_WEIGHT = 0.5
PEAK_BONUS = 15.0
CONTINUING_BREAKOUT_BONUS_MAX = 60.0
CONTINUING_BREAKOUT_MIN_STREAM_PCT = 5.0
CONTINUING_BREAKOUT_MIN_RANK_GAIN = 3
REGIONAL_BEST_DAY_LOOKBACK_DAYS = 14
DEFAULT_POST_MIN_ADJUSTED_SCORE = 12.0
DEFAULT_WEEKEND_POST_MIN_ADJUSTED_SCORE = 35.0
MAIN_STORE_RE_ENTRY_REGIONS = {"global", "us", "gb", "uk", "fr"}

REPEAT_PENALTIES_BY_DAYS_AGO = {
    1: 85.0,
    2: 55.0,
    3: 35.0,
    4: 22.0,
    5: 14.0,
    6: 8.0,
    7: 4.0,
}

MARKET_WEIGHTS = {
    "global": 1.18,
    "us": 1.14,
    "gb": 1.10,
    "uk": 1.10,
    "de": 1.08,
    "br": 1.08,
    "mx": 1.07,
    "fr": 1.06,
    "ca": 1.06,
    "au": 1.05,
    "ph": 1.05,
    "id": 1.05,
    "in": 1.05,
    "es": 1.04,
    "it": 1.04,
    "nl": 1.03,
    "se": 1.03,
    "ar": 1.03,
    "cl": 1.03,
    "co": 1.03,
    "tr": 1.03,
    "jp": 1.03,
    "pl": 1.02,
    "my": 1.02,
    "th": 1.02,
    "sg": 1.02,
    "nz": 1.01,
    "ie": 1.01,
}


@dataclass(frozen=True)
class RegionScore:
    region: str
    score: float
    normalized_score: float
    market_weight: float
    market_score: float
    repeat_penalty: float
    adjusted_score: float
    reason: str
    up: int
    down: int
    in_: int
    out: int
    songs: int
    continuation_breakouts: int
    recent_best_days: int
    breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def detail(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "up": self.up,
            "down": self.down,
            "in": self.in_,
            "out": self.out,
            "songs": self.songs,
            "continuation_breakouts": self.continuation_breakouts,
            "recent_best_days": self.recent_best_days,
            "normalized_score": self.normalized_score,
            "market_weight": self.market_weight,
            "market_score": self.market_score,
            "repeat_penalty": self.repeat_penalty,
            "adjusted_score": self.adjusted_score,
            "reason": self.reason,
            "breakdown": self.breakdown,
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["in"] = data.pop("in_")
        return data


def _to_int(value: object) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _rank_weight(rank: int) -> float:
    return 1.0 + (200 - _clamp(rank, 1, 200)) / 200.0


def _rounded_breakdown(parts: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 1) for key, value in parts.items()}


def _market_weight(region: str, enabled: bool) -> float:
    if not enabled:
        return 1.0
    return MARKET_WEIGHTS.get(region.lower(), 1.0)


def _repeat_penalty(days_ago: int | None) -> float:
    if days_ago is None:
        return 0.0
    return REPEAT_PENALTIES_BY_DAYS_AGO.get(days_ago, 0.0)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def post_min_adjusted_score_for_date(chart_date: str | date) -> float:
    day = chart_date
    if isinstance(day, str):
        day = datetime.strptime(day, "%Y-%m-%d").date()
    weekday_threshold = _env_float("SPOTIFY_REGION_POST_MIN_ADJUSTED_SCORE", DEFAULT_POST_MIN_ADJUSTED_SCORE)
    weekend_threshold = _env_float(
        "SPOTIFY_REGION_POST_WEEKEND_MIN_ADJUSTED_SCORE",
        DEFAULT_WEEKEND_POST_MIN_ADJUSTED_SCORE,
    )
    return weekend_threshold if day.weekday() >= 5 else weekday_threshold


def _reason(
    *,
    up: int,
    down: int,
    entries_in: int,
    out: int,
    continuation_breakouts: int,
    recent_best_days: int,
    breakdown: dict[str, float],
) -> str:
    pieces: list[str] = []
    if recent_best_days:
        pieces.append("regional best day")
    if continuation_breakouts:
        pieces.append("continuing breakout")
    if breakdown.get("re_points", 0) > 0:
        pieces.append("RE lift")
    if breakdown.get("main_store_re_bonus", 0) > 0:
        pieces.append("main store RE")
    if breakdown.get("new_points", 0) > 0:
        pieces.append("NEW lift")
    if out:
        pieces.append(f"{out} OUT")
    if up:
        pieces.append(f"{up} climb{'s' if up != 1 else ''}")
    if down:
        pieces.append(f"{down} drop{'s' if down != 1 else ''}")
    stream_points = breakdown.get("streams_points", 0) + breakdown.get("weekly_points", 0)
    if abs(stream_points) >= 20:
        pieces.append("stream gain" if stream_points > 0 else "stream drag")
    if breakdown.get("peak_bonus", 0) > 0:
        pieces.append("new peak")
    if not pieces and entries_in:
        pieces.append(f"{entries_in} entry")
    return ", ".join(pieces[:4]) or "steady movement"


def snapshot_entry_for_region(
    snapshot_by_track: dict[str, list[dict]],
    track_id: str | None,
    region: str,
) -> dict | None:
    if not track_id:
        return None
    for entry in snapshot_by_track.get(str(track_id), []):
        if isinstance(entry, dict) and entry.get("country") == region:
            return entry
    return None


def score_region_snapshot(
    region: str,
    rows: list[dict],
    prev_day_by_track: dict[str, list[dict]] | None = None,
    *,
    days_since_last_post: int | None = None,
    use_market_weight: bool = True,
) -> RegionScore:
    """Return the signed score for one region's current snapshot rows."""
    parts = {
        "rank_points": 0.0,
        "streams_points": 0.0,
        "weekly_points": 0.0,
        "new_points": 0.0,
        "re_points": 0.0,
        "main_store_re_bonus": 0.0,
        "out_penalty": 0.0,
        "peak_bonus": 0.0,
        "continuation_bonus": 0.0,
    }
    moves_up = moves_down = entries_in = 0
    continuation_breakouts = 0
    recent_best_days = 0
    charting_ids: set[str] = set()

    for row in rows:
        track_id = str(row.get("_track_id_uri") or row.get("track_id") or "")
        if track_id:
            charting_ids.add(track_id)
        rank = _to_int(row.get("rank"))
        if rank is None:
            continue
        prev_rank = _to_int(row.get("previous_rank"))
        rank_for_points = _clamp(rank, 1, 200)
        rank_gain = (prev_rank - rank) if prev_rank is not None and prev_rank > 0 else 0

        if row.get("is_new"):
            points = NEW_ENTRY_BASE + (201 - rank_for_points) * NEW_ENTRY_RANK_FACTOR
            parts["new_points"] += points
            entries_in += 1
        elif row.get("is_re_entry"):
            points = RE_ENTRY_BASE + (201 - rank_for_points) * RE_ENTRY_RANK_FACTOR
            parts["re_points"] += points
            if region.lower() in MAIN_STORE_RE_ENTRY_REGIONS:
                parts["main_store_re_bonus"] += (
                    MAIN_STORE_RE_ENTRY_BONUS
                    + (201 - rank_for_points) * MAIN_STORE_RE_ENTRY_RANK_FACTOR
                )
            entries_in += 1
        elif prev_rank is not None and prev_rank > 0:
            parts["rank_points"] += rank_gain * _rank_weight(min(rank, prev_rank))
            if rank_gain > 0:
                moves_up += 1
                peak_rank = _to_int(row.get("peak_rank"))
                if peak_rank is not None and rank <= peak_rank:
                    parts["peak_bonus"] += PEAK_BONUS * _rank_weight(rank)
            elif rank_gain < 0:
                moves_down += 1

        pct = row.get("stream_change_pct")
        if isinstance(pct, (int, float)):
            stream_pct = float(pct)
            parts["streams_points"] += _clamp(stream_pct, -STREAM_PCT_CAP, STREAM_PCT_CAP) * STREAM_PCT_WEIGHT
            is_recent_best_day = bool(row.get("_is_recent_region_best_day"))
            if is_recent_best_day and stream_pct > 0:
                recent_best_days += 1
            if (
                stream_pct >= CONTINUING_BREAKOUT_MIN_STREAM_PCT
                or rank_gain >= CONTINUING_BREAKOUT_MIN_RANK_GAIN
                or (is_recent_best_day and stream_pct > 0)
            ):
                continuation_breakouts += 1
                stream_strength = min(1.0, stream_pct / 25.0)
                rank_strength = min(1.0, max(rank_gain, 0) / 25.0)
                parts["continuation_bonus"] += (
                    18.0
                    * _rank_weight(rank)
                    * (0.65 * stream_strength + 0.35 * rank_strength)
                )
        weekly_pct = row.get("weekly_stream_change_pct")
        if isinstance(weekly_pct, (int, float)):
            parts["weekly_points"] += (
                _clamp(float(weekly_pct), -WEEKLY_STREAM_PCT_CAP, WEEKLY_STREAM_PCT_CAP)
                * WEEKLY_STREAM_PCT_WEIGHT
            )

    dropouts = 0
    for track_id, entries in (prev_day_by_track or {}).items():
        if str(track_id) in charting_ids:
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("country") == region:
                prev_rank = _to_int(entry.get("rank"))
                if prev_rank is not None:
                    parts["out_penalty"] -= DROPOUT_BASE + (201 - _clamp(prev_rank, 1, 200)) * DROPOUT_RANK_FACTOR
                    dropouts += 1
                break

    parts["continuation_bonus"] = min(CONTINUING_BREAKOUT_BONUS_MAX, parts["continuation_bonus"])
    raw_score = round(sum(parts.values()), 1)
    normalized_score = round(raw_score / max(1, len(rows)), 1)
    market_weight = _market_weight(region, use_market_weight)
    market_score = round(raw_score * market_weight, 1)
    repeat_penalty = _repeat_penalty(days_since_last_post)
    adjusted_score = round(market_score - repeat_penalty, 1)
    breakdown = _rounded_breakdown(parts)

    return RegionScore(
        region=region,
        score=raw_score,
        normalized_score=normalized_score,
        market_weight=round(market_weight, 3),
        market_score=market_score,
        repeat_penalty=round(repeat_penalty, 1),
        adjusted_score=adjusted_score,
        reason=_reason(
            up=moves_up,
            down=moves_down,
            entries_in=entries_in,
            out=dropouts,
            continuation_breakouts=continuation_breakouts,
            recent_best_days=recent_best_days,
            breakdown=breakdown,
        ),
        up=moves_up,
        down=moves_down,
        in_=entries_in,
        out=dropouts,
        songs=len(rows),
        continuation_breakouts=continuation_breakouts,
        recent_best_days=recent_best_days,
        breakdown=breakdown,
    )


def region_score(
    region: str,
    rows: list[dict],
    prev_day_by_track: dict[str, list[dict]] | None = None,
    *,
    days_since_last_post: int | None = None,
    use_market_weight: bool = True,
) -> RegionScore:
    return score_region_snapshot(
        region,
        rows,
        prev_day_by_track,
        days_since_last_post=days_since_last_post,
        use_market_weight=use_market_weight,
    )


def score_regions(
    by_region: dict[str, list[dict]],
    prev_day_by_track: dict[str, list[dict]] | None = None,
    *,
    recent_post_days: dict[str, int] | None = None,
    use_market_weight: bool = True,
    sort_key: str = "adjusted_score",
) -> list[RegionScore]:
    scores = [
        score_region_snapshot(
            region,
            rows,
            prev_day_by_track,
            days_since_last_post=(recent_post_days or {}).get(region),
            use_market_weight=use_market_weight,
        )
        for region, rows in by_region.items()
    ]
    return sorted(scores, key=lambda item: getattr(item, sort_key), reverse=True)


def _worldwide_history_path(chart_date: str) -> Path:
    return spotify_chart_dir("worldwide", chart_date) / f"ts_worldwide_{chart_date}.json"


def load_snapshot_by_track(chart_date: str) -> dict[str, list[dict]]:
    path = _worldwide_history_path(chart_date)
    if not path.exists():
        path = legacy_spotify_chart_dir("worldwide", chart_date) / f"ts_worldwide_{chart_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"missing worldwide snapshot for {chart_date}: {path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    by_track = data.get("by_track") if isinstance(data, dict) else None
    if not isinstance(by_track, dict):
        raise ValueError(f"invalid worldwide snapshot format: {path}")
    return by_track


def rows_by_region(snapshot_by_track: dict[str, list[dict]]) -> dict[str, list[dict]]:
    by_region: dict[str, list[dict]] = {}
    for track_id, entries in snapshot_by_track.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            region = str(entry.get("country") or "").strip()
            if not region:
                continue
            row = {**entry, "_track_id_uri": str(track_id), "track_id": str(track_id)}
            by_region.setdefault(region, []).append(row)

    for rows in by_region.values():
        rows.sort(key=lambda row: row.get("rank") or 9999)
    return by_region


def annotate_recent_region_records(
    chart_date: str,
    by_region: dict[str, list[dict]],
    *,
    lookback_days: int = REGIONAL_BEST_DAY_LOOKBACK_DAYS,
) -> None:
    current = datetime.strptime(chart_date, "%Y-%m-%d").date()
    best_streams: dict[tuple[str, str], int] = {}
    for offset in range(1, lookback_days + 1):
        day = (current - timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            snapshot = load_snapshot_by_track(day)
        except FileNotFoundError:
            continue
        for track_id, entries in snapshot.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                region = str(entry.get("country") or "").strip()
                streams = _to_int(entry.get("streams"))
                if not region or streams is None:
                    continue
                key = (str(track_id), region)
                best_streams[key] = max(best_streams.get(key, 0), streams)

    for region, rows in by_region.items():
        for row in rows:
            track_id = str(row.get("_track_id_uri") or row.get("track_id") or "")
            streams = _to_int(row.get("streams"))
            previous_best = best_streams.get((track_id, region))
            if streams is not None and previous_best is not None and streams > previous_best:
                row["_is_recent_region_best_day"] = True
                row["_previous_recent_region_best_streams"] = previous_best
                row["_region_best_day_lookback"] = lookback_days


def _snapshot_dates_through(end_date: str) -> list[str]:
    root = ROOT / "snapshots" / "spotify_charts"
    dates: list[str] = []
    for path in root.glob("*/*/*/worldwide/ts_worldwide_*.json"):
        day = path.parent.parent.name
        if day <= end_date:
            dates.append(day)
    return sorted(set(dates))


def score_snapshot_date(
    chart_date: str,
    *,
    recent_post_days: dict[str, int] | None = None,
    use_market_weight: bool = True,
) -> list[RegionScore]:
    current = datetime.strptime(chart_date, "%Y-%m-%d").date()
    previous = (current - timedelta(days=1)).strftime("%Y-%m-%d")
    snapshot = load_snapshot_by_track(chart_date)
    by_region = rows_by_region(snapshot)
    annotate_recent_region_records(chart_date, by_region)
    try:
        prev_snapshot = load_snapshot_by_track(previous)
    except FileNotFoundError:
        prev_snapshot = {}
    return score_regions(
        by_region,
        prev_snapshot,
        recent_post_days=recent_post_days,
        use_market_weight=use_market_weight,
    )


def write_region_scores(chart_date: str, scores: list[RegionScore]) -> Path:
    out_path = spotify_chart_dir("worldwide", chart_date) / f"regional_scores_{chart_date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": chart_date, "regions": [score.to_dict() for score in scores]}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _print_scores(scores: list[RegionScore], *, limit: int) -> None:
    for item in scores[:limit]:
        print(
            f"{item.region:>6} raw={item.score:+7.1f} adj={item.adjusted_score:+7.1f} "
            f"norm={item.normalized_score:+6.1f} mw={item.market_weight:.2f} "
            f"(up:{item.up} down:{item.down} in:{item.in_} out:{item.out} songs:{item.songs}) "
            f"{item.reason}"
        )


def _backtest(
    end_date: str,
    days: int,
    *,
    min_songs: int,
    exclude_regions: set[str],
    use_market_weight: bool,
    min_adjusted_score: float | None,
) -> list[dict[str, Any]]:
    dates = _snapshot_dates_through(end_date)[-days:]
    simulated_recent: dict[str, date] = {}
    picks: list[dict[str, Any]] = []
    for day_text in dates:
        day = datetime.strptime(day_text, "%Y-%m-%d").date()
        recent_days = {
            region: (day - last_day).days
            for region, last_day in simulated_recent.items()
            if 1 <= (day - last_day).days <= max(REPEAT_PENALTIES_BY_DAYS_AGO)
        }
        scores = [
            score
            for score in score_snapshot_date(day_text, recent_post_days=recent_days, use_market_weight=use_market_weight)
            if score.region not in exclude_regions
            and score.songs >= min_songs
            and score.adjusted_score >= (min_adjusted_score if min_adjusted_score is not None else post_min_adjusted_score_for_date(day_text))
        ]
        eligible = [
            score
            for score in scores
            if recent_days.get(score.region) != 1 or score.continuation_breakouts > 0
        ]
        pick = eligible[0] if eligible else None
        if pick:
            simulated_recent[pick.region] = day
        picks.append(
            {
                "date": day_text,
                "region": pick.region if pick else None,
                "score": pick.score if pick else None,
                "adjusted_score": pick.adjusted_score if pick else None,
                "threshold": min_adjusted_score if min_adjusted_score is not None else post_min_adjusted_score_for_date(day_text),
                "reason": pick.reason if pick else "no eligible region above threshold",
            }
        )
    return picks


def main() -> int:
    parser = argparse.ArgumentParser(description="Score Spotify Charts regions for a worldwide snapshot.")
    parser.add_argument("date", metavar="YYYY-MM-DD")
    parser.add_argument("--region", help="Only print one region code, e.g. br.")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--json", action="store_true", help="Print machine-readable scores.")
    parser.add_argument("--output", metavar="PATH", help="Write regional score history JSON.")
    parser.add_argument("--no-market-weight", action="store_true", help="Disable gentle market weighting.")
    parser.add_argument("--backtest-days", type=int, default=0, help="Simulate picked regions over the last N snapshots.")
    parser.add_argument("--min-songs", type=int, default=1, help="Minimum charting songs for CLI/backtest eligibility.")
    parser.add_argument(
        "--min-adjusted-score",
        type=float,
        help="Minimum adjusted score for CLI/backtest eligibility. Defaults to weekday/weekend thresholds.",
    )
    parser.add_argument(
        "--exclude-priority",
        action="store_true",
        help="Exclude global/fr/us, matching scored regional posts.",
    )
    args = parser.parse_args()

    exclude_regions = {"global", "fr", "us"} if args.exclude_priority else set()
    use_market_weight = not args.no_market_weight

    if args.backtest_days:
        picks = _backtest(
            args.date,
            args.backtest_days,
            min_songs=args.min_songs,
            exclude_regions=exclude_regions,
            use_market_weight=use_market_weight,
            min_adjusted_score=args.min_adjusted_score,
        )
        if args.json:
            print(json.dumps(picks, ensure_ascii=False, indent=2))
        else:
            for pick in picks:
                region = pick["region"] or "-"
                score = pick["score"]
                adjusted = pick["adjusted_score"]
                threshold = pick["threshold"]
                score_text = f"raw={score:+.1f} adj={adjusted:+.1f}" if score is not None else "no pick"
                print(f"{pick['date']} {region:>6} {score_text} threshold={threshold:.1f} {pick['reason']}")
        return 0

    scores = score_snapshot_date(args.date, use_market_weight=use_market_weight)
    scores = [
        item
        for item in scores
        if (not args.region or item.region == args.region)
        and item.region not in exclude_regions
        and item.songs >= args.min_songs
        and (args.min_adjusted_score is None or item.adjusted_score >= args.min_adjusted_score)
    ]

    if args.output:
        path = Path(args.output)
        payload = {"date": args.date, "regions": [item.to_dict() for item in scores]}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps([item.to_dict() for item in scores[: args.limit]], ensure_ascii=False, indent=2))
        return 0

    _print_scores(scores, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
