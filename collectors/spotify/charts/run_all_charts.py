#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

CHARTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CHARTS_ROOT.parents[2]
sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))
from core.data_paths import legacy_spotify_chart_dir, spotify_chart_dir

_WARP_CLI = Path(r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe")


def _warp_connect() -> None:
    cli = str(_WARP_CLI) if _WARP_CLI.exists() else "warp-cli"
    try:
        status = subprocess.run([cli, "status"], timeout=5, check=False, capture_output=True, text=True)
        if "Connected" in (status.stdout or ""):
            print("[WARP] deja connecte")
            return
        t0 = time.perf_counter()
        print("[WARP] connexion en cours...")
        subprocess.run([cli, "connect"], timeout=15, check=False, capture_output=True)
        for _ in range(15):
            status = subprocess.run([cli, "status"], timeout=5, check=False, capture_output=True, text=True)
            if "Connected" in (status.stdout or ""):
                break
            time.sleep(1)
        else:
            time.sleep(3)
        print(f"[WARP] connecté ({_fmt(time.perf_counter() - t0)})")
    except Exception as e:
        print(f"[WARP] impossible de connecter ({e})")


def _warp_disconnect() -> None:
    print("[WARP] garde connecte")


REPO_ENV_FILE = REPO_ROOT / ".env"
load_dotenv(REPO_ENV_FILE, override=False)
R2_ENV_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
SPOTIFY_API_BASE = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
SPOTIFY_CHARTS_URL = "https://charts.spotify.com/charts/view/regional-global-daily/latest"
SPOTIFY_SESSION = CHARTS_ROOT / "global" / "tools" / "json" / "spotify_session.json"
SPOTIFY_TOKEN_TTL = 50 * 60
AVAILABILITY_RETRY_SECONDS = 10
AVAILABILITY_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_AVAILABILITY_MAX_ATTEMPTS", "0"))
AVAILABILITY_MAX_SECONDS = int(os.getenv("SPOTIFY_AVAILABILITY_MAX_SECONDS", "0"))
WATCH_MAX_SECONDS = int(os.getenv("SPOTIFY_WATCH_MAX_SECONDS", "0"))
WATCH_BASE_SECONDS = int(os.getenv("SPOTIFY_WATCH_BASE_SECONDS", "60"))
WATCH_LATE_SECONDS = int(os.getenv("SPOTIFY_WATCH_LATE_SECONDS", "180"))
WATCH_HOT_SECONDS = int(os.getenv("SPOTIFY_WATCH_HOT_SECONDS", "20"))
WATCH_ERROR_SECONDS = int(os.getenv("SPOTIFY_WATCH_ERROR_SECONDS", "120"))
RATE_LIMIT_RETRY_SECONDS = int(os.getenv("SPOTIFY_RATE_LIMIT_RETRY_SECONDS", "120"))
WORLDWIDE_VALIDATE_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_WORLDWIDE_VALIDATE_MAX_ATTEMPTS", "0"))
WORLDWIDE_VALIDATE_WAIT_SECONDS = int(os.getenv("SPOTIFY_WORLDWIDE_VALIDATE_WAIT_SECONDS", "180"))
WORLDWIDE_VALIDATE_TOTAL_RATIO = float(os.getenv("SPOTIFY_WORLDWIDE_VALIDATE_TOTAL_RATIO", "0.80"))
WORLDWIDE_VALIDATE_TRACK_RATIO = float(os.getenv("SPOTIFY_WORLDWIDE_VALIDATE_TRACK_RATIO", "0.70"))
PLAYWRIGHT_LAUNCH_TIMEOUT_MS = int(os.getenv("SPOTIFY_PLAYWRIGHT_LAUNCH_TIMEOUT_MS", "15000"))
PLAYWRIGHT_GOTO_TIMEOUT_MS = int(os.getenv("SPOTIFY_PLAYWRIGHT_GOTO_TIMEOUT_MS", "15000"))
PLAYWRIGHT_TOKEN_WAIT_SECONDS = int(os.getenv("SPOTIFY_PLAYWRIGHT_TOKEN_WAIT_SECONDS", "10"))
USE_PLAYWRIGHT_TOKEN = os.getenv("SPOTIFY_USE_PLAYWRIGHT_TOKEN", "0").strip().lower() in {"1", "true", "yes", "on"}
SPOTIFY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

# artists_global, global et fr postent dès leur collecte terminée (pas d'attente de worldwide)
# us/uk sont geres par worldwide.
COLLECT_RUNNERS: list[tuple[str, Path, list[str]]] = [
    ("global",         CHARTS_ROOT / "global"         / "daily.py",         ["--force"]),
    ("fr",             CHARTS_ROOT / "fr"             / "daily.py",         ["--force"]),
    ("worldwide",      CHARTS_ROOT / "worldwide"      / "daily.py",         ["--force"]),
]

CHART_AVAILABILITY: dict[str, str] = {
    "artists_global": "artist-global-daily",
    "global": "regional-global-daily",
    "fr": "regional-fr-daily",
}

def _region_lock(name: str, target: date, lock_name: str) -> Path:
    return spotify_chart_dir(name, target) / lock_name


def _legacy_region_lock(name: str, target: date, lock_name: str) -> Path:
    return legacy_spotify_chart_dir(name, target) / lock_name


def _region_lock_exists(name: str, target: date, lock_name: str) -> bool:
    return _region_lock(name, target, lock_name).exists() or _legacy_region_lock(name, target, lock_name).exists()


def _region_data_exists(name: str, target: date) -> bool:
    day_dirs = [spotify_chart_dir(name, target), legacy_spotify_chart_dir(name, target)]
    for day_dir in day_dirs:
        if name == "artists_global":
            if (day_dir / "artist_global_daily.json").exists() or (day_dir / "artist_global_daily.csv").exists():
                return True
        elif name == "worldwide":
            if (day_dir / f"ts_worldwide_{target}.json").exists():
                return True
        elif (day_dir / "ts_all_songs.csv").exists() or (day_dir / f"ts_chart_{target}.json").exists():
            return True
    return False


def _worldwide_json_path() -> Path:
    return REPO_ROOT / "website" / "site" / "data" / "charts_worldwide.json"


def _worldwide_json_date() -> str | None:
    try:
        return str(json.loads(_worldwide_json_path().read_text(encoding="utf-8-sig")).get("date") or "")
    except Exception:
        return None


def _worldwide_data_ready(target: date) -> bool:
    actual = _worldwide_json_date()
    if actual == str(target):
        ok, detail = _validate_worldwide_snapshot(target)
        if not ok:
            print(f"[FAIL] snapshot worldwide invalide pour cards: {detail}")
        return ok
    print(f"[FAIL] charts_worldwide.json contient {actual!r}, attendu {str(target)!r}")
    return False


def _worldwide_snapshot_path(target: date) -> Path:
    return spotify_chart_dir("worldwide", target) / f"ts_worldwide_{target}.json"


def _load_worldwide_snapshot(target: date) -> dict | None:
    for path in (
        _worldwide_snapshot_path(target),
        legacy_spotify_chart_dir("worldwide", target) / f"ts_worldwide_{target}.json",
    ):
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            print(f"[WARN] snapshot worldwide illisible {path}: {exc}")
    return None


def _worldwide_metrics(snapshot: dict | None) -> tuple[dict[str, int], int, int]:
    by_track = snapshot.get("by_track", {}) if isinstance(snapshot, dict) else {}
    counts = {
        str(track_id): len(entries)
        for track_id, entries in by_track.items()
        if isinstance(entries, list)
    }
    total = sum(counts.values())
    max_regions = max(counts.values(), default=0)
    return counts, total, max_regions


def _validate_worldwide_snapshot(target: date) -> tuple[bool, str]:
    current = _load_worldwide_snapshot(target)
    if not current:
        return False, f"snapshot mondial absent pour {target}"
    prev_target = target - timedelta(days=1)
    previous = _load_worldwide_snapshot(prev_target)
    if not previous:
        return True, f"pas de snapshot veille ({prev_target}), validation partielle ignoree"

    curr_counts, curr_total, curr_max = _worldwide_metrics(current)
    prev_counts, prev_total, prev_max = _worldwide_metrics(previous)
    if not curr_counts:
        return False, "snapshot mondial vide"

    min_total = int(prev_total * WORLDWIDE_VALIDATE_TOTAL_RATIO)
    if prev_total >= 10 and curr_total < min_total:
        return False, f"total regions trop bas: {curr_total}/{prev_total} (min {min_total})"

    min_max = int(prev_max * WORLDWIDE_VALIDATE_TRACK_RATIO)
    if prev_max >= 10 and curr_max < min_max:
        return False, f"top song trop partielle: {curr_max}/{prev_max} regions (min {min_max})"

    large_drops: list[str] = []
    for track_id, prev_count in prev_counts.items():
        curr_count = curr_counts.get(track_id, 0)
        if prev_count >= 10 and curr_count < int(prev_count * WORLDWIDE_VALIDATE_TRACK_RATIO):
            large_drops.append(f"{track_id}:{curr_count}/{prev_count}")
    if large_drops:
        return False, "tracks trop partiels: " + ", ".join(large_drops[:5])

    return True, f"{len(curr_counts)} songs, {curr_total} regions (veille: {len(prev_counts)} songs, {prev_total} regions)"


def _runner_args_for_run_all(name: str, fixed: list[str], forwarded: list[str], target_date: date, explicit_target_date: bool) -> list[str]:
    if name != "artists_global":
        return list(dict.fromkeys([*fixed, *forwarded]))
    artist_args = list(fixed)
    if explicit_target_date:
        artist_args.extend(["--date", str(target_date)])
    return list(dict.fromkeys(artist_args))


def _already_done(
    runners: list[tuple[str, Path, list[str]]],
    target: date,
    post_parts: set[str],
) -> bool:
    return not _filter_pending_runners(runners, target, post_parts)


def _runner_done(name: str, target: date, post_parts: set[str]) -> bool:
    updated = _region_lock_exists(name, target, "updated.lock")
    data_exists = _region_data_exists(name, target)
    if name == "artists_global":
        return data_exists
    if name in {"global", "fr"} and name in post_parts:
        posted = _region_lock_exists(name, target, "posted.lock")
        return posted and (updated or data_exists)
    if name == "worldwide" and (updated or data_exists):
        ok, detail = _validate_worldwide_snapshot(target)
        if not ok:
            print(f"[WARN] worldwide incomplet pour {target}: {detail}")
        return ok
    return updated or data_exists


def _filter_pending_runners(
    runners: list[tuple[str, Path, list[str]]],
    target: date,
    post_parts: set[str],
) -> list[tuple[str, Path, list[str]]]:
    pending: list[tuple[str, Path, list[str]]] = []
    skipped: list[str] = []
    for runner in runners:
        name, _, _ = runner
        if _runner_done(name, target, post_parts):
            skipped.append("worldwide-data" if name == "worldwide" else name)
        else:
            pending.append(runner)
    if skipped:
        print(f"[SKIP] deja fait pour {target}: {', '.join(skipped)}")
    return pending


def _print_already_done(
    runners: list[tuple[str, Path, list[str]]],
    target: date,
    post_parts: set[str],
) -> None:
    names = [n for n, _, _ in runners]
    updated = [n for n in names if _region_lock_exists(n, target, "updated.lock")]
    posted_names = sorted(post_parts & {"global", "fr"})
    posted = [n for n in posted_names if _region_lock_exists(n, target, "posted.lock")]
    message = f"[SKIP] donnees deja a jour ({', '.join(updated)})"
    if posted:
        message += f", posts deja faits ({', '.join(posted)})"
    print(f"{message} pour {target}")


_active_procs: list[subprocess.Popen] = []
_active_procs_lock = threading.Lock()
_stop_event = threading.Event()


def _kill_all() -> None:
    with _active_procs_lock:
        for proc in list(_active_procs):
            try:
                proc.kill()
            except Exception:
                pass


def _fmt(value: float) -> str:
    m, s = divmod(int(value), 60)
    return f"{m}m {s:02d}s"


def _build_env() -> dict[str, str]:
    load_dotenv(REPO_ENV_FILE, override=True)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["UPLOAD_TO_R2"] = "0"  # individual scripts must not upload; only export_for_web.py does
    missing = [k for k in R2_ENV_VARS if not env.get(k, "").strip()]
    if missing:
        print(f"[WARN] R2 vars manquantes: {', '.join(missing)}")
    return env


def _bearer_cache_path(name: str) -> Path:
    return CHARTS_ROOT / name / "tools" / "json" / "bearer_cache.json"


def _load_cached_bearer(name: str, *, allow_stale: bool = False) -> str | None:
    try:
        data = json.loads(_bearer_cache_path(name).read_text(encoding="utf-8-sig"))
        is_fresh = time.time() - float(data.get("ts", 0)) < SPOTIFY_TOKEN_TTL
        if is_fresh or allow_stale:
            token = str(data.get("token") or "").strip()
            return token or None
    except Exception:
        return None
    return None


def _save_bearer_to_caches(token: str, names: list[str]) -> None:
    payload = json.dumps({"token": token, "ts": time.time()})
    for name in names:
        try:
            path = _bearer_cache_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        except Exception as e:
            print(f"[WARN] cache bearer {name} non sauvegarde: {e}")


def _acquire_bearer_token_via_http(names: list[str]) -> str | None:
    cookies: dict[str, str] = {}
    try:
        session_data = json.loads(SPOTIFY_SESSION.read_text(encoding="utf-8-sig"))
        for cookie in session_data.get("cookies", []):
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            if name and value:
                cookies[name] = value
    except Exception:
        return None

    if not cookies:
        return None

    try:
        resp = requests.get(
            "https://open.spotify.com/get_access_token",
            params={"reason": "transport", "productType": "web_player"},
            headers={
                "Accept": "application/json",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                "User-Agent": SPOTIFY_UA,
            },
            cookies=cookies,
            timeout=15,
        )
        token = str(resp.json().get("accessToken") or "").strip() if resp.ok else ""
    except Exception:
        return None

    if not token:
        return None

    print("[CHECK] token Spotify recupere via HTTP direct.")
    _save_bearer_to_caches(token, names)
    return token


def _acquire_bearer_token(names: list[str], *, refresh: bool = False, allow_stale: bool = False) -> str:
    if not refresh:
        for name in names:
            token = _load_cached_bearer(name)
            if token:
                return token

    token = _acquire_bearer_token_via_http(names)
    if token:
        return token

    if not USE_PLAYWRIGHT_TOKEN:
        if allow_stale:
            for name in names:
                token = _load_cached_bearer(name, allow_stale=True)
                if token:
                    print("[CHECK] HTTP token indisponible, essai avec le dernier bearer cache.")
                    return token
        raise RuntimeError("Bearer token introuvable via HTTP direct")

    print("[CHECK] token Spotify absent/expire, recuperation via Playwright...")

    token_holder: list[str] = []
    api_host = SPOTIFY_API_BASE.split("//", 1)[1].split("/", 1)[0]

    def _on_request(req) -> None:
        if api_host in req.url and not token_holder:
            auth = req.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token_holder.append(auth[7:])

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                timeout=PLAYWRIGHT_LAUNCH_TIMEOUT_MS,
            )
            try:
                context = browser.new_context(
                    storage_state=str(SPOTIFY_SESSION),
                    user_agent=SPOTIFY_UA,
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()
                page.on("request", _on_request)
                page.goto(SPOTIFY_CHARTS_URL, wait_until="domcontentloaded", timeout=PLAYWRIGHT_GOTO_TIMEOUT_MS)
                deadline = time.time() + PLAYWRIGHT_TOKEN_WAIT_SECONDS
                while not token_holder and time.time() < deadline:
                    page.wait_for_timeout(300)
            finally:
                browser.close()
    except Exception as e:
        short = str(e).split("\n")[0][:120]
        print(f"[CHECK] Playwright token indisponible ({short})")

    if not token_holder:
        if allow_stale:
            for name in names:
                token = _load_cached_bearer(name, allow_stale=True)
                if token:
                    print("[CHECK] Playwright indisponible, essai avec le dernier bearer cache.")
                    return token
        raise RuntimeError(f"Bearer token introuvable avec {SPOTIFY_SESSION}")

    token = token_holder[0]
    _save_bearer_to_caches(token, names)
    return token


def _extract_target_date(forwarded: list[str]) -> tuple[date, bool]:
    for value in forwarded:
        if value.startswith("--"):
            continue
        try:
            return datetime.strptime(value, "%Y-%m-%d").date(), True
        except ValueError:
            continue
    return date.today() - timedelta(days=1), False


def _find_first_date(value) -> str | None:
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


def _extract_chart_date_from_text(text: str) -> str | None:
    for pattern in (r"\b\d{4}-\d{2}-\d{2}\b", r"\b[A-Z][a-z]+ \d{1,2}, \d{4}\b"):
        match = re.search(pattern, text or "")
        if not match:
            continue
        value = match.group(0)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return value
        try:
            return datetime.strptime(value, "%B %d, %Y").strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def _latest_chart_page_date() -> str | None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            try:
                context = browser.new_context(
                    storage_state=str(SPOTIFY_SESSION),
                    user_agent=SPOTIFY_UA,
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()
                page.goto(SPOTIFY_CHARTS_URL, wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_function(
                        "() => document.body && /\\b[A-Z][a-z]+ \\d{1,2}, \\d{4}\\b|\\b\\d{4}-\\d{2}-\\d{2}\\b/.test(document.body.innerText)",
                        timeout=10_000,
                    )
                except Exception:
                    pass
                body_text = (page.locator("body").inner_text(timeout=5_000) or "").strip()
                return _extract_chart_date_from_text(body_text)
            finally:
                browser.close()
    except Exception as e:
        short = str(e).split("\n")[0][:120]
        print(f"[CHECK] date latest via page indisponible ({short})")
        return None


def _request_chart_api(chart_id: str, route_value: str, token: str) -> requests.Response:
    return requests.get(
        f"{SPOTIFY_API_BASE}/{chart_id}/{route_value}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Referer": "https://charts.spotify.com/",
            "User-Agent": SPOTIFY_UA,
        },
        timeout=15,
    )


def _chart_available(
    chart_id: str,
    target: date | None,
    token: str,
) -> tuple[bool, str, int | None, date | None]:
    if target is None:
        try:
            resp = _request_chart_api(chart_id, "latest", token)
        except Exception as e:
            short = str(e).split("\n")[0][:120]
            return False, f"reseau: latest {short}", None, None
        retry_after = resp.headers.get("Retry-After")
        retry_after_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
        if resp.status_code != 200:
            return False, f"latest=HTTP {resp.status_code}", retry_after_seconds, None
        try:
            data = resp.json()
            entries = data.get("entries") or []
            detected = _find_first_date(data)
        except Exception:
            entries = []
            detected = None
        if detected and entries:
            return True, f"latest={detected} ({len(entries)} lignes)", None, date.fromisoformat(detected)
        if detected:
            return False, f"latest={detected} sans lignes", None, None
        return False, f"latest sans date ({len(entries)} lignes)", None, None

    try:
        resp = _request_chart_api(chart_id, str(target), token)
    except Exception as e:
        short = str(e).split("\n")[0][:120]
        return False, f"reseau: {short}", None, None

    if resp.status_code == 200:
        try:
            entries = resp.json().get("entries") or []
        except Exception:
            entries = []
        return bool(entries), f"HTTP 200 ({len(entries)} lignes)", None, target

    if resp.status_code == 404:
        try:
            latest_resp = _request_chart_api(chart_id, "latest", token)
        except Exception as e:
            short = str(e).split("\n")[0][:120]
            return False, f"reseau: latest {short}", None, None
        if latest_resp.status_code == 200:
            try:
                latest_data = latest_resp.json()
                entries = latest_data.get("entries") or []
                detected = _find_first_date(latest_data)
            except Exception:
                entries = []
                detected = None
            if detected == str(target) and entries:
                return True, f"HTTP 404 date, latest={detected} ({len(entries)} lignes)", None, target
            if detected:
                return False, f"HTTP 404 date, latest pointe vers {detected}", None, None
            page_detected = _latest_chart_page_date()
            if page_detected == str(target) and entries:
                return True, f"HTTP 404 date, page latest={page_detected} ({len(entries)} lignes)", None, target
            if page_detected:
                return False, f"HTTP 404 date, page latest pointe vers {page_detected}", None, None
            return False, f"HTTP 404 date, latest sans date ({len(entries)} lignes)", None, None
        retry_after = latest_resp.headers.get("Retry-After")
        retry_after_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
        return False, f"HTTP 404 date, latest=HTTP {latest_resp.status_code}", retry_after_seconds, None

    retry_after = resp.headers.get("Retry-After")
    retry_after_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
    return False, f"HTTP {resp.status_code}", retry_after_seconds, None


def _watch_wait_seconds(
    *,
    detail: str,
    elapsed: float,
    retry_after: int | None,
    base_seconds: int,
    late_seconds: int,
    hot_seconds: int,
    error_seconds: int,
) -> int:
    if retry_after is not None:
        return max(30, min(retry_after, 15 * 60))
    if detail.startswith("HTTP 200"):
        return hot_seconds
    if detail.startswith("reseau:") or detail in {"token indisponible", "HTTP 401", "HTTP 403", "HTTP 429"}:
        return error_seconds
    if elapsed > 2 * 60 * 60:
        return max(late_seconds, base_seconds)
    return base_seconds


def _wait_for_charts_available(
    runners: list[tuple[str, Path, list[str]]],
    *,
    target: date | None,
    dry_run: bool,
    watch_release: bool = False,
    watch_max_seconds: int = WATCH_MAX_SECONDS,
    watch_base_seconds: int = WATCH_BASE_SECONDS,
    watch_late_seconds: int = WATCH_LATE_SECONDS,
    watch_hot_seconds: int = WATCH_HOT_SECONDS,
    watch_error_seconds: int = WATCH_ERROR_SECONDS,
    warp_on_token_fail: bool = False,
    initial_warp_active: bool = False,
) -> tuple[bool, date | None]:
    names = [name for name, _, _ in runners if name in CHART_AVAILABILITY]
    if dry_run or not names:
        return False, target

    probe = next((n for n in ("global", "fr") if n in names), names[0])
    probe_chart = CHART_AVAILABILITY[probe]

    attempt = 1
    refresh_token = False
    warp_active = initial_warp_active
    consecutive_429 = 0
    started = time.monotonic()
    mode = "watch-release" if watch_release else "check"
    target_label = str(target) if target is not None else "latest"
    print(f"\n[CHECK] disponibilite Spotify pour {target_label} (via {probe}, mode {mode})")
    while not _stop_event.is_set():
        elapsed = time.monotonic() - started
        max_seconds = watch_max_seconds if watch_release else AVAILABILITY_MAX_SECONDS
        attempts_exhausted = (
            not watch_release
            and AVAILABILITY_MAX_ATTEMPTS > 0
            and attempt > AVAILABILITY_MAX_ATTEMPTS
        )
        time_exhausted = max_seconds > 0 and elapsed > max_seconds
        if attempts_exhausted or time_exhausted:
            if warp_active:
                _warp_disconnect()
            limits = []
            if max_seconds > 0:
                limits.append(_fmt(max_seconds))
            if not watch_release and AVAILABILITY_MAX_ATTEMPTS > 0:
                limits.append(f"{AVAILABILITY_MAX_ATTEMPTS} tentatives")
            raise TimeoutError(
                "Spotify chart indisponible apres "
                f"{attempt - 1} tentative(s) et {_fmt(elapsed)} "
                f"(limite: {', '.join(limits)})."
            )

        try:
            token = _acquire_bearer_token(names, refresh=refresh_token, allow_stale=watch_release)
            refresh_token = False
        except Exception as e:
            print(f"[CHECK] tentative #{attempt}: token indisponible ({e})")
            token = None
            if warp_on_token_fail:
                print("[CHECK] route normale bloquee - bascule via WARP...")
                warp_started_for_token = False
                if not warp_active:
                    _warp_connect()
                    warp_active = True
                    warp_started_for_token = True
                try:
                    token = _acquire_bearer_token(names, refresh=True, allow_stale=watch_release)
                    refresh_token = False
                except Exception as warp_error:
                    print(f"[CHECK] token via WARP indisponible ({warp_error})")
                if not token and warp_started_for_token:
                    _warp_disconnect()
                    warp_active = False
            if not token:
                wait = (
                    min(watch_error_seconds * min(attempt, 5), 10 * 60)
                    if watch_release
                    else AVAILABILITY_RETRY_SECONDS
                )
                print(f"[CHECK] token indisponible - retry dans {wait}s")
                time.sleep(wait)
                attempt += 1
                continue

        ok, detail, retry_after, resolved_target = _chart_available(probe_chart, target, token)
        print(f"[CHECK] tentative #{attempt}: {probe}={detail}")
        if ok:
            print(f"[CHECK] charts disponibles pour {resolved_target or target_label}")
            return warp_active, resolved_target or target
        is_network_err = detail.startswith("reseau:")
        is_rate_limited = "HTTP 429" in detail
        if is_rate_limited:
            consecutive_429 += 1
        else:
            consecutive_429 = 0
        if "HTTP 401" in detail or "HTTP 403" in detail:
            refresh_token = True

        if is_network_err and warp_on_token_fail and not warp_active:
            print("[CHECK] reseau instable - bascule via WARP pour le prochain probe...")
            _warp_connect()
            warp_active = True
            wait = 5
            label = "reseau"
            print(f"[CHECK] {label} - retry dans {wait}s")
            time.sleep(wait)
            attempt += 1
            continue
        if is_rate_limited and consecutive_429 >= 2 and warp_on_token_fail and not warp_active:
            print("[CHECK] 2 HTTP 429 Spotify consecutifs - bascule immediate via WARP...")
            _warp_connect()
            warp_active = True
            consecutive_429 = 0
            attempt += 1
            continue
        if watch_release:
            wait = _watch_wait_seconds(
                detail=detail,
                elapsed=elapsed,
                retry_after=retry_after,
                base_seconds=watch_base_seconds,
                late_seconds=watch_late_seconds,
                hot_seconds=watch_hot_seconds,
                error_seconds=watch_error_seconds,
            )
        else:
            if retry_after is not None:
                wait = max(30, min(retry_after, 15 * 60))
            elif detail == "HTTP 429":
                wait = RATE_LIMIT_RETRY_SECONDS
            else:
                wait = 5 if is_network_err else AVAILABILITY_RETRY_SECONDS
        label = "reseau" if is_network_err else "chart indisponible"
        print(f"[CHECK] {label} - retry dans {wait}s")
        time.sleep(wait)
        attempt += 1


_KEEP_LEVELS = {"ERROR", "WARN", "STEP"}
_TS_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ")


def _is_live_line(line: str) -> bool:
    return "] [" in line and any(f"] [{lvl}]" in line for lvl in _KEEP_LEVELS)


def _strip_ts(line: str) -> str:
    """Enlève le préfixe '[YYYY-MM-DD HH:MM:SS] ' si présent."""
    return _TS_RE.sub("", line, count=1)


def _run(
    name: str,
    script: Path,
    args: list[str],
    *,
    dry_run: bool,
    env: dict[str, str],
    verbose: bool = False,
) -> int:
    if not script.exists():
        print(f"[FAIL] {name}: script introuvable")
        return 127
    if dry_run:
        print(f"[SKIP] {name}")
        return 0
    if _stop_event.is_set():
        return -1

    print(f"[RUN ] {name}")
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, str(script), *args],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    with _active_procs_lock:
        _active_procs.append(proc)

    captured: list[str] = []
    prefix = f"  [{name:<9}]"
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n").rstrip("\r")
            captured.append(line)
            if verbose:
                print(f"{prefix} {line}")
            elif _is_live_line(line):
                print(f"{prefix} {_strip_ts(line)}")
        proc.wait()
    finally:
        with _active_procs_lock:
            try:
                _active_procs.remove(proc)
            except ValueError:
                pass

    rc = proc.returncode if proc.returncode is not None else -1
    elapsed = _fmt(time.perf_counter() - t0)
    tag = "[ OK ]" if rc == 0 else "[FAIL]"
    print(f"{tag} {name:<12} {elapsed}")

    if rc != 0 and not verbose:
        non_live = [ln for ln in captured if not _is_live_line(ln) and ln.strip()]
        if non_live:
            print("\n".join(non_live))

    return rc


def _run_parallel(
    runners: list[tuple[str, Path, list[str]]],
    *,
    forwarded: list[str],
    target_date: date,
    explicit_target_date: bool,
    dry_run: bool,
    env: dict[str, str],
    verbose: bool = False,
) -> list[tuple[str, int]]:
    failures: list[tuple[str, int]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(runners)) as ex:
        futures = {
            ex.submit(
                _run,
                name,
                script,
                _runner_args_for_run_all(name, fixed, forwarded, target_date, explicit_target_date),
                dry_run=dry_run,
                env=env,
                verbose=verbose,
            ): name
            for name, script, fixed in runners
        }
        try:
            for f in concurrent.futures.as_completed(futures):
                name = futures[f]
                try:
                    rc = f.result()
                except Exception as e:
                    print(f"[FAIL] {name}: crash ({e})")
                    rc = 1
                if rc != 0:
                    failures.append((name, rc))
        except KeyboardInterrupt:
            _stop_event.set()
            _kill_all()
            raise
    return failures


def _ensure_worldwide_valid(
    runners: list[tuple[str, Path, list[str]]],
    *,
    forwarded: list[str],
    target_date: date,
    explicit_target_date: bool,
    env: dict[str, str],
    verbose: bool,
) -> tuple[bool, int]:
    runner = next((r for r in runners if r[0] == "worldwide"), None)
    if runner is None:
        return True, 0

    ok, detail = _validate_worldwide_snapshot(target_date)
    print(f"[CHECK] validation worldwide {target_date}: {detail}")
    if ok:
        return True, 0

    name, script, fixed = runner
    reruns = 0
    attempt = 2
    while WORLDWIDE_VALIDATE_MAX_ATTEMPTS <= 0 or attempt <= WORLDWIDE_VALIDATE_MAX_ATTEMPTS:
        wait = WORLDWIDE_VALIDATE_WAIT_SECONDS
        print(f"[WARN] worldwide partiel - retry #{attempt} dans {wait}s")
        time.sleep(wait)
        rc = _run(
            name,
            script,
            _runner_args_for_run_all(name, fixed, forwarded, target_date, explicit_target_date),
            dry_run=False,
            env=env,
            verbose=verbose,
        )
        reruns += 1
        if rc != 0:
            return False, reruns
        ok, detail = _validate_worldwide_snapshot(target_date)
        print(f"[CHECK] validation worldwide {target_date}: {detail}")
        if ok:
            return True, reruns
        attempt += 1

    return False, reruns


_ALL_POST_PARTS = {"artists", "global", "fr", "cards"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all Spotify chart daily scripts.")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--post",
        nargs="+",
        choices=sorted(_ALL_POST_PARTS),
        metavar="PART",
        default=None,
        help=(
            "Parties à poster sur Twitter: artists, cards, fr, global. "
            "Défaut: toutes. Exemple: --post global fr"
        ),
    )
    parser.add_argument("--no-post", action="store_true", help="Désactive tout le posting Twitter.")
    parser.add_argument("--force", action="store_true", help="Relance la collecte meme si les donnees existent deja.")
    parser.add_argument("--force-cards", action="store_true", help="Force la regeneration des cards worldwide.")
    parser.add_argument("--skip-uk", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--verbose", "-v", action="store_true", help="Affiche la sortie complète des scripts.")
    parser.add_argument(
        "--watch-release",
        action="store_true",
        help="Surveille la publication Spotify avec un polling adaptatif avant de lancer la collecte.",
    )
    parser.add_argument("--watch-max-seconds", type=int, default=WATCH_MAX_SECONDS)
    parser.add_argument("--watch-base-seconds", type=int, default=WATCH_BASE_SECONDS)
    parser.add_argument("--watch-late-seconds", type=int, default=WATCH_LATE_SECONDS)
    parser.add_argument("--watch-hot-seconds", type=int, default=WATCH_HOT_SECONDS)
    parser.add_argument("--watch-error-seconds", type=int, default=WATCH_ERROR_SECONDS)
    parser.add_argument(
        "--no-warp",
        action="store_true",
        help="Desactive le fallback Cloudflare WARP.",
    )
    args, passthrough = parser.parse_known_args()

    forwarded = list(passthrough)

    # Détermine quelles parties postent sur Twitter
    if args.no_post:
        post_parts: set[str] = set()
    elif args.post is not None:
        post_parts = set(args.post)
    else:
        post_parts = {"artists", "global", "fr", "cards"}  # défaut: tout poster

    started = time.perf_counter()
    env = _build_env()

    collect_runners = []
    for name, script, fixed in COLLECT_RUNNERS:
        # worldwide/daily.py ne poste jamais : generate_card_images.py (PHASE3) gère ça avec images
        if name == "worldwide":
            extra = ["--no-post"] if "--no-post" not in fixed else []
        elif name == "artists_global":
            extra = ["--no-post"] if "artists" not in post_parts else []
        else:
            extra = ["--no-post"] if name in {"global", "fr"} and name not in post_parts else []
        collect_runners.append((name, script, fixed + extra))

    target_date, _explicit_target_date = _extract_target_date(forwarded)

    # Si on ne poste que les cards, pas besoin de collecter — les données sont déjà là
    needs_collect = post_parts != {"cards"}

    failures: list[tuple[str, int]] = []
    ran_collect = False

    if needs_collect:
        original_collect_runners = collect_runners
        if args.force and not args.dry_run:
            print(f"[FORCE] pre-skip ignore pour {target_date}: collecte relancee")
        elif not args.dry_run:
            collect_runners = _filter_pending_runners(collect_runners, target_date, post_parts)
        if not collect_runners:
            _print_already_done(original_collect_runners, target_date, post_parts)
        else:
            if not args.dry_run:
                print(f"[CHECK] collecte requise pour {target_date}: {', '.join(n for n, _, _ in collect_runners)}")
            warp_active = False
            if not args.dry_run and not args.no_warp:
                _warp_connect()
                warp_active = True
            try:
                warp_active, resolved_target_date = _wait_for_charts_available(
                    collect_runners,
                    target=target_date,
                    dry_run=args.dry_run,
                    watch_release=args.watch_release,
                    watch_max_seconds=args.watch_max_seconds,
                    watch_base_seconds=args.watch_base_seconds,
                    watch_late_seconds=args.watch_late_seconds,
                    watch_hot_seconds=args.watch_hot_seconds,
                    watch_error_seconds=args.watch_error_seconds,
                    warp_on_token_fail=not args.no_warp,
                    initial_warp_active=warp_active,
                )
                warp_active = warp_active or False
                if resolved_target_date is not None:
                    target_date = resolved_target_date
                env["SPOTIFY_CHARTS_ALREADY_AVAILABLE"] = "1"
            except TimeoutError as e:
                print(f"[FAIL] {e}")
                if warp_active:
                    _warp_disconnect()
                return 1

            names_str = ", ".join(n for n, _, _ in collect_runners)
            print(f"\n[PHASE1] collecte en parallèle: {names_str}")
            t_phase1 = time.perf_counter()
            failures = _run_parallel(
                collect_runners,
                forwarded=forwarded,
                target_date=target_date,
                explicit_target_date=_explicit_target_date,
                dry_run=args.dry_run,
                env=env,
                verbose=args.verbose,
            )
            ran_collect = True
            print(f"[PHASE1] collecte terminée ({_fmt(time.perf_counter() - t_phase1)})")

            if failures:
                failed_names = {n for n, _ in failures}
                print(f"[WARN] Echecs collecte: {', '.join(failed_names)}")
                if args.stop_on_error:
                    print(f"[FAIL] stop-on-error — {_fmt(time.perf_counter() - started)}")
                    if warp_active:
                        _warp_disconnect()
                    return 1
            if not args.dry_run:
                for name, _, _ in collect_runners:
                    if name in {"global", "fr"} and name in post_parts and not _region_lock_exists(name, target_date, "posted.lock"):
                        print(f"[FAIL] {name}: posted.lock absent apres collecte pour {target_date}")
                        failures.append((f"{name}-post", 1))
            if not args.dry_run and "worldwide" in {n for n, _, _ in collect_runners} and "worldwide" not in {n for n, _ in failures}:
                worldwide_ok, worldwide_reruns = _ensure_worldwide_valid(
                    collect_runners,
                    forwarded=forwarded,
                    target_date=target_date,
                    explicit_target_date=_explicit_target_date,
                    env=env,
                    verbose=args.verbose,
                )
                if worldwide_reruns:
                    ran_collect = True
                if not worldwide_ok:
                    failures.append(("worldwide-validation", 1))
            if warp_active:
                _warp_disconnect()

    if not args.dry_run and needs_collect and ran_collect:
        print("\n[PHASE2] export web + upload R2...")
        rc_export = _run(
            "export",
            REPO_ROOT / "scripts" / "export_for_web.py",
            ["--new-date", str(target_date)],
            dry_run=False,
            env={**env, "UPLOAD_TO_R2": "1"},
            verbose=args.verbose,
        )
        if rc_export != 0:
            failures.append(("export", rc_export))
    elif not args.dry_run and needs_collect:
        print("\n[SKIP] export web + upload R2 (aucune collecte relancée)")

    should_generate_cards = "cards" in post_parts or args.force_cards or (args.no_post and args.force)
    should_post_cards = "cards" in post_parts

    if not args.dry_run and should_generate_cards and not _worldwide_data_ready(target_date):
        failures.append(("cards-data", 1))

    if not args.dry_run and should_generate_cards and not failures:
        if should_post_cards:
            print("\n[PHASE3] generation et publication des card images worldwide...")
        else:
            print("\n[PHASE3] generation des card images worldwide (no-post)...")
        cards_args = [str(target_date), "--min-countries", "1"]
        if should_post_cards:
            cards_args.append("--post")
        if args.force_cards or args.force:
            cards_args.append("--force")
        rc_cards = _run(
            "cards",
            CHARTS_ROOT / "worldwide" / "tools" / "scripts" / "generate_card_images.py",
            cards_args,
            dry_run=False,
            env=env,
            verbose=args.verbose,
        )
        if rc_cards != 0:
            failures.append(("cards", rc_cards))

    total = _fmt(time.perf_counter() - started)
    if failures:
        print(f"[FAIL] {', '.join(n for n, _ in failures)} — {total}")
        return 1
    print(f"[ OK ] tout terminé — {total}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C — arrêt en cours...")
        _stop_event.set()
        _kill_all()
        sys.exit(130)
