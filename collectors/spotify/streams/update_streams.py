from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import threading
import time
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

sys.path.insert(0, str(_SCRIPT_DIR / "tools" / "scripts"))
sys.path.insert(0, str(_SCRIPT_DIR / "extras"))
sys.path.insert(0, str(_SCRIPT_DIR.parents[0]))  # collectors/spotify/ for core.*

import export_for_web
from finalize_update import FinalizeContext, run_final_update_tasks
from reporting import ProgressLogger, print_remaining_details, print_summary_block, update_json_logs_from_summary
import spotify_api as _spotify_api
from stream_utils import block_unneeded, format_int, get_previous_stats_date_str, get_stats_date_str, launch_browser
from spotify_api import (
    AdaptiveWorkerState,
    TokenManager,
    _probe_via_api,
    _warp_connect,
    fetch_playcount_api,
)
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
    has_real_update,
    load_active_track_ids_from_discography,
    load_album_sections_flat,
    load_history_rows,
    load_history_track_ids_for_date,
    load_track_priorities_from_specific_date,
    load_tracks_from_discography,
    push_updated_track_histories_to_r2,
    save_history_rows,
)
from artist_metadata import scrape_artist_metadata, scrape_artist_top_tracks, update_artist_metadata
from git_ops import git_commit_and_push
from config import NTFY_TOPIC
from core.data_paths import archived_db_file, update_streams_dir
from core.notify import send as notify

ROOT = _REPO_ROOT / "website"
DATA_DIR = ROOT / "data"
_DB_ROOT = _REPO_ROOT / "db"
_ARCHIVE_DB_ROOT = _REPO_ROOT / "data" / "_archive" / "original" / "db"

HISTORY_PATH = (
    _DB_ROOT / "streams_history.csv"
    if (_DB_ROOT / "streams_history.csv").exists()
    else archived_db_file("streams_history.csv")
)
ARTIST_MONTHLY_HISTORY_PATH = (
    _DB_ROOT / "artist_monthly_listeners_history.csv"
    if (_DB_ROOT / "artist_monthly_listeners_history.csv").exists()
    else archived_db_file("artist_monthly_listeners_history.csv")
)
FAILED_PATH = DATA_DIR / "not_found_today.csv"
PENDING_LOG_PATH = DATA_DIR / "pending_debug_today.csv"
LAST_SUCCESSFUL_UPDATE_JSON = DATA_DIR / "last_successful_updates.json"
LAST_UNFINISHED_UPDATE_JSON = DATA_DIR / "last_unfinished_updates.json"

DISCOGRAPHY_DIR = _DB_ROOT / "discography"
DB_ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
DB_SONGS_JSON = DISCOGRAPHY_DIR / "songs.json"
ARTIST_PATH = DISCOGRAPHY_DIR / "artist.json"
ARTIST_URL = "https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02"

# Spotify daily update happens around this local hour; before it, we're still in the previous day's window
SPOTIFY_UPDATE_HOUR = 15

HEADLESS = True
MAX_PARALLEL_PAGES = 10
PAGE_GOTO_TIMEOUT_MS = 20_000
DEBUG_PAGE_PREVIEW = False
# Logging
# - default: compact output
# - --verbose: per-track lines + extra debug prints
# - --quiet: only periodic summaries + errors
LOG_MODE = "normal"  # "quiet" | "normal" | "verbose"

# Hill climbing
HILL_WINDOW        = 12     # completions par fenêtre d'évaluation (was 20 — react faster)
HILL_429_THRESHOLD = 0.15   # taux de 429 au-delà duquel on retire 1 worker
HILL_MIN_WORKERS   = 2
HILL_INITIAL       = 9      # point de départ (was 6 — start near max immediately)

PROBE_CANDIDATES = 10  # top N tracks (by streams) used as probe candidates

PENDING_RETRY_SLEEP_SECONDS = 0
POST_BETWEEN_STREAMS_POSTS_SECONDS = 30
INCREMENTAL_PUBLISH_ON_UPDATE = False

NOT_FOUND_STREAK_PATH = DATA_DIR / "not_found_streak.json"
MAX_NOT_FOUND_DAYS = 7

