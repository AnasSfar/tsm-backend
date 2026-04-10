"""Swift Top 100 — weekly chart (Billboard-style).

Generates a weekly Top 100 ranking of Taylor Swift songs based on Spotify streams.

Data sources:
- db/streams_history.csv (weekly streams = sum of daily_streams over 7 days)
- db/charts_history_global.csv (bonus points based on best rank during the week)
- db/discography/* (metadata: title, album, image_url)

Outputs:
- db/swift_top_100_history.csv (append/replace by week-ending date)
- website/site/data/swift_top_100.json (latest snapshot)

Run:
  python collectors/billboard/swift_top_100.py
  python collectors/billboard/swift_top_100.py --date 2026-04-03
  python collectors/billboard/swift_top_100.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[1]

# Match existing collector scripts: import shared core utilities from collectors/spotify/core/
sys.path.insert(0, str((_REPO_ROOT / "collectors" / "spotify").resolve()))
from core.logger import Logger  # noqa: E402
_DB_DIR = _REPO_ROOT / "db"
_SITE_DATA_DIR = _REPO_ROOT / "website" / "site" / "data"

STREAMS_HISTORY_CSV = _DB_DIR / "streams_history.csv"
CHARTS_GLOBAL_CSV = _DB_DIR / "charts_history_global.csv"
APPLE_MUSIC_GLOBAL_CSV = _DB_DIR / "apple_music_global.csv"
APPLE_MUSIC_TS_TOP_SONGS_CSV = _DB_DIR / "apple_music_ts_top_songs.csv"
SWIFT_TOP_100_HISTORY_CSV = _DB_DIR / "swift_top_100_history.csv"
SWIFT_TOP_100_BONUSES_JSON = _DB_DIR / "swift_top_100_bonuses.json"

DISCOGRAPHY_DIR = _DB_DIR / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
MISC_JSON = DISCOGRAPHY_DIR / "songs.json"

OUTPUT_JSON = _SITE_DATA_DIR / "swift_top_100.json"
OUTPUT_PNG = _SITE_DATA_DIR / "swift_top_100.png"

_TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")


def _parse_iso_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _format_date(value: date) -> str:
    return value.isoformat()


def _extract_track_id(url: str | None) -> str | None:
    if not url:
        return None
    m = _TRACK_ID_RE.search(url)
    return m.group(1) if m else None


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_PAREN_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_DASH_SPLIT_RE = re.compile(r"\s+-\s+")


def _normalize_title(value: str) -> str:
    """Best-effort normalization for matching chart CSV titles."""
    s = (value or "").strip().casefold()
    if not s:
        return ""
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')

    # Remove bracketed qualifiers: (Taylor's Version), [feat. ...], etc.
    s = _PAREN_RE.sub(" ", s)

    # Keep main title when CSV includes: "Song - Remastered".
    s = _DASH_SPLIT_RE.split(s, maxsplit=1)[0]

    s = _NORMALIZE_RE.sub(" ", s)
    s = " ".join(s.split())
    return s

def _format_number(value: int | float | None, decimals: int = 2) -> str:
    """Format a number with K/M/B suffixes. E.g., 1234567 → '1.23M'."""
    if value is None or value == 0:
        return "0"
    
    value = float(value)
    if abs(value) < 1_000:
        return str(int(value)) if value == int(value) else f"{value:.{decimals}f}".rstrip('0').rstrip('.')
    elif abs(value) < 1_000_000:
        return f"{value / 1_000:.{decimals}f}".rstrip('0').rstrip('.') + "k"
    elif abs(value) < 1_000_000_000:
        return f"{value / 1_000_000:.{decimals}f}".rstrip('0').rstrip('.') + "M"
    else:
        return f"{value / 1_000_000_000:.{decimals}f}".rstrip('0').rstrip('.') + "B"

@dataclass(frozen=True)
class TrackMeta:
    track_id: str
    title: str
    spotify_url: str
    image_url: str | None
    primary_album: str | None
    historical_track_ids: tuple[str, ...] = ()


def _iter_discography_tracks() -> list[TrackMeta]:
    items: dict[str, TrackMeta] = {}

    def _ingest_track(track: dict, album_name: str | None) -> None:
        url = (track.get("url") or track.get("spotify_url") or "").strip()
        track_id = _extract_track_id(url)
        if not track_id or track_id in items:
            return
        title = (track.get("title") or "").strip()
        if not title:
            return
        spotify_url = f"https://open.spotify.com/track/{track_id}"
        image_url = track.get("image_url") or None
        historical_track_ids = tuple(
            h for h in (track.get("historical_track_ids") or []) if isinstance(h, str) and h and h != track_id
        )
        items[track_id] = TrackMeta(
            track_id=track_id,
            title=title,
            spotify_url=spotify_url,
            image_url=image_url,
            primary_album=album_name,
            historical_track_ids=historical_track_ids,
        )

    # Albums
    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            album_name = (payload.get("album") or album_file.stem).strip() or album_file.stem
            for section in payload.get("sections", []) or []:
                if not isinstance(section, dict):
                    continue
                for track in section.get("tracks", []) or []:
                    if isinstance(track, dict):
                        _ingest_track(track, album_name)

    # Misc songs.json (sections list)
    if MISC_JSON.exists():
        try:
            payload = json.loads(MISC_JSON.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, list):
            for section in payload:
                if not isinstance(section, dict):
                    continue
                album_name = (section.get("album") or "").strip() or None
                for track in section.get("tracks", []) or []:
                    if isinstance(track, dict):
                        _ingest_track(track, album_name)

    return list(items.values())


def _latest_streams_date() -> date | None:
    if not STREAMS_HISTORY_CSV.exists():
        return None

    latest: date | None = None
    with STREAMS_HISTORY_CSV.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = _parse_iso_date(row.get("date") or "")
            if d is None:
                continue
            if latest is None or d > latest:
                latest = d

    return latest


def _week_dates(week_end: date) -> tuple[date, list[str]]:
    week_start = week_end - timedelta(days=6)
    days = [_format_date(week_start + timedelta(days=i)) for i in range(7)]
    return week_start, days


def _aggregate_weekly_streams(
    *, week_dates: set[str], logger: Logger
) -> tuple[dict[str, int], dict[str, int], dict[str, dict[str, int]]]:
    """Return (weekly_streams_by_track, row_count_by_date, daily_streams_by_track).

    daily_streams_by_track: {track_id: {date_str: streams}}
    """
    weekly: dict[str, int] = {}
    counts: dict[str, int] = {d: 0 for d in week_dates}
    daily: dict[str, dict[str, int]] = {}

    if not STREAMS_HISTORY_CSV.exists():
        logger.log(f"[swift_top_100] Missing file: {STREAMS_HISTORY_CSV}")
        return weekly, counts, daily

    def _to_int(v: str | None) -> int:
        try:
            return int((v or "").strip())
        except Exception:
            return 0

    with STREAMS_HISTORY_CSV.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            day = (row.get("date") or "").strip()
            if day not in week_dates:
                continue
            track_id = (row.get("track_id") or "").strip()
            if not track_id:
                continue
            streams = _to_int(row.get("daily_streams"))
            counts[day] = counts.get(day, 0) + 1
            weekly[track_id] = weekly.get(track_id, 0) + streams
            daily.setdefault(track_id, {})[day] = daily.get(track_id, {}).get(day, 0) + streams

    return weekly, counts, daily


def _best_global_rank_by_title(*, week_dates: set[str], logger: Logger) -> dict[str, int]:
    """Return normalized_title -> best rank (min)."""
    best: dict[str, int] = {}
    if not CHARTS_GLOBAL_CSV.exists():
        logger.log(f"[swift_top_100] Missing file: {CHARTS_GLOBAL_CSV} (bonus disabled)")
        return best

    def _to_int(v: str | None) -> int | None:
        try:
            return int((v or "").strip())
        except Exception:
            return None

    matched_rows = 0
    with CHARTS_GLOBAL_CSV.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            day = (row.get("date") or "").strip()
            if day not in week_dates:
                continue
            title = (row.get("song_name") or "").strip()
            rank = _to_int(row.get("rank"))
            if not title or not rank:
                continue
            key = _normalize_title(title)
            if not key:
                continue
            prev = best.get(key)
            if prev is None or rank < prev:
                best[key] = rank
            matched_rows += 1

    logger.log(f"[swift_top_100] Global charts rows in window: {matched_rows}")
    return best



def _weekly_charts_streams_by_title(*, week_dates: set[str], logger: Logger) -> dict[str, int]:
    """Return normalized_title -> total filtered Spotify Global chart streams over the week."""
    totals: dict[str, int] = {}
    if not CHARTS_GLOBAL_CSV.exists():
        return totals

    def _to_int(v: str | None) -> int:
        try:
            return int((v or "").strip())
        except Exception:
            return 0

    # Deduplicate: (key, streams_value) seen set to detect stale re-scraped data
    # (same exact stream count for the same song on two different days = scraper repeated previous day)
    seen: set[tuple[str, int]] = set()

    with CHARTS_GLOBAL_CSV.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            day = (row.get("date") or "").strip()
            if day not in week_dates:
                continue
            title = (row.get("song_name") or "").strip()
            streams = _to_int(row.get("streams"))
            if not title or streams <= 0:
                continue
            key = _normalize_title(title)
            if not key:
                continue
            dedup_key = (key, streams)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            totals[key] = totals.get(key, 0) + streams

    return totals


def _load_bonuses(chart_date: str) -> dict[str, int]:
    """Return {track_id: bonus_points} for the given week-ending date.

    Config file: db/swift_top_100_bonuses.json
    Format: [{"track_id": "...", "week_end": "YYYY-MM-DD", "bonus": 600, "reason": "..."}]
    """
    if not SWIFT_TOP_100_BONUSES_JSON.exists():
        return {}
    try:
        entries = json.loads(SWIFT_TOP_100_BONUSES_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: dict[str, int] = {}
    for entry in entries:
        if (entry.get("week_end") or "").strip() == chart_date:
            tid = (entry.get("track_id") or "").strip()
            bonus = entry.get("bonus", 0)
            if tid and isinstance(bonus, (int, float)) and bonus > 0:
                result[tid] = result.get(tid, 0) + int(bonus)
    return result


def _rank_to_am_units_score(rank: int) -> float:
    """Loi de puissance : 500 / rang^0.75. Rang 1 → 500.0, Rang 100 → ~15.8."""
    if rank < 1:
        return 0.0
    return 500.0 / (rank ** 0.75)


def _weekly_apple_music_global_points(*, week_dates: set[str], logger: Logger) -> dict[str, float]:
    """Return normalized_title -> sum of daily AM Global raw scores over the week.

    Formula: 500 / rank^0.75 per day (power law). Best rank per (title, day) kept.
    Multiply by 1000 externally when computing units_am.
    """
    scores: dict[str, float] = {}
    if not APPLE_MUSIC_GLOBAL_CSV.exists():
        logger.log(f"[swift_top_100] Missing file: {APPLE_MUSIC_GLOBAL_CSV} (Apple Music disabled)")
        return scores

    def _to_int(v: str | None) -> int | None:
        try:
            return int((v or "").strip())
        except Exception:
            return None

    best_per_day: dict[tuple[str, str], int] = {}
    matched_rows = 0
    with APPLE_MUSIC_GLOBAL_CSV.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            day = (row.get("date") or "").strip()
            if day not in week_dates:
                continue
            chart_type = (row.get("chart_type") or "").strip().lower()
            if chart_type and chart_type != "global":
                continue
            title = (row.get("song_name") or "").strip()
            rank = _to_int(row.get("rank"))
            if not title or not rank or rank < 1 or rank > 100:
                continue
            key = _normalize_title(title)
            if not key:
                continue
            cell = (key, day)
            if cell not in best_per_day or rank < best_per_day[cell]:
                best_per_day[cell] = rank
            matched_rows += 1

    for (key, _), rank in best_per_day.items():
        scores[key] = scores.get(key, 0.0) + _rank_to_am_units_score(rank)

    logger.log(f"[swift_top_100] Apple Music Global rows in window: {matched_rows}")
    return scores


def _weekly_apple_music_ts_points(*, week_dates: set[str], logger: Logger) -> dict[str, float]:
    """Return normalized_title -> sum of daily AM TS Top Songs raw scores over the week.

    Formula: 500 / rank^0.75 per day (power law). Best rank per (title, day) kept.
    Multiply by 1000 externally when computing units_am.
    """
    scores: dict[str, float] = {}
    if not APPLE_MUSIC_TS_TOP_SONGS_CSV.exists():
        logger.log(f"[swift_top_100] Missing file: {APPLE_MUSIC_TS_TOP_SONGS_CSV} (Apple Music TS Top Songs disabled)")
        return scores

    def _to_int(v: str | None) -> int | None:
        try:
            return int((v or "").strip())
        except Exception:
            return None

    best_per_day: dict[tuple[str, str], int] = {}
    matched_rows = 0
    with APPLE_MUSIC_TS_TOP_SONGS_CSV.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            day = (row.get("date") or "").strip()
            if day not in week_dates:
                continue
            title = (row.get("song_name") or "").strip()
            rank = _to_int(row.get("rank"))
            if not title or not rank or rank < 1 or rank > 100:
                continue
            key = _normalize_title(title)
            if not key:
                continue
            cell = (key, day)
            if cell not in best_per_day or rank < best_per_day[cell]:
                best_per_day[cell] = rank
            matched_rows += 1

    for (key, _), rank in best_per_day.items():
        scores[key] = scores.get(key, 0.0) + _rank_to_am_units_score(rank)

    logger.log(f"[swift_top_100] Apple Music TS Top Songs rows in window: {matched_rows}")
    return scores


def _load_existing_history_excluding_date(chart_date: str, logger: Logger) -> list[dict]:
    if not SWIFT_TOP_100_HISTORY_CSV.exists():
        return []

    try:
        with SWIFT_TOP_100_HISTORY_CSV.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:
        logger.log(f"[swift_top_100] Failed to read history CSV: {exc}")
        return []

    return [r for r in rows if (r.get("date") or "").strip() != chart_date]


def _history_stats(rows: list[dict]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Return (weeks_on_chart_by_track, peak_position_by_track, times_at_peak_by_track)."""
    seen_weeks: dict[str, set[str]] = {}
    all_ranks: dict[str, list[int]] = {}

    def _to_int(v: str | None) -> int | None:
        try:
            return int((v or "").strip())
        except Exception:
            return None

    for row in rows:
        tid = (row.get("track_id") or "").strip()
        d = (row.get("date") or "").strip()
        rk = _to_int(row.get("rank"))
        if not tid or not d:
            continue
        seen_weeks.setdefault(tid, set()).add(d)
        if rk:
            all_ranks.setdefault(tid, []).append(rk)

    weeks_on_chart = {tid: len(ds) for tid, ds in seen_weeks.items()}
    peaks = {tid: min(ranks) for tid, ranks in all_ranks.items()}
    times_at_peak = {tid: ranks.count(peaks[tid]) for tid, ranks in all_ranks.items()}
    return weeks_on_chart, peaks, times_at_peak


