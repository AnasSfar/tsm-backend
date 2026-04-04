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


class _AuthError(RuntimeError):
    pass


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
) -> Optional[list[dict]]:
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
        return rows
    if resp.status_code in (401, 403):
        raise _AuthError(f"Auth failed ({resp.status_code})")
    if resp.status_code in (404, 400):
        return None
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Spotify Charts US Taylor Swift archive CSV")
    parser.add_argument("--start", default="2017-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date inclusive (YYYY-MM-DD), default: today")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_CSV), help="Output CSV path")
    parser.add_argument("--resume", action="store_true", default=True, help="Resume from existing CSV")
    parser.add_argument("--no-resume", action="store_false", dest="resume", help="Do not resume")
    parser.add_argument("--force", action="store_true", help="Overwrite output CSV")
    args = parser.parse_args()

    start_date = _parse_ymd(args.start)
    end_date = _parse_ymd(args.end) if args.end else date.today()

    if end_date < start_date:
        raise SystemExit("--end must be >= --start")

    output_csv = Path(args.output)

    if args.force and output_csv.exists():
        output_csv.unlink()

    song_state: dict[str, SongState] = {}
    last_done: Optional[str] = None

    if args.resume and output_csv.exists() and output_csv.stat().st_size > 0:
        song_state, last_done = _load_resume_state(output_csv)

    if last_done:
        resume_from = _parse_ymd(last_done) + timedelta(days=1)
        if resume_from > start_date:
            start_date = resume_from

    _write_header_if_needed(output_csv)

    http = requests.Session()
    token = _get_bearer_token(force_refresh=False)

    total_days = (end_date - start_date).days + 1
    print(f"Backfill US charts: {args.start} -> {_ymd(end_date)} ({total_days} jours)")
    if last_done:
        print(f"Resume: last date in CSV = {last_done}")

    processed = 0
    skipped_no_data = 0
    failures = 0

    d = start_date
    while d <= end_date:
        ds = _ymd(d)
        t0 = time.time()

        rows: Optional[list[dict]] = None
        for attempt in range(1, 4):
            try:
                rows = _fetch_chart_rows(http, ds, token)
                break
            except _AuthError:
                token = _get_bearer_token(force_refresh=True)
                continue
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt >= 3:
                    raise RuntimeError(f"Network error for {ds}: {e}") from e
                time.sleep(1.0 * attempt)
            except Exception as e:
                if attempt >= 3:
                    raise
                time.sleep(1.0 * attempt)

        if not rows:
            skipped_no_data += 1
            if skipped_no_data <= 3 or skipped_no_data % 200 == 0:
                print(f"{ds}: no data")
            d += timedelta(days=1)
            continue

        ts_rows = [r for r in rows if TS_NAME.lower() in (r.get("artist_names") or "").lower()]
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
        if processed % 25 == 0 or ds.endswith("-01"):
            _save_state(ds, song_state, output_csv)

        dt = time.time() - t0
        if out_rows:
            print(f"{ds}: TS={len(out_rows)} ({dt:.2f}s)")
        else:
            print(f"{ds}: TS=0 ({dt:.2f}s)")

        # Tiny sleep to reduce the risk of rate-limits.
        time.sleep(0.05)
        d += timedelta(days=1)

    if processed > 0:
        _save_state(_ymd(end_date), song_state, output_csv)

    print("\nDone")
    print(f"  processed dates: {processed}")
    print(f"  no-data dates:   {skipped_no_data}")
    print(f"  failures:        {failures}")
    print(f"  output:          {output_csv}")


if __name__ == "__main__":
    main()