MAX_DAILY_INCREASE = 50_000_000

# ── API GraphQL Spotify ───────────────────────────────────────────────────────
START_TIME = None


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

# ── Live update signal ────────────────────────────────────────────────────────
_UPDATE_SIGNAL_SENT = threading.Event()

# ── Per-track incremental R2 upload ──────────────────────────────────────────


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

  python update_streams.py --local-test YYYY-MM-DD
      Force re-scrape even if the date already exists, but skip history writes,
      R2, Twitter, git, forecast, and image metadata refresh.

  python update_streams.py --no-post
      Run full pipeline but skip all Twitter posting steps.

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
    if allow_r2:
        export_for_web.export_for_web(stats_date=stats_date)
        return

    previous = os.environ.get("UPLOAD_TO_R2")
    os.environ["UPLOAD_TO_R2"] = "0"
    try:
        export_for_web.export_for_web(stats_date=stats_date)
    finally:
        if previous is None:
            os.environ.pop("UPLOAD_TO_R2", None)
        else:
            os.environ["UPLOAD_TO_R2"] = previous


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
    # daily_streams ideally uses yesterday's total. If yesterday is missing (partial run,
    # newly added track, not-found yesterday), fall back to last known total so daily isn't blank.
    daily_base = previous_day_total if previous_day_total is not None else last_total
    daily = compute_daily(daily_base, total)

    if last_total is None:
        reason = "first_seen"
        real_update = True
    elif total == last_total:
        reason = "same_total"
        real_update = False
    elif total < last_total:
        reason = "lower_than_previous"
        real_update = True
    elif total - last_total > MAX_DAILY_INCREASE:
        reason = f"anomaly_delta_gt_{MAX_DAILY_INCREASE}"
        real_update = False
    else:
        reason = "updated"
        real_update = True

    if real_update and dry_run_mode:
        status = "pending"
    elif real_update:
        if write_history:
            with lock:
                if history_index is not None:
                    history_index.append(stats_date, track_id, total, daily)
                else:
                    append_history_row([stats_date, track_id, total, daily if daily is not None else ""])
        status = "updated"

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
        "status": status,
        "streams": total,
        "daily_streams": daily,
        "previous_streams": last_total,
        "delta": (total - last_total) if last_total is not None else None,
        "reason": reason,
    }


def _probe_on_page(probe_tracks: list[dict], page) -> dict:
    """
    Logique séquentielle :
    - Cherche la 1ère chanson OK → si non updatée → can_start=False
    - Si 1ère updatée → cherche la 2ème chanson OK → si updatée → can_start=True
    """
    results = []
    successful_probes = 0
    updated_probes = 0
    can_start_full_run = False

    for track in probe_tracks:
        title = track["title"]
        url = track["spotify_url"]
        total, raw, scrape_status, _ = scrape_track_total(page, title, url)
        last_total = get_last_history_total(track["track_id"])

        if scrape_status == "ok" and total is not None:
            updated = has_real_update(last_total, total)
            successful_probes += 1
            if updated:
                updated_probes += 1
            results.append({
                "title": title,
                "status": "ok",
                "streams": total,
                "previous_streams": last_total,
                "updated": updated,
                "raw": raw,
            })
            if successful_probes == 1 and not updated:
                break  # 1ère chanson pas updatée → inutile de continuer
            if successful_probes == 2:
                can_start_full_run = updated
                break  # 2ème chanson OK → décision finale
        else:
            results.append({
                "title": title,
                "status": scrape_status,
                "streams": None,
                "previous_streams": last_total,
                "updated": False,
                "raw": None,
            })

    return {
        "can_start_full_run": can_start_full_run,
        "successful_probes": successful_probes,
        "updated_probes": updated_probes,
        "results": results,
    }


def build_probe_tracks(tracks: list[dict]) -> list[dict]:
    """Retourne les PROBE_CANDIDATES tracks avec le plus de streams hier, triées décroissant."""
    last_totals = get_all_last_history_totals()
    scored = [(t, last_totals.get(t["track_id"], 0)) for t in tracks]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:PROBE_CANDIDATES]]