def _write_history_csv(rows: list[dict], logger: Logger) -> None:
    SWIFT_TOP_100_HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date",
        "week_start",
        "rank",
        "track_id",
        "title",
        "weekly_streams",
        "units_am",
        "units_spotify",
        "units_charts",
        "units_surplus",
        "total_units",
        "streams_pct",
        "airplay_pct",
        "sales_pct",
        "bonus_points",
        "points",
        "global_best_rank",
        "am_ts_score",
        "am_global_score",
        "prev_rank",
        "prev_points",
        "change",
        "rank_change",
        "percentage_change",
        "weeks_on_chart",
        "peak_position",
        "times_at_peak",
    ]

    with SWIFT_TOP_100_HISTORY_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logger.log(f"[swift_top_100] Wrote history CSV: {SWIFT_TOP_100_HISTORY_CSV} ({len(rows)} rows)")


def _write_snapshot_json(payload: dict, logger: Logger) -> None:
    _SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.log(f"[swift_top_100] Wrote snapshot JSON: {OUTPUT_JSON}")


def _maybe_upload_to_r2(*, logger: Logger) -> None:
    # Load repo-level .env if available so scheduled/manual runs share the same config source.
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(_REPO_ROOT / ".env", override=True)
    except Exception:
        pass

    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        logger.log("[swift_top_100] R2 upload skipped (UPLOAD_TO_R2 explicitly disabled)")
        return

    required_env = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    missing = [name for name in required_env if not os.getenv(name, "").strip()]
    if missing:
        logger.log("[swift_top_100] R2 upload skipped: missing env var(s): " + ", ".join(missing))
        return

    r2_script = _REPO_ROOT / "scripts" / "r2.py"
    if not r2_script.exists():
        logger.log(f"[swift_top_100] R2 upload script missing: {r2_script}")
        return

    logger.log("[swift_top_100] Uploading exported data to R2...")
    try:
        completed = subprocess.run(
            [sys.executable, str(r2_script)],
            cwd=str(_REPO_ROOT),
            check=False,
        )
        if completed.returncode == 0:
            logger.log("[swift_top_100] R2 upload completed")
        else:
            logger.log(f"[swift_top_100] R2 upload failed (exit code {completed.returncode})")
    except Exception as exc:
        logger.log(f"[swift_top_100] R2 upload failed (exception): {exc}")


