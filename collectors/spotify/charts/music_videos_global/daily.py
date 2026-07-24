#!/usr/bin/env python3
"""
Fetch Spotify's global music video chart.

Default:
    python daily.py

Useful options:
    python daily.py --date latest
    python daily.py --date 2026-07-22
    python daily.py --no-wait
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[4]
CHARTS_ROOT = ROOT / "collectors" / "spotify" / "charts"
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import WEB_EXPORT_DATA_DIR, spotify_chart_dir, spotify_chart_snapshot_candidates

API_BASE = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
TOKEN_ACQUIRE_URL = "https://charts.spotify.com/charts/view/regional-global-daily/latest"
SESSION_FILE = CHARTS_ROOT / "global" / "tools" / "json" / "spotify_session.json"
BEARER_CACHE = CHARTS_ROOT / "global" / "tools" / "json" / "bearer_cache.json"
OUTPUT_PATH = WEB_EXPORT_DATA_DIR / "charts_music_videos_global.json"

DEFAULT_WAIT_SECONDS = 10
TOKEN_TTL = 50 * 60
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

# Spotify has only just exposed this chart. Keep candidates explicit and logged
# so a slug mismatch blocks collection rather than producing fake data.
DEFAULT_CHART_ID_CANDIDATES = [
    "music-video-global-daily",
    "music-videos-global-daily",
    "video-global-daily",
    "videos-global-daily",
]

FIELDNAMES = [
    "rank",
    "video_name",
    "track_name",
    "artist_name",
    "album_name",
    "spotify_track_id",
    "spotify_video_id",
    "spotify_url",
    "streams",
    "previous_rank",
    "peak_rank",
    "streak",
    "image_url",
    "is_taylor",
]


def _chart_id_candidates() -> list[str]:
    raw = os.getenv("SPOTIFY_MUSIC_VIDEO_CHART_IDS", "").strip()
    if not raw:
        return DEFAULT_CHART_ID_CANDIDATES
    return [part.strip() for part in raw.split(",") if part.strip()]


def _load_cached_token() -> str | None:
    try:
        data = json.loads(BEARER_CACHE.read_text(encoding="utf-8-sig"))
        if time.time() - float(data.get("ts", 0)) < TOKEN_TTL:
            token = str(data.get("token") or "").strip()
            return token or None
    except Exception:
        return None
    return None


def _save_cached_token(token: str) -> None:
    BEARER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    BEARER_CACHE.write_text(json.dumps({"token": token, "ts": time.time()}), encoding="utf-8")


def _get_bearer_token(*, refresh: bool = False) -> str:
    if not refresh:
        cached = _load_cached_token()
        if cached:
            return cached

    if not SESSION_FILE.exists():
        raise RuntimeError(f"Spotify session file not found: {SESSION_FILE}")

    from playwright.sync_api import sync_playwright

    token_holder: list[str] = []
    api_host = API_BASE.split("//", 1)[1].split("/", 1)[0]

    def _on_request(req: Any) -> None:
        if api_host in req.url and not token_holder:
            auth = req.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token_holder.append(auth[7:])

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            context = browser.new_context(
                storage_state=str(SESSION_FILE),
                user_agent=UA,
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.on("request", _on_request)
            page.goto(TOKEN_ACQUIRE_URL, wait_until="domcontentloaded", timeout=30_000)
            deadline = time.time() + 20
            while not token_holder and time.time() < deadline:
                page.wait_for_timeout(300)
        finally:
            browser.close()

    if not token_holder:
        raise RuntimeError("Bearer token not found; refresh spotify_session.json")
    token = token_holder[0]
    _save_cached_token(token)
    return token


def _clean_int(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"-?\d[\d,\s.]*", str(value))
    if not match:
        return None
    return int(re.sub(r"[^\d-]", "", match.group(0)))


def _image_url(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    if text.startswith("spotify:image:"):
        return text.replace("spotify:image:", "https://i.scdn.co/image/", 1)
    return text


def _spotify_id(value: Any, kind: str) -> str | None:
    if not value:
        return None
    text = str(value)
    match = re.search(rf"{re.escape(kind)}[:/]([A-Za-z0-9]+)", text)
    return match.group(1) if match else None


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _find_first_date(value: Any) -> str | None:
    if isinstance(value, str):
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", value)
        return match.group(0) if match else None
    if isinstance(value, dict):
        for key in ("date", "chartDate", "displayDate", "latestDate"):
            found = _find_first_date(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_first_date(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_first_date(item)
            if found:
                return found
    return None


def _iter_entries(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    entries = data.get("entries")
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    chart = data.get("chart")
    if isinstance(chart, dict) and isinstance(chart.get("entries"), list):
        return [entry for entry in chart["entries"] if isinstance(entry, dict)]
    return []


def _parse_video_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in _iter_entries(data):
        chart = entry.get("chartEntryData") or entry.get("entryData") or {}
        meta = (
            entry.get("videoMetadata")
            or entry.get("musicVideoMetadata")
            or entry.get("trackMetadata")
            or entry.get("metadata")
            or {}
        )
        track_meta = entry.get("trackMetadata") or meta
        rank = _clean_int(chart.get("currentRank") or entry.get("currentRank") or entry.get("rank"))
        video_name = _first_text(
            meta.get("videoName"),
            meta.get("name"),
            meta.get("title"),
            track_meta.get("trackName"),
            entry.get("name"),
        )
        artist_name = _first_text(
            meta.get("artistName"),
            track_meta.get("artistName"),
            entry.get("artistName"),
        )
        if rank is None or not video_name:
            continue

        metric = chart.get("rankingMetric") or entry.get("rankingMetric") or {}
        track_uri = _first_text(
            track_meta.get("trackUri"),
            meta.get("trackUri"),
            meta.get("uri"),
            entry.get("trackUri"),
        )
        video_uri = _first_text(
            meta.get("videoUri"),
            meta.get("musicVideoUri"),
            entry.get("videoUri"),
            entry.get("uri"),
        )
        track_id = _spotify_id(track_uri, "track") or _spotify_id(meta.get("externalUrl"), "track")
        video_id = _spotify_id(video_uri, "video") or _spotify_id(video_uri, "episode")
        spotify_url = _first_text(meta.get("externalUrl"), entry.get("externalUrl"))
        if not spotify_url and track_id:
            spotify_url = f"https://open.spotify.com/track/{track_id}"

        rows.append({
            "rank": rank,
            "video_name": video_name,
            "track_name": _first_text(track_meta.get("trackName"), meta.get("trackName"), video_name),
            "artist_name": artist_name,
            "album_name": _first_text(track_meta.get("albumName"), meta.get("albumName")),
            "spotify_track_id": track_id,
            "spotify_video_id": video_id,
            "spotify_url": spotify_url or None,
            "streams": _clean_int(metric.get("value") or entry.get("streams")),
            "previous_rank": _clean_int(chart.get("previousRank") or entry.get("previousRank")),
            "peak_rank": _clean_int(chart.get("peakRank") or entry.get("peakRank")),
            "streak": _clean_int(
                chart.get("consecutiveAppearancesOnChart")
                or chart.get("appearancesOnChart")
                or entry.get("streak")
            ),
            "image_url": _image_url(
                meta.get("displayImageUri")
                or meta.get("imageUri")
                or meta.get("imageUrl")
                or track_meta.get("displayImageUri")
                or entry.get("imageUrl")
            ),
            "is_taylor": "taylor swift" in artist_name.lower(),
        })

    rows.sort(key=lambda row: row["rank"])
    return rows


def _request_chart(chart_id: str, route_value: str, token: str) -> requests.Response:
    return requests.get(
        f"{API_BASE}/{chart_id}/{route_value}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Referer": "https://charts.spotify.com/",
            "User-Agent": UA,
        },
        timeout=30,
    )


def _fetch_chart(route_value: str, token: str) -> tuple[list[dict[str, Any]], str | None, str, str | None]:
    statuses: list[str] = []
    for chart_id in _chart_id_candidates():
        try:
            resp = _request_chart(chart_id, route_value, token)
        except requests.RequestException as exc:
            statuses.append(f"{chart_id}=request error: {exc}")
            continue

        if resp.status_code in (401, 403):
            token = _get_bearer_token(refresh=True)
            try:
                resp = _request_chart(chart_id, route_value, token)
            except requests.RequestException as exc:
                statuses.append(f"{chart_id}=request error after refresh: {exc}")
                continue

        if resp.status_code != 200:
            statuses.append(f"{chart_id}=HTTP {resp.status_code}")
            continue

        try:
            data = resp.json()
        except ValueError as exc:
            statuses.append(f"{chart_id}=invalid JSON: {exc}")
            continue
        rows = _parse_video_entries(data)
        if rows:
            return rows, _find_first_date(data), f"HTTP 200 ({chart_id})", chart_id
        statuses.append(f"{chart_id}=HTTP 200, 0 parseable rows")

    return [], None, "; ".join(statuses) or "no chart ids configured", None


def _history_json_path(chart_date: str) -> Path:
    return spotify_chart_dir("music_videos_global", chart_date) / "music_videos_global_daily.json"


def _history_csv_path(chart_date: str) -> Path:
    return spotify_chart_dir("music_videos_global", chart_date) / "music_videos_global_daily.csv"


def _updated_lock(chart_date: str) -> Path:
    return spotify_chart_dir("music_videos_global", chart_date) / "updated.lock"


def _pending_path(chart_date: str) -> Path:
    return spotify_chart_dir("music_videos_global", chart_date) / "pending.json"


def _write_pending(chart_date: str, *, route_value: str, reason: str) -> Path:
    path = _pending_path(chart_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": chart_date,
        "route_value": route_value,
        "status": "pending",
        "reason": reason,
        "chart_id_candidates": _chart_id_candidates(),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _load_previous_rows(chart_date: date) -> list[dict[str, Any]] | None:
    for path in spotify_chart_snapshot_candidates(
        "music_videos_global",
        chart_date.isoformat(),
        "music_videos_global_daily.json",
    ):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        videos = data.get("videos")
        if isinstance(videos, list):
            return [row for row in videos if isinstance(row, dict)]
    return None


def _video_key(row: dict[str, Any]) -> str:
    video_id = str(row.get("spotify_video_id") or "").strip().lower()
    track_id = str(row.get("spotify_track_id") or "").strip().lower()
    if video_id:
        return f"video:{video_id}"
    if track_id:
        return f"track:{track_id}"
    return f"name:{str(row.get('video_name') or '').strip().lower()}"


def _add_days_at_pos(rows: list[dict[str, Any]], chart_date: str) -> None:
    try:
        cursor = datetime.strptime(chart_date, "%Y-%m-%d").date()
    except ValueError:
        for row in rows:
            row["days_at_pos"] = 1
        return

    counters: dict[str, int] = {_video_key(row): 1 for row in rows}
    active: dict[str, int] = {
        _video_key(row): int(row["rank"])
        for row in rows
        if _video_key(row) != "name:" and row.get("rank") is not None
    }
    cursor -= timedelta(days=1)
    while active:
        previous_rows = _load_previous_rows(cursor)
        if not previous_rows:
            break
        previous_by_key = {_video_key(row): row for row in previous_rows}
        still_active: dict[str, int] = {}
        for key, rank in active.items():
            previous = previous_by_key.get(key)
            if previous is None:
                continue
            try:
                previous_rank = int(previous.get("rank"))
            except (TypeError, ValueError):
                continue
            if previous_rank == rank:
                counters[key] += 1
                still_active[key] = rank
        active = still_active
        cursor -= timedelta(days=1)

    for row in rows:
        row["days_at_pos"] = counters.get(_video_key(row), 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Spotify Music Video Charts Global.")
    parser.add_argument("date_arg", nargs="?", help="YYYY-MM-DD or latest.")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD or latest (default: latest).")
    parser.add_argument("--no-wait", action="store_true", help="Do not retry if the chart is unavailable.")
    parser.add_argument("--retry-seconds", type=int, default=DEFAULT_WAIT_SECONDS)
    parser.add_argument("--no-csv", action="store_true", help="Do not write the CSV snapshot.")
    parser.add_argument("--no-post", action="store_true", help="Accepted for run_all compatibility; no video post is wired yet.")
    parser.add_argument("--force", action="store_true", help="Accepted for run_all compatibility.")
    parser.add_argument(
        "--allow-unavailable",
        action="store_true",
        help="Write pending.json and exit 0 if Spotify does not expose this chart yet.",
    )
    args = parser.parse_args()

    route_value = (args.date or args.date_arg or "latest").strip() or "latest"
    if route_value != "latest":
        try:
            datetime.strptime(route_value, "%Y-%m-%d")
        except ValueError:
            print(f"[ERROR] Invalid --date value: {route_value!r}")
            return 1

    expected_date = str(date.today() - timedelta(days=1)) if route_value == "latest" else None
    token = _get_bearer_token()
    attempt = 1
    chart_id = None
    while True:
        rows, detected_date, status, chart_id = _fetch_chart(route_value, token)
        if rows and (expected_date is None or detected_date == expected_date):
            break
        if args.no_wait:
            if not rows:
                reason = f"Music video chart {route_value} unavailable ({status})"
                if args.allow_unavailable:
                    pending_date = expected_date or (route_value if route_value != "latest" else date.today().isoformat())
                    pending = _write_pending(pending_date, route_value=route_value, reason=reason)
                    print(f"[PENDING] {reason}")
                    print(f"[PENDING] Written -> {pending}")
                    return 0
                print(f"[ERROR] {reason}")
            else:
                reason = f"Music video chart date mismatch: got {detected_date}, expected {expected_date}"
                if args.allow_unavailable:
                    pending_date = expected_date or detected_date or date.today().isoformat()
                    pending = _write_pending(pending_date, route_value=route_value, reason=reason)
                    print(f"[PENDING] {reason}")
                    print(f"[PENDING] Written -> {pending}")
                    return 0
                print(f"[ERROR] {reason}")
            return 1
        if rows:
            print(
                f"[WAIT] Music video chart not yet updated (got {detected_date}, expected {expected_date}, "
                f"attempt #{attempt}) - retry in {args.retry_seconds}s"
            )
        else:
            print(
                f"[WAIT] Music video chart {route_value} unavailable "
                f"({status}, attempt #{attempt}) - retry in {args.retry_seconds}s"
            )
        time.sleep(args.retry_seconds)
        attempt += 1

    chart_date = detected_date or (route_value if route_value != "latest" else expected_date)
    if not chart_date:
        print("[ERROR] Could not resolve chart date")
        return 1

    _add_days_at_pos(rows, chart_date)
    taylor_rows = [row for row in rows if row.get("is_taylor")]
    output = {
        "date": chart_date,
        "chart_id": chart_id,
        "source_url": f"https://charts.spotify.com/charts/view/{chart_id}/{route_value}" if chart_id else None,
        "videos": rows,
        "taylor_videos": taylor_rows,
    }

    history_json = _history_json_path(chart_date)
    history_json.parent.mkdir(parents=True, exist_ok=True)
    history_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] Written -> {history_json}")

    if not args.no_csv:
        history_csv = _history_csv_path(chart_date)
        _write_csv(history_csv, rows)
        print(f"[DONE] Written -> {history_csv}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] Written latest -> {OUTPUT_PATH}")

    lock = _updated_lock(chart_date)
    lock.write_text("updated=true\n", encoding="utf-8")
    print(f"[DONE] updated.lock -> {lock}")

    if args.no_post:
        print("[INFO] Twitter post skipped (--no-post)")
    else:
        print("[INFO] Twitter post not implemented for music videos yet; data collection complete.")

    print(f"[OK] {len(rows)} music videos collected for {chart_date}; Taylor rows: {len(taylor_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
