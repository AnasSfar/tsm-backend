from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from datetime import date, timedelta
from pathlib import Path
from queue import Empty, Queue
import sys
import random

import requests as _requests
from requests.adapters import HTTPAdapter

from playwright.sync_api import sync_playwright

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[2]

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
sys.path.insert(0, str(_SCRIPT_DIR.parents[0]))  # collectors/spotify/ for core.*
sys.path.insert(0, str(_SCRIPT_DIR / "tools" / "scripts"))
sys.path.insert(0, str(_SCRIPT_DIR / "extras"))
sys.path.insert(0, str(_REPO_ROOT / "collectors" / "comp"))

import export_for_web
from backfill_discography_from_spotify import run_backfill as run_discography_backfill
from finalize_update import (
    ALBUM_UPDATE_TARGETS,
    POST_ONLY_STEPS,
    FinalizeContext,
    PartialWebExporter,
    ReadyAlbumBestDaySincePoster,
    ReadyAlbumUpdatePoster,
    ReadyDebutReleasePoster,
    ReadyBestDaySincePoster,
    SharedWebExportGate,
    run_final_update_tasks,
    run_post_only_steps,
)
from release_targets import is_recent_release_date
from reporting import ProgressLogger, print_remaining_details, print_summary_block, update_json_logs_from_summary
import spotify_api as _spotify_api
from stream_utils import (
    block_unneeded,
    format_int,
    get_previous_stats_date_str,
    get_scrape_date_str,
    get_stats_date_str,
    launch_browser,
)
from spotify_api import (
    AdaptiveWorkerState,
    TokenManager,
    _probe_via_api,
    _warp_connect,
    fetch_playcount_api,
)
from track_cover_cache import merge_track_cover_cache
import page_scraper as _page_scraper
from page_scraper import scrape_track_total
import run_logs as _run_logs
from run_logs import (
    load_last_unfinished_update_track_ids,
    load_not_found_streak,
    purge_stale_tracks,
    save_failed_rows,
    save_last_successful_updates_json,
    save_last_unfinished_updates_json,
    save_not_found_streak,
    save_pending_debug_rows,
    update_not_found_streak,
)
import history_store as _history_store
from history_store import (
    HistoryIndex,
    album_tracks_done_for,
    all_album_tracks_done,
    append_history_row,
    build_track_lookup,
    compute_daily,
    days_covered_by_row,
    dedupe_history_rows_by_date_track,
    delete_history_rows_for_date,
    ensure_history_file,
    extract_track_id,
    find_biggest_album_gainer_for_spotlight,
    get_all_last_history_totals,
    get_history_total_for_date,
    get_last_history_total,
    get_last_stats_date_in_history,
    get_previous_total_before_date,
    get_priority_top_50_track_ids_from_previous_day,
    load_active_track_ids_from_discography,
    load_album_track_ids,
    load_album_track_ids_for_album,
    load_album_sections_flat,
    load_history_rows,
    load_history_track_ids_for_date,
    load_history_track_ids_with_daily_for_date,
    load_track_priorities_from_specific_date,
    load_tracks_from_discography,
    push_updated_track_histories_to_r2,
    save_history_rows,
)
from artist_metadata import scrape_artist_metadata, scrape_artist_top_tracks, update_artist_metadata
from git_ops import git_commit_and_push
from config import NTFY_TOPIC
from core.data_paths import RUNTIME_ROOT, first_existing_db_history, update_streams_dir
from core.notify import send as notify

ROOT = RUNTIME_ROOT
DATA_DIR = ROOT / "spotify_streams"
_DB_ROOT = _REPO_ROOT / "db"
_ARCHIVE_DB_ROOT = _REPO_ROOT / "data" / "_archive" / "original" / "db"

HISTORY_PATH = (
    first_existing_db_history("streams_history.csv")
)
ARTIST_MONTHLY_HISTORY_PATH = (
    first_existing_db_history("artist_monthly_listeners_history.csv")
)
FAILED_PATH = DATA_DIR / "not_found_today.csv"
PENDING_LOG_PATH = DATA_DIR / "pending_debug_today.csv"
LAST_SUCCESSFUL_UPDATE_JSON = DATA_DIR / "last_successful_updates.json"
LAST_UNFINISHED_UPDATE_JSON = DATA_DIR / "last_unfinished_updates.json"
STREAMS_SCRAPED_LOCK_NAME = "streams_scraped.lock"
STREAMS_UPDATE_COMPLETE_LOCK_NAME = "streams_update_complete.lock"

DISCOGRAPHY_DIR = _DB_ROOT / "discography"
DB_ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
DB_SONGS_JSON = DISCOGRAPHY_DIR / "songs.json"
ARTIST_PATH = DISCOGRAPHY_DIR / "artist.json"
ARTIST_URL = "https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02"

# Spotify daily update happens around this local hour; before it, we're still in the previous day's window
SPOTIFY_UPDATE_HOUR = 15

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


HEADLESS = _env_bool("TSM_HEADLESS", True)
MAX_PARALLEL_PAGES = 10
PAGE_GOTO_TIMEOUT_MS = 20_000
DEBUG_PAGE_PREVIEW = False
# Logging
# - default: compact output
# - --verbose: per-track lines + extra debug prints
# - --quiet: only periodic summaries + errors
LOG_MODE = "normal"  # "quiet" | "normal" | "verbose"

# Hill climbing
HILL_WINDOW        = 12     # completions par fenÃªtre d'Ã©valuation (was 20 â€” react faster)
HILL_429_THRESHOLD = 0.15   # taux de 429 au-delÃ  duquel on retire 1 worker
HILL_MIN_WORKERS   = 2
HILL_INITIAL       = 9      # point de dÃ©part (was 6 â€” start near max immediately)

PROBE_NON_EXTRA_SAMPLE_SIZE = 15
PROBE_SAMPLE_SIZE = PROBE_NON_EXTRA_SAMPLE_SIZE
PROBE_RECENT_BATCH_MEMORY = 5
PROBE_REQUIRED_UPDATED = 10  # non-extra (chart_extra=False) Spotify probe tracks that must show a real update
CHARTSNAPSHOT_REQUIRED_VALIDATED = 20  # non-extra tracks with total - daily == our previous-day total
CHARTSNAPSHOT_ARTIST_URI = "06HL4z0CvFAxyc27GXpf02"
CHARTSNAPSHOT_TOP_SONGS_URL = "https://www.chartsnapshot.com/get_top_songs"
EARLY_BEST_DAY_MIN_DAILY_STREAMS = 30_000
EARLY_BEST_DAY_WATCHLIST_MIN_DAILY_STREAMS = 30_000
EARLY_BEST_DAY_TRACK_LIMIT = 80
EARLY_BEST_DAY_MAX_POSTS = 3
EARLY_BEST_DAY_MIN_PCT_CHANGE = 10.0
EARLY_BEST_DAY_MIN_SCORE = 58.0
EARLY_BEST_DAY_PRIORITY_AFTER_DAYS = 60
EARLY_BEST_DAY_PRIORITY_RECENT_PEAK_RATIO = 0.90
GROWER_NOTIFY_LIMIT = 3
GROWER_NOTIFY_WINDOW_DAYS = 7
GROWER_NOTIFY_MIN_BASELINE_DAILY = 1_000
PENDING_RETRY_SLEEP_SECONDS = 20
EXTRA_PENDING_RETRY_ROUNDS_BEFORE_ZERO = 5
INFINITE_RETRY_PREVIOUS_DAY_TOP_N = 70
POST_BETWEEN_STREAMS_POSTS_SECONDS = 0
INCREMENTAL_PUBLISH_ON_UPDATE = False

NOT_FOUND_STREAK_PATH = DATA_DIR / "not_found_streak.json"
KNOWN_STUCK_PENDING_PATH = ROOT / "spotify_streams" / "known_stuck_pending_tracks.json"
MAX_NOT_FOUND_DAYS = 7

MAX_DAILY_INCREASE = 50_000_000
MAX_ESTIMATED_STREAM_GAP_DAYS = 4
ESTIMATED_MISSING_DAY_REASON = "missing_daily_gap"

# --admin: accept whatever total Spotify shows as-is, writing the raw diff as
# daily_streams even when negative (unlike --over, which still clamps a
# negative diff to blank via compute_daily). Set once in main() from argv.
ADMIN_OVERRIDE_MODE = False
NEW_RELEASE_RETRY_ATTEMPTS = int(os.getenv("NEW_RELEASE_RETRY_ATTEMPTS", "12"))
NEW_RELEASE_RETRY_SLEEP_SECONDS = int(os.getenv("NEW_RELEASE_RETRY_SLEEP_SECONDS", "10"))

# â”€â”€ API GraphQL Spotify â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
START_TIME = None


def _daily_lock_path(stats_date: str, lock_name: str) -> Path:
    return update_streams_dir(stats_date) / lock_name


def _write_daily_lock(stats_date: str, lock_name: str, payload: dict) -> None:
    path = _daily_lock_path(stats_date, lock_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "stats_date": stats_date,
        "generated_at": get_scrape_date_str(),
        **payload,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_known_stuck_pending_tracks() -> dict[str, dict]:
    if not KNOWN_STUCK_PENDING_PATH.exists():
        return {}
    try:
        payload = json.loads(KNOWN_STUCK_PENDING_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    tracks = payload.get("tracks") if isinstance(payload, dict) else payload
    if not isinstance(tracks, dict):
        return {}
    return {
        str(track_id): info if isinstance(info, dict) else {}
        for track_id, info in tracks.items()
        if str(track_id).strip()
    }


def save_known_stuck_pending_tracks(tracks: dict[str, dict]) -> None:
    KNOWN_STUCK_PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": get_scrape_date_str(),
        "extra_retry_limit_after_non_extra_done": EXTRA_PENDING_RETRY_ROUNDS_BEFORE_ZERO,
        "tracks": dict(sorted(tracks.items())),
    }
    KNOWN_STUCK_PENDING_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def remember_known_stuck_pending_tracks(
    known_stuck: dict[str, dict],
    *,
    pending_results: list[dict],
    stats_date: str,
) -> bool:
    changed = False
    for result in pending_results:
        track_id = str(result.get("track_id") or "").strip()
        if not track_id:
            continue
        previous = known_stuck.get(track_id, {})
        known_stuck[track_id] = {
            "track_id": track_id,
            "title": result.get("title") or previous.get("title") or "",
            "spotify_url": result.get("spotify_url") or previous.get("spotify_url") or "",
            "first_stuck_date": previous.get("first_stuck_date") or stats_date,
            "last_stuck_date": stats_date,
            "last_reason": result.get("reason") or "",
            "last_streams": result.get("streams"),
            "times_stuck": int(previous.get("times_stuck") or 0) + 1,
        }
        changed = True
    return changed


def load_previous_same_total_pending_track_ids(stats_date: str) -> set[str]:
    previous_stats_date = get_previous_stats_date_str(stats_date)
    same_total_paths = [
        update_streams_dir(previous_stats_date) / "same_total.json",
        update_streams_dir(previous_stats_date) / LAST_UNFINISHED_UPDATE_JSON.name,
    ]

    persisted: set[str] = set()
    for path in same_total_paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if str(payload.get("stats_date") or "") != previous_stats_date:
            continue
        for row in payload.get("tracks") or []:
            if not isinstance(row, dict):
                continue
            track_id = str(row.get("track_id") or "").strip()
            if not track_id:
                continue
            if row.get("reason") not in {
                "same_total",
                "same_total_zero",
                "assumed_zero_low_traffic_extra",
                "persistent_same_total_extra_zero",
            }:
                continue
            streams = row.get("streams")
            previous_streams = row.get("previous_streams")
            if streams is None or previous_streams is None:
                continue
            try:
                if int(streams) == int(previous_streams):
                    persisted.add(track_id)
            except (TypeError, ValueError):
                continue
    return persisted


def load_admin_override_unchanged_zero_track_ids(stats_date: str) -> set[str]:
    """Rows force-accepted by --admin with daily=0 and unchanged total.

    These rows are useful evidence that a page had not advanced yet, but they
    should not let a normal run take the "already complete" path.
    """
    previous_stats_date = get_previous_stats_date_str(stats_date)
    rows_by_date_track: dict[tuple[str, str], dict] = {}
    for row in load_history_rows():
        row_date = str(row.get("date") or "").strip()
        track_id = str(row.get("track_id") or "").strip()
        if row_date in {stats_date, previous_stats_date} and track_id:
            rows_by_date_track[(row_date, track_id)] = row

    stale_ids: set[str] = set()
    for (row_date, track_id), row in rows_by_date_track.items():
        if row_date != stats_date:
            continue
        if (row.get("estimated_reason") or "").strip() != "admin_override":
            continue
        if (row.get("daily_streams") or "").strip() != "0":
            continue
        previous = rows_by_date_track.get((previous_stats_date, track_id))
        if previous is None:
            continue
        try:
            current_total = int(row.get("streams") or 0)
            previous_total = int(previous.get("streams") or 0)
        except (TypeError, ValueError):
            continue
        if current_total == previous_total:
            stale_ids.add(track_id)
    return stale_ids


def _daily_lock_exists(stats_date: str, lock_name: str) -> bool:
    return _daily_lock_path(stats_date, lock_name).exists()


def _build_existing_history_summary(stats_date: str, total_tracks: int, total_all_tracks: int) -> dict:
    history_index = HistoryIndex.load()
    done_ids = history_index.done_ids_for_date(stats_date)
    all_done = len(done_ids) >= total_tracks
    return {
        "stats_date": stats_date,
        "total_tracks": total_tracks,
        "total_all_tracks": total_all_tracks,
        "done_tracks": len(done_ids),
        "remaining_tracks": max(total_tracks - len(done_ids), 0),
        "all_done": all_done,
        "updated_this_run": 0,
        "pending_this_run": 0 if all_done else max(total_tracks - len(done_ids), 0),
        "skipped_this_run": len(done_ids),
        "timeout_this_run": 0,
        "error_this_run": 0,
        "not_found_this_run": 0,
        "results": [],
        "failed_results": [],
        "updated_track_ids": set(),
        "history_index": history_index,
        "api_metrics": {},
    }


class ApiRunMetrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counters: dict[str, int] = {}
        self.status_counts: dict[str, int] = {}

    def add(self, metrics: dict) -> None:
        with self.lock:
            for key in ("requests", "network_errors", "token_refreshes", "rate_limited", "server_retries"):
                self.counters[key] = self.counters.get(key, 0) + int(metrics.get(key) or 0)
            for status, count in (metrics.get("status_counts") or {}).items():
                self.status_counts[status] = self.status_counts.get(status, 0) + int(count or 0)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                **self.counters,
                "status_counts": dict(sorted(self.status_counts.items())),
            }


def configure_daily_data_paths(stats_date: str) -> None:
    global DATA_DIR, FAILED_PATH, PENDING_LOG_PATH
    global LAST_SUCCESSFUL_UPDATE_JSON, LAST_UNFINISHED_UPDATE_JSON, NOT_FOUND_STREAK_PATH

    DATA_DIR = update_streams_dir(stats_date)
    FAILED_PATH = DATA_DIR / "not_found_today.csv"
    PENDING_LOG_PATH = DATA_DIR / "pending_debug_today.csv"
    LAST_SUCCESSFUL_UPDATE_JSON = DATA_DIR / "last_successful_updates.json"
    LAST_UNFINISHED_UPDATE_JSON = DATA_DIR / "last_unfinished_updates.json"
    NOT_FOUND_STREAK_PATH = DATA_DIR / "not_found_streak.json"
    _run_logs.configure_daily_data_paths(stats_date)

# â”€â”€ Live update signal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_UPDATE_SIGNAL_SENT = threading.Event()

# â”€â”€ Per-track incremental R2 upload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _upload_update_signal(stats_date: str) -> None:
    """Upload data/update_signal.json to R2 when the first track update lands."""
    from datetime import timezone, datetime as _dt
    try:
        import boto3 as _boto3
    except ImportError:
        return

    r2_account = os.getenv("R2_ACCOUNT_ID", "").strip()
    r2_key_id  = os.getenv("R2_ACCESS_KEY_ID", "").strip()
    r2_secret  = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
    r2_bucket  = os.getenv("R2_BUCKET", "").strip()

    if not all([r2_account, r2_key_id, r2_secret, r2_bucket]):
        return

    payload = json.dumps({
        "updated_at": _dt.now(timezone.utc).isoformat(),
        "date": stats_date,
    }).encode("utf-8")

    try:
        s3 = _boto3.client(
            "s3",
            endpoint_url=f"https://{r2_account}.r2.cloudflarestorage.com",
            aws_access_key_id=r2_key_id,
            aws_secret_access_key=r2_secret,
        )
        s3.put_object(
            Bucket=r2_bucket,
            Key="data/update_signal.json",
            Body=payload,
            ContentType="application/json",
        )
        print(f"[signal] Update signal uploaded for {stats_date}")
    except Exception as e:
        print(f"[signal] Upload failed (non-blocking): {e}")


