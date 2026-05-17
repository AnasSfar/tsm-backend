#!/usr/bin/env python3
"""
Fetch Spotify Charts' global daily artist chart.

Default:
    python artist_global_daily.py

Useful options:
    python artist_global_daily.py --date latest
    python artist_global_daily.py --date 2026-05-06
    python artist_global_daily.py --wait
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
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
COLLECTOR_ROOT = CHARTS_ROOT / "artists_global"
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import spotify_chart_dir

CHART_ID = "artist-global-daily"
API_BASE = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
CHART_URL = f"https://charts.spotify.com/charts/view/{CHART_ID}/latest"
# Use the songs chart page to acquire the bearer token (more reliable than artist chart page)
_TOKEN_ACQUIRE_URL = "https://charts.spotify.com/charts/view/regional-global-daily/latest"
SESSION_FILE = CHARTS_ROOT / "global" / "tools" / "json" / "spotify_session.json"
BEARER_CACHE = CHARTS_ROOT / "global" / "tools" / "json" / "bearer_cache.json"
OUTPUT_PATH = ROOT / "website" / "site" / "data" / "charts_artists_global.json"

TOKEN_TTL = 50 * 60
DEFAULT_WAIT_SECONDS = 10
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

_WARP_CLI = Path(r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe")


def _warp_connect() -> None:
    cli = str(_WARP_CLI) if _WARP_CLI.exists() else "warp-cli"
    try:
        status = subprocess.run([cli, "status"], timeout=5, check=False, capture_output=True, text=True)
        if "Connected" in (status.stdout or ""):
            print("[WARP] deja connecte")
            return
        print("[WARP] connexion en cours...")
        subprocess.run([cli, "connect"], timeout=15, check=False, capture_output=True)
        for _ in range(15):
            status = subprocess.run([cli, "status"], timeout=5, check=False, capture_output=True, text=True)
            if "Connected" in (status.stdout or ""):
                break
            time.sleep(1)
        else:
            time.sleep(3)
        print("[WARP] connecté")
    except Exception as e:
        print(f"[WARP] impossible de connecter ({e})")


def _warp_disconnect() -> None:
    print("[WARP] garde connecte")


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
    BEARER_CACHE.write_text(
        json.dumps({"token": token, "ts": time.time()}),
        encoding="utf-8",
    )


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

    _MAX_ATTEMPTS = 3
    _RETRY_DELAY = 10
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt > 0:
            print(f"[INFO] Playwright retry {attempt}/{_MAX_ATTEMPTS - 1} (attente {_RETRY_DELAY}s)...")
            time.sleep(_RETRY_DELAY)
        token_holder.clear()
        print(f"[INFO] Acquiring Spotify bearer token via Playwright (tentative {attempt + 1})...")
        try:
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
                    page.goto(_TOKEN_ACQUIRE_URL, wait_until="domcontentloaded", timeout=30_000)
                    deadline = time.time() + 20
                    while not token_holder and time.time() < deadline:
                        page.wait_for_timeout(300)
                finally:
                    browser.close()
            if token_holder:
                break
        except Exception as e:
            last_error = e
            print(f"[WARN] Playwright tentative {attempt + 1} échouée: {e}")

    if not token_holder:
        if last_error:
            raise last_error
        raise RuntimeError("Bearer token not found; refresh spotify_session.json")

    token = token_holder[0]
    _save_cached_token(token)
    return token


def _clean_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
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


def _spotify_id(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    match = re.search(r"artist[:/]([A-Za-z0-9]+)", text)
    return match.group(1) if match else None


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


def _parse_artist_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in data.get("entries") or []:
        chart = entry.get("chartEntryData") or {}
        meta = (
            entry.get("artistMetadata")
            or entry.get("trackMetadata")
            or entry.get("metadata")
            or {}
        )
        artist_name = (
            meta.get("artistName")
            or meta.get("name")
            or meta.get("displayName")
            or entry.get("artistName")
            or entry.get("name")
            or ""
        )
        rank = _clean_int(chart.get("currentRank") or entry.get("currentRank"))
        if not artist_name or rank is None:
            continue

        metric = chart.get("rankingMetric") or {}
        artist_uri = meta.get("artistUri") or meta.get("uri") or entry.get("uri")
        artist_id = _spotify_id(artist_uri) or _spotify_id(meta.get("externalUrl"))

        rows.append({
            "rank": rank,
            "artist_name": str(artist_name).strip(),
            "artist_id": artist_id,
            "spotify_url": f"https://open.spotify.com/artist/{artist_id}" if artist_id else None,
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
                or entry.get("imageUrl")
            ),
        })

    rows.sort(key=lambda row: row["rank"])
    return rows


def _request_chart(route_value: str, token: str) -> requests.Response:
    return requests.get(
        f"{API_BASE}/{CHART_ID}/{route_value}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Referer": "https://charts.spotify.com/",
            "User-Agent": UA,
        },
        timeout=30,
    )


def _fetch_chart(route_value: str, token: str) -> tuple[list[dict[str, Any]], str | None, str]:
    try:
        resp = _request_chart(route_value, token)
    except requests.RequestException as exc:
        return [], None, f"request error: {exc}"

    if resp.status_code in (401, 403):
        token = _get_bearer_token(refresh=True)
        try:
            resp = _request_chart(route_value, token)
        except requests.RequestException as exc:
            return [], None, f"request error: {exc}"

    if resp.status_code != 200:
        return [], None, f"HTTP {resp.status_code}"

    try:
        data = resp.json()
    except ValueError as exc:
        return [], None, f"invalid JSON: {exc}"
    return _parse_artist_entries(data), _find_first_date(data), "HTTP 200"


def _history_json_path(chart_date: str) -> Path:
    return spotify_chart_dir("artists_global", chart_date) / "artist_global_daily.json"


def _history_csv_path(chart_date: str) -> Path:
    return spotify_chart_dir("artists_global", chart_date) / "artist_global_daily.csv"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank",
        "artist_name",
        "artist_id",
        "spotify_url",
        "streams",
        "previous_rank",
        "peak_rank",
        "streak",
        "image_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def maybe_upload_to_r2() -> None:
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        print("[INFO] R2 upload skipped (UPLOAD_TO_R2 explicitly disabled)")
        return

    r2_script = ROOT / "scripts" / "r2.py"
    if not r2_script.exists():
        print(f"[WARN] R2 upload script missing: {r2_script}")
        return

    print("[STEP] Uploading exported data to R2")
    result = subprocess.run([sys.executable, str(r2_script)], check=False, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"[WARN] R2 upload failed with code {result.returncode} (non-blocking)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Spotify artist-global-daily chart.")
    parser.add_argument("--date", default="latest", help="YYYY-MM-DD or latest (default: latest).")
    parser.add_argument("--no-wait", action="store_true", help="Ne pas retenter si le chart est indisponible.")
    parser.add_argument("--retry-seconds", type=int, default=DEFAULT_WAIT_SECONDS)
    parser.add_argument("--no-csv", action="store_true", help="Do not write the CSV snapshot.")
    parser.add_argument("--no-upload", action="store_true", help="Skip the R2 upload step.")
    parser.add_argument("--no-post", action="store_true", help="Skip image generation and Twitter posting.")
    parser.add_argument("--no-warp", action="store_true", help="Skip Cloudflare WARP connect/disconnect.")
    args = parser.parse_args()

    route_value = args.date.strip() or "latest"
    if route_value != "latest":
        try:
            datetime.strptime(route_value, "%Y-%m-%d")
        except ValueError:
            print(f"[ERROR] Invalid --date value: {route_value!r}")
            return 1

    expected_date = str(date.today() - timedelta(days=1)) if route_value == "latest" else None

    token = _get_bearer_token()
    attempt = 1
    while True:
        rows, detected_date, status = _fetch_chart(route_value, token)
        if rows and (expected_date is None or detected_date == expected_date):
            break
        if args.no_wait:
            if not rows:
                print(f"[ERROR] Chart {route_value} unavailable ({status}, 0 rows)")
            else:
                print(f"[ERROR] Chart date mismatch: got {detected_date}, expected {expected_date}")
            return 1
        if rows:
            print(
                f"[WAIT] Chart not yet updated (got {detected_date}, expected {expected_date}, "
                f"attempt #{attempt}) - retry in {args.retry_seconds}s"
            )
        else:
            print(
                f"[WAIT] Chart {route_value} unavailable "
                f"({status}, attempt #{attempt}) - retry in {args.retry_seconds}s"
            )
        time.sleep(args.retry_seconds)
        attempt += 1

    if detected_date:
        chart_date = detected_date
    elif route_value != "latest":
        chart_date = route_value
    else:
        chart_date = expected_date

    output = {
        "date": chart_date,
        "chart_id": CHART_ID,
        "source_url": f"https://charts.spotify.com/charts/view/{CHART_ID}/{route_value}",
        "artists": rows,
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

    if args.no_upload:
        print("[INFO] R2 upload skipped (--no-upload)")
    else:
        maybe_upload_to_r2()

    if args.no_post:
        print("[INFO] Image generation and Twitter post skipped (--no-post)")
    else:
        generate_script = COLLECTOR_ROOT / "tools" / "scripts" / "generate_artist_chart_image.py"
        if generate_script.exists():
            print("[STEP] Generating image and posting to Twitter...")
            cmd = [sys.executable, str(generate_script), chart_date]
            result = subprocess.run(cmd, cwd=str(ROOT), check=False)
            if result.returncode != 0:
                print(f"[WARN] generate_artist_chart_image.py failed (code {result.returncode})")
        else:
            print(f"[WARN] Image generation script not found: {generate_script}")

    print(f"[OK] {len(rows)} artists collected for {chart_date}")
    return 0


if __name__ == "__main__":
    no_warp = "--no-warp" in sys.argv[1:]
    if not no_warp:
        _warp_connect()
    try:
        raise SystemExit(main())
    finally:
        if not no_warp:
            _warp_disconnect()
