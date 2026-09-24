"""TayBoard Albums/Eras — LIVE in-progress-week projection (not the official chart).

Added 2026-09-23 as the deferred follow-up documented in
swift_top_100_live.py / .claude/skills/collector-billboard/CONTEXTE.md
"Live projection" section (albums/eras were shipped songs-only first under
time pressure). Reuses the exact same day-of-week-seasonality/momentum
projection machinery as the songs live script — this module adds no new
projection math at all, it only aggregates already-projected song-level
numbers up to album/era granularity.

Pipeline:
1. `swift_top_100_live._build_live_variant(variant="not-combined",
   include_album_agg=True, ...)` computes the in-progress week's projected
   final numbers for every song, **not-combined** (versions/TV kept separate,
   matching the source `swift_top_100_not_combined_songs_history.csv` shape
   that the official `swift_top_albums.py` reads) — same actual/projected
   day-alignment logic as the combined variant, reused unchanged. This is
   purely an in-memory intermediate: never written to any file, never the
   old public not-combined live JSON/CSV (removed from the product
   2026-09-23 — not resurrected here).
2. Each not-combined live entry is reshaped into a `swift_top_100_not_combined
   _songs_history.csv`-row-shaped dict, with every numeric field stringified
   (`swift_top_albums._build_album_week()` does `(row.get(field) or "").strip()`
   internally via its `_to_int`/`_to_float`/`_score_to_units` helpers, so it
   expects `csv.DictReader`-shaped string values, not native numbers).
3. `swift_top_albums._build_album_week(chart_date=<as_of tag>, song_rows=...,
   track_to_album=..., albums_by_id=..., track_meta_by_id=...)` (the exact
   function the official weekly Albums/Eras chart uses) aggregates those rows
   into per-album/era totals + per-song breakdown, keyed on an arbitrary
   `chart_date` tag string (here: the as-of date) since the function only
   ever compares that tag against the `date` field on the rows we hand it.
4. `rank_change`/`percentage_change`/`peak_position`/`times_at_peak`/
   `weeks_on_chart`/`change` (NEW/RE) are computed against the **last
   published official** `swift_top_albums_history.csv`/`swift_top_eras_history
   .csv` row per album/era — same "vs. last complete official chart" pattern
   already used by swift_top_100_live.py for songs, just keyed by `album_id`
   instead of `track_id` (no title-fallback matching needed: unlike track_id,
   which can change between a song's versions, `album_id` is a stable slug of
   the album/era title, see `swift_top_albums._normalize_album_id`).
   `_build_album_week()`'s own prev-week diffing is NOT used for this (it
   expects a previous week's rows in the same `song_rows` list, which don't
   exist for live data) — mirrors `swift_top_100_live.py`'s comment on the
   same subject.
5. Confidence bucket per entry reuses
   `swift_top_100_live._confidence_for(days_actual)` verbatim.

Outputs (never the official swift_top_albums*/swift_top_eras*.{csv,json} paths):
- db/swift_top_albums_live_history.csv, db/swift_top_eras_live_history.csv —
  one row per (as_of_date, album_id).
- runtime/exports/web/site/data/swift_top_albums_live.json (+ dated copy),
  runtime/exports/web/site/data/swift_top_eras_live.json (+ dated copy).
- R2: scripts/r2.py::upload_slugs(["swift_top_albums_live", "swift_top_eras_live"]).
- No git commit, no PNG image generation (live preview only).

Run:
  python collectors/billboard/swift_top_albums_live.py
  python collectors/billboard/swift_top_albums_live.py --date 2026-09-22
  python collectors/billboard/swift_top_albums_live.py --dry-run
  python collectors/billboard/swift_top_albums_live.py --skip-r2
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

if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import swift_top_100 as top100  # noqa: E402
import swift_top_100_live as songs_live  # noqa: E402
import swift_top_albums as albums_engine  # noqa: E402

_DB_DIR = top100._DB_DIR
_SITE_DATA_DIR = top100._SITE_DATA_DIR

ALBUM_LIVE_HISTORY_FIELDNAMES = [
    "as_of_date",
    "album_id",
    "title",
    "weekly_streams",
    "units_am",
    "units_am_ts",
    "units_am_overall",
    "units_youtube",
    "units_spotify",
    "units_charts",
    "units_surplus",
    "total_units",
    "track_count",
    "points_projected_final",
    "rank_projected",
    "prev_rank",
    "rank_change",
    "change",
    "percentage_change",
    "weeks_on_chart",
    "peak_position",
    "times_at_peak",
    "confidence",
]

_VARIANTS = (
    ("albums", "TayBoard Albums — Live Projection", _DB_DIR / "swift_top_albums_live_history.csv",
     _SITE_DATA_DIR / "swift_top_albums_live.json", "swift_top_albums_live"),
    ("eras", "TayBoard Eras — Live Projection", _DB_DIR / "swift_top_eras_live_history.csv",
     _SITE_DATA_DIR / "swift_top_eras_live.json", "swift_top_eras_live"),
)


def _build_not_combined_song_rows(
    *,
    as_of: date,
    week_start: date,
    week_end: date,
    days_actual: list[str],
    days_remaining: list[str],
    date_tag: str,
    logger,
) -> tuple[list[dict], dict]:
    """In-memory not-combined live song rows, shaped for `_build_album_week`.

    Never written to any file/JSON of its own — purely an intermediate for
    the album/era aggregation below. See module docstring step 1-2.
    """
    result = songs_live._build_live_variant(
        variant="not-combined",
        as_of=as_of,
        week_start=week_start,
        week_end=week_end,
        days_actual=days_actual,
        days_remaining=days_remaining,
        logger=logger,
        include_album_agg=True,
    )

    rows: list[dict] = []
    for e in result["entries"]:
        agg = e.get("_album_agg") or {}
        units_am = e["units_am_actual"] + e["units_am_projected"]
        units_youtube = e["units_youtube_actual"] + e["units_youtube_projected"]
        units_spotify = e["units_spotify_actual"] + e["units_spotify_projected"]
        rows.append(
            {
                "track_id": e["track_id"],
                "date": date_tag,
                "weekly_streams": str(agg.get("weekly_streams", 0)),
                "units_am": str(units_am),
                "am_ts_score": str(agg.get("am_ts_score", 0)),
                "am_overall_score": str(agg.get("am_overall_score", 0)),
                "units_youtube": str(units_youtube),
                "units_spotify": str(units_spotify),
                "units_charts": str(agg.get("units_charts", 0)),
                "units_surplus": str(agg.get("units_surplus", 0)),
                "song_family": agg.get("song_family") or "",
                "title_clean": "",
                "base_title": agg.get("base_title") or "",
                "title": e["title"],
                "rank": str(e["rank_projected"]),
                "points": str(e["points_projected_final"]),
                "total_units": str(e["total_units_projected_final"]),
                "change": e.get("change") or "",
                "rank_change": "" if e.get("rank_change") is None else str(e["rank_change"]),
                "percentage_change": "" if e.get("percentage_change") is None else str(e["percentage_change"]),
                "weeks_on_chart": "" if e.get("weeks_on_chart") is None else str(e["weeks_on_chart"]),
                "peak_position": "" if e.get("peak_position") is None else str(e["peak_position"]),
                "times_at_peak": "" if e.get("times_at_peak") is None else str(e["times_at_peak"]),
            }
        )
    return rows, result["data_as_of"]


def _build_album_variant_entries(
    *,
    variant: str,
    song_rows: list[dict],
    date_tag: str,
    week_end: date,
    days_actual: list[str],
    logger,
) -> list[dict]:
    albums_engine._configure_variant(variant)

    albums = albums_engine._augment_era_albums_with_matched_extras(
        albums_engine._albums_for_chart_variant(albums_engine._iter_discography_albums())
    )
    albums_by_id = {a.album_id: a for a in albums}
    logger.log(
        f"  discography    : {len(albums)} {variant} indexed, "
        f"{sum(len(a.track_ids) for a in albums)} tracks"
    )

    track_to_album: dict[str, albums_engine.AlbumMeta] = {}
    for album in albums:
        for tid in album.track_ids:
            if tid not in track_to_album:
                track_to_album[tid] = album

    track_meta_by_id = {t.track_id: t for t in top100._iter_discography_tracks()}

    curr_scored, curr_ranks, curr_songs_by_album = albums_engine._build_album_week(
        chart_date=date_tag,
        song_rows=song_rows,
        track_to_album=track_to_album,
        albums_by_id=albums_by_id,
        logger=logger,
        track_meta_by_id=track_meta_by_id,
    )

    # vs. last complete OFFICIAL chart for this variant (swift_top_albums_history.csv
    # or swift_top_eras_history.csv, whichever _configure_variant just pointed
    # SWIFT_TOP_ALBUMS_HISTORY_CSV at) — same idea as swift_top_100_live.py's
    # songs comparison, one level up, keyed by album_id (stable, no title
    # fallback needed).
    week_end_str = albums_engine._format_date(week_end)
    existing_rows = albums_engine._load_existing_history(logger)
    prior_rows = albums_engine._history_rows_before_date(existing_rows, week_end_str)
    weeks_on_chart_by_album, peak_by_album, times_at_peak_by_album = albums_engine._history_stats(prior_rows)

    prev_week_end = week_end - timedelta(days=7)
    prev_week_str = albums_engine._format_date(prev_week_end)
    prev_week_rows = [r for r in prior_rows if (r.get("date") or "").strip() == prev_week_str]
    prev_ranks: dict[str, int] = {
        r["album_id"]: int(r["rank"]) for r in prev_week_rows if r.get("album_id") and r.get("rank")
    }
    prev_total_units_by_album: dict[str, int] = {
        r["album_id"]: int(r["total_units"]) for r in prev_week_rows if r.get("album_id") and r.get("total_units")
    }
    logger.log(
        f"  last_official  : {prev_week_str} ({len(prev_week_rows)} {variant})"
        if prev_week_rows else f"  last_official  : no published chart for {prev_week_str}"
    )

    confidence = songs_live._confidence_for(len(days_actual))

    entries: list[dict] = []
    for aid, rank in sorted(curr_ranks.items(), key=lambda kv: kv[1]):
        row = curr_scored[aid]
        meta = albums_by_id.get(aid)

        pr = prev_ranks.get(aid)
        prev_units_val = prev_total_units_by_album.get(aid)
        change = None
        if pr is None:
            change = "RE" if weeks_on_chart_by_album.get(aid, 0) > 0 else "NEW"

        percentage_change = None
        if pr is not None and prev_units_val and prev_units_val > 0:
            percentage_change = round(((row["total_units"] - prev_units_val) / prev_units_val) * 100, 1)

        weeks_on_chart = weeks_on_chart_by_album.get(aid, 0) + 1
        hist_peak = peak_by_album.get(aid, 9999)
        hist_times = times_at_peak_by_album.get(aid, 0)
        peak_position = min(hist_peak, rank)
        if rank < hist_peak:
            times_at_peak = 1
        elif rank == hist_peak:
            times_at_peak = hist_times + 1
        else:
            times_at_peak = hist_times

        entries.append(
            {
                "album_id": aid,
                "title": meta.title if meta else aid,
                "image_url": meta.cover_url if meta else None,
                "spotify_url": meta.spotify_url if meta else None,
                "weekly_streams": row["weekly_streams"],
                "units_am": row["units_am"],
                "units_am_ts": row.get("units_am_ts", 0),
                "units_am_overall": row.get("units_am_overall", 0),
                "units_youtube": row.get("units_youtube", 0),
                "units_spotify": row["units_spotify"],
                "units_charts": row["units_charts"],
                "units_surplus": row["units_surplus"],
                "total_units": row["total_units"],
                "track_count": row["track_count"],
                "points_projected_final": round(row["total_units"] / 100_000, 1),
                "rank_projected": rank,
                "prev_rank": pr,
                "rank_change": (pr - rank) if pr is not None else None,
                "change": change,
                "percentage_change": percentage_change,
                "weeks_on_chart": weeks_on_chart,
                "peak_position": peak_position,
                "times_at_peak": times_at_peak,
                "confidence": confidence,
                "songs": curr_songs_by_album.get(aid, []),
            }
        )

    logger.log(f"  live_rank      : {len(entries)} {variant} ranked")
    return entries


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
    top100._atomic_write_csv(history_path, ALBUM_LIVE_HISTORY_FIELDNAMES, combined)
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


def run(*, as_of: date, dry_run: bool, skip_r2: bool) -> int:
    logger = top100.Logger()
    week_end = songs_live._current_week_end(as_of)
    week_start, day_list = top100._week_dates(week_end)
    as_of_str = top100._format_date(as_of)
    days_actual = [d for d in day_list if d <= as_of_str]
    days_remaining = [d for d in day_list if d > as_of_str]

    logger.log(
        f"LIVE TayBoard Albums/Eras (in-progress) · as_of={as_of_str} "
        f"week={top100._format_date(week_start)}->{top100._format_date(week_end)} "
        f"actual={len(days_actual)}d remaining={len(days_remaining)}d"
    )

    logger.log("-- internal not-combined song projection --")
    song_rows, data_as_of = _build_not_combined_song_rows(
        as_of=as_of,
        week_start=week_start,
        week_end=week_end,
        days_actual=days_actual,
        days_remaining=days_remaining,
        date_tag=as_of_str,
        logger=logger,
    )
    logger.log(f"  song_rows      : {len(song_rows)} not-combined songs")

    uploaded_slugs: list[str] = []
    for variant, title, history_path, output_json_path, slug in _VARIANTS:
        logger.log(f"-- variant: {variant} --")
        entries = _build_album_variant_entries(
            variant=variant,
            song_rows=song_rows,
            date_tag=as_of_str,
            week_end=week_end,
            days_actual=days_actual,
            logger=logger,
        )
        if not entries:
            logger.log("WARN abort          : no entries generated for this variant; skipping write")
            continue

        if dry_run:
            logger.log("WARN DRY-RUN — no files written")
            continue

        history_rows = [
            {
                "as_of_date": as_of_str,
                "album_id": e["album_id"],
                "title": e["title"],
                "weekly_streams": e["weekly_streams"],
                "units_am": e["units_am"],
                "units_am_ts": e["units_am_ts"],
                "units_am_overall": e["units_am_overall"],
                "units_youtube": e["units_youtube"],
                "units_spotify": e["units_spotify"],
                "units_charts": e["units_charts"],
                "units_surplus": e["units_surplus"],
                "total_units": e["total_units"],
                "track_count": e["track_count"],
                "points_projected_final": e["points_projected_final"],
                "rank_projected": e["rank_projected"],
                "prev_rank": e["prev_rank"],
                "rank_change": e["rank_change"],
                "change": e["change"],
                "percentage_change": e["percentage_change"],
                "weeks_on_chart": e["weeks_on_chart"],
                "peak_position": e["peak_position"],
                "times_at_peak": e["times_at_peak"],
                "confidence": e["confidence"],
            }
            for e in entries
        ]
        _write_live_history(history_path, as_of_str, history_rows, logger)

        payload = {
            "title": title,
            "as_of_date": as_of_str,
            "week_start": top100._format_date(week_start),
            "week_end": top100._format_date(week_end),
            "days_actual": len(days_actual),
            "days_remaining": len(days_remaining),
            "data_as_of": data_as_of,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": entries,
        }
        _write_live_snapshot(output_json_path, payload, logger)
        uploaded_slugs.append(slug)

    if not dry_run and uploaded_slugs and not skip_r2:
        songs_live._maybe_upload_to_r2_live(logger=logger, slugs=uploaded_slugs)
    elif not dry_run:
        logger.log("  r2             : skipped (--skip-r2 or no entries)")

    logs_dir = _SCRIPT_DIR / "logs"
    if not dry_run:
        logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            logger.save(str(logs_dir / f"swift_top_albums_live_{as_of_str}.log"))
        except OSError as exc:
            print(f"[swift_top_albums_live] Warning: could not save log: {exc}")

    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live in-progress-week TayBoard Albums/Eras projection")
    p.add_argument("--date", dest="date", default=None, help="As-of date (YYYY-MM-DD); default today")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="Compute only; do not write files")
    p.add_argument("--skip-r2", dest="skip_r2", action="store_true", help="Do not upload generated files to R2")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    as_of = top100._parse_iso_date(args.date) if args.date else date.today()
    if as_of is None:
        print(f"[swift_top_albums_live] Invalid --date: {args.date}")
        raise SystemExit(2)
    raise SystemExit(run(as_of=as_of, dry_run=bool(args.dry_run), skip_r2=bool(args.skip_r2)))


if __name__ == "__main__":
    main()