def print_help() -> None:
    print(
        """
Usage:
  python update_streams.py
      Run normal for yesterday's stats date.

  python update_streams.py YYYY-MM-DD
      Run normal for a specific stats date.

  python update_streams.py --debug-daily
      Retry unfinished tracks for yesterday's stats date, writes to history,
      but skips Twitter / git / forecast / images / notify.

  python update_streams.py --debug-daily YYYY-MM-DD
      Same as above for a specific stats date.

  python update_streams.py --debug-total YYYY-MM-DD
      Re-scrape totals and replace totals for the given date in streams_history.csv.
      Recomputes daily_streams from the previous date. No Twitter / git / forecast / images / notify.

  python update_streams.py --dry-run
      Scrape only. No writes anywhere.

  python update_streams.py YYYY-MM-DD --over
      Override the reject-on-decrease guard for this run. Still clamps a negative
      delta to a blank daily_streams (compute_daily) â€” use --admin instead if the
      raw total decreased and that decrease itself should be recorded.

  python update_streams.py YYYY-MM-DD --admin
      Accept the raw Spotify total as-is with no clamp at all (implies --over
      AND --force â€” a track already holding a blank/partial row for this date
      is otherwise skipped as "already done" before the override ever runs):
      writes daily_streams as the literal (possibly negative) diff, tagged
      estimated_reason=admin_override so the completeness gate counts it as done
      despite the negative/blank value. Use only for a verified Spotify-side
      merge/relink/correction you're vouching for by hand â€” it bypasses the
      "never publish a negative daily" guarantee. Forces a full re-scrape of
      every track for that date (via --force), so expect the full run time.

  python update_streams.py --local-test YYYY-MM-DD
      Force re-scrape even if the date already exists, but skip history writes,
      R2, Twitter, git, forecast, and image metadata refresh.

  python update_streams.py --test [YYYY-MM-DD]
      Run finalization scripts against existing history data for the latest
      available date (or the provided date), with no R2, no web export writes,
      no Twitter posts, no git, and no scraping.

  python update_streams.py [YYYY-MM-DD] --post-only top-eras
  python update_streams.py [YYYY-MM-DD] --post-only recap,top-eras,top20
      Post only the selected step(s) from existing history data for the latest
      available date (or the provided date). No scraping, no web export, no git.
      Completeness guards, posting locks and weekday rules still apply.
      Steps (comma- or space-separated): top-eras, all-albums, top20, recap,
      milestones, overtakes, best-day-since, debut, gainers, album-updates.
      Add --no-post to only generate the images without posting.

  python update_streams.py --no-post
      Run full pipeline but skip all Twitter posting steps.

  python update_streams.py YYYY-MM-DD --throwback --throwback-action released --throwback-event "..."
      Generate/post a throwback thread for that stats date instead of normal daily posts.
      Existing throwback images are reused unless --force or --throwback-force is passed.

  python update_streams.py --reset-last-date
      Delete all rows for the latest date found in streams_history.csv before running.

  python update_streams.py --reset-date YYYY-MM-DD
      Delete all rows for that date before running.

  python update_streams.py --quiet
      Reduce terminal output (periodic summaries + errors only).

  python update_streams.py --verbose
      Verbose per-track output (debug-friendly).

  python update_streams.py --help
      Show this help.

Notes:
  - Normal mode writes official updates and can post/export/push.
    - --no-post keeps processing/export/commit but skips Twitter posts.
  - --debug-daily writes missing updates into history, but stays local/no posting.
  - --debug-total rewrites an existing date's totals in history.
  - --throwback uses the target stats date and requires --throwback-action announced|released
    plus --throwback-event "what happened".
        """.strip()
    )


def incremental_publish_update(
    track: dict,
    stats_date: str,
    publish_lock: threading.Lock,
) -> None:
    if not INCREMENTAL_PUBLISH_ON_UPDATE:
        return

    with publish_lock:
        try:
            print(
                f"Incremental publish | {track['title']} | "
                f"{track['track_id']} | stats_date={stats_date}"
            )
            export_web_data(stats_date=stats_date)
            git_commit_and_push(_REPO_ROOT, f"track update {stats_date} {track['track_id']}")
        except Exception as e:
            print(
                f"Incremental publish failed for {track['title']} "
                f"({track['track_id']}): {e}"
            )


def export_web_data(*, allow_r2: bool = True, stats_date: str | None = None) -> None:
    export_for_web.export_for_web(
        stats_date=stats_date,
        allow_r2=allow_r2,
        r2_export_lock_path=(update_streams_dir(stats_date) / "r2_exported.lock") if allow_r2 and stats_date else None,
    )


