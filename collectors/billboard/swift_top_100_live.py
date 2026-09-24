"""Swift Top 100 — LIVE in-progress-week projection (not the official chart).

Projects the current Friday->Thursday tracking week's final total_units
ranking from whatever days are actual so far, using day-of-week seasonality
+ trend momentum (see live_projection.py) for the remaining days. This is a
best-effort daily preview, never a substitute for swift_top_100.py's Thursday
official run — it writes to entirely separate files (see OUTPUTS below) and
never touches db/swift_top_100_history.csv, swift_top_100.json, or any other
official artifact.

Combined songs only in this script's own public output (not-combined dropped
from the live *product* 2026-09-23 — the official weekly swift_top_100.py
chart still has both variants, this script's own JSON/CSV does not).

Albums/eras (added 2026-09-23): `_build_live_variant(variant="not-combined",
include_album_agg=True, ...)` is reused internally (never written to any
file) by `collectors/billboard/swift_top_albums_live.py`, which feeds its
per-song entries into `swift_top_albums._build_album_week()` to produce the
live Albums/Eras projections. See that module's docstring for the full
pipeline; this file only exposes the `include_album_agg` opt-in on
`_build_live_variant` (default False, so this script's own combined run is
unaffected) that attaches the extra raw per-track fields
(`_album_agg`: weekly_streams/am_ts_score/am_overall_score/units_charts/
units_surplus/base_title/song_family) `_build_album_week` needs but which
aren't otherwise on a live entry.

Outputs (never the official swift_top_100*.{csv,json} paths):
- db/swift_top_100_live_history.csv, one row per (as_of_date, track_id)
- runtime/exports/web/site/data/swift_top_100_live.json (latest projection)
- runtime/exports/web/site/data/swift_top_100_live_YYYY-MM-DD.json (dated copy —
  required so scripts/r2.py's _collect_slug_tasks() glob `{slug}_????-??-??.json`
  picks it up)
- R2: scripts/r2.py::upload_slugs(["swift_top_100_live"])

Run:
  python collectors/billboard/swift_top_100_live.py
  python collectors/billboard/swift_top_100_live.py --date 2026-09-22
  python collectors/billboard/swift_top_100_live.py --dry-run
  python collectors/billboard/swift_top_100_live.py --skip-r2
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[1]

# Same import pattern as swift_top_combined.py / swift_top_seperate.py: add
# this directory so `import swift_top_100` resolves, then reuse its own
# sys.path setup (collectors/spotify/core) rather than duplicating it.
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import swift_top_100 as top100  # noqa: E402
import live_projection  # noqa: E402

_DB_DIR = top100._DB_DIR
_SITE_DATA_DIR = top100._SITE_DATA_DIR

LIVE_HISTORY_FIELDNAMES = [
    "as_of_date",
    "track_id",
    "title",
    "days_actual",
    "days_remaining",
    "total_units_actual_so_far",
    "total_units_projected_final",
    "rank_projected",
    "prev_rank",
    "rank_change",
    "change",
    "percentage_change",
    "weeks_on_chart",
    "peak_position",
    "times_at_peak",
    "points_projected_final",
    "units_spotify_actual",
    "units_spotify_projected",
    "units_am_actual",
    "units_am_projected",
    "units_youtube_actual",
    "units_youtube_projected",
    "units_am_ts",
    "units_am_overall",
    "units_spotify_charts",
    "units_spotify_streams",
    "units_am_ts_pct",
    "units_am_overall_pct",
    "units_spotify_charts_pct",
    "units_spotify_streams_pct",
    "units_youtube_pct",
    "confidence",
]

# Trailing window used to build each track's/key's own seasonality+momentum
# history (live_projection.py's "history_daily" input). ~8 weeks matches
# live_projection.TRAILING_WEEKS; kept as a separate constant here since this
# module also uses it to slice CSV/history scans, not just the in-memory dict.
HISTORY_WEEKS_BACK = 8


def _current_week_end(as_of: date) -> date:
    """Return the Thursday (weekday 3) ending the Fri->Thu week containing as_of."""
    days_until_thursday = (3 - as_of.weekday()) % 7
    return as_of + timedelta(days=days_until_thursday)


def _confidence_for(days_actual_count: int) -> str:
    if days_actual_count <= 2:
        return "early_estimate"
    if days_actual_count <= 5:
        return "firming_up"
    return "nearly_final"  # 6 (spec) and 7 (as_of == week's own Thursday)


def _merge_historical_ids(
    *, weekly_actual: dict[str, int], daily_actual: dict[str, dict[str, int]], tracks: dict
) -> None:
    """In-place: fold historical_track_ids into their current primary track id.

    Same logic as swift_top_100.py::_build_week_chart's historical-id merge
    step, restricted to the actual-so-far days only.
    """
    for meta in tracks.values():
        for h_id in meta.historical_track_ids:
            if h_id in daily_actual:
                h_daily = daily_actual.pop(h_id)
                primary_daily = daily_actual.setdefault(meta.track_id, {})
                for d, s in h_daily.items():
                    primary_daily[d] = max(primary_daily.get(d, 0), s)
                weekly_actual[meta.track_id] = sum(primary_daily.values())
                weekly_actual.pop(h_id, None)
            elif h_id in weekly_actual:
                weekly_actual[meta.track_id] = weekly_actual.get(meta.track_id, 0) + weekly_actual.pop(h_id)


def _merge_combined_versions(
    *,
    weekly_actual: dict[str, int],
    daily_actual: dict[str, dict[str, int]],
    tracks: dict,
    combine: bool,
) -> tuple[dict[str, int], dict[str, dict[str, int]], dict[str, list[str]]]:
    """Group same-song versions under one primary track_id (combined variant only).

    Returns (merged_weekly, merged_daily, group_members) where group_members
    maps primary_track_id -> every post-historical-fold track_id that was
    rolled into it (used afterwards to pull each version's own trailing
    history for the seasonality/momentum projection).
    """
    if not combine:
        return dict(weekly_actual), {k: dict(v) for k, v in daily_actual.items()}, {
            tid: [tid] for tid in weekly_actual
        }

    groups: dict[str, list[str]] = {}
    for tid in weekly_actual:
        meta = tracks.get(tid)
        if meta is None:
            continue
        key = top100._chart_lookup_key(
            meta.title, combined=True, base_title=meta.base_title, song_family=meta.song_family
        )
        if key:
            groups.setdefault(key, []).append(tid)

    merged_weekly: dict[str, int] = {}
    merged_daily: dict[str, dict[str, int]] = {}
    group_members: dict[str, list[str]] = {}
    for tids in groups.values():
        tids_sorted = sorted(tids, key=lambda t: weekly_actual.get(t, 0), reverse=True)
        primary = tids_sorted[0]
        total = 0
        day_sums: dict[str, int] = {}
        for t in tids_sorted:
            total += weekly_actual.get(t, 0)
            for day, v in daily_actual.get(t, {}).items():
                day_sums[day] = day_sums.get(day, 0) + v
        merged_weekly[primary] = total
        merged_daily[primary] = day_sums
        group_members[primary] = tids_sorted
    return merged_weekly, merged_daily, group_members


def _history_daily_for_tids(
    *,
    daily_by_track: dict[str, dict[str, int]],
    tids: list[str],
    tracks: dict,
    before_date_str: str,
) -> dict[str, int]:
    """Sum daily Spotify streams across every version/historical id in `tids`
    for dates strictly before `before_date_str`, over a trailing window."""
    cutoff = (date.fromisoformat(before_date_str) - timedelta(weeks=HISTORY_WEEKS_BACK)).isoformat()
    all_ids: set[str] = set()
    for tid in tids:
        all_ids.add(tid)
        meta = tracks.get(tid)
        if meta:
            all_ids.update(meta.historical_track_ids)
    combined: dict[str, int] = {}
    for t in all_ids:
        for day, v in daily_by_track.get(t, {}).items():
            if cutoff <= day < before_date_str:
                combined[day] = combined.get(day, 0) + v
    return combined


def _am_ts_with_floor(raw_value: float, floor_value: float) -> float:
    return raw_value if raw_value > 0 else floor_value


def _pct_change(curr: float, prev_raw: str | None) -> float | None:
    """% change vs a raw string field from a swift_top_100_history.csv row."""
    try:
        prev = float(prev_raw)
    except (TypeError, ValueError):
        return None
    if prev <= 0:
        return None
    return round(((curr - prev) / prev) * 100, 1)


def _max_date_across(*daily_by_key_dicts: dict[str, dict[str, float]]) -> str | None:
    latest: str | None = None
    for by_key in daily_by_key_dicts:
        for day_map in by_key.values():
            for day in day_map:
                if latest is None or day > latest:
                    latest = day
    return latest


def _build_live_variant(
    *,
    variant: str,
    as_of: date,
    week_start: date,
    week_end: date,
    days_actual: list[str],
    days_remaining: list[str],
    logger,
    include_album_agg: bool = False,
) -> dict:
    top100._configure_variant(variant)
    combine = top100.COMBINE_VERSIONS
    week_start_str = top100._format_date(week_start)
    day_list_full = [top100._format_date(week_start + timedelta(days=i)) for i in range(7)]
    week_set_full = set(day_list_full)

    tracks_list = top100._iter_discography_tracks()
    tracks = {t.track_id: t for t in tracks_list}
    logger.log(f"  discography    : {len(tracks)} tracks indexed")

    # Rank change / % change / peak / weeks-on-chart are computed against the
    # LAST COMPLETE official chart (not yesterday's live projection) — same
    # source and same fallback-by-title logic as swift_top_100.py's own
    # week-over-week comparison (run(), ~line 2097-2238), just anchored on
    # week_end (the upcoming Thursday) instead of a chart_date that has
    # already happened.
    existing_rows = top100._load_existing_history_before_date(top100._format_date(week_end), logger)
    weeks_on_chart_by_track, peak_by_track, times_at_peak_by_track = top100._history_stats(existing_rows)

    hist_tid_by_title: dict[str, str] = {}
    for _r in existing_rows:
        _k = top100._normalize_title(_r.get("title") or "")
        _t = (_r.get("track_id") or "").strip()
        if _k and _t and _k not in hist_tid_by_title:
            hist_tid_by_title[_k] = _t

    prev_week_end = week_end - timedelta(days=7)
    prev_week_str = top100._format_date(prev_week_end)
    prev_week_rows = [r for r in existing_rows if (r.get("date") or "").strip() == prev_week_str]
    prev_ranks: dict[str, int] = {
        r["track_id"]: int(r["rank"]) for r in prev_week_rows if r.get("track_id") and r.get("rank")
    }
    prev_total_units_by_track: dict[str, int] = {
        r["track_id"]: int(r["total_units"]) for r in prev_week_rows if r.get("track_id") and r.get("total_units")
    }
    prev_tid_by_title: dict[str, str] = {
        top100._normalize_title(r.get("title") or ""): r["track_id"]
        for r in prev_week_rows
        if r.get("track_id") and top100._normalize_title(r.get("title") or "")
    }
    prev_row_by_track: dict[str, dict] = {
        r["track_id"]: r for r in prev_week_rows if r.get("track_id")
    }
    logger.log(
        f"  last_official  : {prev_week_str} ({len(prev_week_rows)} tracks)"
        if prev_week_rows else f"  last_official  : no published chart for {prev_week_str}"
    )

    daily_by_track, counts_by_date = top100._load_stream_daily_cache()

    # Actual-so-far Spotify streams: scan the WHOLE week (day_list_full), not
    # just calendar days_actual (<=as_of) — a collector can lag behind as_of
    # by a day or two (e.g. Spotify's own snapshot for yesterday isn't in yet
    # even though today already is), and days_actual silently dropped those
    # lagging-but-past days entirely instead of treating them as needing
    # projection, which undercounted every track (found 2026-09-23: 2 days of
    # real Spotify volume were missing from "actual" and never projected
    # either, since only days strictly after as_of were ever projected).
    # Real data for genuinely future days simply won't exist in the CSV, so
    # scanning the full week is always safe. Never uses
    # _aggregate_weekly_streams's missing-day estimator (capped at 2 days,
    # meant for small retroactive gaps in an already-finished week).
    weekly_actual: dict[str, int] = {}
    daily_actual: dict[str, dict[str, int]] = {}
    for tid, points in daily_by_track.items():
        for day in day_list_full:
            v = points.get(day)
            if v is None:
                continue
            weekly_actual[tid] = weekly_actual.get(tid, 0) + v
            daily_actual.setdefault(tid, {})[day] = v

    _merge_historical_ids(weekly_actual=weekly_actual, daily_actual=daily_actual, tracks=tracks)

    # Real latest Spotify data date (may be behind as_of) — using `as_of`
    # itself here would silently no-op the merge dedup on any day the
    # streams collector hasn't run yet yet (no row for that exact date to
    # detect the merge from), letting a merged-away track_id (e.g. Shake It
    # Off "Best Work Edition", Love Story "Pop Mix") re-surface as a bogus
    # duplicate chart entry (found 2026-09-23).
    spotify_data_as_of = max((d for d in day_list_full if counts_by_date.get(d, 0) > 0), default=None)
    try:
        merge_check_date = date.fromisoformat(spotify_data_as_of) if spotify_data_as_of else as_of
        merged_losers = top100._currently_merged_track_ids(chart_date=merge_check_date, tracks=tracks, logger=logger)
    except Exception as exc:
        logger.log(f"  merge_dedup    : skipped — {exc}")
        merged_losers = set()
    for tid in merged_losers:
        weekly_actual.pop(tid, None)
        daily_actual.pop(tid, None)

    merged_weekly, merged_daily, group_members = _merge_combined_versions(
        weekly_actual=weekly_actual, daily_actual=daily_actual, tracks=tracks, combine=combine
    )

    spotify_missing_dates = [
        date.fromisoformat(d) for d in day_list_full if counts_by_date.get(d, 0) == 0
    ]
    logger.log(
        f"  streams        : {len(merged_weekly)} songs · "
        f"{len(day_list_full) - len(spotify_missing_dates)}/{len(day_list_full)} actual day(s) so far"
    )

    # Platform per-day breakdowns, actual-so-far and trailing history (for
    # weekday-seasonality + momentum), one CSV/JSON scan pass each.
    history_start = (week_start - timedelta(weeks=HISTORY_WEEKS_BACK))
    history_days = {
        top100._format_date(history_start + timedelta(days=i))
        for i in range((week_start - history_start).days)
    }

    # Scan the WHOLE week (week_set_full), same reasoning as Spotify above —
    # a source can be behind as_of; restricting to days_actual_set silently
    # dropped those lagging days from "actual" (and they were never projected
    # either, since project targets were also as_of-derived), undercounting
    # every eligible track. Real future days simply return no rows.
    am_ts_actual, am_ts_daily_actual = top100._weekly_apple_music_ts_points(
        week_dates=week_set_full, logger=logger, return_daily=True
    )
    am_global_actual, am_global_daily_actual = top100._weekly_apple_music_global_points(
        week_dates=week_set_full, logger=logger, return_daily=True
    )
    am_country_actual, am_country_daily_actual = top100._weekly_apple_music_country_points(
        week_dates=week_set_full, logger=logger, return_daily=True
    )
    am_genre_actual, am_genre_daily_actual = top100._weekly_apple_music_genre_points(
        week_dates=week_set_full, logger=logger, return_daily=True
    )
    youtube_actual, youtube_daily_actual = top100._weekly_youtube_views(
        week_dates=week_set_full, logger=logger, return_daily=True
    )
    charts_actual, charts_daily_actual = top100._weekly_charts_streams_by_title(
        week_dates=week_set_full, tracks=tracks, logger=logger, return_daily=True
    )

    apple_music_data_as_of = _max_date_across(
        am_ts_daily_actual, am_global_daily_actual, am_country_daily_actual, am_genre_daily_actual
    )
    youtube_data_as_of = _max_date_across(youtube_daily_actual)
    charts_data_as_of = _max_date_across(charts_daily_actual)

    # Per-platform "still needs projecting" dates, each anchored on that
    # platform's OWN real latest date — not a single blanket days_remaining
    # derived from as_of. A platform behind as_of gets its lagging days
    # projected too, not silently treated as zero.
    def _missing_dates(data_as_of: str | None) -> list[date]:
        if not data_as_of:
            return [date.fromisoformat(d) for d in day_list_full]
        return [date.fromisoformat(d) for d in day_list_full if d > data_as_of]

    am_remaining_dates = _missing_dates(apple_music_data_as_of)
    youtube_remaining_dates = _missing_dates(youtube_data_as_of)
    charts_remaining_dates = _missing_dates(charts_data_as_of)
    need_projection = bool(am_remaining_dates or youtube_remaining_dates or charts_remaining_dates)

    if need_projection:
        _, am_ts_hist = top100._weekly_apple_music_ts_points(
            week_dates=history_days, logger=logger, return_daily=True
        )
        _, am_global_hist = top100._weekly_apple_music_global_points(
            week_dates=history_days, logger=logger, return_daily=True
        )
        _, am_country_hist = top100._weekly_apple_music_country_points(
            week_dates=history_days, logger=logger, return_daily=True
        )
        _, am_genre_hist = top100._weekly_apple_music_genre_points(
            week_dates=history_days, logger=logger, return_daily=True
        )
        _, youtube_hist = top100._weekly_youtube_views(
            week_dates=history_days, logger=logger, return_daily=True
        )
        _, charts_hist = top100._weekly_charts_streams_by_title(
            week_dates=history_days, tracks=tracks, logger=logger, return_daily=True
        )
    else:
        am_ts_hist = am_global_hist = am_country_hist = am_genre_hist = youtube_hist = charts_hist = {}

    am_actual_days = {d for d in day_list_full if apple_music_data_as_of and d <= apple_music_data_as_of}
    floor_actual = top100._apple_music_ts_floor_score(am_actual_days)
    floor_final = top100._apple_music_ts_floor_score(week_set_full)

    entries = []
    eff_tid_by_primary: dict[str, str] = {}
    for primary, wk_actual in merged_weekly.items():
        if wk_actual <= 0:
            continue
        meta = tracks.get(primary)
        if meta is None:
            continue
        eligible = meta.apple_music_floor_eligible
        key = top100._chart_lookup_key(
            meta.title, combined=combine, base_title=meta.base_title, song_family=meta.song_family
        )

        # ---- vs. last complete official chart (swift_top_100.py:2156-2178 pattern) ----
        title_key = top100._normalize_title(meta.title or meta.base_title or "")
        pr = prev_ranks.get(primary)
        alt_prev_tid = prev_tid_by_title.get(title_key) if eligible else None
        if pr is None and alt_prev_tid and alt_prev_tid != primary:
            pr = prev_ranks.get(alt_prev_tid)
        alt_hist_tid = hist_tid_by_title.get(title_key) if eligible else None
        eff_tid = primary if primary in weeks_on_chart_by_track else (alt_hist_tid or primary)
        eff_tid_by_primary[primary] = eff_tid
        change = ("RE" if weeks_on_chart_by_track.get(eff_tid, 0) > 0 else "NEW") if pr is None else None
        weeks_on_chart = weeks_on_chart_by_track.get(eff_tid, 0) + 1
        prev_total_units_val = prev_total_units_by_track.get(primary) or (
            prev_total_units_by_track.get(alt_prev_tid) if alt_prev_tid else None
        )

        # ---- actual-so-far raw platform sums ----
        am_ts_raw_actual = am_ts_actual.get(key, 0.0) if eligible else 0.0
        am_global_raw_actual = am_global_actual.get(key, 0.0) if eligible else 0.0
        am_country_raw_actual = (
            am_country_actual.get(key, 0.0) + am_genre_actual.get(key, 0.0) if eligible else 0.0
        )
        am_overall_raw_actual = am_global_raw_actual + am_country_raw_actual
        yt_actual = youtube_actual.get(key, 0)
        raw_charts_actual = charts_actual.get(key, 0)

        actual_units = top100.compute_track_units(
            weekly_streams=wk_actual,
            raw_units_charts=raw_charts_actual,
            am_ts_raw=_am_ts_with_floor(am_ts_raw_actual, floor_actual) if eligible else 0.0,
            am_overall_raw=am_overall_raw_actual,
            weekly_youtube_views=yt_actual,
        )

        # ---- project remaining days per platform, then combine for the final total ----
        tids_in_group = group_members.get(primary, [primary])
        spotify_history = _history_daily_for_tids(
            daily_by_track=daily_by_track, tids=tids_in_group, tracks=tracks, before_date_str=week_start_str
        )
        spotify_projected_by_day = live_projection.project_remaining_days(
            merged_daily.get(primary, {}), spotify_missing_dates, spotify_history
        )
        spotify_projected_total = round(sum(spotify_projected_by_day.values()))
        wk_final = wk_actual + spotify_projected_total

        def _project_sum(actual_daily_by_key, history_by_key, remaining_dates):
            actual_daily = actual_daily_by_key.get(key, {})
            history = history_by_key.get(key, {})
            projected = live_projection.project_remaining_days(actual_daily, remaining_dates, history)
            return sum(projected.values())

        am_ts_projected_sum = _project_sum(am_ts_daily_actual, am_ts_hist, am_remaining_dates) if eligible else 0.0
        am_global_projected_sum = _project_sum(am_global_daily_actual, am_global_hist, am_remaining_dates) if eligible else 0.0
        am_country_projected_sum = (
            _project_sum(am_country_daily_actual, am_country_hist, am_remaining_dates)
            + _project_sum(am_genre_daily_actual, am_genre_hist, am_remaining_dates)
        ) if eligible else 0.0
        yt_projected_sum = _project_sum(youtube_daily_actual, youtube_hist, youtube_remaining_dates)
        charts_projected_sum = _project_sum(charts_daily_actual, charts_hist, charts_remaining_dates)

        am_ts_raw_final = am_ts_raw_actual + am_ts_projected_sum
        am_overall_raw_final = am_overall_raw_actual + am_global_projected_sum + am_country_projected_sum
        yt_final = yt_actual + yt_projected_sum
        raw_charts_final = raw_charts_actual + charts_projected_sum

        am_ts_raw_final_floored = _am_ts_with_floor(am_ts_raw_final, floor_final) if eligible else 0.0

        final_units = top100.compute_track_units(
            weekly_streams=wk_final,
            raw_units_charts=round(raw_charts_final),
            am_ts_raw=am_ts_raw_final_floored,
            am_overall_raw=am_overall_raw_final,
            weekly_youtube_views=round(yt_final),
        )

        # Sub-unit breakdown for the table's AM TS/Overall and Spotify
        # Charts/Streams columns — same weighted-display formula as the
        # official chart's am_ts_units_display/am_global_units_display/
        # units_charts_display/units_surplus_display (swift_top_100.py:2301-2307).
        units_am_ts = round(am_ts_raw_final_floored * 1000 * top100.AM_WEIGHT)
        units_am_overall = round(am_overall_raw_final * 1000 * top100.AM_WEIGHT)
        units_spotify_charts = round(final_units["units_charts"] * top100.SPOTIFY_WEIGHT)
        units_spotify_streams = round(final_units["units_surplus"] * top100.SPOTIFY_WEIGHT)

        percentage_change = None
        if pr is not None and prev_total_units_val and prev_total_units_val > 0:
            percentage_change = round(
                ((final_units["total_units"] - prev_total_units_val) / prev_total_units_val) * 100, 1
            )

        # Per-unit % change vs the same last official chart row — % is scale
        # invariant so raw-vs-raw (am_ts_score/am_overall_score/units_charts/
        # units_surplus, all stored unweighted in the history CSV) matches
        # weighted-vs-weighted exactly; units_youtube is stored pre-weighted
        # in the CSV so compared directly against the weighted current value.
        prev_row = prev_row_by_track.get(primary) or (
            prev_row_by_track.get(alt_prev_tid) if alt_prev_tid else None
        )
        if pr is not None and prev_row:
            units_am_ts_pct = _pct_change(am_ts_raw_final_floored, prev_row.get("am_ts_score"))
            units_am_overall_pct = _pct_change(am_overall_raw_final, prev_row.get("am_overall_score"))
            units_spotify_charts_pct = _pct_change(final_units["units_charts"], prev_row.get("units_charts"))
            units_spotify_streams_pct = _pct_change(final_units["units_surplus"], prev_row.get("units_surplus"))
            units_youtube_pct = _pct_change(final_units["units_youtube"], prev_row.get("units_youtube"))
        else:
            units_am_ts_pct = units_am_overall_pct = None
            units_spotify_charts_pct = units_spotify_streams_pct = None
            units_youtube_pct = None

        entries.append(
            {
                "track_id": primary,
                "title": meta.base_title or meta.title,
                "primary_album": meta.primary_album,
                "spotify_url": meta.spotify_url,
                "image_url": meta.image_url,
                "days_actual": len(days_actual),
                "days_remaining": len(days_remaining),
                "total_units_actual_so_far": actual_units["total_units"],
                "total_units_projected_final": final_units["total_units"],
                "points_projected_final": round(final_units["total_units"] / 100_000, 1),
                "prev_rank": pr,
                "change": change,
                "percentage_change": percentage_change,
                "weeks_on_chart": weeks_on_chart,
                "units_spotify_actual": actual_units["units_spotify"],
                "units_spotify_projected": final_units["units_spotify"] - actual_units["units_spotify"],
                "units_am_actual": actual_units["units_am"],
                "units_am_projected": final_units["units_am"] - actual_units["units_am"],
                "units_youtube_actual": actual_units["units_youtube"],
                "units_youtube_projected": final_units["units_youtube"] - actual_units["units_youtube"],
                "units_am_ts": units_am_ts,
                "units_am_overall": units_am_overall,
                "units_spotify_charts": units_spotify_charts,
                "units_spotify_streams": units_spotify_streams,
                "units_am_ts_pct": units_am_ts_pct,
                "units_am_overall_pct": units_am_overall_pct,
                "units_spotify_charts_pct": units_spotify_charts_pct,
                "units_spotify_streams_pct": units_spotify_streams_pct,
                "units_youtube_pct": units_youtube_pct,
                "confidence": _confidence_for(len(days_actual)),
            }
        )
        if include_album_agg:
            # Internal-only aggregation fields consumed by
            # swift_top_albums_live.py to feed swift_top_albums._build_album_week().
            # Never exposed in the public not-combined product (dropped
            # 2026-09-23) and never present on the combined variant's public
            # JSON — only attached when a caller explicitly opts in, and this
            # entries list is never itself written to a snapshot file.
            entries[-1]["_album_agg"] = {
                "weekly_streams": wk_final,
                "am_ts_score": round(am_ts_raw_final_floored, 2),
                "am_overall_score": round(am_overall_raw_final, 2),
                "units_charts": final_units["units_charts"],
                "units_surplus": final_units["units_surplus"],
                "base_title": meta.base_title,
                "song_family": meta.song_family,
            }

    entries.sort(key=lambda e: (e["total_units_projected_final"], e["track_id"]), reverse=True)
    for i, e in enumerate(entries, 1):
        e["rank_projected"] = i
        pr = e.get("prev_rank")
        e["rank_change"] = (pr - i) if pr is not None else None
        eff_tid = eff_tid_by_primary.get(e["track_id"], e["track_id"])
        hist_peak = peak_by_track.get(eff_tid, 9999)
        hist_times = times_at_peak_by_track.get(eff_tid, 0)
        e["peak_position"] = min(hist_peak, i)
        if i < hist_peak:
            e["times_at_peak"] = 1
        elif i == hist_peak:
            e["times_at_peak"] = hist_times + 1
        else:
            e["times_at_peak"] = hist_times

    logger.log(f"  live_rank      : {len(entries)} songs ranked ({variant})")

    # Match the official chart's Top 100 cap (swift_top_100.py:2395,
    # `top_snapshot_entries = snapshot_entries[:100]`) — rank/rank_change
    # above are computed against the full ranked pool, only the exported
    # list is truncated. Skipped when include_album_agg=True: that caller
    # (swift_top_albums_live.py) needs the FULL scored pool, matching how
    # the official pipeline feeds swift_top_albums.py from the UNCAPPED
    # swift_top_100_not_combined_songs_history.csv (full_song_rows in
    # swift_top_100.py::run(), not the capped top_snapshot_entries) — capping
    # here would silently undercount every album/era whose songs aren't all
    # in the combined-chart top 100.
    if not include_album_agg:
        entries = entries[:100]

    return {
        "entries": entries,
        "data_as_of": {
            "spotify": spotify_data_as_of,
            "apple_music": apple_music_data_as_of,
            "youtube": youtube_data_as_of,
        },
    }


def _write_live_history(history_path: Path, as_of_str: str, rows: list[dict], logger) -> None:
    existing = []
    if history_path.exists():
        try:
            with history_path.open("r", newline="", encoding="utf-8-sig") as f:
                existing = list(csv.DictReader(f))
        except Exception as exc:
            logger.log(f"  history        : failed to read existing CSV — {exc}")
    combined = [r for r in existing if (r.get("as_of_date") or "").strip() != as_of_str] + rows
    combined.sort(key=lambda r: ((r.get("as_of_date") or ""), int(r.get("rank_projected") or 9999)))
    top100._atomic_write_csv(history_path, LIVE_HISTORY_FIELDNAMES, combined)
    logger.log(f"OK CSV -> {history_path.name} ({len(combined)} rows)")


def _write_live_snapshot(output_json_path: Path, payload: dict, logger) -> None:
    _SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    as_of_str = payload["as_of_date"]
    dated_path = output_json_path.with_name(f"{output_json_path.stem}_{as_of_str}.json")
    top100._atomic_write_text(dated_path, content)
    logger.log(f"OK JSON -> {dated_path.name}")
    top100._atomic_write_text(output_json_path, content)
    logger.log(f"OK JSON -> {output_json_path.name} (latest)")


def _maybe_upload_to_r2_live(*, logger, slugs: list[str]) -> None:
    """Best-effort R2 upload, mirroring swift_top_100.py::_maybe_upload_to_r2
    (same import pattern, same never-fatal try/except) — without the
    highlights-cache regen step, since the live projection isn't part of the
    Charts Gallery highlights source."""
    logger.log("  r2             : uploading...")
    try:
        scripts_dir = str(_REPO_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import r2 as _r2
        ok = _r2.upload_slugs(slugs)
        if ok:
            logger.log("OK r2             : upload complete")
        else:
            logger.log("  r2             : skipped (credentials / config)")
    except Exception as exc:
        logger.log(f"WARN r2             : upload failed — {exc}")


def run(*, as_of: date, dry_run: bool, skip_r2: bool) -> int:
    logger = top100.Logger()
    week_end = _current_week_end(as_of)
    week_start, day_list = top100._week_dates(week_end)
    as_of_str = top100._format_date(as_of)
    days_actual = [d for d in day_list if d <= as_of_str]
    days_remaining = [d for d in day_list if d > as_of_str]

    logger.log(
        f"LIVE TayBoard TOP 100 (in-progress) · as_of={as_of_str} "
        f"week={top100._format_date(week_start)}->{top100._format_date(week_end)} "
        f"actual={len(days_actual)}d remaining={len(days_remaining)}d"
    )

    # Not-combined variant was dropped from the live projection product-wide
    # (2026-09-23, Anas) — combined-only, matching the simplified frontend.
    outputs = [
        ("combined", _DB_DIR / "swift_top_100_live_history.csv", _SITE_DATA_DIR / "swift_top_100_live.json", "swift_top_100_live"),
    ]

    for variant, history_path, output_json_path, slug in outputs:
        logger.log(f"-- variant: {variant} --")
        result = _build_live_variant(
            variant=variant,
            as_of=as_of,
            week_start=week_start,
            week_end=week_end,
            days_actual=days_actual,
            days_remaining=days_remaining,
            logger=logger,
        )
        entries = result["entries"]
        if not entries:
            logger.log("WARN abort          : no entries generated for this variant; skipping write")
            continue

        if dry_run:
            logger.log("WARN DRY-RUN — no files written")
            continue

        history_rows = [
            {
                "as_of_date": as_of_str,
                "track_id": e["track_id"],
                "title": e["title"],
                "days_actual": e["days_actual"],
                "days_remaining": e["days_remaining"],
                "total_units_actual_so_far": e["total_units_actual_so_far"],
                "total_units_projected_final": e["total_units_projected_final"],
                "points_projected_final": e["points_projected_final"],
                "rank_projected": e["rank_projected"],
                "prev_rank": e["prev_rank"],
                "rank_change": e["rank_change"],
                "change": e["change"],
                "percentage_change": e["percentage_change"],
                "weeks_on_chart": e["weeks_on_chart"],
                "peak_position": e["peak_position"],
                "times_at_peak": e["times_at_peak"],
                "units_spotify_actual": e["units_spotify_actual"],
                "units_spotify_projected": e["units_spotify_projected"],
                "units_am_actual": e["units_am_actual"],
                "units_am_projected": e["units_am_projected"],
                "units_youtube_actual": e["units_youtube_actual"],
                "units_youtube_projected": e["units_youtube_projected"],
                "units_am_ts": e["units_am_ts"],
                "units_am_overall": e["units_am_overall"],
                "units_spotify_charts": e["units_spotify_charts"],
                "units_spotify_streams": e["units_spotify_streams"],
                "units_am_ts_pct": e["units_am_ts_pct"],
                "units_am_overall_pct": e["units_am_overall_pct"],
                "units_spotify_charts_pct": e["units_spotify_charts_pct"],
                "units_spotify_streams_pct": e["units_spotify_streams_pct"],
                "units_youtube_pct": e["units_youtube_pct"],
                "confidence": e["confidence"],
            }
            for e in entries
        ]
        _write_live_history(history_path, as_of_str, history_rows, logger)

        payload = {
            "title": "TayBoard TOP 100 — Live Projection",
            "as_of_date": as_of_str,
            "week_start": top100._format_date(week_start),
            "week_end": top100._format_date(week_end),
            "days_actual": len(days_actual),
            "days_remaining": len(days_remaining),
            "data_as_of": result["data_as_of"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": entries,
        }
        _write_live_snapshot(output_json_path, payload, logger)

    if not dry_run and not skip_r2:
        _maybe_upload_to_r2_live(logger=logger, slugs=["swift_top_100_live"])
    elif not dry_run:
        logger.log("  r2             : skipped (--skip-r2)")

    logs_dir = _SCRIPT_DIR / "logs"
    if not dry_run:
        logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            logger.save(str(logs_dir / f"swift_top_100_live_{as_of_str}.log"))
        except OSError as exc:
            print(f"[swift_top_100_live] Warning: could not save log: {exc}")

    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live in-progress-week TayBoard TOP 100 projection")
    p.add_argument("--date", dest="date", default=None, help="As-of date (YYYY-MM-DD); default today")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="Compute only; do not write files")
    p.add_argument("--skip-r2", dest="skip_r2", action="store_true", help="Do not upload generated files to R2")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    as_of = top100._parse_iso_date(args.date) if args.date else date.today()
    if as_of is None:
        print(f"[swift_top_100_live] Invalid --date: {args.date}")
        raise SystemExit(2)
    raise SystemExit(run(as_of=as_of, dry_run=bool(args.dry_run), skip_r2=bool(args.skip_r2)))


if __name__ == "__main__":
    main()