def run_probe(tracks: list[dict]) -> dict:
    probe_tracks = build_probe_tracks(tracks)

    if not probe_tracks:
        print("Probe skipped: no probe tracks found in database.")
        return {
            "can_start_full_run": True,
            "successful_probes": 0,
            "updated_probes": 0,
            "results": [],
        }

    p = sync_playwright().start()
    browser = launch_browser(p)
    context = browser.new_context(locale="fr-FR")
    page = context.new_page()
    page.route("**/*", block_unneeded)

    try:
        return _probe_on_page(probe_tracks, page)
    finally:
        browser.close()
        p.stop()


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
    write_history: bool = True,
    compare_before_stats_date: bool = False,
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
            elif token_mgr is not None and token_mgr.available:
                # Primary: API GraphQL Spotify (3 attempts)
                api_result = None
                for _api_attempt in range(3):
                    api_result = fetch_playcount_api(
                        track["track_id"],
                        token_mgr,
                        _api_session,
                        metrics=api_metrics,
                    )
                    if api_result is not None:
                        break
                if api_result is not None:
                    total, raw, scrape_status = api_result, str(api_result), "ok"
                else:
                    scrape_status = "error"
                    total, raw = None, ""
            else:
                scrape_status = "error"
                total, raw = None, ""

            if adaptive is not None:
                adaptive.record(got_429=bool(api_metrics.get("had_429")))

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
                }
                with lock:
                    failed_results.append(dict(result))

            elif scrape_status == "not_found" or total is None:
                # Retry API immédiat pour les tracks du top-50
                if track["track_id"] in priority_top_50_ids and token_mgr is not None and token_mgr.available:
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