def _build_week_chart(
    *,
    week_end: date,
    tracks: dict[str, TrackMeta],
    logger: Logger,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Return (points_by_track, rank_by_track) for top-100 only."""
    week_start, day_list = _week_dates(week_end)
    week_set = set(day_list)

    weekly_streams, row_counts, daily_streams = _aggregate_weekly_streams(week_dates=week_set, logger=logger)
    logger.log(
        "[swift_top_100] Streams rows per date: "
        + ", ".join(f"{d}:{row_counts.get(d, 0)}" for d in sorted(week_set))
    )

    # Merge historical track IDs streams into their primary track ID.
    # Uses max() to avoid double-counting when both IDs tracked the same streams in parallel.
    # For true re-releases (non-overlapping periods), one of the two values will be 0 so max == sum.
    for meta in tracks.values():
        for h_id in meta.historical_track_ids:
            if h_id in weekly_streams:
                weekly_streams[meta.track_id] = max(weekly_streams.get(meta.track_id, 0), weekly_streams.pop(h_id))
            if h_id in daily_streams:
                h_daily = daily_streams.pop(h_id)
                primary_daily = daily_streams.setdefault(meta.track_id, {})
                for d, s in h_daily.items():
                    primary_daily[d] = max(primary_daily.get(d, 0), s)

    best_rank = _best_global_rank_by_title(week_dates=week_set, logger=logger)

    scored: list[dict] = []
    for tid, wk_streams in weekly_streams.items():
        if wk_streams <= 0:
            continue
        meta = tracks.get(tid)
        title = meta.title if meta else tid
        norm_title = _normalize_title(title)
        br = best_rank.get(norm_title)
        # Points calculated later after top-100 selection (need sum of top 100 streams)
        points = wk_streams
        scored.append(
            {
                "track_id": tid,
                "title": title,
                "weekly_streams": wk_streams,
                "bonus_points": 0,
                "points": points,
                "global_best_rank": br,
                "week_start": _format_date(week_start),
                "week_end": _format_date(week_end),
            }
        )

    scored.sort(
        key=lambda r: (
            r.get("points") or 0,
            r.get("weekly_streams") or 0,
            (r.get("title") or "").casefold(),
            r.get("track_id") or "",
        ),
        reverse=True,
    )

    # Deduplicate by normalized title: same song may have multiple Spotify IDs.
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for r in scored:
        key = _normalize_title(r.get("title") or "")
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(r)

    top = deduped[:100]

    # Points: 1 pt per 30 000 Spotify streams (predictable, ~800 pts for a 25M-streams #1)
    for r in top:
        r["points"] = round(r["weekly_streams"] / 30_000, 2)

    points_by_track = {r["track_id"]: r for r in top}
    rank_by_track = {r["track_id"]: i for i, r in enumerate(top, 1)}

    logger.log(
        f"[swift_top_100] Built chart week_end={_format_date(week_end)}: "
        f"{len(top)} entries (candidates={len(scored)})"
    )

    return points_by_track, rank_by_track


def run(
    *,
    chart_date: date | None,
    dry_run: bool,
) -> int:
    logger = Logger()

    logger.log("[swift_top_100] Starting")
    if chart_date is None:
        chart_date = _latest_streams_date()

    if chart_date is None:
        logger.log(f"[swift_top_100] No dates found in {STREAMS_HISTORY_CSV}")
        return 2

    week_start, _ = _week_dates(chart_date)
    _, day_list = _week_dates(chart_date)
    week_set = set(day_list)
    prev_week_end = chart_date - timedelta(days=7)

    logger.log(f"[swift_top_100] chart_date={_format_date(chart_date)} week_start={_format_date(week_start)}")
    logger.log(f"[swift_top_100] prev_week_end={_format_date(prev_week_end)}")

    tracks_list = _iter_discography_tracks()
    tracks = {t.track_id: t for t in tracks_list}
    logger.log(f"[swift_top_100] Discography tracks indexed: {len(tracks)}")

    curr_points, curr_ranks = _build_week_chart(week_end=chart_date, tracks=tracks, logger=logger)

    am_best_rank = _weekly_apple_music_global_points(week_dates=week_set, logger=logger)
    am_ts_best_rank = _weekly_apple_music_ts_points(week_dates=week_set, logger=logger)
    charts_streams_by_title = _weekly_charts_streams_by_title(week_dates=week_set, logger=logger)

    chart_date_str = _format_date(chart_date)

    bonuses = _load_bonuses(chart_date_str)
    if bonuses:
        logger.log(f"[swift_top_100] Bonuses applied: {bonuses}")
        for tid, bonus in bonuses.items():
            if tid in curr_points:
                curr_points[tid]["bonus_points"] = bonus
                curr_points[tid]["points"] = round(curr_points[tid]["points"] + bonus, 2)

    existing_rows = _load_existing_history_excluding_date(chart_date_str, logger)
    weeks_on_chart_by_track, peak_by_track, times_at_peak_by_track = _history_stats(existing_rows)

    is_first_run = len(existing_rows) == 0
    if is_first_run:
        logger.log("[swift_top_100] No existing history — first run, all entries will be NEW")
        prev_points: dict = {}
        prev_ranks: dict = {}
    else:
        prev_points, prev_ranks = _build_week_chart(week_end=prev_week_end, tracks=tracks, logger=logger)

    out_entries: list[dict] = []
    snapshot_entries: list[dict] = []

    for tid, rank in sorted(curr_ranks.items(), key=lambda kv: kv[1]):
        row = curr_points[tid]
        meta = tracks.get(tid)

        pr = prev_ranks.get(tid)
        prev_row = prev_points.get(tid)
        prev_points_value = prev_row.get("points") if prev_row else None

        change = "NEW" if pr is None else None
        rank_change = (pr - rank) if pr is not None else None
        pct_change = None
        if pr is not None and prev_points_value and prev_points_value > 0:
            pct_change = ((row["points"] - prev_points_value) / prev_points_value) * 100

        weeks_on_chart = weeks_on_chart_by_track.get(tid, 0) + 1
        hist_peak = peak_by_track.get(tid, 9999)
        peak_position = min(hist_peak, rank)
        hist_times = times_at_peak_by_track.get(tid, 0)
        times_at_peak = hist_times + (1 if rank <= hist_peak else 0)

        key = _normalize_title(row["title"])
        weekly_streams = row["weekly_streams"]

        # Apple Music units (loi de puissance × 1000)
        am_ts_raw = am_ts_best_rank.get(key, 0.0)
        am_global_raw = am_best_rank.get(key, 0.0)
        units_am = round((am_ts_raw + am_global_raw) * 1000)

        # Spotify units (on-chart + surplus × 0.7)
        units_charts = charts_streams_by_title.get(key, 0)
        units_surplus = max(0, weekly_streams - units_charts)
        units_spotify = round(units_charts + units_surplus * 0.7)

        # Total (pas de données iTunes)
        total_units = units_spotify + units_am

        # Répartition (points calculés après — placeholder 0)
        streams_pct = round(units_spotify / total_units * 100, 1) if total_units else 0.0
        airplay_pct = round(units_am / total_units * 100, 1) if total_units else 0.0

        out_entries.append(
            {
                "date": chart_date_str,
                "week_start": row["week_start"],
                "rank": rank,
                "track_id": tid,
                "title": row["title"],
                "weekly_streams": weekly_streams,
                "units_am": units_am,
                "units_spotify": units_spotify,
                "units_charts": units_charts,
                "units_surplus": units_surplus,
                "total_units": total_units,
                "streams_pct": streams_pct,
                "airplay_pct": airplay_pct,
                "sales_pct": 0,
                "bonus_points": row["bonus_points"],
                "points": 0,  # calculé après normalisation
                "global_best_rank": row.get("global_best_rank"),
                "am_ts_score": round(am_ts_raw, 2),
                "am_global_score": round(am_global_raw, 2),
                "prev_rank": pr,
                "prev_points": prev_points_value,
                "change": change,
                "rank_change": rank_change,
                "percentage_change": pct_change,
                "weeks_on_chart": weeks_on_chart,
                "peak_position": peak_position,
                "times_at_peak": times_at_peak,
            }
        )

        snapshot_entries.append(
            {
                "rank": rank,
                "track_id": tid,
                "title": row["title"],
                "primary_album": meta.primary_album if meta else None,
                "spotify_url": meta.spotify_url if meta else None,
                "image_url": (meta.image_url if meta and meta.image_url else None),
                "weekly_streams": weekly_streams,
                "units_am": units_am,
                "units_spotify": units_spotify,
                "units_charts": units_charts,
                "units_surplus": units_surplus,
                "total_units": total_units,
                "units": _format_number(total_units),
                "am_ts_units_display": _format_number(round(am_ts_raw * 1000)),
                "am_global_units_display": _format_number(round(am_global_raw * 1000)),
                "units_charts_display": _format_number(units_charts),
                "units_surplus_display": _format_number(units_surplus),
                "streams_pct": streams_pct,
                "airplay_pct": airplay_pct,
                "sales_pct": 0,
                "bonus_points": row["bonus_points"],
                "points": 0,  # calculé après normalisation
                "global_best_rank": row.get("global_best_rank"),
                "am_ts_score": round(am_ts_raw, 2),
                "am_global_score": round(am_global_raw, 2),
                "prev_rank": pr,
                "change": change,
                "rank_change": rank_change,
                "percentage_change": pct_change,
                "weeks_on_chart": weeks_on_chart,
                "peak_position": peak_position,
                "times_at_peak": times_at_peak,
            }
        )

    # Normalisation dynamique des points : somme top 100 / 15 000
    sum_total_units = sum(e["total_units"] for e in out_entries)
    factor = sum_total_units / 15_000 if sum_total_units > 0 else 1.0
    for e in out_entries:
        bonus = e.get("bonus_points") or 0
        base = round(e["total_units"] / factor, 1)
        e["points"] = round(base + bonus, 1)
    for e in snapshot_entries:
        bonus = e.get("bonus_points") or 0
        base = round(e["total_units"] / factor, 1)
        points = round(base + bonus, 1)
        e["points"] = points
        e["points_display"] = _format_number(points)

    # Reclassify by total_units (Spotify + Apple Music combined)
    snapshot_entries.sort(
        key=lambda e: (
            e.get("total_units") or 0,
            e.get("weekly_streams") or 0,
            (e.get("title") or "").casefold(),
            e.get("track_id") or "",
        ),
        reverse=True,
    )
    
    # Reassign ranks based on total_units ordering
    for i, e in enumerate(snapshot_entries, 1):
        e["rank"] = i

    # Keep CSV/history fields aligned with the final ranking used in the snapshot/UI.
    final_rank_by_track = {e["track_id"]: e["rank"] for e in snapshot_entries}
    out_by_track = {e["track_id"]: e for e in out_entries}

    for tid, out in out_by_track.items():
        final_rank = final_rank_by_track.get(tid)
        if final_rank is None:
            continue
        out["rank"] = final_rank
        hist_peak = peak_by_track.get(tid, 9999)
        hist_times = times_at_peak_by_track.get(tid, 0)
        out["peak_position"] = min(hist_peak, final_rank)
        out["times_at_peak"] = hist_times + (1 if final_rank <= hist_peak else 0)

    for snap in snapshot_entries:
        out = out_by_track.get(snap["track_id"])
        if not out:
            continue
        snap["peak_position"] = out["peak_position"]
        snap["times_at_peak"] = out["times_at_peak"]

    if dry_run:
        logger.log("[swift_top_100] DRY-RUN: no files written")
    else:
        combined_rows = existing_rows + out_entries
        combined_rows.sort(key=lambda r: ((r.get("date") or ""), int(r.get("rank") or 9999), r.get("track_id") or ""))
        _write_history_csv(combined_rows, logger)

        payload = {
            "title": "Swift Top 100",
            "chart_date": chart_date_str,
            "week_start": _format_date(week_start),
            "week_end": chart_date_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": snapshot_entries,
        }
        _write_snapshot_json(payload, logger)

        # Génération automatique de 4 images de 25 chansons
        try:
            from swift_top_100_image import render_png
            import shutil
            # Génération des images dans website/site/data/
            image_paths = []
            for i in range(4):
                out_path = _SITE_DATA_DIR / f"swift_top_100_{i+1}.png"
                render_png(
                    payload=payload,
                    output_path=out_path,
                    columns=1,
                    limit=25,
                    offset=i * 25,
                    width=1400,
                    scale=2,
                )
                logger.log(f"[swift_top_100] Wrote chart image: {out_path}")
                image_paths.append(out_path)
            # Copie dans collectors/billboard/history/<date>/
            history_dir = _SCRIPT_DIR / "history" / chart_date_str
            history_dir.mkdir(parents=True, exist_ok=True)
            for i, src in enumerate(image_paths, 1):
                dst = history_dir / f"swift_top_100_{i}.png"
                shutil.copy2(src, dst)
                logger.log(f"[swift_top_100] Copied image to: {dst}")
        except Exception as exc:
            logger.log(f"[swift_top_100] Image generation failed (skipped): {exc}")

        _maybe_upload_to_r2(logger=logger)

    # Save log
    logs_dir = _SCRIPT_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"swift_top_100_{chart_date_str}.log"
    logger.save(str(log_path))

    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Swift Top 100 weekly chart")
    p.add_argument("--date", dest="date", default=None, help="Week ending date (YYYY-MM-DD)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="Compute only; do not write files")
    # Suppression des options image, tout est généré par défaut
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    chart_date = _parse_iso_date(args.date) if args.date else None
    code = run(
        chart_date=chart_date,
        dry_run=bool(args.dry_run),
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
