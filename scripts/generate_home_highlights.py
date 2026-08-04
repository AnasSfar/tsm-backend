#!/usr/bin/env python3
"""Precompute the Charts Gallery highlights + version snapshot dates.

Ports the aggregation logic from tsm-frontend's api/routes/home_highlights.py
and api/routes/version.py to run locally against freshly exported backend
data, then uploads the result to the same R2 cache keys those routes read
(cache/home_highlights.json, cache/version.json) via api/data/precompute_cache.py.
Live requests then read the cache directly instead of recomputing.

This intentionally reimplements only the exact, track-id-keyed parts of that
aggregation (streams history, TayBoard/Apple Music snapshots which already
carry resolved titles/images, and the Spotify Charts CSV which already has a
`track_id`/`movement` column filled in at collection time). It does NOT port
the frontend's fuzzy song-name matching (`_pick_best_song_match` and friends
in api/routes/charts.py) — that logic exists only to reconcile years of
legacy/ambiguous rows and stays the frontend's job.

Called (best-effort, non-fatal — failures here must never block a pipeline)
at the end of update_streams.py, run_all_charts.py, run_apple_music.py,
swift_top_100.py and swift_top_albums.py. Each of those only refreshes part
of the data this aggregates, so every trigger recomputes the full picture
from whatever is locally freshest at that moment.

CLI:
    python scripts/generate_home_highlights.py [--dry-run] [--quiet]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from datetime import date as _date
from pathlib import Path

import boto3
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import (  # noqa: E402
    DB_ROOT as DB_DIR,
    LEGACY_WEBSITE_DATA_DIR,
    LEGACY_WEBSITE_HISTORY_DIR,
    WEB_EXPORT_DATA_DIR,
    WEB_EXPORT_HISTORY_DIR,
)

sys.path.insert(0, str(ROOT / "collectors" / "spotify" / "streams"))
import best_day_since  # noqa: E402

import r2_keys  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=True)

SITE_DATA_DIR = WEB_EXPORT_DATA_DIR if WEB_EXPORT_DATA_DIR.exists() else LEGACY_WEBSITE_DATA_DIR
HISTORY_DIR = WEB_EXPORT_HISTORY_DIR if WEB_EXPORT_HISTORY_DIR.exists() else LEGACY_WEBSITE_HISTORY_DIR

CACHE_KEY_HIGHLIGHTS = r2_keys.CACHE_HOME_HIGHLIGHTS_KEY
CACHE_KEY_VERSION = r2_keys.CACHE_VERSION_KEY

# Round-number cumulative totals worth calling out when a song crosses one
# between yesterday and today (a "milestone" highlight). Kept in sync with
# tsm-frontend/api/routes/home_highlights.py's _MILESTONE_THRESHOLDS.
_MILESTONE_THRESHOLDS = [
    10_000_000, 25_000_000, 50_000_000, 75_000_000,
    100_000_000, 150_000_000, 200_000_000, 250_000_000, 300_000_000, 400_000_000,
    500_000_000, 600_000_000, 700_000_000, 800_000_000, 900_000_000,
    1_000_000_000, 1_250_000_000, 1_500_000_000, 1_750_000_000,
    2_000_000_000, 2_500_000_000, 3_000_000_000, 4_000_000_000, 5_000_000_000,
]


# --------------------------------------------------------------------- R2 --

def _get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def get_r2_client():
    account_id = _get_env("R2_ACCOUNT_ID")
    access_key_id = _get_env("R2_ACCESS_KEY_ID")
    secret_access_key = _get_env("R2_SECRET_ACCESS_KEY")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )


def get_bucket_name() -> str:
    return os.getenv("R2_BUCKET", "taylor-data").strip() or "taylor-data"


def upload_cache_json(client, bucket: str, key: str, payload: dict) -> None:
    body = dict(payload)
    body["_cache_generated_at"] = time.time()
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType="application/json; charset=utf-8")


# --------------------------------------------------------------- loaders --

def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def load_song_map() -> dict:
    data = _load_json(SITE_DATA_DIR / "songs.json") or {}
    songs = data.get("songs", []) if isinstance(data, dict) else []
    return {s.get("track_id"): s for s in songs if s.get("track_id")}


def load_history_dates() -> list[str]:
    data = _load_json(HISTORY_DIR / "index.json") or {}
    dates = data.get("dates", []) if isinstance(data, dict) else []
    return sorted({d for d in dates if isinstance(d, str)})


def load_history_day(date: str) -> dict:
    return _load_json(HISTORY_DIR / f"{date}.json") or {}


def load_tayboard_snapshot(slug: str) -> dict:
    return _load_json(SITE_DATA_DIR / f"{slug}.json") or {}


def load_swift_top_100_dates() -> list[str]:
    data = _load_json(SITE_DATA_DIR / "swift_top_100_index.json")
    dates = data if isinstance(data, list) else []
    return sorted({d for d in dates if isinstance(d, str)}, reverse=True)


def load_apple_music_global() -> tuple[list[dict], str | None]:
    data = _load_json(SITE_DATA_DIR / "applemusic.json") or {}
    global_chart = data.get("global_chart")
    if isinstance(global_chart, dict):
        return global_chart.get("entries", []) or [], global_chart.get("date") or data.get("scraped_at")
    if isinstance(global_chart, list):
        return global_chart, data.get("scraped_at")
    return [], None


def load_apple_music_dates() -> list[str]:
    data = _load_json(SITE_DATA_DIR / "applemusic_history.json") or {}
    return data.get("dates", []) if isinstance(data, dict) else []


_CHART_CSV_FILENAMES = {
    "global": "charts_history_global.csv",
    "fr": "charts_history_fr.csv",
    "us": "charts_history_us.csv",
    "uk": "charts_history_uk.csv",
}


def load_chart_csv_rows(region: str) -> list[dict]:
    filename = _CHART_CSV_FILENAMES.get(region, _CHART_CSV_FILENAMES["global"])
    return _load_csv(DB_DIR / filename)


def load_youtube_dates() -> list[str]:
    rows = _load_csv(DB_DIR / "youtube_views_history.csv")
    return [r.get("date") for r in rows if r.get("date")]


# --------------------------------------------------------------- helpers --

def _streams(entry: dict | None) -> int:
    if not entry:
        return 0
    return int(entry.get("s") or entry.get("streams") or 0)


def _daily(entry: dict | None) -> int:
    if not entry:
        return 0
    return int(entry.get("d") or entry.get("daily_streams") or 0)


def _rank(entry: dict) -> int | None:
    try:
        return int(entry.get("rank"))
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_rank_one(entries: list[dict]) -> dict | None:
    if not entries:
        return None
    return next((e for e in entries if _rank(e) == 1), entries[0])


# --------------------------------------------------------------- version --

def compute_version() -> dict:
    dates = load_history_dates()
    latest_date = dates[-1] if dates else None

    chart_dates: list[str] = []
    for region in ("global", "fr"):
        rows = load_chart_csv_rows(region)
        row_dates = [r.get("date") for r in rows if r.get("date")]
        if row_dates:
            chart_dates.append(max(row_dates))
    latest_chart_date = max(chart_dates) if chart_dates else None

    t100_dates = load_swift_top_100_dates()
    latest_tayboard_date = t100_dates[0] if t100_dates else None

    am_dates = load_apple_music_dates()
    latest_apple_music_raw = max(am_dates) if am_dates else None
    latest_apple_music_date = latest_apple_music_raw[:10] if latest_apple_music_raw else None

    yt_dates = load_youtube_dates()
    latest_youtube_date = max(yt_dates) if yt_dates else None

    return {
        "latest_date": latest_date,
        "latest_chart_date": latest_chart_date,
        "latest_tayboard_date": latest_tayboard_date,
        "latest_apple_music_date": latest_apple_music_date,
        "latest_apple_music_updated_at": latest_apple_music_raw,
        "latest_youtube_date": latest_youtube_date,
    }


# ------------------------------------------------------------ highlights --

def compute_best_day_since_highlight(song_map: dict, target_date_str: str | None) -> dict | None:
    """The single strongest "best day since / ever / biggest of year|month" record for today.

    Reuses best_day_since.py's own functions (same combined-track grouping,
    filters, and ranking it uses to decide what gets posted to Twitter) so
    this highlight always matches what the pipeline would actually call a
    record day — it does not reimplement that logic.
    """
    if not target_date_str:
        return None
    try:
        target_date = _date.fromisoformat(target_date_str)
    except ValueError:
        return None

    try:
        tracks = best_day_since.load_tracks(include_extras=False)
        all_tracks = best_day_since.load_tracks(include_extras=True)
        history = best_day_since.load_history()
    except Exception:
        return None

    rows = []
    seen_families = set()
    for track_id, track in tracks.items():
        family = (track.song_family or track_id).strip()
        if family in seen_families:
            continue
        seen_families.add(family)
        row = best_day_since.compute_best_day_since_combined(
            track,
            best_day_since.combined_tracks_for(all_tracks.get(track_id, track), all_tracks),
            history,
            target_date,
        )
        if row:
            rows.append(row)

    rows = [r for r in rows if best_day_since.passes_filters(r, min_days=best_day_since.DEFAULT_MIN_DAYS)]
    if not rows:
        return None
    rows.sort(key=best_day_since.sort_key, reverse=True)
    best = rows[0]

    return {
        "type": "best_day_since",
        "title": best.get("title") or "—",
        "value": best.get("daily_streams") or 0,
        "image": (song_map.get(best.get("track_id")) or {}).get("image_url"),
        "kind": best.get("kind"),
        "days_since": best.get("days_since"),
        "best_day_since": best.get("best_day_since"),
        "is_biggest_day_of_year": bool(best.get("is_biggest_day_of_year")),
        "is_biggest_day_of_month": bool(best.get("is_biggest_day_of_month")),
        "date": best.get("date") or target_date_str,
    }


def compute_highlights() -> list[dict]:
    song_map = load_song_map()

    def song_title(track_id):
        return (song_map.get(track_id) or {}).get("title") or track_id

    def song_image(track_id):
        return (song_map.get(track_id) or {}).get("image_url")

    dates = load_history_dates()
    latest_date = dates[-1] if dates else None
    prev_date = dates[-2] if len(dates) > 1 else None

    today_data = load_history_day(latest_date) if latest_date else {}
    yesterday_data = load_history_day(prev_date) if prev_date else {}

    items: list[dict] = []

    # BEST DAY SINCE / EVER / BIGGEST OF YEAR-MONTH (today's strongest record, if any)
    best_day_item = compute_best_day_since_highlight(song_map, latest_date)
    if best_day_item:
        items.append(best_day_item)

    # MOST STREAMED TODAY
    if today_data:
        best_id, best_entry = max(
            today_data.items(), key=lambda kv: _daily(kv[1]), default=(None, None)
        )
        if best_id and _daily(best_entry) > 0:
            items.append({
                "type": "most_streamed",
                "title": song_title(best_id),
                "value": _daily(best_entry),
                "image": song_image(best_id),
                "date": latest_date,
            })

    # BIGGEST GAINER (day-over-day daily-rate increase)
    if today_data and yesterday_data:
        best = None
        for track_id, entry in today_data.items():
            prev_entry = yesterday_data.get(track_id)
            if not prev_entry:
                continue
            change = _daily(entry) - _daily(prev_entry)
            if change > 0 and (best is None or change > best[1]):
                best = (track_id, change)
        if best:
            items.append({
                "type": "biggest_gainer",
                "title": song_title(best[0]),
                "value": best[1],
                "image": song_image(best[0]),
                "date": latest_date,
            })

    # TOTAL STREAMS TODAY (all songs combined)
    if today_data:
        total = sum(_daily(e) for e in today_data.values())
        if total > 0:
            items.append({"type": "total_daily", "value": total, "date": latest_date})

    # NEW MILESTONE (a song's cumulative total crossed a round number today)
    if today_data and yesterday_data:
        crossings = []
        for track_id, entry in today_data.items():
            today_total = _streams(entry)
            prev_entry = yesterday_data.get(track_id)
            prev_total = _streams(prev_entry) if prev_entry else (today_total - _daily(entry))
            for threshold in _MILESTONE_THRESHOLDS:
                if prev_total < threshold <= today_total:
                    crossings.append((track_id, threshold))
        if crossings:
            track_id, threshold = random.choice(crossings)
            items.append({
                "type": "milestone",
                "title": song_title(track_id),
                "value": threshold,
                "image": song_image(track_id),
                "date": latest_date,
            })

    # SONG SURPASSES ANOTHER (total-streams rank swap since yesterday)
    if today_data and yesterday_data:
        ids = list(today_data.keys())
        today_totals = {tid: _streams(today_data[tid]) for tid in ids}
        prev_totals = {}
        for tid in ids:
            prev_entry = yesterday_data.get(tid)
            prev_totals[tid] = _streams(prev_entry) if prev_entry else (today_totals[tid] - _daily(today_data[tid]))
        by_today = sorted(ids, key=lambda tid: today_totals[tid], reverse=True)
        by_prev = sorted(ids, key=lambda tid: prev_totals[tid], reverse=True)
        prev_rank = {tid: i for i, tid in enumerate(by_prev)}
        swaps = []
        for i in range(min(len(by_today) - 1, 150)):
            a, b = by_today[i], by_today[i + 1]
            if prev_rank.get(a, 0) > prev_rank.get(b, 0):
                swaps.append((a, b))
        if swaps:
            a, b = random.choice(swaps)
            items.append({
                "type": "surpass",
                "title": song_title(a),
                "otherTitle": song_title(b),
                "image": song_image(a),
                "date": latest_date,
            })

    # TOP ALBUM (TayBoard Albums #1)
    albums_payload = load_tayboard_snapshot("swift_top_albums")
    top_album = _first_rank_one(albums_payload.get("entries", []) if isinstance(albums_payload, dict) else [])
    if top_album:
        items.append({
            "type": "top_album",
            "title": top_album.get("title") or "—",
            "meta": top_album.get("points_display"),
            "image": top_album.get("image_url"),
            "date": albums_payload.get("chart_date") or albums_payload.get("week_end"),
        })

    # TOP ERA (TayBoard Eras #1)
    eras_payload = load_tayboard_snapshot("swift_top_eras")
    top_era = _first_rank_one(eras_payload.get("entries", []) if isinstance(eras_payload, dict) else [])
    if top_era:
        items.append({
            "type": "top_era",
            "title": top_era.get("title") or "—",
            "meta": top_era.get("points_display"),
            "image": top_era.get("image_url"),
            "date": eras_payload.get("chart_date") or eras_payload.get("week_end"),
        })

    # TAYBOARD #1 (weekly Swift Top 100)
    t100_payload = load_tayboard_snapshot("swift_top_100")
    t100_top = _first_rank_one(t100_payload.get("entries", []) if isinstance(t100_payload, dict) else [])
    if t100_top:
        items.append({
            "type": "tayboard_1",
            "title": t100_top.get("song_title") or t100_top.get("title") or "—",
            "meta": t100_top.get("points_display"),
            "image": t100_top.get("image_url"),
            "date": t100_payload.get("chart_date"),
        })

    # APPLE MUSIC GLOBAL #1
    am_entries, am_date = load_apple_music_global()
    am_top = _first_rank_one(am_entries)
    if am_top:
        items.append({
            "type": "apple_music_1",
            "title": am_top.get("song_name") or "—",
            "meta": am_top.get("album_name"),
            "image": am_top.get("image_url"),
            "rank": _rank(am_top) or 1,
            "date": am_date,
        })

    # SPOTIFY CHARTS #1 (global) + a random NEW/RE row.
    # Uses charts_history_global.csv directly: track_id/movement are already
    # resolved by the collector at collection time for current dates, so no
    # fuzzy song-name matching is needed (unlike api/routes/charts.py, which
    # also has to reconcile years of legacy rows without a track_id).
    chart_rows_all = load_chart_csv_rows("global")
    chart_dates_available = sorted({r.get("date") for r in chart_rows_all if r.get("date")})
    if chart_dates_available:
        chart_latest = chart_dates_available[-1]
        today_chart_rows = [r for r in chart_rows_all if r.get("date") == chart_latest]
        today_chart_rows.sort(key=lambda r: _rank(r) if _rank(r) is not None else 9999)

        chart_top = _first_rank_one(today_chart_rows)
        if chart_top:
            track_id = chart_top.get("track_id")
            items.append({
                "type": "chart_1",
                "title": chart_top.get("song_name") or song_title(track_id) or "—",
                "value": _to_int(chart_top.get("streams")),
                "image": song_image(track_id),
                "rank": _rank(chart_top) or 1,
                "date": chart_latest,
            })

        # Note: matches the frontend's current behavior — "OUT" rows live in a
        # separate list there and never actually appear in this pool.
        movement_rows = [
            r for r in today_chart_rows
            if str(r.get("movement") or "").strip().upper() in {"NEW", "RE"}
        ]
        if movement_rows:
            pick = random.choice(movement_rows)
            movement = str(pick.get("movement") or "").strip().upper()
            track_id = pick.get("track_id")
            items.append({
                "type": f"chart_{movement.lower()}",
                "title": pick.get("song_name") or song_title(track_id) or "—",
                "rank": _rank(pick),
                "image": song_image(track_id),
                "date": chart_latest,
            })

    return items


# --------------------------------------------------------------------- CLI --

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Precompute Charts Gallery highlights + version dates and upload them to the R2 cache read by tsm-frontend/api."
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not upload to R2.")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output (used when called from other collector pipelines).")
    args = parser.parse_args()

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg)

    try:
        version = compute_version()
        highlights = compute_highlights()
    except Exception as exc:
        print(f"[generate_home_highlights] computation failed: {exc}", file=sys.stderr)
        return 1

    log(f"[generate_home_highlights] version={version}")
    log(f"[generate_home_highlights] {len(highlights)} highlight(s) computed")

    if args.dry_run:
        log("[generate_home_highlights] --dry-run: skipping R2 upload")
        return 0

    try:
        client = get_r2_client()
        bucket = get_bucket_name()
        upload_cache_json(client, bucket, CACHE_KEY_VERSION, version)
        upload_cache_json(client, bucket, CACHE_KEY_HIGHLIGHTS, {"version": version, "highlights": highlights})
        log(f"[generate_home_highlights] uploaded {CACHE_KEY_VERSION} and {CACHE_KEY_HIGHLIGHTS} to bucket '{bucket}'")
    except Exception as exc:
        print(f"[generate_home_highlights] R2 upload failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