def run_update(
    on_progress=None,
    skip_track_ids: set[str] | None = None,
    stats_date_override: str | None = None,
    dry_run_mode: bool = False,
    only_track_ids: set[str] | None = None,
    token_mgr: "TokenManager | None" = None,
    force_reprocess: bool = False,
    write_history: bool = True,
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
    total_all_tracks = len(tracks)

    previous_day_priorities = load_track_priorities_from_specific_date(
        get_previous_stats_date_str(stats_date)
    )
    tracks.sort(
        key=lambda t: (-previous_day_priorities.get(t["track_id"], 0), t["title"].casefold())
    )

    if only_track_ids is not None:
        tracks = [t for t in tracks if t["track_id"] in only_track_ids]

    total_tracks = len(tracks)

    priority_top_50_ids = get_priority_top_50_track_ids_from_previous_day(tracks, stats_date)

    if priority_top_50_ids and len(priority_top_50_ids) < 50:
        print(f"Warning: only {len(priority_top_50_ids)} priority track(s) found from previous day.")

    pre_scraped: dict[str, int] = {}

    already_done_for_stats_date = history_index.done_ids_for_date(stats_date)

    queue = Queue()
    failed_results: list[dict] = []
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
        initial_workers = min(HILL_INITIAL, queue.qsize())
        max_workers     = min(MAX_PARALLEL_PAGES, queue.qsize())
        adaptive = AdaptiveWorkerState(initial=initial_workers)

        # Spawner max_workers threads dès maintenant.
        # Les workers avec worker_id >= initial_workers attendent leur activation (pas de fenêtre ouverte).
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
                    write_history,
                    force_reprocess,
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

        # ── Retry failures (2ème passe, 30s d'attente, 3 workers max) ──────
        retry_candidates = [
            r for r in failed_results
            if r.get("status") in {"not_found", "timeout", "error"}
        ]
        if retry_candidates:
            print(
                f"\n  {len(retry_candidates)} failure(s) — retry immédiat avec {min(6, len(retry_candidates))} workers..."
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
            retry_workers  = [
                threading.Thread(
                    target=_worker,
                    args=(retry_queue, retry_results, retry_failed, lock, publish_lock,
                          None, retry_total, dry_run_mode, idx, retry_adaptive,
                          frozenset(), None, token_mgr, history_index,
                          write_history, force_reprocess),
                    daemon=True,
                )
                for idx in range(min(6, retry_total))
            ]
            for w in retry_workers:
                w.start()
            retry_queue.join()
            for w in retry_workers:
                w.join(timeout=5)

            # Fusionner dans failed_results : retirer les candidats résolus
            resolved_ids = {
                r["track_id"]
                for r in retry_results
                if r and r.get("status") not in (None, "not_found", "timeout", "error")
            }
            failed_results[:] = [r for r in failed_results if r.get("track_id") not in resolved_ids]
            print(
                f"  Retry terminé : {len(resolved_ids)} récupérés, {len(retry_candidates) - len(resolved_ids)} encore en échec"
            )

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

    _warp_connect()

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
    no_post_mode = "--no-post" in sys.argv
    reset_last_date_mode = "--reset-last-date" in sys.argv
    write_history = not local_test_mode
    force_reprocess = local_test_mode

    if local_test_mode:
        no_post_mode = True

    if debug_daily_mode and debug_total_mode:
        print("Use either --debug-daily or --debug-total, not both.")
        sys.exit(1)
    if local_test_mode and (debug_daily_mode or debug_total_mode or dry_run_mode):
        print("Use --local-test by itself (optionally with a date, --quiet, or --verbose).")
        sys.exit(1)

    remaining_args = [
        a for a in sys.argv[1:]
        if a not in (
            "--debug-daily",
            "--debug-total",
            "--dry-run",
            "--local-test",
            "--no-post",
            "--reset-last-date",
            "--quiet",
            "--verbose",
            "--help",
            "-h",
        )
    ]

    stats_date_override = None
    reset_date_override = None

    i = 0
    while i < len(remaining_args):
        arg = remaining_args[i]

        if arg == "--reset-date":
            if i + 1 >= len(remaining_args):
                print("Missing value after --reset-date (expected YYYY-MM-DD)")
                sys.exit(1)
            reset_date_override = remaining_args[i + 1]
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

    if local_test_mode:
        print("[LOCAL-TEST] Force re-scrape, no history writes, no R2, no Twitter, no git.")
    elif dry_run_mode:
        print("[DRY-RUN] Scraping uniquement — aucune modification.")
    elif debug_daily_mode:
        print("[DEBUG-DAILY] Retry unfinished tracks, writes history, no Twitter/git/forecast/images/notify.")
    elif debug_total_mode:
        print("[DEBUG-TOTAL] Replace totals for an existing date in streams_history.csv.")
    else:
        print("[NORMAL] Official run mode.")

    if dry_run_mode or debug_daily_mode or local_test_mode:
        os.environ["UPLOAD_TO_R2"] = "0"
        print("R2 upload disabled for this run mode.")
    else:
        os.environ["UPLOAD_TO_R2"] = "1"
        print("R2 upload enabled for this run (UPLOAD_TO_R2=1).")

    stats_date = stats_date_override or get_stats_date_str()
    
    print("=" * 70)
    print("Taylor Swift - Spotify Streams Collector")
    print("=" * 70)
    print(f"Target stats date: {stats_date}")
    print()

    if debug_total_mode:
        if stats_date_override is None:
            print("--debug-total requires a date: python update_streams.py --debug-total YYYY-MM-DD")
            sys.exit(1)
        run_debug_total_replace(stats_date)
        return

    active_track_ids = load_active_track_ids_from_discography()
    tracks = load_tracks_from_discography(active_track_ids)

    already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
    done_tracks_before_run = len(already_done_for_stats_date)
    total_tracks = len(tracks)

    print(f"Loaded {total_tracks} track(s) from discography")
    print()

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

    # Si tous les tracks sont déjà done, ou si on backfille une date déjà dépassée,
    # on saute Playwright/API entièrement
    last_history_date = get_last_stats_date_in_history()
    is_backfill = last_history_date is not None and last_history_date > stats_date

    # If stats_date has no data at all but history has a more recent date,
    # the computed date was never captured (e.g. old code mislabeled it). Advance
    # stats_date to the most recent available date so export/post work correctly.
    if is_backfill and done_tracks_before_run == 0:
        print(f"Backfill detected: history has data up to {last_history_date} but {stats_date} has no data.")
        print(f"Advancing stats_date to {last_history_date} for export/post.")
        stats_date = last_history_date
        stats_date_override = stats_date  # propagate to run_update() and summary
        already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
        done_tracks_before_run = len(already_done_for_stats_date)
        is_backfill = False  # stats_date now points to existing data

    scraping_needed = (
        (done_tracks_before_run < total_tracks and not is_backfill)
        or dry_run_mode
        or local_test_mode
        or debug_daily_mode
    )

    if scraping_needed:
        # Capture des tokens API (une seule fois pour tout le run)
        token_mgr = TokenManager()
        if not token_mgr.capture():
            print("TokenManager: échec — impossible d'obtenir les tokens Spotify. Vérifiez la connexion.")
            sys.exit(1)
    else:
        token_mgr = None
        print("Tous les tracks déjà mis à jour pour cette date — Playwright/scraping ignoré.")

    should_run_probe = (
        done_tracks_before_run == 0
        and not is_backfill
        and stats_date_override is None
        and not dry_run_mode
        and not local_test_mode
        and not debug_daily_mode
    )

    if should_run_probe:
        probe_tracks = build_probe_tracks(tracks)

        if not probe_tracks:
            print("Probe skipped: no probe tracks found in database.")
        else:
            def _print_probe(probe):
                print(
                    f"Probe result | successful={probe['successful_probes']} | "
                    f"updated={probe['updated_probes']} | "
                    f"start_full_run={probe['can_start_full_run']}"
                )
                for row in probe["results"]:
                    if row["status"] == "ok":
                        print(
                            f"PROBE {row['title']} | "
                            f"current={format_int(row['streams'])} | "
                            f"previous={format_int(row['previous_streams'])} | "
                            f"updated={'yes' if row['updated'] else 'no'}"
                        )
                    else:
                        print(f"PROBE {row['title']} | status={row['status']}")

            # Essai probe via API
            api_probe = _probe_via_api(probe_tracks, token_mgr)
            if api_probe is not None:
                print("Running probe check... [API]")
                _print_probe(api_probe)
                while not api_probe["can_start_full_run"]:
                    print()
                    print("Spotify does not appear to have started the next daily update yet.")
                    print("Retrying in 2 seconds...")
                    time.sleep(2)
                    print("Running probe check... [API]")
                    api_probe = _probe_via_api(probe_tracks, token_mgr)
                    _print_probe(api_probe)
            else:
                print("Probe via API unavailable (no token) — skipping probe, starting run.")
    elif done_tracks_before_run < total_tracks and not debug_daily_mode:
        print("Partial progress already exists for this stats date.")
        print("Skipping probe and resuming unfinished tracks.")
    elif debug_daily_mode:
        print("Skipping probe in debug-daily mode.")
    else:
        print("All tracks already appear done for this stats date.")
        print("Skipping probe and refreshing export/publish anyway.")

    print()
    print("=" * 70)
    print("Run")
    print("=" * 70)

    _artist_result: list[dict | None] = [None]

    def _scrape_artist_bg():
        _artist_result[0] = scrape_artist_metadata()

    artist_thread = None
    if not dry_run_mode:
        artist_thread = threading.Thread(target=_scrape_artist_bg, daemon=True)
        artist_thread.start()

    progress = ProgressLogger(LOG_MODE)
    summary = run_update(
        on_progress=progress,
        stats_date_override=stats_date_override,
        dry_run_mode=dry_run_mode,
        only_track_ids=unfinished_ids if debug_daily_mode else None,
        token_mgr=token_mgr,
        force_reprocess=force_reprocess,
        write_history=write_history,
    )
    all_updated_track_ids = set(summary.get("updated_track_ids") or set())
    print_summary_block(summary)

    not_found_ids: set[str] = {
        r["track_id"] for r in summary["failed_results"] if r["status"] == "not_found"
    }

    # Check if this is the first run of the day with zero updates (Spotify hasn't updated yet)
    history_entries_for_this_date = load_history_track_ids_for_date(summary["stats_date"])
    is_first_run_of_day = len(history_entries_for_this_date) == 0
    has_zero_real_updates = summary["updated_this_run"] == 0

    retry_round = 0
    while (
        not dry_run_mode
        and not local_test_mode
        and not debug_daily_mode
        and not summary["all_done"]
        and summary["pending_this_run"] > 0
    ):
        # Don't retry on first run of the day if there are zero real updates
        # This means Spotify hasn't done its daily update yet - wait for next run instead
        if retry_round == 0 and is_first_run_of_day and has_zero_real_updates:
            print()
            print(
                f"⚠ {summary['pending_this_run']} unchanged track(s) detected, "
                f"but this is the first run for {stats_date} with zero updates."
            )
            print("Spotify may not have updated yet. Skipping retries for now.")
            break

        retry_round += 1

        print()
        print(
            f"Detected {summary['pending_this_run']} unchanged track(s) "
            f"for {summary['stats_date']}."
        )
        if not_found_ids:
            print(f"Skipping {len(not_found_ids)} not-found track(s) on this retry.")

        print()
        print("=" * 70)
        print(f"Retry round {retry_round}")
        print("=" * 70)

        summary = run_update(
            on_progress=progress,
            skip_track_ids=not_found_ids,
            stats_date_override=stats_date_override,
            dry_run_mode=False,
            token_mgr=token_mgr,
            force_reprocess=force_reprocess,
            write_history=write_history,
        )
        all_updated_track_ids.update(summary.get("updated_track_ids") or set())
        not_found_ids.update(
            r["track_id"] for r in summary["failed_results"] if r["status"] == "not_found"
        )
        print_summary_block(summary)
        if not local_test_mode:
            print("Committing partial progress after retry...")
            git_commit_and_push(_REPO_ROOT, f"partial export {summary['stats_date']} (after retry {retry_round})")

    print_remaining_details(summary)
    if local_test_mode:
        print("[LOCAL-TEST] Skip successful/unfinished JSON log updates.")
    else:
        update_json_logs_from_summary(summary)
    if not dry_run_mode and not local_test_mode:
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
        print("[DRY-RUN] Scraping terminé — aucune modification appliquée.")
        return

    if summary["all_done"]:
        print("All target tracks updated.")
    else:
        print("Run finished, but not all target tracks are done.")
        print("Publishing current data anyway." if not debug_daily_mode else "Keeping local progress only.")

    if local_test_mode:
        print("[LOCAL-TEST] Skip streams history CSV migration.")
    else:
        print("Updating streams history CSV...")
        subprocess.run(
            [sys.executable, str(_SCRIPT_DIR / "tools" / "scripts" / "migrate_streams_to_csv.py")],
            check=False,
        )
        print("Streams history CSV done.")

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
        export_web_data=export_web_data,
        update_artist_metadata=update_artist_metadata,
        album_tracks_done_for=album_tracks_done_for,
        all_album_tracks_done=all_album_tracks_done,
        load_album_sections_flat=load_album_sections_flat,
        extract_track_id=extract_track_id,
        load_history_track_ids_for_date=load_history_track_ids_for_date,
        find_biggest_album_gainer_for_spotlight=find_biggest_album_gainer_for_spotlight,
    ))

    elapsed = time.perf_counter() - START_TIME
    print()
    print("=" * 70)
    print("✓ Execution complete")
    print("=" * 70)
    print(f"  Duration:          {int(elapsed // 60)}m {int(elapsed % 60)}s")
    print(f"  Updated:           {summary['updated_this_run']} track(s)")
    print(f"  Pending (retry):   {summary['pending_this_run']} track(s)")
    print(f"  Not found:         {summary['not_found_this_run']} track(s)")
    print("=" * 70)
    print()

    if local_test_mode:
        print("[LOCAL-TEST] Finished without history writes, R2, Twitter, git, or notify.")

    if not debug_daily_mode and not local_test_mode:
        notify(
            NTFY_TOPIC,
            f"✓ {summary['updated_this_run']} track(s) updated ({summary['stats_date']})\n"
            f"Duration: {int(elapsed // 60)}m {int(elapsed % 60)}s",
            title="Taylor Swift - Streams updated",
            tags="white_check_mark,chart_increasing",
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