class BackgroundFinalWebExport:
    def __init__(
        self,
        *,
        stats_date: str,
        allow_r2: bool,
        force: bool,
    ) -> None:
        self.stats_date = stats_date
        self.allow_r2 = allow_r2
        self.force = force
        self._thread: threading.Thread | None = None
        self._exc: BaseException | None = None
        self._skipped = False

    def start(self) -> None:
        run_dir = update_streams_dir(self.stats_date)
        export_lock = run_dir / "exported.lock"
        daily_site_history = run_dir / "site_history.json"
        if export_lock.exists() and daily_site_history.exists() and not self.force:
            print(f"Web export already done for {self.stats_date} (exported.lock exists), skipping background export.")
            self._skipped = True
            return
        if export_lock.exists() and self.force:
            export_lock.unlink()

        r2_export_lock = run_dir / "r2_exported.lock"
        if self.allow_r2 and r2_export_lock.exists():
            print(
                f"R2 export lock exists for {self.stats_date}; "
                "background final export will force a fresh complete upload."
            )
            r2_export_lock.unlink()

        self._thread = threading.Thread(
            target=self._run,
            name="final-web-export",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            print(f"Starting final web export in background (stats_date={self.stats_date})...")
            export_web_data(allow_r2=self.allow_r2, stats_date=self.stats_date)
            print("Background final web export done.")
        except BaseException as exc:
            self._exc = exc
            print(f"Background final web export failed: {exc}")

    def wait(self) -> None:
        if self._thread is not None:
            self._thread.join()
        if self._exc is not None:
            raise self._exc

    def export_web_data(self, *, allow_r2: bool = True, stats_date: str | None = None) -> None:
        if stats_date == self.stats_date and allow_r2 == self.allow_r2:
            self.wait()
            return
        self.wait()
        export_web_data(allow_r2=allow_r2, stats_date=stats_date)


def try_apply_track_update(
    track: dict,
    total: int,
    stats_date: str,
    lock: threading.Lock,
    publish_lock: threading.Lock,
    history_index: HistoryIndex | None = None,
    dry_run_mode: bool = False,
    write_history: bool = True,
    compare_before_stats_date: bool = False,
    override_stream_guards: bool = False,
) -> dict:
    track_id = track["track_id"]
    if compare_before_stats_date:
        last_total = (
            history_index.get_previous_total_before_date(track_id, stats_date)
            if history_index is not None
            else get_previous_total_before_date(track_id, stats_date)
        )
    else:
        last_total = history_index.get_last_total(track_id) if history_index is not None else get_last_history_total(track_id)
    previous_stats_date = get_previous_stats_date_str(stats_date)
    previous_day_total = (
        history_index.get_total_for_date(track_id, previous_stats_date)
        if history_index is not None
        else get_history_total_for_date(track_id, previous_stats_date)
    )
    daily = compute_daily(previous_day_total, total)
    missing_previous_day_total = previous_day_total is None and last_total is not None
    # Baseline (total Ã©crit, daily VIDE) quand il n'y a pas de ligne J-1 et que
    # le delta depuis la derniÃ¨re ligne connue ne peut pas valoir un daily :
    # extras (comportement historique extra_baseline), ou gap > 4 jours / aucun
    # historique (ex. track ajoutÃ©e en DB avec un backfill chartsnapshot ancien
    # â€” incident du 12/07/2026 oÃ¹ les totaux lifetime sont partis en daily).
    # Les gaps courts (panne WARP 1-4 j) gardent la logique multi-day/canari
    # plus bas, qui produit des posts Â« last N days Â» corrects.
    gap_days_before_stats_date = (
        history_index.get_days_since_previous_row(track_id, stats_date)
        if history_index is not None
        else (days_covered_by_row(track_id, stats_date) if last_total is not None else None)
    )
    missing_previous_day_baseline = previous_day_total is None and (
        bool(track.get("chart_extra"))
        or (
            not is_recent_release_date(track.get("release_date"), stats_date)
            and (
                gap_days_before_stats_date is None
                or gap_days_before_stats_date > MAX_ESTIMATED_STREAM_GAP_DAYS
            )
        )
    )

    if override_stream_guards and ADMIN_OVERRIDE_MODE:
        # --admin accepts Spotify's raw diff, but an unchanged total still has
        # to prove persistence through the retry loop before we write daily=0.
        reference_total = previous_day_total if previous_day_total is not None else last_total
        if reference_total is not None and total == reference_total:
            reason = "admin_same_total"
            real_update = False
            daily = 0
        else:
            # Negative included: the human operator is explicitly vouching for
            # this total, so don't apply compute_daily()'s negative clamp.
            reason = "admin_override"
            real_update = True
            if previous_day_total is not None:
                daily = total - previous_day_total
            elif last_total is not None:
                daily = total - last_total
            else:
                daily = None
    elif override_stream_guards:
        reason = "override_stream_guards"
        real_update = True
        if previous_day_total is None and last_total is not None:
            daily = compute_daily(last_total, total)
    elif missing_previous_day_baseline:
        reason = "missing_previous_day_baseline"
        real_update = False
    elif last_total is None:
        reason = "first_seen"
        real_update = True
    elif total == last_total:
        reason = "same_total_zero" if total == 0 else "same_total"
        real_update = False
    elif total < last_total:
        if missing_previous_day_total:
            # last_total comes from a date beyond the immediate previous day
            # (a gap, e.g. a backfilled/injected later total) â€” our fetch just
            # hasn't caught up to that figure yet, this isn't a real regression.
            reason = "missing_previous_day_total"
            real_update = False
        elif track.get("chart_extra"):
            # Extras (covers, wind ensemble versions, etc.) aren't posted and
            # aren't subject to the "streams never decrease" guarantee we hold
            # non-extra actives to â€” a real total drop here is accepted as-is
            # instead of blocking the run forever waiting for month-start.
            reason = "lower_than_previous_extra"
            real_update = True
        else:
            reason = "lower_than_previous"
            try:
                real_update = date.fromisoformat(stats_date).day == 1
            except ValueError:
                real_update = False
            if not real_update:
                reason = "lower_than_previous_not_month_start"
    elif total - last_total > MAX_DAILY_INCREASE:
        reason = f"anomaly_delta_gt_{MAX_DAILY_INCREASE}"
        real_update = False
    elif missing_previous_day_total:
        # No row for the immediate previous day. Filet de sÃ©curitÃ© : si la
        # derniÃ¨re ligne connue est plus vieille qu'un gap court WARP, le
        # delta n'est pas un daily â€” baseline avec daily vide (cas normalement
        # dÃ©jÃ  couvert par missing_previous_day_baseline ci-dessus).
        gap_days = gap_days_before_stats_date
        if gap_days is not None and gap_days > MAX_ESTIMATED_STREAM_GAP_DAYS:
            reason = "baseline_after_long_gap"
            real_update = True
            daily = None
        else:
            # Use tracks that already have real data for stats_date as a
            # canary: if none of them have shown real growth for stats_date
            # yet, this fetch more likely just caught up to the missing
            # previous day's number, not stats_date's â€” record it under that
            # earlier date instead of mislabeling it. Once some track confirms
            # stats_date growth, treat further catch-ups as real stats_date
            # deltas (possibly spanning more than one day).
            canary_confirmed_stats_date = (
                history_index.has_any_real_update_for_date(stats_date)
                if history_index is not None
                else True
            )
            if canary_confirmed_stats_date:
                reason = "updated_multi_day_gap"
                real_update = True
                daily = compute_daily(last_total, total)
            else:
                reason = "backfilled_previous_day"
                real_update = False
                daily = compute_daily(last_total, total)
    else:
        reason = "updated"
        real_update = True

    if reason == "same_total_zero":
        current_day_total = (
            history_index.get_total_for_date(track_id, stats_date)
            if history_index is not None
            else get_history_total_for_date(track_id, stats_date)
        )
        if write_history and not dry_run_mode and current_day_total is None:
            with lock:
                if history_index is not None:
                    history_index.append(stats_date, track_id, total, 0)
                else:
                    append_history_row([stats_date, track_id, total, 0])
        status = "skipped"
    elif reason == "missing_previous_day_baseline":
        current_day_total = (
            history_index.get_total_for_date(track_id, stats_date)
            if history_index is not None
            else get_history_total_for_date(track_id, stats_date)
        )
        if write_history and not dry_run_mode and current_day_total is None:
            with lock:
                if history_index is not None:
                    history_index.append(stats_date, track_id, total, None)
                else:
                    append_history_row([stats_date, track_id, total, ""])
        status = "skipped"
    elif reason == "backfilled_previous_day":
        current_previous_day_total = (
            history_index.get_total_for_date(track_id, previous_stats_date)
            if history_index is not None
            else get_history_total_for_date(track_id, previous_stats_date)
        )
        if write_history and not dry_run_mode and current_previous_day_total is None:
            with lock:
                if history_index is not None:
                    history_index.append(previous_stats_date, track_id, total, daily)
                else:
                    append_history_row([previous_stats_date, track_id, total, daily if daily is not None else ""])
        status = "pending"
    elif real_update and dry_run_mode:
        status = "pending"
    elif real_update:
        if write_history:
            row_estimated_reason = "admin_override" if reason == "admin_override" else ""
            with lock:
                if history_index is not None:
                    history_index.append(stats_date, track_id, total, daily, row_estimated_reason)
                else:
                    append_history_row([
                        stats_date, track_id, total, daily if daily is not None else "",
                        "", row_estimated_reason,
                    ])
        status = "updated"

        if reason == "lower_than_previous" and not dry_run_mode:
            notify(
                NTFY_TOPIC,
                f"{track['title']} ({track_id}) decreased on {stats_date}: {last_total:,} -> {total:,}",
                title="Taylor Swift - Stream total decreased",
                tags="warning,chart_increasing",
            )

        if write_history and not _UPDATE_SIGNAL_SENT.is_set():
            _UPDATE_SIGNAL_SENT.set()
            threading.Thread(
                target=_upload_update_signal, args=(stats_date,), daemon=True
            ).start()

        if write_history:
            incremental_publish_update(
                track=track,
                stats_date=stats_date,
                publish_lock=publish_lock,
            )
    else:
        status = "pending"

    return {
        "track_id": track_id,
        "title": track["title"],
        "spotify_url": track["spotify_url"],
        "chart_extra": bool(track.get("chart_extra")),
        "status": status,
        "streams": total,
        "daily_streams": daily,
        "previous_streams": last_total,
        "delta": (total - last_total) if last_total is not None else None,
        "reason": reason,
    }


def build_probe_tracks(
    tracks: list[dict],
    *,
    non_extra_size: int = PROBE_NON_EXTRA_SAMPLE_SIZE,
    recent_track_ids: set[str] | None = None,
) -> list[dict]:
    """Return a random non-extra (chart_extra=False) probe sample when possible."""
    eligible_non_extra = [
        t for t in tracks
        if t.get("track_id") and t.get("spotify_url") and not t.get("chart_extra")
    ]
    recent_track_ids = recent_track_ids or set()

    def pick(eligible: list[dict], size: int, excluded: set[str]) -> list[dict]:
        fresh = [t for t in eligible if t["track_id"] not in recent_track_ids and t["track_id"] not in excluded]
        random.shuffle(fresh)
        selected = fresh[:size]
        if len(selected) < size:
            selected_ids = excluded | {t["track_id"] for t in selected}
            fallback = [t for t in eligible if t["track_id"] not in selected_ids]
            random.shuffle(fallback)
            selected.extend(fallback[: size - len(selected)])
        return selected

    return pick(eligible_non_extra, non_extra_size, set())


def build_chartsnapshot_probe_id_map(tracks: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    tracks_by_id: dict[str, dict] = {}
    id_map: dict[str, str] = {}
    for track in tracks:
        track_id = str(track.get("track_id") or "").strip()
        if not track_id:
            continue
        tracks_by_id[track_id] = track
        id_map[track_id] = track_id
        for historical_id in track.get("historical_track_ids") or []:
            historical_id = str(historical_id).strip()
            if historical_id:
                id_map[historical_id] = track_id
    return tracks_by_id, id_map


def parse_chartsnapshot_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def probe_chartsnapshot_update(
    stats_date: str,
    tracks: list[dict],
    *,
    required_validated: int = CHARTSNAPSHOT_REQUIRED_VALIDATED,
) -> dict:
    previous_stats_date = get_previous_stats_date_str(stats_date)
    tracks_by_id, id_map = build_chartsnapshot_probe_id_map(tracks)
    result = {
        "source": "chartsnapshot",
        "can_start_full_run": False,
        "source_rows": 0,
        "validated_rows": 0,
        "validated_non_extra_rows": 0,
        "external_rows": 0,
        "invalid_rows": 0,
        "missing_previous_rows": 0,
        "mismatch_rows": 0,
        "required_validated": required_validated,
        "results": [],
    }

    session = _requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            CHARTSNAPSHOT_TOP_SONGS_URL,
            params={"artist_uri": CHARTSNAPSHOT_ARTIST_URI, "date": stats_date},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except _requests.HTTPError as exc:
        response = exc.response
        result["error"] = str(exc)
        if response is not None and response.status_code == 403:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            message = str(payload.get("error") or "")
            if "Role 3 or higher required" in message:
                result["blocked_reason"] = "chartsnapshot_role_required"
                result["error"] = message
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
    finally:
        session.close()

    if not isinstance(payload, list):
        result["error"] = f"unexpected_payload:{type(payload).__name__}"
        return result

    result["source_rows"] = len(payload)
    for row in payload:
        source_track_id = str(row.get("track_uri") or "").strip()
        if str(row.get("date") or "").strip() != stats_date:
            result["invalid_rows"] += 1
            continue
        total = parse_chartsnapshot_int(row.get("total_streams"))
        daily = parse_chartsnapshot_int(row.get("daily_streams"))
        if not source_track_id or total is None or daily is None or daily < 0:
            result["invalid_rows"] += 1
            continue
        track_id = id_map.get(source_track_id)
        track = tracks_by_id.get(track_id or "")
        if not track_id or not track:
            result["external_rows"] += 1
            continue
        previous_total = get_history_total_for_date(track_id, previous_stats_date)
        if previous_total is None:
            result["missing_previous_rows"] += 1
            continue
        validated = (total - daily) == previous_total
        result["results"].append({
            "title": track.get("title") or row.get("name") or track_id,
            "track_id": track_id,
            "source_track_id": source_track_id,
            "total": total,
            "daily": daily,
            "previous_total": previous_total,
            "validated": validated,
            "chart_extra": bool(track.get("chart_extra")),
        })
        if not validated:
            result["mismatch_rows"] += 1
            continue
        result["validated_rows"] += 1
        if not track.get("chart_extra"):
            result["validated_non_extra_rows"] += 1
            if result["validated_non_extra_rows"] >= required_validated:
                result["can_start_full_run"] = True
                break

    return result


def build_album_post_priority_track_ids(stats_date: str | None = None) -> set[str]:
    """Return track IDs needed before album-update posts can be generated."""
    priority_ids: set[str] = set()
    target_albums = set(ALBUM_UPDATE_TARGETS)
    sections = load_album_sections_flat()

    for section in sections:
        if section.get("album") not in target_albums:
            continue
        for track in section.get("tracks", []):
            track_id = extract_track_id(track.get("url") or track.get("spotify_url") or "")
            if track_id:
                priority_ids.add(track_id)

    return priority_ids


def print_api_metrics(summary: dict) -> None:
    metrics = summary.get("api_metrics") or {}
    statuses = metrics.get("status_counts") or {}
    status_text = ", ".join(f"{code}={count}" for code, count in statuses.items()) or "none"
    print(
        "API metrics | "
        f"requests={metrics.get('requests', 0)} | "
        f"429={metrics.get('rate_limited', 0)} | "
        f"5xx/408 retries={metrics.get('server_retries', 0)} | "
        f"network errors={metrics.get('network_errors', 0)} | "
        f"token refreshes={metrics.get('token_refreshes', 0)} | "
        f"statuses: {status_text}"
    )


def summary_had_http_status(summary: dict | None, status_code: int) -> bool:
    metrics = (summary or {}).get("api_metrics") or {}
    statuses = metrics.get("status_counts") or {}
    try:
        return int(statuses.get(str(status_code)) or 0) > 0
    except (TypeError, ValueError):
        return False


def retry_pending_tracks_until_collected(
    track_ids: set[str],
    *,
    stats_date: str,
    stats_date_override: str | None,
    token_mgr: TokenManager | None,
    force_reprocess: bool,
    write_history: bool,
    collected_ids: set[str],
    use_browser_scrape: bool = False,
    override_stream_guards: bool = False,
) -> None:
    pending_ids = set(track_ids)
    retry_round = 0
    previous_retry_summary: dict | None = None

    while pending_ids:
        retry_round += 1
        if (
            retry_round > 1
            and PENDING_RETRY_SLEEP_SECONDS > 0
            and summary_had_http_status(previous_retry_summary, 409)
        ):
            time.sleep(PENDING_RETRY_SLEEP_SECONDS)

        print()
        print("=" * 70)
        print(
            f"Infinite top-{INFINITE_RETRY_PREVIOUS_DAY_TOP_N} pending retry round "
            f"{retry_round} ({len(pending_ids)} track(s))"
        )
        print("=" * 70)

        retry_summary = run_update(
            on_progress=ProgressLogger(LOG_MODE),
            stats_date_override=stats_date_override,
            dry_run_mode=False,
            only_track_ids=pending_ids,
            token_mgr=token_mgr,
            force_reprocess=force_reprocess,
            write_history=write_history,
            use_browser_scrape=use_browser_scrape,
            override_stream_guards=override_stream_guards,
        )
        print_summary_block(retry_summary)
        print_api_metrics(retry_summary)
        previous_retry_summary = retry_summary

        updated_ids = set(retry_summary.get("updated_track_ids") or set())
        if updated_ids:
            collected_ids.update(updated_ids)
            push_updated_track_histories_to_r2(
                updated_ids,
                retry_summary["history_index"],
            )

        done_ids = load_history_track_ids_for_date(stats_date)
        pending_ids -= done_ids

    print(f"All infinite top-{INFINITE_RETRY_PREVIOUS_DAY_TOP_N} pending retries are collected.")


def run_discography_backfill_after_streams(token_mgr: TokenManager | None, stats_date: str) -> None:
    if token_mgr is None:
        return
    tokens = token_mgr.get()
    if not tokens.get("bearer") or not tokens.get("client_token"):
        return

    print("Running Spotify discography backfill after streams posting...")
    try:
        result = run_discography_backfill(
            apply=True,
            no_backup=False,
            include_non_songs=False,
            skip_api=False,
            tokens=tokens,
            recent_release_limit=12,
            target_release_date=stats_date,
            expand_target_date=True,
            verbose=False,
        )
    except Exception as exc:
        print(f"[discography] Backfill failed (non-blocking): {exc}")
        return

    print(
        "[discography] "
        f"db_duplicates_removed={result.get('db_duplicates_removed', 0)} | "
        f"release_date_updates={result.get('updates', 0)} | "
        f"additions={result.get('additions', 0)} | "
        f"matched_existing={result.get('matched_existing_by_title_streams', 0)} | "
        f"written_files={result.get('written_files', 0)}"
    )


def run_new_release_preflight(token_mgr: TokenManager | None, stats_date: str) -> set[str]:
    if token_mgr is None:
        return set()
    tokens = token_mgr.get()
    if not tokens.get("bearer") or not tokens.get("client_token"):
        return set()

    print("Checking recent Spotify releases before stream collection...")
    scan_dates = [stats_date]
    previous_stats_date = get_previous_stats_date_str(stats_date)
    if previous_stats_date not in scan_dates:
        scan_dates.append(previous_stats_date)

    added_ids: set[str] = set()
    totals = {
        "db_duplicates_removed": 0,
        "updates": 0,
        "additions": 0,
    }
    for scan_date in scan_dates:
        try:
            result = run_discography_backfill(
                apply=True,
                no_backup=False,
                include_non_songs=False,
                skip_api=False,
                tokens=tokens,
                recent_release_limit=12,
                target_release_date=scan_date,
                expand_target_date=True,
                verbose=False,
            )
        except Exception as exc:
            print(f"[discography] Recent release preflight failed for {scan_date} (non-blocking): {exc}")
            continue

        added_ids.update(str(track_id) for track_id in (result.get("added_track_ids") or []) if str(track_id))
        added_ids.update(str(track_id) for track_id in (result.get("resolved_existing_url_ids") or []) if str(track_id))
        for key in totals:
            totals[key] += int(result.get(key, 0) or 0)

    print(
        "[discography] recent preflight | "
        f"scan_dates={','.join(scan_dates)} | "
        f"db_duplicates_removed={totals['db_duplicates_removed']} | "
        f"release_date_updates={totals['updates']} | "
        f"additions={totals['additions']} | "
        f"new_track_ids={len(added_ids)}"
    )
    return added_ids

def filter_tracks_released_on(track_ids: set[str], target_date: str) -> set[str]:
    if not track_ids:
        return set()

    paths = sorted(DB_ALBUMS_DIR.glob("*.json"))
    for extra_path in (DB_SONGS_JSON, DISCOGRAPHY_DIR / "features.json", DISCOGRAPHY_DIR / "misc.json"):
        if extra_path.exists():
            paths.append(extra_path)

    released: set[str] = set()
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        sections = payload.get("sections") if isinstance(payload, dict) else payload
        for section in sections if isinstance(sections, list) else []:
            for track in section.get("tracks") or []:
                track_id = extract_track_id(track.get("url") or track.get("spotify_url") or "")
                if track_id in track_ids and is_recent_release_date(track.get("release_date"), target_date):
                    released.add(track_id)
    return released


def track_is_released_for_stats_date(track: dict, stats_date: str) -> bool:
    release_raw = str(track.get("release_date") or "").strip()
    if not release_raw:
        return True
    try:
        release_day = date.fromisoformat(release_raw[:10])
        target_day = date.fromisoformat(stats_date)
    except ValueError:
        return True
    return release_day <= target_day


def filter_tracks_released_for_stats_date(tracks: list[dict], stats_date: str) -> list[dict]:
    kept = [track for track in tracks if track_is_released_for_stats_date(track, stats_date)]
    skipped = len(tracks) - len(kept)
    if skipped:
        print(f"[discography] Skipping {skipped} unreleased track(s) for stats_date={stats_date}.")
    return kept


def filter_tracks_without_history_before(track_ids: set[str], target_date: str) -> set[str]:
    if not track_ids:
        return set()

    seen_before: set[str] = set()
    for row in load_history_rows():
        track_id = str(row.get("track_id") or "").strip()
        row_date = str(row.get("date") or "").strip()
        try:
            streams = int(str(row.get("streams") or "0").strip() or "0")
        except ValueError:
            streams = 0
        if track_id in track_ids and row_date and row_date < target_date and streams > 0:
            seen_before.add(track_id)

    return set(track_ids) - seen_before


def load_positive_history_track_ids_for_date(target_date: str) -> set[str]:
    positive_ids: set[str] = set()
    for row in load_history_rows():
        track_id = str(row.get("track_id") or "").strip()
        row_date = str(row.get("date") or "").strip()
        if not track_id or row_date != target_date:
            continue
        try:
            streams = int(str(row.get("streams") or "0").strip() or "0")
        except ValueError:
            streams = 0
        if streams > 0:
            positive_ids.add(track_id)
    return positive_ids


def load_positive_history_track_ids_missing_daily_for_date(target_date: str) -> set[str]:
    missing_ids: set[str] = set()
    for row in load_history_rows():
        track_id = str(row.get("track_id") or "").strip()
        row_date = str(row.get("date") or "").strip()
        if not track_id or row_date != target_date:
            continue
        try:
            streams = int(row.get("streams") or 0)
        except Exception:
            streams = 0
        daily_raw = str(row.get("daily_streams") or "").strip()
        if streams > 0 and not daily_raw:
            missing_ids.add(track_id)
    return missing_ids


def load_daily_streams_by_track_for_date(target_date: str) -> dict[str, int]:
    daily_by_track: dict[str, int] = {}
    for row in load_history_rows():
        track_id = str(row.get("track_id") or "").strip()
        row_date = str(row.get("date") or "").strip()
        if not track_id or row_date != target_date:
            continue
        try:
            daily = int(str(row.get("daily_streams") or "").strip())
        except ValueError:
            continue
        daily_by_track[track_id] = daily
    return daily_by_track


def build_priority_best_day_track_ids(
    tracks: list[dict],
    stats_date: str,
    *,
    min_days_since: int = EARLY_BEST_DAY_PRIORITY_AFTER_DAYS,
    min_recent_peak_ratio: float = EARLY_BEST_DAY_PRIORITY_RECENT_PEAK_RATIO,
) -> list[str]:
    """Tracks that can plausibly hit the always-post long best-day rule.

    This is only an early-post priority list: the post script still recomputes
    the exact best-day-since row after today's daily is written.
    """
    try:
        target_day = date.fromisoformat(stats_date)
    except ValueError:
        return []

    active_ids = {
        str(track.get("track_id") or "").strip()
        for track in tracks
        if track.get("track_id") and not track.get("chart_extra")
    }
    if not active_ids:
        return []

    cutoff_day = target_day - timedelta(days=min_days_since)
    points_by_track: dict[str, list[tuple[date, int]]] = {}
    for row in load_history_rows():
        track_id = str(row.get("track_id") or "").strip()
        row_date = str(row.get("date") or "").strip()
        if track_id not in active_ids or not row_date or row_date >= stats_date:
            continue
        try:
            row_day = date.fromisoformat(row_date)
            daily = int(str(row.get("daily_streams") or "").strip())
        except ValueError:
            continue
        if daily <= 0:
            continue
        points_by_track.setdefault(track_id, []).append((row_day, daily))

    candidates: list[tuple[int, int, str]] = []
    for track_id, points in points_by_track.items():
        recent = [(day, daily) for day, daily in points if cutoff_day <= day < target_day]
        older = [(day, daily) for day, daily in points if day < cutoff_day]
        if not recent or not older:
            continue

        recent_peak = max(daily for _day, daily in recent)
        previous_day_daily = next((daily for day, daily in points if day == target_day - timedelta(days=1)), None)
        if previous_day_daily is None:
            continue

        older_peak = max(daily for _day, daily in older)
        if older_peak <= recent_peak:
            continue
        if previous_day_daily < recent_peak * min_recent_peak_ratio:
            continue

        last_older_above_recent_peak = max(
            day for day, daily in older if daily > recent_peak
        )
        potential_days_since = (target_day - last_older_above_recent_peak).days
        if potential_days_since <= min_days_since:
            continue

        # Put near-record low-volume songs first, then the oldest potential records.
        gap_to_recent_peak = max(0, recent_peak - previous_day_daily)
        candidates.append((gap_to_recent_peak, -potential_days_since, track_id))

    candidates.sort()
    return [track_id for _gap, _days, track_id in candidates]


def build_early_best_day_track_ids(
    tracks: list[dict],
    stats_date: str,
    *,
    min_previous_daily_streams: int = EARLY_BEST_DAY_MIN_DAILY_STREAMS,
    limit: int = EARLY_BEST_DAY_TRACK_LIMIT,
) -> list[str]:
    """Limit early best-day checks to plausible high-volume candidates.

    The final best-day step still scans every eligible track after collection,
    so this only reduces mid-run subprocess/export churn.
    """
    previous_stats_date = get_previous_stats_date_str(stats_date)
    previous_daily = load_daily_streams_by_track_for_date(previous_stats_date)
    active_ids = {
        str(track.get("track_id") or "").strip()
        for track in tracks
        if track.get("track_id") and not track.get("chart_extra")
    }
    candidates = [
        (track_id, daily)
        for track_id, daily in previous_daily.items()
        if track_id in active_ids and daily >= min_previous_daily_streams
    ]
    candidates.sort(key=lambda item: item[1], reverse=True)
    return [track_id for track_id, _daily in candidates[:limit]]


def _format_pct_value(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def _format_grower_day(value: date) -> str:
    return f"{value.month}/{value.day}"


def _add_best_day_labels_to_grower_rows(rows: list[dict], stats_date: str) -> None:
    if not rows:
        return
    try:
        import best_day_since

        target_day = date.fromisoformat(stats_date)
        tracks = best_day_since.load_tracks(include_extras=False)
        history = best_day_since.load_history()
        for row in rows:
            track_id = row["track_id"]
            track = tracks.get(track_id)
            if track is None:
                continue
            best_row = best_day_since.compute_best_day_since(
                track,
                history.get(track_id) or [],
                target_day,
            )
            if best_row and best_day_since.passes_filters(
                best_row,
                min_days=best_day_since.DEFAULT_MIN_DAYS,
            ):
                row["best_day_label"] = best_day_since.row_label(best_row)
    except Exception as exc:
        print(f"[grower_notify] best-day checks failed: {exc}")


def _collect_daily_growers_for_notify(
    stats_date: str,
    *,
    limit: int = GROWER_NOTIFY_LIMIT,
    window_days: int = GROWER_NOTIFY_WINDOW_DAYS,
    min_baseline_daily: int = GROWER_NOTIFY_MIN_BASELINE_DAILY,
) -> list[dict]:
    """Top daily growers with a complete exact recent daily window."""
    try:
        target_day = date.fromisoformat(stats_date)
    except ValueError:
        return []

    history = HistoryIndex.load()
    album_ids = load_album_track_ids()
    tracks = [
        track
        for track in load_tracks_from_discography(album_ids)
        if track.get("track_id") in album_ids and not track.get("chart_extra")
    ]

    rows: list[dict] = []
    for track in tracks:
        track_id = str(track.get("track_id") or "").strip()
        if not track_id:
            continue

        daily_today = _history_store._daily_for_spotlight(history, track_id, stats_date)
        daily_yesterday = _history_store._daily_for_spotlight(
            history,
            track_id,
            str(target_day - timedelta(days=1)),
        )
        if daily_today is None or daily_yesterday is None or daily_yesterday < min_baseline_daily:
            continue
        gain = daily_today - daily_yesterday
        if gain <= 0:
            continue

        window: list[dict] = []
        complete_window = True
        for offset in range(window_days - 1, -1, -1):
            day = target_day - timedelta(days=offset)
            day_str = day.isoformat()
            daily = _history_store._daily_for_spotlight(history, track_id, day_str)
            if daily is None or daily <= 0:
                complete_window = False
                break
            previous_daily = _history_store._daily_for_spotlight(
                history,
                track_id,
                str(day - timedelta(days=1)),
            )
            pct = (
                (daily - previous_daily) / previous_daily * 100
                if previous_daily is not None and previous_daily > 0
                else None
            )
            window.append({"date": day, "daily": daily, "pct": pct})
        if not complete_window:
            continue

        pct_today = gain / daily_yesterday * 100
        rows.append({
            "track": track,
            "track_id": track_id,
            "daily_today": daily_today,
            "daily_yesterday": daily_yesterday,
            "gain": gain,
            "pct": pct_today,
            "window": window,
        })

    rows.sort(key=lambda row: (row["pct"], row["gain"], row["daily_today"]), reverse=True)
    picked = rows[:limit]
    _add_best_day_labels_to_grower_rows(picked, stats_date)
    return picked


def notify_daily_growers(stats_date: str) -> None:
    rows = _collect_daily_growers_for_notify(stats_date)
    if not rows:
        print(f"[grower_notify] No complete grower candidates for {stats_date}.")
        return

    lines = [f"Top {len(rows)} growers for {stats_date}:"]
    for index, row in enumerate(rows, 1):
        track = row["track"]
        title = track.get("title") or row["track_id"]
        lines.append(
            f"{index}. {title}: {format_int(row['daily_today'])} "
            f"({_format_pct_value(row['pct'])}, +{format_int(row['gain'])})"
        )
        trend = " | ".join(
            f"{_format_grower_day(day['date'])} {format_int(day['daily'])} [{_format_pct_value(day['pct'])}]"
            for day in row["window"]
        )
        lines.append(f"   {trend}")
        if row.get("best_day_label"):
            lines.append(f"   Best-day: {row['best_day_label']}")

    notify(
        NTFY_TOPIC,
        "\n".join(lines),
        title=f"Taylor Swift - Top growers {stats_date}",
        tags="chart_increasing,musical_note",
    )


def recent_release_track_ids_missing_daily(stats_date: str) -> set[str]:
    active_track_ids = load_active_track_ids_from_discography()
    recent_release_ids = filter_tracks_released_on(active_track_ids, stats_date)
    return recent_release_ids & load_positive_history_track_ids_missing_daily_for_date(stats_date)


def repair_missing_daily_streams_for_date(stats_date: str, track_ids: set[str] | None = None) -> set[str]:
    target_ids = {str(tid) for tid in (track_ids or set()) if str(tid)}
    rows = load_history_rows()
    if not rows:
        return set()

    prior_totals: dict[str, tuple[date, int]] = {}
    target_day = date.fromisoformat(stats_date)
    for row in rows:
        track_id = str(row.get("track_id") or "").strip()
        row_date = str(row.get("date") or "").strip()
        if not track_id or not row_date:
            continue
        if target_ids and track_id not in target_ids:
            continue
        try:
            row_day = date.fromisoformat(row_date)
            streams = int(row.get("streams") or 0)
        except Exception:
            continue
        if row_day >= target_day:
            continue
        previous = prior_totals.get(track_id)
        if previous is None or row_day > previous[0]:
            prior_totals[track_id] = (row_day, streams)

    repaired_ids: set[str] = set()
    for row in rows:
        track_id = str(row.get("track_id") or "").strip()
        if not track_id or (target_ids and track_id not in target_ids):
            continue
        if str(row.get("date") or "").strip() != stats_date:
            continue
        if str(row.get("daily_streams") or "").strip():
            continue
        try:
            streams = int(row.get("streams") or 0)
        except Exception:
            continue
        prior = prior_totals.get(track_id)
        if streams <= 0 or prior is None:
            continue
        daily = streams - prior[1]
        if daily < 0 or daily > MAX_DAILY_INCREASE:
            continue
        row["daily_streams"] = str(daily)
        repaired_ids.add(track_id)

    if not repaired_ids:
        return set()

    save_history_rows(rows)

    day_rows = [
        row for row in rows
        if str(row.get("date") or "").strip() == stats_date
    ]
    daily_path = update_streams_dir(stats_date) / "streams_history.csv"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    with daily_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["date", "track_id", "streams", "daily_streams", "estimated", "estimated_reason"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in day_rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})

    return repaired_ids


def _is_estimated_history_row(row: dict) -> bool:
    return str(row.get("estimated") or "").strip().lower() in {"1", "true", "yes", "y"}


def _estimate_daily_weight(points: list[dict], target_day: date) -> int:
    same_weekday: list[tuple[int, int]] = []
    recent: list[tuple[int, int]] = []

    for point in points:
        if _is_estimated_history_row(point):
            continue
        daily_raw = str(point.get("daily_streams") or "").strip()
        if not daily_raw:
            continue
        try:
            point_day = date.fromisoformat(str(point.get("date") or ""))
            daily = int(daily_raw)
        except Exception:
            continue
        if point_day >= target_day or daily < 0:
            continue
        days_back = (target_day - point_day).days
        if point_day.weekday() == target_day.weekday() and days_back <= 7 * 8:
            same_weekday.append((days_back, daily))
        if days_back <= 35:
            recent.append((days_back, daily))

    same_weekday.sort(key=lambda item: item[0])
    if same_weekday:
        weighted = same_weekday[:4]
        total_weight = 0
        total_value = 0
        for idx, (_, daily) in enumerate(weighted):
            weight = len(weighted) - idx
            total_weight += weight
            total_value += daily * weight
        return max(1, round(total_value / total_weight))

    recent.sort(key=lambda item: item[0])
    if recent:
        values = [daily for _, daily in recent[:7]]
        return max(1, round(sum(values) / len(values)))

    return 1


def estimate_missing_stream_history_gaps(max_gap_days: int = MAX_ESTIMATED_STREAM_GAP_DAYS) -> set[str]:
    # Disabled: streams_history.csv must only ever contain real Spotify totals,
    # never invented/interpolated values, even to backfill a gap.
    return set()


def recent_release_track_ids_missing_positive_history(stats_date: str) -> set[str]:
    active_track_ids = load_active_track_ids_from_discography()
    recent_release_ids = filter_tracks_released_on(active_track_ids, stats_date)
    return recent_release_ids - load_positive_history_track_ids_for_date(stats_date)


def _worker(
    queue,
    results,
    failed_results,
    lock,
    publish_lock,
    on_progress,
    total_tracks,
    dry_run_mode=False,
    worker_id: int = 0,
    adaptive: "AdaptiveWorkerState | None" = None,
    priority_top_50_ids: frozenset = frozenset(),
    pre_scraped: dict | None = None,
    token_mgr: "TokenManager | None" = None,
    history_index: HistoryIndex | None = None,
    api_run_metrics: ApiRunMetrics | None = None,
    write_history: bool = True,
    compare_before_stats_date: bool = False,
    use_browser_scrape: bool = False,
    cover_updates: dict | None = None,
    override_stream_guards: bool = False,
):
    if adaptive is not None:
        while True:
            with adaptive.lock:
                if worker_id < adaptive.target:
                    break
            time.sleep(0.1)

    _api_session = _requests.Session()
    _adapter = HTTPAdapter(pool_connections=1, pool_maxsize=4)
    _api_session.mount("https://", _adapter)
    _playwright = None
    _browser = None
    _browser_context = None
    _browser_page = None

    if use_browser_scrape:
        _playwright = sync_playwright().start()
        _browser = launch_browser(_playwright, headless=HEADLESS)
        _browser_context = _browser.new_context(locale="fr-FR")
        _browser_page = _browser_context.new_page()
        _browser_page.route("**/*", block_unneeded)

    try:
        while True:
            if adaptive is not None:
                while True:
                    with adaptive.lock:
                        target = adaptive.target
                    if worker_id < target:
                        break
                    time.sleep(0.1)

            try:
                item = queue.get_nowait()
            except Empty:
                break

            i = item["index"]
            track = item["track"]
            stats_date = item["stats_date"]
            log_title = f"{track['title']} [{track['track_id'][-6:]}]"

            if on_progress:
                on_progress(i, total_tracks, log_title, None)

            # Use pre-scraped value from artist page if available
            api_metrics = {"had_429": False}
            if pre_scraped and track["track_id"] in pre_scraped:
                total = pre_scraped[track["track_id"]]
                raw = str(total)
                scrape_status = "ok"
                if LOG_MODE == "verbose":
                    print(f"  [pre-scraped] {track['title']} -> {total:,}")
            elif use_browser_scrape and _browser_page is not None:
                total, raw, scrape_status, _ = scrape_track_total(
                    _browser_page,
                    track["title"],
                    track["spotify_url"],
                )
            elif token_mgr is not None and token_mgr.available:
                # fetch_playcount_api already handles transient retries; failures get a run-level retry pass.
                api_result = fetch_playcount_api(
                    track["track_id"],
                    token_mgr,
                    _api_session,
                    metrics=api_metrics,
                )
                if api_result is not None:
                    total, raw, scrape_status = api_result, str(api_result), "ok"
                else:
                    scrape_status = "error"
                    total = None
                    raw = "rate_limited" if int(api_metrics.get("rate_limited") or 0) > 0 else ""
            else:
                scrape_status = "error"
                total, raw = None, ""

            if adaptive is not None:
                adaptive.record(got_429=bool(api_metrics.get("had_429")))
            if api_run_metrics is not None:
                api_run_metrics.add(api_metrics)
            if cover_updates is not None and api_metrics.get("cover_url"):
                with lock:
                    cover_updates[track["track_id"]] = api_metrics["cover_url"]

            if scrape_status == "timeout":
                result = {
                    "track_id": track["track_id"],
                    "title": track["title"],
                    "spotify_url": track["spotify_url"],
                    "status": "timeout",
                }
                with lock:
                    failed_results.append(dict(result))

            elif scrape_status == "error":
                result = {
                    "track_id": track["track_id"],
                    "title": track["title"],
                    "spotify_url": track["spotify_url"],
                    "status": "error",
                    "reason": raw or "api_error",
                }
                with lock:
                    failed_results.append(dict(result))

            elif scrape_status == "not_found" or total is None:
                # Retry API immÃ©diat pour les tracks du top-50
                if (
                    not use_browser_scrape
                    and track["track_id"] in priority_top_50_ids
                    and token_mgr is not None
                    and token_mgr.available
                ):
                    for _retry in range(2):
                        api_result = fetch_playcount_api(
                            track["track_id"],
                            token_mgr,
                            _api_session,
                            metrics=api_metrics,
                        )
                        if api_result is not None:
                            total, raw, scrape_status = api_result, str(api_result), "ok"
                            break

                if scrape_status == "ok" and total is not None:
                    result = try_apply_track_update(
                        track=track,
                        total=total,
                        stats_date=stats_date,
                        lock=lock,
                        publish_lock=publish_lock,
                        history_index=history_index,
                        dry_run_mode=dry_run_mode,
                        write_history=write_history,
                        compare_before_stats_date=compare_before_stats_date,
                        override_stream_guards=override_stream_guards,
                    )
                    result["raw"] = raw
                else:
                    result = {
                        "track_id": track["track_id"],
                        "title": track["title"],
                        "spotify_url": track["spotify_url"],
                        "status": "not_found",
                    }
                    with lock:
                        failed_results.append(dict(result))

            else:
                result = try_apply_track_update(
                    track=track,
                    total=total,
                    stats_date=stats_date,
                    lock=lock,
                    publish_lock=publish_lock,
                    history_index=history_index,
                    dry_run_mode=dry_run_mode,
                    write_history=write_history,
                    compare_before_stats_date=compare_before_stats_date,
                    override_stream_guards=override_stream_guards,
                )
                result["raw"] = raw

            with lock:
                results[i - 1] = result

            if on_progress:
                on_progress(i, total_tracks, log_title, result)

            queue.task_done()

    finally:
        if LOG_MODE == "verbose":
            print("Worker finished.")
        try:
            _api_session.close()
        except Exception:
            pass
        try:
            if _browser is not None:
                _browser.close()
        except Exception:
            pass
        try:
            if _playwright is not None:
                _playwright.stop()
        except Exception:
            pass


def run_update(
    on_progress=None,
    skip_track_ids: set[str] | None = None,
    stats_date_override: str | None = None,
    dry_run_mode: bool = False,
    only_track_ids: set[str] | None = None,
    token_mgr: "TokenManager | None" = None,
    force_reprocess: bool = False,
    write_history: bool = True,
    use_browser_scrape: bool = False,
    override_stream_guards: bool = False,
):
    ensure_history_file()
    removed_duplicates = dedupe_history_rows_by_date_track()
    if removed_duplicates > 0:
        print(f"History dedupe: removed {removed_duplicates} duplicate row(s) by (date, track_id).")
    history_index = HistoryIndex.load()

    stats_date = stats_date_override or get_stats_date_str()
    configure_daily_data_paths(stats_date)
    skip_track_ids = skip_track_ids or set()

    active_track_ids = load_active_track_ids_from_discography()
    tracks = load_tracks_from_discography(active_track_ids)
    tracks = filter_tracks_released_for_stats_date(tracks, stats_date)
    total_all_tracks = len(tracks)

    previous_day_priorities = load_track_priorities_from_specific_date(
        get_previous_stats_date_str(stats_date)
    )
    album_post_priority_ids = build_album_post_priority_track_ids(stats_date)
    tracks.sort(
        key=lambda t: (
            t["track_id"] not in album_post_priority_ids,
            -previous_day_priorities.get(t["track_id"], 0),
            t["title"].casefold(),
        )
    )

    if only_track_ids is not None:
        tracks = [t for t in tracks if t["track_id"] in only_track_ids]

    total_tracks = len(tracks)

    priority_top_50_ids = get_priority_top_50_track_ids_from_previous_day(tracks, stats_date)

    if priority_top_50_ids and len(priority_top_50_ids) < 50:
        print(f"Warning: only {len(priority_top_50_ids)} priority track(s) found from previous day.")

    pre_scraped: dict[str, int] = {}
    api_run_metrics = ApiRunMetrics()

    already_done_for_stats_date = history_index.done_ids_for_date(stats_date)

    queue = Queue()
    failed_results: list[dict] = []
    cover_updates: dict[str, str] = {}
    results = [None] * total_tracks

    for index, track in enumerate(tracks, 1):
        log_title = f"{track['title']} [{track['track_id'][-6:]}]"

        if (not force_reprocess and track["track_id"] in already_done_for_stats_date) or track["track_id"] in skip_track_ids:
            results[index - 1] = {
                "track_id": track["track_id"],
                "title": track["title"],
                "spotify_url": track["spotify_url"],
                "status": "skipped",
            }
            if on_progress:
                on_progress(index, total_tracks, log_title, results[index - 1])
            continue

        queue.put({
            "index": index,
            "track": track,
            "stats_date": stats_date,
        })

    if queue.qsize() > 0:
        lock = threading.Lock()
        publish_lock = threading.Lock()
        worker_cap = 4 if use_browser_scrape else MAX_PARALLEL_PAGES
        initial_workers = min(HILL_INITIAL, worker_cap, queue.qsize())
        max_workers     = min(worker_cap, queue.qsize())
        adaptive = AdaptiveWorkerState(initial=initial_workers)
        if use_browser_scrape:
            print("Using browser page scrape for playcounts (API fallback mode).")

        # Spawner max_workers threads dÃ¨s maintenant.
        # Les workers avec worker_id >= initial_workers attendent leur activation (pas de fenÃªtre ouverte).
        workers = [
            threading.Thread(
                target=_worker,
                args=(
                    queue,
                    results,
                    failed_results,
                    lock,
                    publish_lock,
                    on_progress,
                    total_tracks,
                    dry_run_mode,
                    idx,
                    adaptive,
                    priority_top_50_ids,
                    pre_scraped,
                    token_mgr,
                    history_index,
                    api_run_metrics,
                    write_history,
                    force_reprocess,
                    use_browser_scrape,
                    cover_updates,
                    override_stream_guards,
                ),
                daemon=True,
            )
            for idx in range(max_workers)
        ]

        for w in workers:
            w.start()

        print(f"Waiting for {max_workers} worker(s) to finish (hill climbing actif, init={initial_workers})...")
        queue.join()
        for w in workers:
            w.join(timeout=5)
        print("All worker threads joined.")

        # â”€â”€ Retry failures (2Ã¨me passe, 30s d'attente, 3 workers max) â”€â”€â”€â”€â”€â”€
        retry_candidates = [
            r for r in failed_results
            if r.get("status") in {"not_found", "timeout", "error"}
        ]
        if retry_candidates:
            print(
                f"\n  {len(retry_candidates)} failure(s) â€” retry immÃ©diat avec {min(6, len(retry_candidates))} workers..."
            )

            retry_queue: Queue = Queue()
            for idx, r in enumerate(retry_candidates, 1):
                retry_queue.put({
                    "index": idx,
                    "track": {"title": r["title"], "track_id": r["track_id"], "spotify_url": r["spotify_url"]},
                    "stats_date": stats_date,
                })

            retry_total   = retry_queue.qsize()
            retry_results = [None] * retry_total
            retry_failed: list[dict] = []
            retry_adaptive = AdaptiveWorkerState(initial=min(6, retry_total))
            retry_worker_cap = min(4 if use_browser_scrape else 6, retry_total)
            retry_workers  = [
                threading.Thread(
                    target=_worker,
                    args=(retry_queue, retry_results, retry_failed, lock, publish_lock,
                          None, retry_total, dry_run_mode, idx, retry_adaptive,
                          frozenset(), None, token_mgr, history_index,
                          api_run_metrics, write_history, force_reprocess,
                          use_browser_scrape, cover_updates,
                          override_stream_guards),
                    daemon=True,
                )
                for idx in range(retry_worker_cap)
            ]
            for w in retry_workers:
                w.start()
            retry_queue.join()
            for w in retry_workers:
                w.join(timeout=5)

            # Fusionner dans failed_results : retirer les candidats rÃ©solus
            resolved_ids = {
                r["track_id"]
                for r in retry_results
                if r and r.get("status") not in (None, "not_found", "timeout", "error")
            }
            failed_results[:] = [r for r in failed_results if r.get("track_id") not in resolved_ids]
            print(
                f"  Retry terminÃ© : {len(resolved_ids)} rÃ©cupÃ©rÃ©s, {len(retry_candidates) - len(resolved_ids)} encore en Ã©chec"
            )

    if cover_updates:
        merge_track_cover_cache(cover_updates)

    final_done_for_stats_date = history_index.done_ids_for_date(stats_date)
    filtered_results = [r for r in results if r is not None]
    updated_track_ids = {
        r["track_id"] for r in filtered_results
        if r and r.get("status") == "updated"
    }
    if write_history:
        updated_track_ids.update(final_done_for_stats_date - already_done_for_stats_date)

    return {
        "stats_date": stats_date,
        "total_tracks": total_tracks,
        "total_all_tracks": total_all_tracks,
        "done_tracks": len(final_done_for_stats_date),
        "remaining_tracks": max(total_tracks - len([r for r in filtered_results if r["status"] in {"updated", "skipped"}]), 0),
        "all_done": len([r for r in filtered_results if r["status"] in {"updated", "skipped"}]) >= total_tracks,
        "updated_this_run": sum(1 for r in filtered_results if r["status"] == "updated"),
        "pending_this_run": sum(1 for r in filtered_results if r["status"] == "pending"),
        "skipped_this_run": sum(1 for r in filtered_results if r["status"] == "skipped"),
        "timeout_this_run": len([r for r in failed_results if r["status"] == "timeout"]),
        "error_this_run": len([r for r in failed_results if r["status"] == "error"]),
        "not_found_this_run": len([r for r in failed_results if r["status"] == "not_found"]),
        "results": filtered_results,
        "failed_results": failed_results,
        "updated_track_ids": updated_track_ids,
        "history_index": history_index,
        "api_metrics": api_run_metrics.snapshot(),
    }


def run_debug_total_replace(stats_date: str) -> None:
    ensure_history_file()

    target_track_ids = load_history_track_ids_for_date(stats_date)
    if not target_track_ids:
        print(f"No rows found for {stats_date} in streams_history.csv.")
        return

    active_track_ids = load_active_track_ids_from_discography()
    tracks = load_tracks_from_discography(active_track_ids)
    tracks = [t for t in tracks if t["track_id"] in target_track_ids]
    tracks.sort(key=lambda t: t["title"].casefold())

    print(f"[DEBUG-TOTAL] Re-scraping {len(tracks)} track(s) for {stats_date}...")

    summary = run_update(
        on_progress=ProgressLogger(LOG_MODE),
        stats_date_override=stats_date,
        dry_run_mode=True,
        only_track_ids=target_track_ids,
    )

    rows = load_history_rows()
    replacements: dict[str, dict] = {}

    for r in summary["results"]:
        if not r or r.get("status") not in {"updated", "pending"}:
            continue

        track_id = r.get("track_id")
        new_total = r.get("streams")
        if not track_id or new_total is None:
            continue

        prev_total = get_previous_total_before_date(track_id, stats_date)
        new_daily = compute_daily(prev_total, new_total)

        replacements[track_id] = {
            "streams": str(new_total),
            "daily_streams": "" if new_daily is None else str(new_daily),
            "previous_streams": prev_total,
        }

    replaced_count = 0
    for row in rows:
        if (row.get("date") or "").strip() != stats_date:
            continue
        track_id = (row.get("track_id") or "").strip()
        repl = replacements.get(track_id)
        if not repl:
            continue
        row["streams"] = repl["streams"]
        row["daily_streams"] = repl["daily_streams"]
        replaced_count += 1

    save_history_rows(rows)

    log_path = DATA_DIR / f"debug_total_replace_{stats_date}.csv"
    with log_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "track_id", "previous_total_before_date", "new_total", "new_daily", "status", "reason"])
        for r in summary["results"]:
            if not r:
                continue
            track_id = r.get("track_id")
            repl = replacements.get(track_id, {})
            writer.writerow([
                r.get("title", ""),
                track_id or "",
                repl.get("previous_streams", ""),
                r.get("streams", ""),
                repl.get("daily_streams", ""),
                r.get("status", ""),
                r.get("reason", ""),
            ])

    print(f"[DEBUG-TOTAL] Replaced {replaced_count} row(s) for {stats_date}.")
    print(f"[DEBUG-TOTAL] Log written: {log_path}")


