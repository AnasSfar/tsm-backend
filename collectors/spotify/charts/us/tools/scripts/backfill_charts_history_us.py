#!/usr/bin/env python3
"""Backfill Taylor Swift entries from Spotify Charts (US daily) into db/charts_history_us.csv.

Goal:
- Fetch chart data via Spotify's internal Charts API (same data behind charts.spotify.com)
- Filter only rows where artist list contains "Taylor Swift" (incl. feats/collabs)
- Produce an archive CSV with columns:
  date,song_name,rank,streams,previous_rank,peak_rank,total_days,streak,movement

This script is designed to be fast and reliable:
- No Last.fm / MusicBrainz enrichment
- HTTP session + retries
- Bearer token cached for ~50 minutes; refreshed automatically on 401/403
- Resume supported from existing output CSV

Usage examples:
  python backfill_charts_history_us.py
  python backfill_charts_history_us.py --start 2017-01-01 --end 2026-04-03
  python backfill_charts_history_us.py --force
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests
from playwright.sync_api import sync_playwright

# Ensure UTF-8 output on Windows terminals.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Import fmt_delta from collectors/spotify/core
sys.path.insert(0, str(Path(__file__).parents[4]))
from core.fmt import fmt_delta  # noqa: E402


TS_NAME = "Taylor Swift"
CHART_ID = "regional-us-daily"
_API_BASE = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)
_TOKEN_TTL_SECONDS = 50 * 60  # 50 minutes (conservative)

ROOT = Path(__file__).parent
TOOLS_DIR = ROOT.parent
SESSION_FILE = TOOLS_DIR / "json" / "spotify_session.json"
BEARER_CACHE_FILE = TOOLS_DIR / "json" / "bearer_cache.json"
STATE_FILE = TOOLS_DIR / "json" / "backfill_us_state.json"
DEFAULT_FAILED_DATES_CSV = TOOLS_DIR / "json" / "backfill_us_failed_dates.csv"
CACHE_FILE_DEFAULT = TOOLS_DIR / "json" / "backfill_us_cache.json"

DEFAULT_OUTPUT_CSV = Path(__file__).resolve().parents[6] / "db" / "charts_history_us.csv"

CSV_COLUMNS = [
    "date",
    "song_name",
    "rank",
    "streams",
    "previous_rank",
    "peak_rank",
    "total_days",
    "streak",
    "movement",
]


FAILED_COLUMNS = [
    "date",
    "error_type",
    "http_status",
    "message",
    "saved_at",
]


def _load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _cache_last_date(cache: dict[str, dict]) -> Optional[str]:
    dates = [k for k in cache.keys() if isinstance(k, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", k)]
    return max(dates) if dates else None


def _cache_put_ok(cache: dict[str, dict], chart_date: str, status: int, ts_rows: list[dict]) -> None:
    cache[chart_date] = {
        "status": status,
        "ts_rows": ts_rows,
        "saved_at": int(time.time()),
    }


def _cache_put_err(cache: dict[str, dict], chart_date: str, error_type: str, status: Optional[int], message: str) -> None:
    cache[chart_date] = {
        "status": status,
        "error_type": error_type,
        "message": (message or "")[:500],
        "saved_at": int(time.time()),
    }


def _is_err(cache_entry: dict) -> bool:
    return isinstance(cache_entry, dict) and bool(cache_entry.get("error_type"))


def _is_ok(cache_entry: dict) -> bool:
    return isinstance(cache_entry, dict) and cache_entry.get("status") == 200 and isinstance(cache_entry.get("ts_rows"), list)


def _is_done(cache_entry: dict) -> bool:
    if not isinstance(cache_entry, dict):
        return False
    if cache_entry.get("error_type"):
        return False
    status = cache_entry.get("status")
    if status in (200, 400, 404):
        return isinstance(cache_entry.get("ts_rows"), list)
    return False


def _pending_dates(cache: dict[str, dict], start_date: date, end_date: date) -> list[str]:
    pending: list[str] = []
    d = start_date
    while d <= end_date:
        ds = _ymd(d)
        entry = cache.get(ds)
        if not entry or not _is_done(entry):
            pending.append(ds)
        d += timedelta(days=1)
    return pending


def _build_archive_csv_from_cache(
    *,
    cache: dict[str, dict],
    start_date: date,
    end_date: date,
    output_csv: Path,
) -> None:
    tmp = output_csv.with_suffix(output_csv.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()

        song_state: dict[str, SongState] = {}
        d = start_date
        while d <= end_date:
            ds = _ymd(d)
            entry = cache.get(ds) if isinstance(cache, dict) else None
            if entry and _is_ok(entry):
                ts_rows = entry.get("ts_rows") or []
                for r in ts_rows:
                    song = (r.get("song_name") or r.get("track_name") or "").strip()
                    if not song:
                        continue

                    rank = r.get("rank")
                    streams = r.get("streams")
                    previous_rank = r.get("previous_rank")
                    peak_rank = r.get("peak_rank")

                    prev_state = song_state.get(song)
                    if prev_state and prev_state.last_seen == _ymd(d - timedelta(days=1)):
                        streak = prev_state.streak + 1
                    else:
                        streak = 1

                    total_days_for_song = (prev_state.total_days + 1) if prev_state else 1
                    song_state[song] = SongState(total_days=total_days_for_song, streak=streak, last_seen=ds)

                    movement = fmt_delta(
                        rank=int(rank) if rank not in (None, "") else None,
                        previous_rank=int(previous_rank) if previous_rank not in (None, "") else None,
                        peak_rank=int(peak_rank) if peak_rank not in (None, "") else None,
                        total_days=int(total_days_for_song),
                    )

                    w.writerow(
                        {
                            "date": ds,
                            "song_name": song,
                            "rank": rank or "",
                            "streams": streams or "",
                            "previous_rank": previous_rank or "",
                            "peak_rank": peak_rank or "",
                            "total_days": total_days_for_song,
                            "streak": streak,
                            "movement": movement,
                        }
                    )
            d += timedelta(days=1)

    # Atomic-ish replace
    if output_csv.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = output_csv.with_name(output_csv.name + f".bak.{ts}")
        output_csv.replace(backup)
        print(f"Backed up existing output to: {backup}")
    tmp.replace(output_csv)
    print(f"Wrote rebuilt archive CSV: {output_csv}")


class _AuthError(RuntimeError):
    pass


class _RateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: Optional[int] = None):
        super().__init__("Rate limited")
        self.retry_after_seconds = retry_after_seconds


class _UpstreamError(RuntimeError):
    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"Upstream error ({status_code})")
        self.status_code = status_code


def _parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _ymd(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _clean_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None

    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"[,_\s]", "", s)

    if re.fullmatch(r"-?\d+", s):
        n = int(s)
        return n if n > 0 else None
    return None


def _load_cached_token() -> Optional[str]:
    if not BEARER_CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(BEARER_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    token = (payload.get("token") or "").strip()
    saved_at = payload.get("saved_at") or payload.get("ts") or payload.get("time")

    if not token or not saved_at:
        return None

    try:
        saved_at = float(saved_at)
    except Exception:
        return None

    if time.time() - saved_at > _TOKEN_TTL_SECONDS:
        return None
    return token


def _save_cached_token(token: str) -> None:
    BEARER_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": token,
        "saved_at": time.time(),
    }
    BEARER_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_bearer_token(*, force_refresh: bool = False) -> str:
    if not force_refresh:
        cached = _load_cached_token()
        if cached:
            return cached

    if not SESSION_FILE.exists():
        raise RuntimeError(
            f"Missing {SESSION_FILE}. You need a valid Playwright session to extract the Charts API token."
        )

    token_holder: list[str] = []

    def _on_request(req):
        if "charts-spotify-com-service.spotify.com" not in req.url:
            return
        auth = req.headers.get("authorization") or req.headers.get("Authorization")
        if not auth:
            return
        if auth.lower().startswith("bearer ") and not token_holder:
            token_holder.append(auth.split(" ", 1)[1].strip())

    # One browser launch should be enough; keep timeouts conservative.
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                storage_state=str(SESSION_FILE),
                user_agent=_UA,
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()
            page.on("request", _on_request)
            page.goto(
                f"https://charts.spotify.com/charts/view/{CHART_ID}/latest",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            deadline = time.time() + 20
            while not token_holder and time.time() < deadline:
                page.wait_for_timeout(300)
    except Exception as e:
        raise RuntimeError(f"Failed to extract bearer token via Playwright: {e}") from e

    if not token_holder:
        raise RuntimeError("Bearer token not found. Your spotify_session.json may be expired.")

    _save_cached_token(token_holder[0])
    return token_holder[0]


def _parse_api_entries(data: dict) -> list[dict]:
    rows: list[dict] = []
    for entry in (data.get("entries") or []):
        ced = entry.get("chartEntryData") or {}
        meta = entry.get("trackMetadata") or {}

        rank = _clean_int(ced.get("currentRank"))
        if rank is None:
            continue

        track = (meta.get("trackName") or "").strip()
        if not track:
            continue

        artists = meta.get("artists") or []
        artist_str = ", ".join(a.get("name", "") for a in artists if a.get("name"))

        streams = _clean_int((ced.get("rankingMetric") or {}).get("value"))

        rows.append(
            {
                "rank": rank,
                "track_name": track,
                "artist_names": artist_str.strip(),
                "streams": streams,
                "previous_rank": _clean_int(ced.get("previousRank")),
                "peak_rank": _clean_int(ced.get("peakRank")),
            }
        )

    rows.sort(key=lambda r: r["rank"])
    return rows


def _fetch_chart_rows(
    http: requests.Session,
    chart_date: str,
    token: str,
) -> tuple[Optional[list[dict]], int]:
    url = f"{_API_BASE}/{CHART_ID}/{chart_date}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Referer": "https://charts.spotify.com/",
        "User-Agent": _UA,
    }

    resp = http.get(url, headers=headers, timeout=30)
    if resp.status_code == 200:
        rows = _parse_api_entries(resp.json())
        return rows, resp.status_code
    if resp.status_code in (401, 403):
        raise _AuthError(f"Auth failed ({resp.status_code})")
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        retry_after_seconds = None
        if retry_after and str(retry_after).strip().isdigit():
            retry_after_seconds = int(str(retry_after).strip())
        raise _RateLimitError(retry_after_seconds=retry_after_seconds)
    if resp.status_code in (404, 400):
        return None, resp.status_code
    if 500 <= resp.status_code <= 599:
        raise _UpstreamError(resp.status_code, message=f"Unexpected status {resp.status_code} for {chart_date}: {resp.text[:200]}")
    raise RuntimeError(f"Unexpected status {resp.status_code} for {chart_date}: {resp.text[:200]}")


@dataclass
class SongState:
    total_days: int
    streak: int
    last_seen: str  # YYYY-MM-DD


def _load_resume_state(output_csv: Path) -> tuple[dict[str, SongState], Optional[str]]:
    """Rebuild per-song state from an existing archive CSV."""
    if not output_csv.exists():
        return {}, None

    song_state: dict[str, SongState] = {}
    last_date: Optional[str] = None

    with output_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = (row.get("date") or "").strip()
            song = (row.get("song_name") or "").strip()
            if not d or not song:
                continue

            try:
                total_days = int(float(row.get("total_days") or 0))
            except Exception:
                total_days = 0

            try:
                streak = int(float(row.get("streak") or 0))
            except Exception:
                streak = 0

            song_state[song] = SongState(total_days=total_days, streak=streak, last_seen=d)
            last_date = d

    return song_state, last_date


def _write_header_if_needed(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()


def _append_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        for r in rows:
            w.writerow(r)


def _ensure_failed_dates_header(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FAILED_COLUMNS)
        w.writeheader()


def _log_failed_date(path: Path, chart_date: str, error_type: str, http_status: Optional[int], message: str) -> None:
    _ensure_failed_dates_header(path)
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FAILED_COLUMNS)
        w.writerow(
            {
                "date": chart_date,
                "error_type": error_type,
                "http_status": "" if http_status is None else http_status,
                "message": (message or "")[:500],
                "saved_at": int(time.time()),
            }
        )


def _save_state(last_completed_date: str, song_state: dict[str, SongState], output_csv: Path) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_completed_date": last_completed_date,
        "output_csv": str(output_csv),
        "saved_at": time.time(),
        "songs": {
            k: {"total_days": v.total_days, "streak": v.streak, "last_seen": v.last_seen}
            for k, v in song_state.items()
        },
    }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _load_state_file(output_csv: Path) -> tuple[dict[str, SongState], Optional[str]]:
    if not STATE_FILE.exists():
        return {}, None
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}, None

    if str(output_csv) != str(payload.get("output_csv") or ""):
        return {}, None

    last_done = (payload.get("last_completed_date") or "").strip() or None
    songs = payload.get("songs")
    if not isinstance(songs, dict):
        return {}, last_done

    song_state: dict[str, SongState] = {}
    for k, v in songs.items():
        if not isinstance(v, dict):
            continue
        try:
            song_state[str(k)] = SongState(
                total_days=int(v.get("total_days") or 0),
                streak=int(v.get("streak") or 0),
                last_seen=str(v.get("last_seen") or "").strip(),
            )
        except Exception:
            continue

    return song_state, last_done


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Spotify Charts US Taylor Swift archive CSV")
    parser.add_argument("--start", default="2017-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument(
        "--end",
        default=None,
        help="End date inclusive (YYYY-MM-DD), default: yesterday (safer than today)",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_CSV), help="Output CSV path")
    parser.add_argument(
        "--failed-dates",
        default=str(DEFAULT_FAILED_DATES_CSV),
        help="CSV file to record failed dates (for later re-run)",
    )
    parser.add_argument(
        "--only-failed",
        action="store_true",
        help="Only process dates listed in --failed-dates (ignores --start/--end)",
    )
    parser.add_argument(
        "--retry-at-end",
        action="store_true",
        help="Do one pass with no per-date retries, then retry failed dates at end and rebuild output CSV from cache",
    )
    parser.add_argument(
        "--until-complete",
        action="store_true",
        help="With --retry-at-end: keep retrying pending dates until the whole date range is complete (runs indefinitely until success or Ctrl+C)",
    )
    parser.add_argument(
        "--retry-cycle-sleep",
        type=float,
        default=120.0,
        help="With --until-complete: sleep this many seconds between retry cycles when no progress is made",
    )
    parser.add_argument(
        "--cache",
        default=str(CACHE_FILE_DEFAULT),
        help="Cache JSON file for per-date TS rows (used by --retry-at-end and resume)",
    )
    parser.add_argument("--resume", action="store_true", default=True, help="Resume from existing CSV")
    parser.add_argument("--no-resume", action="store_false", dest="resume", help="Do not resume")
    parser.add_argument("--force", action="store_true", help="Overwrite output CSV (backs up existing file)")
    parser.add_argument("--verbose", action="store_true", help="Log every checked date")
    parser.add_argument(
        "--log-http",
        action="store_true",
        help="Log HTTP status codes and retry sleeps",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=25,
        help="Heartbeat logging cadence (every N checked dates)",
    )
    parser.add_argument(
        "--min-delay",
        type=float,
        default=0.10,
        help="Minimum sleep between successful HTTP calls (seconds)",
    )
    parser.add_argument(
        "--base-rate-limit-sleep",
        type=float,
        default=15.0,
        help="Base cooldown sleep when receiving HTTP 429 without Retry-After (seconds)",
    )
    parser.add_argument(
        "--max-rate-limit-retries",
        type=int,
        default=12,
        help="Max 429 retries per date before marking it as failure and moving on",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="If a date errors (429/timeout/5xx/etc.), log once and move to next date (no retries)",
    )
    args = parser.parse_args()

    start_date = _parse_ymd(args.start)
    end_date = _parse_ymd(args.end) if args.end else (date.today() - timedelta(days=1))

    requested_start_date = start_date
    requested_end_date = end_date

    if end_date < start_date:
        raise SystemExit("--end must be >= --start")

    output_csv = Path(args.output)
    failed_dates_csv = Path(args.failed_dates)
    cache_path = Path(args.cache)

    if args.only_failed and args.retry_at_end:
        raise SystemExit("--only-failed is not compatible with --retry-at-end")
    if args.until_complete and not args.retry_at_end:
        raise SystemExit("--until-complete requires --retry-at-end")

    if args.force and output_csv.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = output_csv.with_name(output_csv.name + f".bak.{ts}")
        output_csv.replace(backup)
        print(f"Backed up existing output to: {backup}")

    song_state: dict[str, SongState] = {}
    last_done: Optional[str] = None

    if args.resume and not args.retry_at_end:
        # Prefer state file (works even when many days have TS=0 / no-data)
        song_state, last_done = _load_state_file(output_csv)
        # Fallback to parsing output CSV if no usable state exists.
        if not last_done and output_csv.exists() and output_csv.stat().st_size > 0:
            song_state, last_done = _load_resume_state(output_csv)

    cache: dict[str, dict] = {}
    if args.retry_at_end:
        cache = _load_cache(cache_path)
        # In retry-at-end mode, we drive resume by the cache per date (skip done days).
        start_date = requested_start_date
        end_date = requested_end_date

    if last_done and not args.retry_at_end:
        resume_from = _parse_ymd(last_done) + timedelta(days=1)
        if resume_from > start_date:
            start_date = resume_from

    if not args.retry_at_end:
        _write_header_if_needed(output_csv)

    http = requests.Session()
    token = _get_bearer_token(force_refresh=False)

    # Load failed dates if requested.
    failed_dates_to_run: Optional[list[str]] = None
    if args.only_failed:
        if not failed_dates_csv.exists():
            raise SystemExit(f"--only-failed set but failed dates file not found: {failed_dates_csv}")
        try:
            with failed_dates_csv.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                failed_dates_to_run = sorted({(r.get("date") or "").strip() for r in reader if (r.get("date") or "").strip()})
        except Exception as e:
            raise SystemExit(f"Could not read failed dates file: {e}")

        if not failed_dates_to_run:
            print("No failed dates to run.")
            return

        start_date = _parse_ymd(failed_dates_to_run[0])
        end_date = _parse_ymd(failed_dates_to_run[-1])
        total_days = len(failed_dates_to_run)
        print(f"Reprocessing only failed dates: {total_days} dates")

    if failed_dates_to_run is None:
        total_days = (end_date - start_date).days + 1
        print(f"Backfill US charts: {_ymd(start_date)} -> {_ymd(end_date)} ({total_days} jours)")
    if last_done:
        print(f"Resume: last date in CSV = {last_done}")

    processed = 0
    skipped_no_data = 0
    failures = 0
    checked = 0

    # Adaptive throttling: increases when we hit 429 and slowly decays.
    throttle_delay_s = max(0.0, float(args.min_delay))
    consecutive_429 = 0

    d = start_date
    failed_idx = 0
    while d <= end_date:
        ds = _ymd(d)

        if args.retry_at_end:
            entry = cache.get(ds)
            if entry and _is_done(entry):
                checked += 1
                if args.verbose and (checked % max(1, int(args.log_every)) == 0 or ds.endswith("-01")):
                    print(f"{ds}: cached done (checked={checked}/{(end_date-start_date).days+1})")
                d += timedelta(days=1)
                continue

        if failed_dates_to_run is not None:
            # Advance index to current date; skip non-failed dates.
            while failed_idx < len(failed_dates_to_run) and failed_dates_to_run[failed_idx] < ds:
                failed_idx += 1
            if failed_idx >= len(failed_dates_to_run) or failed_dates_to_run[failed_idx] != ds:
                d += timedelta(days=1)
                continue

        t0 = time.time()

        checked += 1

        rows: Optional[list[dict]] = None
        status_code: Optional[int] = None
        had_failure = False
        rate_limit_retries = 0
        attempt = 0

        # In retry-at-end mode, first pass is always single-attempt per date.
        max_attempts = 1 if (args.no_retry or args.retry_at_end) else 3
        max_rate_limit_retries = 0 if (args.no_retry or args.retry_at_end) else int(args.max_rate_limit_retries)

        while True:
            attempt += 1
            if attempt > max_attempts and rate_limit_retries == 0:
                # Default retry budget for non-429 errors.
                break
            if rate_limit_retries > 0 and rate_limit_retries > max_rate_limit_retries:
                failures += 1
                had_failure = True
                print(f"{ds}: too many 429 retries ({rate_limit_retries}), skipping date")
                if args.retry_at_end:
                    _cache_put_err(cache, ds, "rate_limit", 429, f"too many 429 retries ({rate_limit_retries})")
                    _log_failed_date(
                        failed_dates_csv,
                        ds,
                        "rate_limit",
                        429,
                        f"too many 429 retries ({rate_limit_retries})",
                    )
                break

            # Global throttle before each attempt (helps avoid 429 bursts).
            if throttle_delay_s > 0:
                if args.log_http and throttle_delay_s >= 1.0:
                    print(f"{ds}: throttle sleep {throttle_delay_s:.1f}s")
                time.sleep(throttle_delay_s)

            try:
                if args.log_http and attempt > 1:
                    print(f"{ds}: retry attempt={attempt}")

                fetch_t0 = time.time()
                rows, status_code = _fetch_chart_rows(http, ds, token)
                fetch_dt = time.time() - fetch_t0
                if args.log_http:
                    sc = status_code if status_code is not None else "?"
                    print(f"{ds}: HTTP {sc} ({fetch_dt:.2f}s)")

                # Success: decay throttle and reset 429 streak.
                consecutive_429 = 0
                throttle_delay_s = max(float(args.min_delay), throttle_delay_s * 0.95)
                break
            except _AuthError:
                if args.log_http:
                    print(f"{ds}: auth failed -> refreshing token")
                token = _get_bearer_token(force_refresh=True)
                if args.no_retry:
                    failures += 1
                    had_failure = True
                    print(f"{ds}: AUTH ERROR, skipping date (--no-retry)")
                    _log_failed_date(failed_dates_csv, ds, "auth", status_code, "auth error")
                    break
                if args.retry_at_end:
                    failures += 1
                    had_failure = True
                    print(f"{ds}: AUTH ERROR, deferring to end (--retry-at-end)")
                    _cache_put_err(cache, ds, "auth", status_code, "auth error")
                    _log_failed_date(failed_dates_csv, ds, "auth", status_code, "auth error")
                    break
                continue
            except _RateLimitError as e:
                if args.no_retry:
                    failures += 1
                    had_failure = True
                    base = float(args.base_rate_limit_sleep)
                    if e.retry_after_seconds is not None:
                        sleep_s = float(e.retry_after_seconds)
                    else:
                        sleep_s = base
                    sleep_s = min(300.0, max(base, sleep_s))
                    print(f"{ds}: 429 rate-limit -> sleep {sleep_s:.1f}s then skip date (--no-retry)")
                    _log_failed_date(failed_dates_csv, ds, "rate_limit", 429, f"rate limited; slept {sleep_s:.1f}s")
                    time.sleep(sleep_s)
                    break
                if args.retry_at_end:
                    # Cooldown, but skip this date (no retry now).
                    base = float(args.base_rate_limit_sleep)
                    sleep_s = float(e.retry_after_seconds) if e.retry_after_seconds is not None else base
                    sleep_s = min(300.0, max(base, sleep_s))
                    print(f"{ds}: 429 rate-limit -> sleep {sleep_s:.1f}s then defer to end (--retry-at-end)")
                    _log_failed_date(failed_dates_csv, ds, "rate_limit", 429, f"deferred; slept {sleep_s:.1f}s")
                    _cache_put_err(cache, ds, "rate_limit", 429, "deferred")
                    time.sleep(sleep_s)
                    had_failure = True
                    failures += 1
                    break
                rate_limit_retries += 1
                consecutive_429 += 1
                base = float(args.base_rate_limit_sleep)
                # If Spotify provides Retry-After, trust it; otherwise exponential backoff.
                if e.retry_after_seconds is not None:
                    sleep_s = float(e.retry_after_seconds)
                else:
                    sleep_s = base * (1.5 ** min(6, consecutive_429 - 1))
                sleep_s = min(300.0, max(base, sleep_s))

                # Increase global throttle after rate limits.
                throttle_delay_s = min(10.0, max(throttle_delay_s, base / 10.0))

                if args.log_http:
                    print(
                        f"{ds}: 429 rate-limit -> cooldown {sleep_s:.1f}s "
                        f"(429_retries={rate_limit_retries}, throttle={throttle_delay_s:.2f}s)"
                    )
                time.sleep(sleep_s)
                continue
            except _UpstreamError as e:
                if args.no_retry:
                    failures += 1
                    had_failure = True
                    print(f"{ds}: upstream {e.status_code}, skipping date (--no-retry)")
                    _log_failed_date(failed_dates_csv, ds, "upstream", e.status_code, str(e))
                    break
                if args.retry_at_end:
                    failures += 1
                    had_failure = True
                    print(f"{ds}: upstream {e.status_code}, deferring to end (--retry-at-end)")
                    _cache_put_err(cache, ds, "upstream", e.status_code, str(e))
                    _log_failed_date(failed_dates_csv, ds, "upstream", e.status_code, str(e))
                    break
                sleep_s = min(30.0, float(2.0 * attempt))
                if args.log_http:
                    print(f"{ds}: upstream {e.status_code} -> sleep {sleep_s:.1f}s")
                time.sleep(sleep_s)
                continue
            except (requests.Timeout, requests.ConnectionError) as e:
                if args.retry_at_end:
                    failures += 1
                    had_failure = True
                    print(f"{ds}: NETWORK ERROR, deferring to end (--retry-at-end): {e}")
                    _cache_put_err(cache, ds, "network", status_code, str(e))
                    _log_failed_date(failed_dates_csv, ds, "network", status_code, str(e))
                    break
                if args.no_retry or attempt >= max_attempts:
                    failures += 1
                    print(f"{ds}: NETWORK ERROR after retries: {e}")
                    had_failure = True
                    _log_failed_date(failed_dates_csv, ds, "network", status_code, str(e))
                    break
                time.sleep(1.0 * attempt)
            except Exception as e:
                if args.retry_at_end:
                    failures += 1
                    had_failure = True
                    print(f"{ds}: ERROR, deferring to end (--retry-at-end): {e}")
                    _cache_put_err(cache, ds, "error", status_code, str(e))
                    _log_failed_date(failed_dates_csv, ds, "error", status_code, str(e))
                    break
                if args.no_retry or attempt >= max_attempts:
                    failures += 1
                    print(f"{ds}: FAILED after retries: {e}")
                    had_failure = True
                    _log_failed_date(failed_dates_csv, ds, "error", status_code, str(e))
                    break
                time.sleep(1.0 * attempt)

        if had_failure:
            # Save progress even on failures so resume can keep moving forward.
            if args.retry_at_end:
                if checked % max(1, int(args.log_every)) == 0 or ds.endswith("-01"):
                    _save_cache(cache_path, cache)
            else:
                if checked % 25 == 0 or ds.endswith("-01"):
                    _save_state(ds, song_state, output_csv)
            time.sleep(0.05)
            d += timedelta(days=1)
            continue

        if rows is None and rate_limit_retries > 0:
            # We exhausted retries due to 429/cooldowns; do not mislabel as "no data".
            failures += 1
            if args.log_http:
                print(f"{ds}: giving up after 429 retries ({rate_limit_retries})")
            if checked % max(1, int(args.log_every)) == 0 or ds.endswith("-01"):
                _save_state(ds, song_state, output_csv)
            d += timedelta(days=1)
            continue

        if not rows:
            skipped_no_data += 1
            should_log = (
                args.verbose
                or ds.endswith("-01")
                or checked % max(1, int(args.log_every)) == 0
                or skipped_no_data <= 3
                or skipped_no_data % 200 == 0
            )
            if should_log:
                sc = status_code if status_code is not None else "?"
                print(f"{ds}: no data (HTTP {sc}) (checked={checked}/{total_days}, failures={failures})")
            if args.retry_at_end:
                _cache_put_ok(cache, ds, int(status_code or 0), [])
                if checked % max(1, int(args.log_every)) == 0 or ds.endswith("-01"):
                    _save_cache(cache_path, cache)
            else:
                if checked % max(1, int(args.log_every)) == 0 or ds.endswith("-01"):
                    _save_state(ds, song_state, output_csv)
            time.sleep(0.05)
            d += timedelta(days=1)
            continue

        ts_rows = [r for r in rows if TS_NAME.lower() in (r.get("artist_names") or "").lower()]
        if args.retry_at_end:
            # Cache minimal per-day TS rows for later rebuild.
            cached_rows = []
            for r in ts_rows:
                cached_rows.append(
                    {
                        "song_name": (r.get("track_name") or "").strip(),
                        "rank": r.get("rank"),
                        "streams": r.get("streams"),
                        "previous_rank": r.get("previous_rank"),
                        "peak_rank": r.get("peak_rank"),
                    }
                )
            _cache_put_ok(cache, ds, int(status_code or 200), cached_rows)
            if checked % max(1, int(args.log_every)) == 0 or ds.endswith("-01"):
                _save_cache(cache_path, cache)
            processed += 1
            dt = time.time() - t0
            if args.verbose or cached_rows or ds.endswith("-01") or checked % max(1, int(args.log_every)) == 0:
                print(
                    f"{ds}: TS={len(cached_rows)} ({dt:.2f}s) "
                    f"(checked={checked}/{total_days}, no-data={skipped_no_data}, failures={failures})"
                )
            time.sleep(max(0.0, float(args.min_delay)))
            d += timedelta(days=1)
            continue

        out_rows: list[dict] = []

        for r in ts_rows:
            song = r.get("track_name") or ""
            rank = r.get("rank")
            streams = r.get("streams")
            previous_rank = r.get("previous_rank")
            peak_rank = r.get("peak_rank")

            prev_state = song_state.get(song)
            if prev_state and prev_state.last_seen == _ymd(d - timedelta(days=1)):
                streak = prev_state.streak + 1
            else:
                streak = 1

            total_days_for_song = (prev_state.total_days + 1) if prev_state else 1

            song_state[song] = SongState(total_days=total_days_for_song, streak=streak, last_seen=ds)

            movement = fmt_delta(
                rank=int(rank) if rank is not None else None,
                previous_rank=int(previous_rank) if previous_rank is not None else None,
                peak_rank=int(peak_rank) if peak_rank is not None else None,
                total_days=int(total_days_for_song),
            )

            out_rows.append(
                {
                    "date": ds,
                    "song_name": song,
                    "rank": rank or "",
                    "streams": streams or "",
                    "previous_rank": previous_rank or "",
                    "peak_rank": peak_rank or "",
                    "total_days": total_days_for_song,
                    "streak": streak,
                    "movement": movement,
                }
            )

        if out_rows:
            _append_rows(output_csv, out_rows)

        processed += 1
        if checked % max(1, int(args.log_every)) == 0 or ds.endswith("-01"):
            _save_state(ds, song_state, output_csv)

        dt = time.time() - t0
        if args.verbose or out_rows or ds.endswith("-01") or checked % max(1, int(args.log_every)) == 0:
            print(
                f"{ds}: TS={len(out_rows)} ({dt:.2f}s) "
                f"(checked={checked}/{total_days}, no-data={skipped_no_data}, failures={failures})"
            )

        # Minimum sleep to reduce the risk of rate-limits.
        time.sleep(max(0.0, float(args.min_delay)))
        d += timedelta(days=1)

    if args.retry_at_end:
        cycle = 0
        total_recovered = 0
        while True:
            pending = _pending_dates(cache, requested_start_date, requested_end_date)
            if not pending:
                break

            cycle += 1
            print(f"\nRetry cycle {cycle}: pending={len(pending)}")

            recovered_this_cycle = 0
            for ds in pending:
                # Retry with normal retry budget (even if --no-retry was set)
                try:
                    token = _get_bearer_token(force_refresh=False)
                except Exception:
                    pass

                status_code = None
                rows = None
                for attempt in range(1, 4):
                    try:
                        fetch_t0 = time.time()
                        rows, status_code = _fetch_chart_rows(http, ds, token)
                        fetch_dt = time.time() - fetch_t0
                        if args.log_http:
                            sc = status_code if status_code is not None else "?"
                            print(f"{ds} (retry): HTTP {sc} ({fetch_dt:.2f}s)")
                        break
                    except _AuthError:
                        token = _get_bearer_token(force_refresh=True)
                    except _RateLimitError as e:
                        base = float(args.base_rate_limit_sleep)
                        sleep_s = float(e.retry_after_seconds) if e.retry_after_seconds is not None else base
                        sleep_s = min(300.0, max(base, sleep_s))
                        if args.log_http:
                            print(f"{ds} (retry): 429 -> sleep {sleep_s:.1f}s")
                        time.sleep(sleep_s)
                    except _UpstreamError:
                        time.sleep(min(30.0, float(2.0 * attempt)))
                    except Exception:
                        time.sleep(min(10.0, float(attempt)))

                if status_code in (400, 404):
                    _cache_put_ok(cache, ds, int(status_code), [])
                    continue

                if status_code == 200 and rows is not None:
                    ts_rows = [r for r in rows if TS_NAME.lower() in (r.get("artist_names") or "").lower()]
                    cached_rows = []
                    for r in ts_rows:
                        cached_rows.append(
                            {
                                "song_name": (r.get("track_name") or "").strip(),
                                "rank": r.get("rank"),
                                "streams": r.get("streams"),
                                "previous_rank": r.get("previous_rank"),
                                "peak_rank": r.get("peak_rank"),
                            }
                        )
                    _cache_put_ok(cache, ds, 200, cached_rows)
                    recovered_this_cycle += 1
                    total_recovered += 1
                else:
                    _cache_put_err(cache, ds, (cache.get(ds) or {}).get("error_type") or "error", status_code, "retry failed")

                if (recovered_this_cycle + 1) % 25 == 0:
                    _save_cache(cache_path, cache)

            _save_cache(cache_path, cache)
            print(f"Retry cycle {cycle}: recovered={recovered_this_cycle}, still_pending={len(_pending_dates(cache, requested_start_date, requested_end_date))}")

            if recovered_this_cycle == 0:
                if not args.until_complete:
                    break
                sleep_s = max(1.0, float(args.retry_cycle_sleep))
                print(f"No progress this cycle -> sleeping {sleep_s:.1f}s before next cycle (Ctrl+C to stop)")
                time.sleep(sleep_s)

        pending_final = _pending_dates(cache, requested_start_date, requested_end_date)
        if pending_final:
            print(f"\nRetry-at-end finished with pending={len(pending_final)} (use --until-complete to loop forever)")
        else:
            print(f"\nRetry-at-end complete: all dates done (total recovered during retries={total_recovered})")

        _build_archive_csv_from_cache(
            cache=cache,
            start_date=requested_start_date,
            end_date=requested_end_date,
            output_csv=output_csv,
        )

        print("\nDone")
        print(f"  checked dates:   {checked}")
        print(f"  no-data dates:   {skipped_no_data}")
        print(f"  failures:        {failures}")
        print(f"  output:          {output_csv}")
        print(f"  cache:           {cache_path}")
        return

    if processed > 0:
        _save_state(_ymd(end_date), song_state, output_csv)

    print("\nDone")
    print(f"  processed dates: {processed}")
    print(f"  no-data dates:   {skipped_no_data}")
    print(f"  failures:        {failures}")
    print(f"  output:          {output_csv}")


if __name__ == "__main__":
    main()