def main():
    global START_TIME
    START_TIME = time.perf_counter()

    if "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        return

    ensure_history_file()

    global LOG_MODE
    if "--quiet" in sys.argv:
        LOG_MODE = "quiet"
    if "--verbose" in sys.argv:
        LOG_MODE = "verbose"
    _history_store.LOG_MODE = LOG_MODE
    _page_scraper.LOG_MODE = LOG_MODE
    _page_scraper.DEBUG_PAGE_PREVIEW = DEBUG_PAGE_PREVIEW
    _spotify_api.LOG_MODE = LOG_MODE

    debug_daily_mode = "--debug-daily" in sys.argv
    debug_total_mode = "--debug-total" in sys.argv
    dry_run_mode = "--dry-run" in sys.argv
    local_test_mode = "--local-test" in sys.argv
    test_mode = "--test" in sys.argv
    no_post_mode = "--no-post" in sys.argv
    override_stream_guards = "--over" in sys.argv or "--override-stream-guards" in sys.argv
    admin_override_mode = "--admin" in sys.argv
    throwback_mode = "--throwback" in sys.argv
    throwback_force = "--force" in sys.argv or "--throwback-force" in sys.argv
    reset_last_date_mode = "--reset-last-date" in sys.argv
    write_history = not local_test_mode
    force_reprocess = local_test_mode or ("--force" in sys.argv and not throwback_mode)

    if admin_override_mode:
        override_stream_guards = True
        force_reprocess = True
    global ADMIN_OVERRIDE_MODE
    ADMIN_OVERRIDE_MODE = admin_override_mode

    if test_mode:
        no_post_mode = True
    if local_test_mode:
        no_post_mode = True
    if admin_override_mode:
        print(
            "WARNING: --admin enabled; accepting Spotify's raw total as-is for targeted tracks, "
            "including a negative daily_streams if the total dropped (marked estimated_reason=admin_override)."
        )
    elif override_stream_guards:
        print("WARNING: --over enabled; stream safety guards are disabled for accepted scrape totals.")

    if debug_daily_mode and debug_total_mode:
        print("Use either --debug-daily or --debug-total, not both.")
        sys.exit(1)
    if local_test_mode and (debug_daily_mode or debug_total_mode or dry_run_mode):
        print("Use --local-test by itself (optionally with a date, --quiet, or --verbose).")
        sys.exit(1)
    if test_mode and (debug_daily_mode or debug_total_mode or dry_run_mode or local_test_mode or throwback_mode):
        print("Use --test by itself (optionally with a date, --quiet, or --verbose).")
        sys.exit(1)

    remaining_args = [
        a for a in sys.argv[1:]
        if a not in (
            "--debug-daily",
            "--debug-total",
            "--dry-run",
            "--local-test",
            "--test",
            "--no-post",
            "--over",
            "--override-stream-guards",
            "--admin",
            "--throwback",
            "--throwback-force",
            "--force",
            "--reset-last-date",
            "--quiet",
            "--verbose",
            "--help",
            "-h",
        )
    ]

    stats_date_override = None
    reset_date_override = None
    throwback_action = None
    throwback_event = None
    throwback_label = None
    post_only_steps_raw = None

    i = 0
    while i < len(remaining_args):
        arg = remaining_args[i]

        if arg == "--post-only":
            # Accepte les Ã©tapes sÃ©parÃ©es par virgules et/ou espaces (PowerShell
            # Ã©clate Â« a, b, c Â» en plusieurs arguments) ; une date ISO ou un
            # flag arrÃªte la consommation.
            j = i + 1
            value_parts: list[str] = []
            while j < len(remaining_args):
                nxt = remaining_args[j]
                if nxt.startswith("-"):
                    break
                try:
                    date.fromisoformat(nxt)
                    break
                except ValueError:
                    pass
                value_parts.append(nxt)
                j += 1
            if not value_parts:
                print("Missing value after --post-only (e.g. --post-only top-eras or --post-only top20,gainers)")
                sys.exit(1)
            post_only_steps_raw = ",".join(value_parts)
            i = j
            continue

        if arg == "--reset-date":
            if i + 1 >= len(remaining_args):
                print("Missing value after --reset-date (expected YYYY-MM-DD)")
                sys.exit(1)
            reset_date_override = remaining_args[i + 1]
            i += 2
            continue

        if arg == "--throwback-action":
            if i + 1 >= len(remaining_args):
                print("Missing value after --throwback-action (expected announced or released)")
                sys.exit(1)
            throwback_action = remaining_args[i + 1]
            i += 2
            continue

        if arg == "--throwback-event":
            if i + 1 >= len(remaining_args):
                print("Missing value after --throwback-event")
                sys.exit(1)
            throwback_event = remaining_args[i + 1]
            i += 2
            continue

        if arg == "--throwback-label":
            if i + 1 >= len(remaining_args):
                print("Missing value after --throwback-label")
                sys.exit(1)
            throwback_label = remaining_args[i + 1]
            i += 2
            continue

        try:
            date.fromisoformat(arg)
            stats_date_override = arg
        except ValueError:
            print(f"Invalid argument '{arg}'")
            sys.exit(1)

        i += 1

    if reset_last_date_mode and reset_date_override:
        print("Use either --reset-last-date or --reset-date YYYY-MM-DD, not both.")
        sys.exit(1)

    post_only_mode = post_only_steps_raw is not None
    post_only_steps: list[str] = []
    if post_only_mode:
        if any((debug_daily_mode, debug_total_mode, dry_run_mode, local_test_mode, test_mode, throwback_mode)):
            print("Use --post-only by itself (optionally with a date, --no-post, --quiet, or --verbose).")
            sys.exit(1)
        post_only_steps = [s.strip() for s in post_only_steps_raw.split(",") if s.strip()]
        unknown_steps = [s for s in post_only_steps if s not in POST_ONLY_STEPS]
        if not post_only_steps or unknown_steps:
            if unknown_steps:
                print(f"Unknown --post-only step(s): {', '.join(unknown_steps)}")
            print(f"Available steps: {', '.join(POST_ONLY_STEPS)}")
            sys.exit(1)

    if throwback_mode:
        if throwback_action not in {"announced", "released"}:
            print("--throwback requires --throwback-action announced|released")
            sys.exit(1)
        if not throwback_event:
            print('--throwback requires --throwback-event "what Taylor Swift announced/released"')
            sys.exit(1)

    if reset_last_date_mode:
        last_date = get_last_stats_date_in_history()
        if not last_date:
            print("No history date found to reset.")
        else:
            removed = delete_history_rows_for_date(last_date)
            print(f"[RESET] Removed {removed} row(s) for last stats date: {last_date}")

    if reset_date_override:
        try:
            date.fromisoformat(reset_date_override)
        except ValueError:
            print(f"Invalid reset date '{reset_date_override}', expected YYYY-MM-DD")
            sys.exit(1)

        removed = delete_history_rows_for_date(reset_date_override)
        print(f"[RESET] Removed {removed} row(s) for stats date: {reset_date_override}")

    if test_mode:
        print("[TEST] Run finalization scripts from existing history; no R2, web export writes, posts, git, or scraping.")
    elif local_test_mode:
        print("[LOCAL-TEST] Force re-scrape, no history writes, no R2, no Twitter, no git.")
    elif dry_run_mode:
        print("[DRY-RUN] Scraping uniquement â€” aucune modification.")
    elif debug_daily_mode:
        print("[DEBUG-DAILY] Retry unfinished tracks, writes history, no Twitter/git/forecast/images/notify.")
    elif debug_total_mode:
        print("[DEBUG-TOTAL] Replace totals for an existing date in streams_history.csv.")
    elif throwback_mode:
        print("[THROWBACK] Generate/post throwback thread instead of normal daily posts.")
    elif post_only_mode:
        print(f"[POST-ONLY] Post selected step(s) from existing history, no scraping/export/git: {', '.join(post_only_steps)}")
    else:
        print("[NORMAL] Official run mode.")

    if dry_run_mode or debug_daily_mode or local_test_mode or throwback_mode or test_mode or post_only_mode:
        os.environ["UPLOAD_TO_R2"] = "0"
        print("R2 upload disabled for this run mode.")
    else:
        os.environ["UPLOAD_TO_R2"] = "1"
        print("R2 upload enabled for this run (UPLOAD_TO_R2=1).")

    if (test_mode or post_only_mode) and stats_date_override is None:
        stats_date_override = get_last_stats_date_in_history()
        if not stats_date_override:
            print("No date found in streams_history.csv.")
            sys.exit(1)
        print(f"Using latest history date: {stats_date_override}")

    snapshot_date = stats_date_override or get_stats_date_str()
    snapshot_collected_date = get_scrape_date_str()
    stats_date = snapshot_date

    if force_reprocess and not any((dry_run_mode, local_test_mode, debug_daily_mode, debug_total_mode, test_mode, throwback_mode)):
        removed = delete_history_rows_for_date(stats_date)
        if removed:
            print(f"[FORCE] Removed {removed} row(s) for stats date: {stats_date}")
        else:
            print(f"[FORCE] No existing rows to clear for stats date: {stats_date}")
    
    print("=" * 70)
    print("Taylor Swift - Spotify Streams Collector")
    print("=" * 70)
    print(f"Snapshot date: {snapshot_date}")
    print(f"Snapshot collected date: {snapshot_collected_date}")
    print()

    if throwback_mode:
        print("[THROWBACK] Using existing streams_history.csv data only; skipping Spotify collection.")
        run_final_update_tasks(FinalizeContext(
            script_dir=_SCRIPT_DIR,
            repo_root=_REPO_ROOT,
            stats_date=stats_date,
            summary={
                "stats_date": stats_date,
                "all_done": True,
                "updated_this_run": 0,
                "pending_this_run": 0,
                "not_found_this_run": 0,
            },
            no_post_mode=no_post_mode,
            debug_daily_mode=False,
            local_test_mode=False,
            post_spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
            log_mode=LOG_MODE,
            artist_thread=None,
            artist_result=[None],
            export_web_data=export_web_data,
            update_artist_metadata=update_artist_metadata,
            album_tracks_done_for=album_tracks_done_for,
            all_album_tracks_done=all_album_tracks_done,
            load_album_sections_flat=load_album_sections_flat,
            extract_track_id=extract_track_id,
            load_history_track_ids_for_date=load_history_track_ids_for_date,
            find_biggest_album_gainer_for_spotlight=find_biggest_album_gainer_for_spotlight,
            posted_album_updates=set(),
            initial_post_state={"posted_count": 0, "last_post_at": 0.0},
            throwback_mode=True,
            throwback_action=throwback_action,
            throwback_event=throwback_event,
            throwback_label=throwback_label,
            throwback_force=throwback_force,
        ))
        return

    if test_mode:
        print("[TEST] Using existing streams_history.csv data only; skipping Spotify collection.")

        def _test_export_web_data(**kwargs) -> None:
            print("[TEST] Web export skipped (no website writes, no R2).")

        run_final_update_tasks(FinalizeContext(
            script_dir=_SCRIPT_DIR,
            repo_root=_REPO_ROOT,
            stats_date=stats_date,
            summary={
                "stats_date": stats_date,
                "all_done": True,
                "updated_this_run": 0,
                "pending_this_run": 0,
                "not_found_this_run": 0,
            },
            no_post_mode=True,
            debug_daily_mode=False,
            local_test_mode=False,
            post_spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
            log_mode=LOG_MODE,
            artist_thread=None,
            artist_result=[None],
            export_web_data=_test_export_web_data,
            update_artist_metadata=update_artist_metadata,
            album_tracks_done_for=album_tracks_done_for,
            all_album_tracks_done=all_album_tracks_done,
            load_album_sections_flat=load_album_sections_flat,
            extract_track_id=extract_track_id,
            load_history_track_ids_for_date=load_history_track_ids_for_date,
            find_biggest_album_gainer_for_spotlight=find_biggest_album_gainer_for_spotlight,
            posted_album_updates=set(),
            initial_post_state={"posted_count": 0, "last_post_at": 0.0},
            test_mode=True,
        ))
        return

    if post_only_mode:
        print("[POST-ONLY] Using existing streams_history.csv data only; skipping Spotify collection.")
        run_post_only_steps(FinalizeContext(
            script_dir=_SCRIPT_DIR,
            repo_root=_REPO_ROOT,
            stats_date=stats_date,
            summary={
                "stats_date": stats_date,
                "all_done": True,
                "updated_this_run": 0,
                "pending_this_run": 0,
                "not_found_this_run": 0,
            },
            no_post_mode=no_post_mode,
            debug_daily_mode=False,
            local_test_mode=False,
            post_spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
            log_mode=LOG_MODE,
            artist_thread=None,
            artist_result=[None],
            export_web_data=export_web_data,
            update_artist_metadata=update_artist_metadata,
            album_tracks_done_for=album_tracks_done_for,
            all_album_tracks_done=all_album_tracks_done,
            load_album_sections_flat=load_album_sections_flat,
            extract_track_id=extract_track_id,
            load_history_track_ids_for_date=load_history_track_ids_for_date,
            find_biggest_album_gainer_for_spotlight=find_biggest_album_gainer_for_spotlight,
            posted_album_updates=set(),
            initial_post_state={"posted_count": 0, "last_post_at": 0.0},
        ), post_only_steps)
        return

    if debug_total_mode:
        if stats_date_override is None:
            print("--debug-total requires a date: python update_streams.py --debug-total YYYY-MM-DD")
            sys.exit(1)
        run_debug_total_replace(stats_date)
        return

    normal_lock_mode = not any((
        dry_run_mode,
        debug_daily_mode,
        debug_total_mode,
        local_test_mode,
        test_mode,
        throwback_mode,
        force_reprocess,
        reset_last_date_mode,
        reset_date_override,
    ))

    token_mgr = None
    active_track_ids = load_active_track_ids_from_discography()
    tracks = load_tracks_from_discography(active_track_ids)
    tracks = filter_tracks_released_for_stats_date(tracks, stats_date)

    already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
    done_tracks_before_run = len(already_done_for_stats_date)
    total_tracks = len(tracks)

    print(f"Loaded {total_tracks} track(s) from discography")
    print()

    new_release_track_ids: set[str] = set()
    recent_preflight_done = False
    should_check_recent_releases = (
        not dry_run_mode
        and not local_test_mode
        and not debug_daily_mode
        and stats_date_override is None
    )

    if should_check_recent_releases:
        if token_mgr is None:
            token_mgr = TokenManager()
        if not token_mgr.capture():
            print("TokenManager: ÃƒÂ©chec Ã¢â‚¬â€ impossible d'obtenir les tokens Spotify. VÃƒÂ©rifiez la connexion.")
            sys.exit(1)
        new_release_track_ids = run_new_release_preflight(token_mgr, stats_date)
        active_track_ids = load_active_track_ids_from_discography()
        recent_release_ids = filter_tracks_released_on(active_track_ids, stats_date)
        new_release_track_ids.update(filter_tracks_without_history_before(recent_release_ids, stats_date))
        new_release_track_ids.update(recent_release_track_ids_missing_positive_history(stats_date))
        missing_recent_daily = recent_release_track_ids_missing_daily(stats_date)
        repaired_daily_ids = repair_missing_daily_streams_for_date(stats_date, missing_recent_daily)
        if repaired_daily_ids:
            print(f"[discography] Repaired daily_streams for {len(repaired_daily_ids)} recent release track(s).")
            missing_recent_daily -= repaired_daily_ids
        new_release_track_ids.update(missing_recent_daily)
        if new_release_track_ids:
            tracks = load_tracks_from_discography(active_track_ids)
            tracks = filter_tracks_released_for_stats_date(tracks, stats_date)
            already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
            done_tracks_before_run = len(already_done_for_stats_date)
            total_tracks = len(tracks)
            print(f"[discography] Reloaded {total_tracks} track(s) after new release preflight.")
        recent_preflight_done = True

    if not dry_run_mode and not local_test_mode:
        missing_recent_daily = recent_release_track_ids_missing_daily(stats_date)
        repaired_daily_ids = repair_missing_daily_streams_for_date(stats_date, missing_recent_daily)
        if repaired_daily_ids:
            print(f"[discography] Repaired daily_streams for {len(repaired_daily_ids)} recent release track(s).")
            new_release_track_ids.update(repaired_daily_ids)
            already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
            done_tracks_before_run = len(already_done_for_stats_date)

    if normal_lock_mode and _daily_lock_exists(stats_date, STREAMS_UPDATE_COMPLETE_LOCK_NAME):
        lock_path = _daily_lock_path(stats_date, STREAMS_UPDATE_COMPLETE_LOCK_NAME)
        missing_recent_positive = recent_release_track_ids_missing_positive_history(stats_date)
        missing_recent_daily = recent_release_track_ids_missing_daily(stats_date)
        stale_admin_zero_ids = load_admin_override_unchanged_zero_track_ids(stats_date)
        if missing_recent_positive:
            print(
                f"Streams update lock exists for {stats_date}, but "
                f"{len(missing_recent_positive)} recent release track(s) still need positive streams; ignoring lock."
            )
        elif missing_recent_daily:
            print(
                f"Streams update lock exists for {stats_date}, but "
                f"{len(missing_recent_daily)} recent release track(s) still need daily_streams; ignoring lock."
            )
        elif stale_admin_zero_ids:
            print(
                f"Streams update lock exists for {stats_date}, but "
                f"{len(stale_admin_zero_ids)} admin_override zero row(s) still have unchanged totals; ignoring lock."
            )
        else:
            print(f"Streams update already complete for {stats_date} ({lock_path.name}); skipping.")
            return

    if normal_lock_mode and _daily_lock_exists(stats_date, STREAMS_SCRAPED_LOCK_NAME):
        lock_path = _daily_lock_path(stats_date, STREAMS_SCRAPED_LOCK_NAME)
        summary = _build_existing_history_summary(stats_date, total_tracks, total_tracks)
        missing_recent_positive = recent_release_track_ids_missing_positive_history(stats_date)
        missing_recent_daily = recent_release_track_ids_missing_daily(stats_date)
        stale_admin_zero_ids = load_admin_override_unchanged_zero_track_ids(stats_date)
        if missing_recent_positive:
            print(
                f"Streams scraping lock exists for {stats_date}, but "
                f"{len(missing_recent_positive)} recent release track(s) still need positive streams; ignoring lock."
            )
        elif missing_recent_daily:
            print(
                f"Streams scraping lock exists for {stats_date}, but "
                f"{len(missing_recent_daily)} recent release track(s) still need daily_streams; ignoring lock."
            )
        elif stale_admin_zero_ids:
            print(
                f"Streams scraping lock exists for {stats_date}, but "
                f"{len(stale_admin_zero_ids)} admin_override zero row(s) still have unchanged totals; ignoring lock."
            )
        elif not summary["all_done"]:
            print(
                f"Streams scraping lock exists for {stats_date}, but history is no longer complete "
                f"({summary['done_tracks']}/{total_tracks}); ignoring lock."
            )
        else:
            print(f"Streams scraping already done for {stats_date} ({lock_path.name}); skipping WARP/token/scraping.")
            print_summary_block(summary)
            print_api_metrics(summary)
            estimated_track_ids = estimate_missing_stream_history_gaps()
            if estimated_track_ids:
                summary["history_index"] = HistoryIndex.load()
                push_updated_track_histories_to_r2(estimated_track_ids, summary["history_index"])
            try:
                notify_daily_growers(stats_date)
            except Exception as exc:
                print(f"[grower_notify] Failed to send grower notification: {exc}")
            run_final_update_tasks(FinalizeContext(
                script_dir=_SCRIPT_DIR,
                repo_root=_REPO_ROOT,
                stats_date=stats_date,
                summary=summary,
                no_post_mode=no_post_mode,
                debug_daily_mode=False,
                local_test_mode=False,
                post_spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
                log_mode=LOG_MODE,
                artist_thread=None,
                artist_result=[None],
                export_web_data=export_web_data,
                update_artist_metadata=update_artist_metadata,
                album_tracks_done_for=album_tracks_done_for,
                all_album_tracks_done=all_album_tracks_done,
                load_album_sections_flat=load_album_sections_flat,
                extract_track_id=extract_track_id,
                load_history_track_ids_for_date=load_history_track_ids_for_date,
                find_biggest_album_gainer_for_spotlight=find_biggest_album_gainer_for_spotlight,
                posted_album_updates=set(),
                initial_post_state={"posted_count": 0, "last_post_at": 0.0},
            ))
            _write_daily_lock(stats_date, STREAMS_UPDATE_COMPLETE_LOCK_NAME, {
                "reason": "resumed_from_scraped_lock",
                "total_tracks": total_tracks,
            })
            return

    _warp_connect()

    if not dry_run_mode and not local_test_mode and not debug_daily_mode:
        estimated_track_ids = estimate_missing_stream_history_gaps()
        if estimated_track_ids:
            print(
                f"[streams-estimate] Prepared {len(estimated_track_ids)} track(s) "
                "with estimated rows before collection."
            )
            already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
            done_tracks_before_run = len(already_done_for_stats_date)

    if debug_daily_mode:
        unfinished_ids = load_last_unfinished_update_track_ids(stats_date)
        if not unfinished_ids:
            print("[DEBUG-DAILY] No matching unfinished track list found, fallback to all not-yet-done tracks.")
            unfinished_ids = {t["track_id"] for t in tracks if t["track_id"] not in already_done_for_stats_date}
        else:
            print(f"[DEBUG-DAILY] Loaded {len(unfinished_ids)} unfinished track(s) from JSON.")
    else:
        unfinished_ids = None

    if local_test_mode:
        print(f"[LOCAL-TEST] Re-scraping {total_tracks} tracks; existing {stats_date} rows will not be skipped.")
    elif dry_run_mode:
        print(f"[DRY-RUN] Scraping {total_tracks} tracks.")

    should_check_recent_releases = (
        not recent_preflight_done
        and not dry_run_mode
        and not local_test_mode
        and not debug_daily_mode
        and stats_date_override is None
        and (done_tracks_before_run < total_tracks or force_reprocess)
    )

    if should_check_recent_releases:
        if token_mgr is None:
            token_mgr = TokenManager()
        if not token_mgr.capture():
            print("TokenManager: Ã©chec â€” impossible d'obtenir les tokens Spotify. VÃ©rifiez la connexion.")
            sys.exit(1)
        new_release_track_ids = run_new_release_preflight(token_mgr, stats_date)
        active_track_ids = load_active_track_ids_from_discography()
        recent_release_ids = filter_tracks_released_on(active_track_ids, stats_date)
        new_release_track_ids.update(filter_tracks_without_history_before(recent_release_ids, stats_date))
        new_release_track_ids.update(recent_release_track_ids_missing_positive_history(stats_date))
        missing_recent_daily = recent_release_track_ids_missing_daily(stats_date)
        repaired_daily_ids = repair_missing_daily_streams_for_date(stats_date, missing_recent_daily)
        if repaired_daily_ids:
            print(f"[discography] Repaired daily_streams for {len(repaired_daily_ids)} recent release track(s).")
            missing_recent_daily -= repaired_daily_ids
        new_release_track_ids.update(missing_recent_daily)
        if new_release_track_ids:
            tracks = load_tracks_from_discography(active_track_ids)
            tracks = filter_tracks_released_for_stats_date(tracks, stats_date)
            already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
            done_tracks_before_run = len(already_done_for_stats_date)
            total_tracks = len(tracks)
            print(f"[discography] Reloaded {total_tracks} track(s) after new release preflight.")

    # Si tous les tracks sont dÃ©jÃ  done, ou si on backfille une date dÃ©jÃ  dÃ©passÃ©e,
    # on saute Playwright/API entiÃ¨rement
    last_history_date = get_last_stats_date_in_history()
    is_backfill = last_history_date is not None and last_history_date > stats_date
    stale_admin_zero_ids = (
        set()
        if dry_run_mode or local_test_mode or debug_daily_mode or debug_total_mode or post_only_mode or throwback_mode
        else load_admin_override_unchanged_zero_track_ids(stats_date)
    )

    # If stats_date has no data at all but history has a more recent date,
    # the computed date was never captured (e.g. old code mislabeled it). Advance
    # stats_date to the most recent available date so export/post work correctly.
    if is_backfill and done_tracks_before_run == 0:
        print(f"Backfill detected: history has data up to {last_history_date} but {stats_date} has no data.")
        print(f"Advancing stats_date to {last_history_date} for export/post.")
        stats_date = last_history_date
        stats_date_override = stats_date  # propagate to run_update() and summary
        tracks = filter_tracks_released_for_stats_date(load_tracks_from_discography(active_track_ids), stats_date)
        total_tracks = len(tracks)
        already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
        done_tracks_before_run = len(already_done_for_stats_date)
        is_backfill = False  # stats_date now points to existing data

    scraping_needed = (
        (done_tracks_before_run < total_tracks and not is_backfill)
        or dry_run_mode
        or local_test_mode
        or debug_daily_mode
        or force_reprocess
    )

    if stale_admin_zero_ids and not scraping_needed and not is_backfill:
        print(
            f"{len(stale_admin_zero_ids)} admin_override zero row(s) still have unchanged totals; "
            "retrying exact total replacement before finalization."
        )
        run_debug_total_replace(stats_date)
        stale_admin_zero_ids = load_admin_override_unchanged_zero_track_ids(stats_date)
        already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
        done_tracks_before_run = len(already_done_for_stats_date)
        if stale_admin_zero_ids:
            print(
                f"ERROR: {len(stale_admin_zero_ids)} admin_override zero row(s) still have unchanged totals "
                f"for {stats_date}; blocking finalization until Spotify returns exact updated totals."
            )
            sys.exit(1)

    if scraping_needed:
        # Capture des tokens API (une seule fois pour tout le run)
        if token_mgr is None:
            token_mgr = TokenManager()
            captured_tokens = token_mgr.capture()
        else:
            captured_tokens = True
        if not captured_tokens:
            print("TokenManager: Ã©chec â€” impossible d'obtenir les tokens Spotify. VÃ©rifiez la connexion.")
            sys.exit(1)
    else:
        print("Tous les tracks dÃ©jÃ  mis Ã  jour pour cette date â€” Playwright/scraping ignorÃ©.")

    if not scraping_needed:
        summary = _build_existing_history_summary(stats_date, total_tracks, len(active_track_ids))
        print_summary_block(summary)
        print_api_metrics(summary)
        run_final_update_tasks(FinalizeContext(
            script_dir=_SCRIPT_DIR,
            repo_root=_REPO_ROOT,
            stats_date=stats_date,
            summary=summary,
            no_post_mode=no_post_mode,
            debug_daily_mode=False,
            local_test_mode=False,
            post_spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
            log_mode=LOG_MODE,
            artist_thread=None,
            artist_result=[None],
            export_web_data=export_web_data,
            update_artist_metadata=update_artist_metadata,
            album_tracks_done_for=album_tracks_done_for,
            all_album_tracks_done=all_album_tracks_done,
            load_album_sections_flat=load_album_sections_flat,
            extract_track_id=extract_track_id,
            load_history_track_ids_for_date=load_history_track_ids_for_date,
            find_biggest_album_gainer_for_spotlight=find_biggest_album_gainer_for_spotlight,
            posted_album_updates=set(),
            initial_post_state={"posted_count": 0, "last_post_at": 0.0},
        ))
        if normal_lock_mode and summary["all_done"]:
            _write_daily_lock(stats_date, STREAMS_UPDATE_COMPLETE_LOCK_NAME, {
                "reason": "already_complete_fast_path",
                "total_tracks": summary.get("total_tracks"),
            })
        return

    if (
        new_release_track_ids
        and scraping_needed
        and not dry_run_mode
        and not local_test_mode
        and not debug_daily_mode
    ):
        priority_new_ids = filter_tracks_released_on(new_release_track_ids, stats_date)
        priority_new_ids = filter_tracks_without_history_before(priority_new_ids, stats_date)
        priority_new_ids -= load_positive_history_track_ids_for_date(stats_date)
        priority_new_ids.update(recent_release_track_ids_missing_daily(stats_date))
        if priority_new_ids:
            print()
            print("=" * 70)
            print(f"New Release Priority Run ({len(priority_new_ids)} track(s))")
            print("=" * 70)
            priority_target_ids = set(priority_new_ids)
            for attempt in range(1, NEW_RELEASE_RETRY_ATTEMPTS + 1):
                done_now = load_positive_history_track_ids_for_date(stats_date)
                missing_ids = (priority_target_ids - done_now) | recent_release_track_ids_missing_daily(stats_date)
                if not missing_ids:
                    break

                print(
                    f"[debut] Attempt {attempt}/{NEW_RELEASE_RETRY_ATTEMPTS}: "
                    f"scraping {len(missing_ids)} new release track(s)."
                )
                priority_progress = ProgressLogger(LOG_MODE)
                priority_summary = run_update(
                    on_progress=priority_progress,
                    stats_date_override=stats_date_override,
                    dry_run_mode=False,
                    only_track_ids=missing_ids,
                    token_mgr=token_mgr,
                    force_reprocess=True,
                    write_history=write_history,
                    override_stream_guards=override_stream_guards,
                )
                print_summary_block(priority_summary)
                print_api_metrics(priority_summary)

                done_now = load_positive_history_track_ids_for_date(stats_date)
                missing_ids = (priority_target_ids - done_now) | recent_release_track_ids_missing_daily(stats_date)
                if not missing_ids:
                    break
                if attempt < NEW_RELEASE_RETRY_ATTEMPTS:
                    print(
                        f"[debut] {len(missing_ids)} new release track(s) still missing; "
                        f"retrying in {NEW_RELEASE_RETRY_SLEEP_SECONDS}s..."
                    )
                    time.sleep(NEW_RELEASE_RETRY_SLEEP_SECONDS)

            missing_after_priority = (
                priority_target_ids - load_positive_history_track_ids_for_date(stats_date)
            ) | recent_release_track_ids_missing_daily(stats_date)
            if not missing_after_priority:
                print("[debut] Priority tracks collected; early debut poster will post during collection.")
            else:
                print(
                    f"[debut] {len(missing_after_priority)} new release track(s) still missing after "
                    f"{NEW_RELEASE_RETRY_ATTEMPTS} attempt(s); continuing normal run."
                )

            already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
            done_tracks_before_run = len(already_done_for_stats_date)

    should_run_probe = (
        done_tracks_before_run == 0
        and not is_backfill
        and stats_date_override is None
        and not dry_run_mode
        and not local_test_mode
        and not debug_daily_mode
    )
    use_browser_scrape_for_run = False
    probe_confirmed_full_run = False
    confirmed_probe: dict | None = None

    if should_run_probe:
        recent_probe_batches: deque[set[str]] = deque(maxlen=PROBE_RECENT_BATCH_MEMORY)

        def _recent_probe_track_ids() -> set[str]:
            return set().union(*recent_probe_batches) if recent_probe_batches else set()

        def _next_probe_tracks() -> list[dict]:
            selected = build_probe_tracks(
                tracks,
                non_extra_size=PROBE_NON_EXTRA_SAMPLE_SIZE,
                recent_track_ids=_recent_probe_track_ids(),
            )
            if selected:
                recent_probe_batches.append({t["track_id"] for t in selected if t.get("track_id")})
            return selected

        probe_tracks = _next_probe_tracks()

        if not probe_tracks:
            print("Probe skipped: no probe tracks found in database.")
        else:
            def _print_probe_progress(row, scanned, total):
                kind = "extra" if row.get("chart_extra") else "non-extra"
                if row["status"] == "ok":
                    print(
                        f"PROBE progress {scanned}/{total} | "
                        f"source=spotify | kind={kind} | "
                        f"{row['title']} | current={format_int(row['streams'])} | "
                        f"previous={format_int(row['previous_streams'])} | "
                        f"updated={'yes' if row['updated'] else 'no'}"
                    )
                else:
                    print(
                        f"PROBE progress {scanned}/{total} | "
                        f"source=spotify | kind={kind} | "
                        f"{row['title']} | status={row['status']}"
                    )

            def _print_probe(probe, *, print_rows: bool = False):
                print(
                    f"Probe result | successful={probe['successful_probes']} | "
                    f"updated={probe['updated_probes']} | "
                    f"updated_non_extra={probe.get('updated_non_extra_probes', 0)} | "
                    f"start_full_run={probe['can_start_full_run']}"
                )
                if not print_rows:
                    return
                for row in probe["results"]:
                    kind = "extra" if row.get("chart_extra") else "non-extra"
                    if row["status"] == "ok":
                        print(
                            f"PROBE {row['title']} | "
                            f"source=spotify | kind={kind} | "
                            f"current={format_int(row['streams'])} | "
                            f"previous={format_int(row['previous_streams'])} | "
                            f"updated={'yes' if row['updated'] else 'no'}"
                        )
                    else:
                        print(f"PROBE {row['title']} | source=spotify | kind={kind} | status={row['status']}")

            def _print_chartsnapshot_probe(probe: dict):
                if probe.get("error"):
                    print(f"ChartSnapshot probe failed: {probe['error']}")
                    return
                print(
                    "ChartSnapshot probe result | source=chartsnapshot | "
                    f"rows={probe['source_rows']} | "
                    f"validated={probe['validated_rows']} | "
                    f"validated_non_extra={probe['validated_non_extra_rows']} | "
                    f"external={probe['external_rows']} | "
                    f"invalid={probe['invalid_rows']} | "
                    f"missing_previous={probe['missing_previous_rows']} | "
                    f"mismatch={probe['mismatch_rows']} | "
                    f"start_full_run={probe['can_start_full_run']}"
                )

            # Essai probe via API
            print(f"Running probe check... [API] ({len(probe_tracks)} track(s))")
            api_probe = _probe_via_api(
                probe_tracks,
                token_mgr,
                required_successful=min(PROBE_SAMPLE_SIZE, len(probe_tracks)),
                required_updated=PROBE_REQUIRED_UPDATED,
                progress_callback=_print_probe_progress,
                previous_stats_date=get_previous_stats_date_str(stats_date),
            )
            if api_probe is not None:
                api_probe["source"] = "spotify_api"
                _print_probe(api_probe)
                probe_retry_count = 0
                chartsnapshot_probe_disabled = False
                while not api_probe["can_start_full_run"]:
                    probe_retry_count += 1
                    print()
                    print("Spotify playcount API does not appear to expose the next daily totals yet.")
                    if not chartsnapshot_probe_disabled:
                        print(
                            f"Checking ChartSnapshot for {stats_date} before the next Spotify probe..."
                        )
                        chartsnapshot_probe = probe_chartsnapshot_update(stats_date, tracks)
                        _print_chartsnapshot_probe(chartsnapshot_probe)
                        if chartsnapshot_probe.get("blocked_reason") == "chartsnapshot_role_required":
                            chartsnapshot_probe_disabled = True
                            print("ChartSnapshot historical endpoint requires a higher role; skipping it for the rest of this probe loop.")
                        if chartsnapshot_probe.get("can_start_full_run"):
                            print("ChartSnapshot validated the daily update; starting full Spotify collection.")
                            probe_confirmed_full_run = True
                            confirmed_probe = chartsnapshot_probe
                            break
                    print(
                        f"Retrying probe in 2 seconds until "
                        f"{PROBE_REQUIRED_UPDATED} non-extra track(s) update "
                        f"(attempt {probe_retry_count})."
                    )
                    time.sleep(2)
                    probe_tracks = _next_probe_tracks()
                    if not probe_tracks:
                        print("Probe skipped: no probe tracks found in database.")
                        break
                    print(f"Running probe check... [API] ({len(probe_tracks)} track(s))")
                    api_probe = _probe_via_api(
                        probe_tracks,
                        token_mgr,
                        required_successful=min(PROBE_SAMPLE_SIZE, len(probe_tracks)),
                        required_updated=PROBE_REQUIRED_UPDATED,
                        progress_callback=_print_probe_progress,
                        previous_stats_date=get_previous_stats_date_str(stats_date),
                    )
                    if api_probe is None:
                        print("Probe via API unavailable during retry; starting run.")
                        break
                    api_probe["source"] = "spotify_api"
                    _print_probe(api_probe)
                if api_probe and api_probe.get("can_start_full_run") and not probe_confirmed_full_run:
                    probe_confirmed_full_run = True
                    confirmed_probe = api_probe
            else:
                print("Probe via API unavailable (no token) â€” skipping probe, starting run.")
    elif debug_daily_mode:
        print("Skipping probe in debug-daily mode.")
    elif done_tracks_before_run < total_tracks:
        print("Partial progress already exists for this stats date.")
        print("Skipping probe and resuming unfinished tracks.")
    else:
        print("All tracks already appear done for this stats date.")
        print("Skipping probe and refreshing export/publish anyway.")

    print()
    print("=" * 70)
    print(f"Run â€” stats_date {stats_date}")
    print("=" * 70)

    if probe_confirmed_full_run and confirmed_probe is not None:
        if confirmed_probe.get("source") == "chartsnapshot":
            notify_message = (
                f"ChartSnapshot probe OK for {stats_date}: "
                f"{confirmed_probe.get('validated_non_extra_rows', 0)} validated non-extra "
                f"track(s) with total - daily matching our previous-day total. "
                "Starting full Spotify streams collection."
            )
        else:
            notify_message = (
                f"Spotify probe OK for {stats_date}: "
                f"{confirmed_probe['successful_probes']}/{confirmed_probe.get('total_probe_tracks', '?')} "
                f"successful, {confirmed_probe.get('updated_non_extra_probes', 0)} updated non-extra. "
                "Starting full streams collection."
            )
        notify(
            NTFY_TOPIC,
            notify_message,
            title="Taylor Swift - Streams collection starting",
            tags="white_check_mark,chart_increasing",
        )

    _artist_result: list[dict | None] = [None]

    def _scrape_artist_bg():
        _artist_result[0] = scrape_artist_metadata()

    artist_thread = None
    if not dry_run_mode:
        artist_thread = threading.Thread(target=_scrape_artist_bg, daemon=True)
        artist_thread.start()

    album_sections_flat = load_album_sections_flat()
    album_names = [
        album
        for album in dict.fromkeys(
            section.get("album")
            for section in album_sections_flat
            if section.get("album")
        )
    ]
    early_web_export_gate = SharedWebExportGate(
        export_web_data=export_web_data,
        stats_date=stats_date,
    )

    debut_release_track_ids = set()
    if new_release_track_ids and not dry_run_mode and not local_test_mode and not debug_daily_mode:
        debut_release_track_ids = filter_tracks_released_on(new_release_track_ids, stats_date)

    debut_release_poster = ReadyDebutReleasePoster(
        stats_date=stats_date,
        track_ids=debut_release_track_ids,
        load_history_track_ids_for_date=load_history_track_ids_for_date,
        spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
        log_mode=LOG_MODE,
        enabled=True,
        no_post_mode=no_post_mode,
    )
    debut_release_poster.start()

    album_best_day_since_poster = ReadyAlbumBestDaySincePoster(
        script_dir=_SCRIPT_DIR,
        stats_date=stats_date,
        export_web_data=early_web_export_gate.export_partial,
        album_tracks_done_for=album_tracks_done_for,
        spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
        log_mode=LOG_MODE,
        enabled=True,
        no_post_mode=no_post_mode,
        target_albums=album_names,
        priority_ready=debut_release_poster.is_done,
    )
    album_best_day_since_poster.start()

    album_update_poster = ReadyAlbumUpdatePoster(
        script_dir=_SCRIPT_DIR,
        stats_date=stats_date,
        export_web_data=early_web_export_gate.export_partial,
        album_tracks_done_for=album_tracks_done_for,
        spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
        log_mode=LOG_MODE,
        no_post_mode=no_post_mode,
        target_albums=list(ALBUM_UPDATE_TARGETS),
        # Pas de cards album le week-end (rÃ¨gle posting) : early poster inclus.
        enabled=date.fromisoformat(stats_date).weekday() < 5,
    )
    album_update_poster.start()

    priority_best_day_track_ids = build_priority_best_day_track_ids(
        tracks,
        stats_date,
        min_days_since=EARLY_BEST_DAY_PRIORITY_AFTER_DAYS,
        min_recent_peak_ratio=EARLY_BEST_DAY_PRIORITY_RECENT_PEAK_RATIO,
    )
    early_best_day_track_ids = build_early_best_day_track_ids(
        tracks,
        stats_date,
        min_previous_daily_streams=EARLY_BEST_DAY_WATCHLIST_MIN_DAILY_STREAMS,
        limit=EARLY_BEST_DAY_TRACK_LIMIT,
    )
    print(
        f"Early best-day-since watcher has {len(priority_best_day_track_ids)} "
        f"priority long-gap candidate(s) and {len(early_best_day_track_ids)} "
        f"score-watch track(s) from {get_previous_stats_date_str(stats_date)}."
    )
    best_day_since_poster = ReadyBestDaySincePoster(
        script_dir=_SCRIPT_DIR,
        stats_date=stats_date,
        track_ids=early_best_day_track_ids,
        priority_track_ids=priority_best_day_track_ids,
        export_web_data=early_web_export_gate.export_partial,
        load_history_track_ids_for_date=load_history_track_ids_for_date,
        spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
        log_mode=LOG_MODE,
        enabled=True,
        no_post_mode=no_post_mode,
        max_posts=EARLY_BEST_DAY_MAX_POSTS,
        min_days=21,
        min_daily_streams=EARLY_BEST_DAY_MIN_DAILY_STREAMS,
        min_pct_change=EARLY_BEST_DAY_MIN_PCT_CHANGE,
        min_score=EARLY_BEST_DAY_MIN_SCORE,
        priority_ready=debut_release_poster.is_done,
    )
    best_day_since_poster.start()

    partial_web_exporter = PartialWebExporter(
        stats_date=stats_date,
        export_web_data=early_web_export_gate.export_partial,
        enabled=True,
    )

    infinite_retry_thread: threading.Thread | None = None
    infinite_retry_track_ids: set[str] = set()
    infinite_retry_collected_ids: set[str] = set()

    tracks_by_id = {t["track_id"]: t for t in tracks}

    def _complete_same_total_extra_as_zero(track_id: str, reason: str) -> bool:
        track = tracks_by_id.get(track_id)
        if not track or not track.get("chart_extra"):
            return False

        result = next((r for r in summary.get("results", []) if r and r.get("track_id") == track_id), None)
        if not result or result.get("status") != "pending" or result.get("reason") != "same_total":
            return False

        total = result.get("streams")
        previous_total = result.get("previous_streams")
        if total is None or previous_total is None:
            return False
        try:
            total_int = int(total)
            previous_int = int(previous_total)
        except (TypeError, ValueError):
            return False
        if total_int != previous_int:
            return False

        if write_history and not dry_run_mode:
            history_index = summary.get("history_index")
            if history_index is not None:
                history_index.append(stats_date, track_id, total_int, 0)
            else:
                append_history_row([stats_date, track_id, total_int, 0])

        result["status"] = "updated"
        result["daily_streams"] = 0
        result["reason"] = reason
        summary["updated_this_run"] = int(summary.get("updated_this_run", 0)) + 1
        summary["pending_this_run"] = max(int(summary.get("pending_this_run", 0)) - 1, 0)
        summary["done_tracks"] = int(summary.get("done_tracks", 0)) + 1
        all_updated_track_ids.add(track_id)
        return True

    def _complete_admin_same_total_as_zero(track_id: str) -> bool:
        result = next((r for r in summary.get("results", []) if r and r.get("track_id") == track_id), None)
        if not result or result.get("status") != "pending" or result.get("reason") != "admin_same_total":
            return False

        total = result.get("streams")
        previous_total = result.get("previous_streams")
        if total is None or previous_total is None:
            return False
        try:
            total_int = int(total)
            previous_int = int(previous_total)
        except (TypeError, ValueError):
            return False
        if total_int != previous_int:
            return False

        reason = "admin_override_same_total_after_retries"
        if write_history and not dry_run_mode:
            history_index = summary.get("history_index")
            if history_index is not None:
                history_index.append(stats_date, track_id, total_int, 0, reason)
            else:
                append_history_row([stats_date, track_id, total_int, 0, "", reason])

        result["status"] = "updated"
        result["daily_streams"] = 0
        result["reason"] = reason
        summary["updated_this_run"] = int(summary.get("updated_this_run", 0)) + 1
        summary["pending_this_run"] = max(int(summary.get("pending_this_run", 0)) - 1, 0)
        summary["done_tracks"] = int(summary.get("done_tracks", 0)) + 1
        all_updated_track_ids.add(track_id)
        return True

    progress = ProgressLogger(LOG_MODE)
    summary = run_update(
        on_progress=progress,
        stats_date_override=stats_date_override,
        dry_run_mode=dry_run_mode,
        only_track_ids=unfinished_ids if debug_daily_mode else None,
        token_mgr=token_mgr,
        force_reprocess=force_reprocess,
        write_history=write_history,
        use_browser_scrape=use_browser_scrape_for_run,
        override_stream_guards=override_stream_guards,
    )
    all_updated_track_ids = set(summary.get("updated_track_ids") or set())
    print_summary_block(summary)
    print_api_metrics(summary)
    partial_web_exporter.export_if_updated(summary)

    if (
        new_release_track_ids
        and not dry_run_mode
        and not local_test_mode
        and not debug_daily_mode
    ):
        debut_ids = filter_tracks_released_on(new_release_track_ids, stats_date)
        missing_debut_ids = debut_ids - load_history_track_ids_for_date(stats_date)
        if not missing_debut_ids:
            print("[debut] Tracks collected; early debut poster has priority before best-day-since.")
        else:
            print(f"[debut] Post skipped: {len(missing_debut_ids)} debut track(s) still missing streams.")

    not_found_ids: set[str] = {
        r["track_id"] for r in summary["failed_results"] if r["status"] == "not_found"
    }

    retry_round = 0
    track_retry_counts: dict[str, int] = {}
    extra_retry_counts_after_non_extra_done: dict[str, int] = {}
    previous_same_total_pending_ids = load_previous_same_total_pending_track_ids(stats_date)
    known_stuck_pending_tracks = load_known_stuck_pending_tracks()
    if known_stuck_pending_tracks:
        print(f"Known stuck pending list: {len(known_stuck_pending_tracks)} track(s).")
    previous_pending_signature = {
        (r.get("track_id"), r.get("streams"), r.get("reason"))
        for r in summary.get("results", [])
        if r and r.get("status") == "pending"
    }
    while (
        not dry_run_mode
        and not local_test_mode
        and not debug_daily_mode
        and not summary["all_done"]
        and summary["pending_this_run"] > 0
    ):
        all_pending_ids = {
            r["track_id"]
            for r in summary.get("results", [])
            if r and r.get("status") == "pending" and r.get("track_id")
        }
        for tid in all_pending_ids:
            track_retry_counts[tid] = track_retry_counts.get(tid, 0) + 1

        non_extra_pending_ids_now = {
            tid for tid in all_pending_ids
            if tid in tracks_by_id and not tracks_by_id[tid].get("chart_extra")
        }
        extra_pending_ids_now = {
            tid for tid in all_pending_ids
            if tid in tracks_by_id and tracks_by_id[tid].get("chart_extra")
        }

        if admin_override_mode:
            admin_same_total_ids = {
                r["track_id"]
                for r in summary.get("results", [])
                if r
                and r.get("status") == "pending"
                and r.get("reason") == "admin_same_total"
                and r.get("track_id") in all_pending_ids
            }
            if admin_same_total_ids and retry_round >= EXTRA_PENDING_RETRY_ROUNDS_BEFORE_ZERO:
                completed_admin_zero_ids = {
                    tid for tid in admin_same_total_ids
                    if _complete_admin_same_total_as_zero(tid)
                }
                if completed_admin_zero_ids:
                    titles = sorted(
                        tracks_by_id[tid]["title"] for tid in completed_admin_zero_ids if tid in tracks_by_id
                    )
                    print(
                        f"{len(completed_admin_zero_ids)} admin same-total track(s) closed with daily=0 "
                        f"after {EXTRA_PENDING_RETRY_ROUNDS_BEFORE_ZERO} retry round(s): {', '.join(titles)}"
                    )
                all_pending_ids -= completed_admin_zero_ids
                non_extra_pending_ids_now -= completed_admin_zero_ids
                extra_pending_ids_now -= completed_admin_zero_ids
                summary["all_done"] = summary.get("pending_this_run", 0) == 0

                if not all_pending_ids:
                    print("All pending tracks are resolved.")
                    break
            elif admin_same_total_ids:
                print(
                    f"{len(admin_same_total_ids)} admin same-total track(s) still pending; "
                    f"completed retry rounds {retry_round} / {EXTRA_PENDING_RETRY_ROUNDS_BEFORE_ZERO}."
                )

        if not non_extra_pending_ids_now and extra_pending_ids_now:
            yesterday_persistent_ids = extra_pending_ids_now & previous_same_total_pending_ids
            new_persistent_ids = extra_pending_ids_now - previous_same_total_pending_ids
            retry_exhausted_new_ids = {
                tid for tid in new_persistent_ids
                if extra_retry_counts_after_non_extra_done.get(tid, 0) >= EXTRA_PENDING_RETRY_ROUNDS_BEFORE_ZERO
            }
            zero_ids = yesterday_persistent_ids | retry_exhausted_new_ids
            completed_zero_ids = {
                tid
                for tid in zero_ids
                if _complete_same_total_extra_as_zero(tid, "persistent_same_total_extra_zero")
            }
            if completed_zero_ids:
                titles = sorted(
                    tracks_by_id[tid]["title"] for tid in completed_zero_ids if tid in tracks_by_id
                )
                print(
                    f"{len(completed_zero_ids)} extra same-total track(s) closed with daily=0 "
                    f"after non-extra completion: {', '.join(titles)}"
                )

            all_pending_ids -= completed_zero_ids
            extra_pending_ids_now -= completed_zero_ids
            summary["all_done"] = summary.get("pending_this_run", 0) == 0

            if not all_pending_ids:
                print("All pending tracks are resolved.")
                break

            waiting_new_extra_ids = extra_pending_ids_now - previous_same_total_pending_ids
            if waiting_new_extra_ids:
                waiting_counts = sorted(
                    extra_retry_counts_after_non_extra_done.get(tid, 0)
                    for tid in waiting_new_extra_ids
                )
                print(
                    f"{len(waiting_new_extra_ids)} new extra same-total track(s) still pending after "
                    f"non-extra completion; retry counts {waiting_counts} / "
                    f"{EXTRA_PENDING_RETRY_ROUNDS_BEFORE_ZERO}."
                )

        pending_retry_ids = set(all_pending_ids)

        if not pending_retry_ids:
            print("No pending track IDs left to retry; stopping pending retries.")
            break

        if not non_extra_pending_ids_now:
            for tid in pending_retry_ids:
                if tid in tracks_by_id and tracks_by_id[tid].get("chart_extra") and tid not in previous_same_total_pending_ids:
                    extra_retry_counts_after_non_extra_done[tid] = (
                        extra_retry_counts_after_non_extra_done.get(tid, 0) + 1
                    )

        if (
            retry_round > 0
            and PENDING_RETRY_SLEEP_SECONDS > 0
            and summary_had_http_status(summary, 409)
        ):
            print()
            print(
                f"Waiting {PENDING_RETRY_SLEEP_SECONDS}s after HTTP 409 before retrying "
                f"{len(pending_retry_ids)} pending unchanged-total track(s)..."
            )
            time.sleep(PENDING_RETRY_SLEEP_SECONDS)

        retry_round += 1

        print()
        print(
            f"Detected {summary['pending_this_run']} pending unchanged-total track(s) "
            f"for {summary['stats_date']}."
        )
        if not_found_ids:
            print(f"Skipping {len(not_found_ids)} not-found track(s) on this retry.")

        print()
        print("=" * 70)
        print(f"Retry round {retry_round} â€” stats_date {stats_date}")
        print("=" * 70)

        retry_progress = ProgressLogger(LOG_MODE)
        summary = run_update(
            on_progress=retry_progress,
            skip_track_ids=not_found_ids,
            stats_date_override=stats_date_override,
            dry_run_mode=False,
            only_track_ids=pending_retry_ids,
            token_mgr=token_mgr,
            force_reprocess=force_reprocess,
            write_history=write_history,
            use_browser_scrape=use_browser_scrape_for_run,
            override_stream_guards=override_stream_guards,
        )
        all_updated_track_ids.update(summary.get("updated_track_ids") or set())
        not_found_ids.update(
            r["track_id"] for r in summary["failed_results"] if r["status"] == "not_found"
        )
        print_summary_block(summary)
        print_api_metrics(summary)
        partial_web_exporter.export_if_updated(summary)
        if not local_test_mode and summary.get("updated_this_run", 0) > 0:
            print("Committing partial progress after retry...")
            git_commit_and_push(_REPO_ROOT, f"partial export {summary['stats_date']} (after retry {retry_round})")
        elif not local_test_mode:
            print("No updated tracks after retry; skipping partial-progress commit.")

        current_pending_signature = {
            (r.get("track_id"), r.get("streams"), r.get("reason"))
            for r in summary.get("results", [])
            if r and r.get("status") == "pending"
        }
        if summary["updated_this_run"] == 0 and current_pending_signature == previous_pending_signature:
            print()
            print(
                f"The same {summary['pending_this_run']} track(s) remained unchanged after retry "
                f"{retry_round}; keeping the collection open."
            )
        previous_pending_signature = current_pending_signature


    current_pending_ids = {
        r["track_id"] for r in summary.get("results", [])
        if r and r.get("status") == "pending" and r.get("track_id")
    }
    non_extra_pending_ids = {
        tid for tid in current_pending_ids
        if tid in tracks_by_id and not tracks_by_id[tid].get("chart_extra")
    }
    extra_pending_ids = {
        tid for tid in current_pending_ids
        if tid in tracks_by_id and tracks_by_id[tid].get("chart_extra")
    }
    if non_extra_pending_ids and not dry_run_mode and not local_test_mode and not debug_daily_mode:
        print(
            f"{len(non_extra_pending_ids)} non-extra pending track(s) remain; "
            "posting remains blocked."
        )
    if extra_pending_ids and not dry_run_mode and not local_test_mode and not debug_daily_mode:
        print(
            f"{len(extra_pending_ids)} extra pending track(s) remain after the extra retry rule; "
            "posting remains blocked."
        )
    blocking_pending_ids = set(current_pending_ids)
    if blocking_pending_ids and not dry_run_mode and not local_test_mode and not debug_daily_mode:
        print(
            f"Blocking final export/post: {len(blocking_pending_ids)} pending track(s) are still unresolved."
        )

    print_remaining_details(summary)
    if local_test_mode:
        print("[LOCAL-TEST] Skip successful/unfinished JSON log updates.")
    else:
        update_json_logs_from_summary(summary)
    if not dry_run_mode and not local_test_mode:
        estimated_track_ids = estimate_missing_stream_history_gaps()
        if estimated_track_ids:
            all_updated_track_ids.update(estimated_track_ids)
            summary["history_index"] = HistoryIndex.load()
        push_updated_track_histories_to_r2(
            all_updated_track_ids,
            summary["history_index"],
        )

    all_tracks = load_tracks_from_discography()
    updated_ids: set[str] = {
        r["track_id"] for r in summary.get("results", [])
        if r and r.get("status") == "updated"
    }

    streak = load_not_found_streak()
    if local_test_mode:
        print("[LOCAL-TEST] Skip not-found streak updates and auto-delete.")
    else:
        update_not_found_streak(streak, not_found_ids, updated_ids)
        deleted = purge_stale_tracks(streak, all_tracks)
        if deleted:
            print(f"Auto-deleted {len(deleted)} stale track(s) not found for {MAX_NOT_FOUND_DAYS}+ days.")
        save_not_found_streak(streak)

    if dry_run_mode:
        print("[DRY-RUN] Scraping terminÃ© â€” aucune modification appliquÃ©e.")
        return

    if not blocking_pending_ids:
        print("All target tracks updated or explicitly closed.")
        if not summary.get("all_done"):
            # summary["all_done"] (from run_update) requires every track to have
            # reached status updated/skipped, but a chart_extra track stuck as
            # error/timeout (e.g. merged by Spotify into another track_id) never
            # gets a "results" entry, so that formula can stay False forever even
            # though blocking_pending_ids (the actual completeness gate above,
            # which only counts real "pending" tracks) is already empty. Keep
            # all_done in sync with the decision already made here, since
            # finalize_update.run_final_update_tasks re-checks summary["all_done"]
            # and would otherwise skip every posting step and the git commit.
            summary["all_done"] = True
        if normal_lock_mode:
            _write_daily_lock(stats_date, STREAMS_SCRAPED_LOCK_NAME, {
                "total_tracks": summary.get("total_tracks"),
                "updated_this_run": summary.get("updated_this_run"),
                "pending_this_run": summary.get("pending_this_run"),
                "not_found_this_run": summary.get("not_found_this_run"),
            })
    else:
        print("Run finished, but not all target tracks are done.")
        print("Keeping local progress only; final export/post will run after all blocking tracks are collected.")
        album_update_poster.stop()
        best_day_since_poster.stop()
        if not debug_daily_mode and not local_test_mode:
            notify(
                NTFY_TOPIC,
                f"Streams collection incomplete ({summary['stats_date']})\n"
                f"Done: {summary.get('done_tracks')}/{summary.get('total_tracks')} track(s)\n"
                f"Pending: {summary['pending_this_run']} | Errors: {summary['error_this_run']} | "
                f"Timeouts: {summary['timeout_this_run']} | Not found: {summary['not_found_this_run']}",
                title="Taylor Swift - Streams incomplete",
                tags="warning,chart_increasing",
            )
        return

    # Guard: every non-extra track must have a history entry for today before we post.
    # Retry indefinitely until all non-extra tracks are collected.
    if not local_test_mode and not debug_daily_mode:
        completeness_round = 0
        completeness_block_notified = False
        while True:
            active_track_ids_for_check = load_active_track_ids_from_discography()
            all_tracks_for_check = load_tracks_from_discography(active_track_ids_for_check)
            non_extra_ids = {t["track_id"] for t in all_tracks_for_check if not t.get("chart_extra")}
            done_ids_for_date = load_history_track_ids_with_daily_for_date(stats_date)
            missing_non_extra = non_extra_ids - done_ids_for_date - infinite_retry_track_ids
            if not missing_non_extra:
                break

            missing_titles = sorted(
                t["title"] for t in all_tracks_for_check if t["track_id"] in missing_non_extra
            )
            tracks_list = "\n".join(f"â€¢ {title}" for title in missing_titles)
            completeness_round += 1
            print(
                f"\nâ›” Completeness check round {completeness_round}: "
                f"{len(missing_non_extra)} non-extra track(s) still missing for {stats_date}:\n{tracks_list}"
            )
            if not completeness_block_notified:
                notify(
                    NTFY_TOPIC,
                    f"â›” Posting blocked (round {completeness_round}): {len(missing_non_extra)} non-extra track(s) missing ({stats_date}):\n{tracks_list}",
                    title="Taylor Swift - Completeness check failed",
                    tags="no_entry,chart_increasing",
                )
                completeness_block_notified = True
            if PENDING_RETRY_SLEEP_SECONDS > 0 and summary_had_http_status(summary, 409):
                print(f"Retrying in {PENDING_RETRY_SLEEP_SECONDS}s after HTTP 409...")
                time.sleep(PENDING_RETRY_SLEEP_SECONDS)
            else:
                print("Retrying immediately; previous pass did not report HTTP 409.")
            completeness_summary = run_update(
                on_progress=ProgressLogger(LOG_MODE),
                stats_date_override=stats_date_override,
                only_track_ids=missing_non_extra,
                token_mgr=token_mgr,
                force_reprocess=True,
                write_history=write_history,
                use_browser_scrape=use_browser_scrape_for_run,
                override_stream_guards=override_stream_guards,
            )
            all_updated_track_ids.update(completeness_summary.get("updated_track_ids") or set())
            print_summary_block(completeness_summary)
            print_api_metrics(completeness_summary)
            summary = completeness_summary

        previous_stats_date = get_previous_stats_date_str(stats_date)
        previous_done_ids = load_history_track_ids_with_daily_for_date(previous_stats_date)
        missing_previous_non_extra = non_extra_ids - previous_done_ids
        if missing_previous_non_extra:
            missing_titles = sorted(
                t["title"] for t in all_tracks_for_check if t["track_id"] in missing_previous_non_extra
            )
            tracks_list = "\n".join(f"â€¢ {title}" for title in missing_titles[:50])
            if len(missing_titles) > 50:
                tracks_list += f"\nâ€¦ and {len(missing_titles) - 50} more"
            print(
                f"\nâ›” Posting blocked: {len(missing_previous_non_extra)} non-extra track(s) "
                f"are missing comparison history for {previous_stats_date}:\n{tracks_list}"
            )
            notify(
                NTFY_TOPIC,
                f"Posting blocked: {len(missing_previous_non_extra)} non-extra track(s) missing comparison history for {previous_stats_date}",
                title="Taylor Swift - Comparison history missing",
                tags="no_entry,chart_increasing",
            )
            return

        print("Streams collection complete for the day; sending grower notification before posting/finalize.")
        try:
            notify_daily_growers(stats_date)
        except Exception as exc:
            print(f"[grower_notify] Failed to send grower notification: {exc}")

    if local_test_mode:
        print("[LOCAL-TEST] Skip streams history CSV migration.")
    else:
        print("Skipping legacy site-history CSV migration: this collector writes db/streams_history.csv directly.")

    posted_album_best_day_updates = album_best_day_since_poster.stop()
    posted_album_updates = album_update_poster.stop()
    posted_best_day_since_tracks = best_day_since_poster.stop()
    debut_post_state = debut_release_poster.stop()
    album_best_day_post_state = album_best_day_since_poster.post_state()
    album_post_state = album_update_poster.post_state()
    best_day_since_post_state = best_day_since_poster.post_state()
    initial_post_state = {
        "posted_count": (
            album_best_day_post_state["posted_count"]
            + album_post_state["posted_count"]
            + best_day_since_post_state["posted_count"]
            + debut_post_state["posted_count"]
        ),
        "last_post_at": max(
            album_best_day_post_state["last_post_at"],
            album_post_state["last_post_at"],
            best_day_since_post_state["last_post_at"],
            debut_post_state["last_post_at"],
        ),
    }

    final_web_export: BackgroundFinalWebExport | None = None
    if not local_test_mode and not debug_daily_mode:
        artist_metadata_updated_before_finalize = False
        if artist_thread is not None:
            print("Updating artist metadata before starting final web export...")
            artist_thread.join(timeout=60)
            update_artist_metadata(pre_scraped=_artist_result[0])
            artist_thread = None
            artist_metadata_updated_before_finalize = True

        final_web_export = BackgroundFinalWebExport(
            stats_date=stats_date,
            allow_r2=True,
            force=artist_metadata_updated_before_finalize,
        )
        final_web_export.start()

    def _album_tracks_done_for_finalize(album_name: str, check_date: str) -> bool:
        if check_date != stats_date or not infinite_retry_track_ids:
            return album_tracks_done_for(album_name, check_date)
        album_ids = load_album_track_ids_for_album(album_name)
        if not album_ids:
            return False
        done_ids = load_history_track_ids_with_daily_for_date(check_date)
        return (album_ids - infinite_retry_track_ids).issubset(done_ids)

    def _all_album_tracks_done_finalize(check_date: str) -> bool:
        if check_date != stats_date or not infinite_retry_track_ids:
            return all_album_tracks_done(check_date)
        album_ids = load_album_track_ids()
        if not album_ids:
            return True
        done_ids = load_history_track_ids_with_daily_for_date(check_date)
        return (album_ids - infinite_retry_track_ids).issubset(done_ids)

    run_final_update_tasks(FinalizeContext(
        script_dir=_SCRIPT_DIR,
        repo_root=_REPO_ROOT,
        stats_date=stats_date,
        summary=summary,
        no_post_mode=no_post_mode,
        debug_daily_mode=debug_daily_mode,
        local_test_mode=local_test_mode,
        post_spacing_seconds=POST_BETWEEN_STREAMS_POSTS_SECONDS,
        log_mode=LOG_MODE,
        artist_thread=artist_thread,
        artist_result=_artist_result,
        export_web_data=final_web_export.export_web_data if final_web_export is not None else export_web_data,
        update_artist_metadata=update_artist_metadata,
        album_tracks_done_for=_album_tracks_done_for_finalize,
        all_album_tracks_done=_all_album_tracks_done_finalize,
        load_album_sections_flat=load_album_sections_flat,
        extract_track_id=extract_track_id,
        load_history_track_ids_for_date=load_history_track_ids_for_date,
        find_biggest_album_gainer_for_spotlight=find_biggest_album_gainer_for_spotlight,
        posted_album_updates=posted_album_updates | posted_album_best_day_updates,
        initial_post_state=initial_post_state,
        posted_best_day_since_tracks=posted_best_day_since_tracks,
        throwback_mode=throwback_mode,
        throwback_action=throwback_action,
        throwback_event=throwback_event,
        throwback_label=throwback_label,
        throwback_force=throwback_force,
    ))

    if infinite_retry_track_ids and infinite_retry_thread is None and not dry_run_mode and not local_test_mode and not debug_daily_mode:
        print(
            f"Starting extra retry worker after final export/post "
            f"({len(infinite_retry_track_ids)} track(s))."
        )
        infinite_retry_thread = threading.Thread(
            target=retry_pending_tracks_until_collected,
            args=(set(infinite_retry_track_ids),),
            kwargs={
                "stats_date": stats_date,
                "stats_date_override": stats_date_override,
                "token_mgr": token_mgr,
                "force_reprocess": force_reprocess,
                "write_history": write_history,
                "collected_ids": infinite_retry_collected_ids,
                "use_browser_scrape": use_browser_scrape_for_run,
                "override_stream_guards": override_stream_guards,
            },
            daemon=True,
        )
        infinite_retry_thread.start()

    if infinite_retry_thread is not None:
        print(
            f"Waiting for infinite top-{INFINITE_RETRY_PREVIOUS_DAY_TOP_N} retry worker "
            "to finish after posting..."
        )
        infinite_retry_thread.join()
        if infinite_retry_collected_ids:
            all_updated_track_ids.update(infinite_retry_collected_ids)
            git_commit_and_push(
                _REPO_ROOT,
                f"partial export {stats_date} (top {INFINITE_RETRY_PREVIOUS_DAY_TOP_N} infinite retry)",
            )
        print("Infinite retry worker finished.")

    if not dry_run_mode and not debug_daily_mode and not local_test_mode and not throwback_mode:
        run_discography_backfill_after_streams(token_mgr, stats_date)

    if normal_lock_mode and summary["all_done"]:
        _write_daily_lock(stats_date, STREAMS_UPDATE_COMPLETE_LOCK_NAME, {
            "total_tracks": summary.get("total_tracks"),
            "updated_this_run": summary.get("updated_this_run"),
            "pending_this_run": summary.get("pending_this_run"),
            "not_found_this_run": summary.get("not_found_this_run"),
        })

    elapsed = time.perf_counter() - START_TIME
    print()
    print("=" * 70)
    print("âœ“ Execution complete")
    print("=" * 70)
    print(f"  Duration:          {int(elapsed // 60)}m {int(elapsed % 60)}s")
    print(f"  Updated:           {summary['updated_this_run']} track(s)")
    print(f"  Pending (retry):   {summary['pending_this_run']} track(s)")
    print(f"  Not found:         {summary['not_found_this_run']} track(s)")
    print("=" * 70)
    print()

    if local_test_mode:
        print("[LOCAL-TEST] Finished without history writes, R2, Twitter, git, or notify.")

    if not debug_daily_mode and not local_test_mode and not throwback_mode:
        notify(
            NTFY_TOPIC,
            f"âœ“ {summary['updated_this_run']} track(s) updated ({summary['stats_date']})\n"
            f"Duration: {int(elapsed // 60)}m {int(elapsed % 60)}s",
            title="Taylor Swift - Streams updated",
            tags="white_check_mark,chart_increasing",
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as _exc:
        import traceback as _traceback
        _tb = _traceback.format_exc()
        print(_tb, flush=True)
        try:
            notify(
                NTFY_TOPIC,
                f"{_exc}\n\n{_tb[-1500:]}",
                title="Taylor Swift - Streams pipeline CRASHED",
                tags="rotating_light,warning",
                priority="urgent",
            )
        except Exception:
            pass
        raise
