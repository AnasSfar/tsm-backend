#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

CHARTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CHARTS_ROOT.parents[2]
sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))

import requests
from dotenv import load_dotenv
from core.data_paths import legacy_spotify_chart_dir, run_all_charts_root, spotify_chart_dir
from core.data_paths import LEGACY_WEBSITE_DATA_DIR, WEB_EXPORT_DATA_DIR, first_existing
from core.data_paths import spotify_chart_snapshot_files
from core.git_ops import git_commit_and_push
from core.notify import send as _notify
from core.retention import cleanup_generated_artifacts
from core.swift_top_gate import check_swift_top_gate, mark_swift_top_done

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

NTFY_TOPIC_CHARTS = os.getenv("NTFY_TOPIC_CHARTS", "taylormuseum-charts")
NTFY_TOPIC_SPCHARTS_DEFAULT = "tsm-spcharts"
# "total days passed" and "streak inactive" alerts only make sense where we have
# a deep, continuous chart history. Restrict them to the three curated regions;
# every other region's history is partial and/or frozen (see stale-region skip).
SPCHARTS_RANKED_HISTORY_REGIONS = {"global", "us", "uk"}

_WARP_CLI = Path(r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


HEADLESS = _env_bool("TSM_HEADLESS", True)


def _warp_status_text() -> str:
    cli = str(_WARP_CLI) if _WARP_CLI.exists() else "warp-cli"
    try:
        status = subprocess.run([cli, "status"], timeout=5, check=False, capture_output=True, text=True)
        return "\n".join(part for part in (status.stdout, status.stderr) if part).strip()
    except Exception as e:
        return f"status unavailable: {e}"


def _warp_status_problem(status_text: str) -> str | None:
    lowered = status_text.lower()
    if "disconnected" in lowered or "not connected" in lowered:
        return "deconnecte"
    if "connected" not in lowered:
        return "deconnecte"
    if "unstable" in lowered:
        return "reseau instable"
    return None


def _warp_connect() -> None:
    cli = str(_WARP_CLI) if _WARP_CLI.exists() else "warp-cli"
    try:
        status_text = _warp_status_text()
        problem = _warp_status_problem(status_text)
        if problem is None:
            print("[WARP] deja connecte")
            return
        if "connected" in status_text.lower() and problem == "reseau instable":
            print("[WARP] connecte mais reseau instable - reconnexion du tunnel")
            subprocess.run([cli, "disconnect"], timeout=15, check=False, capture_output=True)
            time.sleep(2)
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


def _ensure_warp_still_connected() -> bool:
    status_text = _warp_status_text()
    problem = _warp_status_problem(status_text)
    if problem is None:
        return True
    print(
        f"[WARP] tunnel {problem} - reconnexion dans {WARP_RECONNECT_DELAY_SECONDS}s",
        flush=True,
    )
    time.sleep(WARP_RECONNECT_DELAY_SECONDS)
    _warp_connect()
    return _warp_status_problem(_warp_status_text()) is None


REPO_ENV_FILE = REPO_ROOT / ".env"
load_dotenv(REPO_ENV_FILE, override=False)
R2_ENV_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
WARP_RECONNECT_DELAY_SECONDS = int(os.getenv("TSM_WARP_RECONNECT_DELAY_SECONDS", "120"))
STREAMS_HISTORY_CSV = REPO_ROOT / "db" / "streams_history.csv"
ARCHIVED_STREAMS_HISTORY_CSV = REPO_ROOT / "data" / "_archive" / "original" / "db" / "streams_history.csv"
SPOTIFY_API_BASE = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
SPOTIFY_CHARTS_URL = "https://charts.spotify.com/charts/view/regional-global-daily/latest"
SPOTIFY_SESSION = CHARTS_ROOT / "global" / "tools" / "json" / "spotify_session.json"
SPOTIFY_TOKEN_TTL = 50 * 60
AVAILABILITY_RETRY_SECONDS = 10
AVAILABILITY_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_AVAILABILITY_MAX_ATTEMPTS", "0"))
AVAILABILITY_MAX_SECONDS = int(os.getenv("SPOTIFY_AVAILABILITY_MAX_SECONDS", "0"))
WATCH_MAX_SECONDS = int(os.getenv("SPOTIFY_WATCH_MAX_SECONDS", "0"))
WATCH_BASE_SECONDS = int(os.getenv("SPOTIFY_WATCH_BASE_SECONDS", "10"))
WATCH_LATE_SECONDS = int(os.getenv("SPOTIFY_WATCH_LATE_SECONDS", "10"))
WATCH_HOT_SECONDS = int(os.getenv("SPOTIFY_WATCH_HOT_SECONDS", "20"))
WATCH_ERROR_SECONDS = int(os.getenv("SPOTIFY_WATCH_ERROR_SECONDS", "10"))
RATE_LIMIT_RETRY_SECONDS = int(os.getenv("SPOTIFY_RATE_LIMIT_RETRY_SECONDS", "120"))
CARDS_POST_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_CARDS_POST_MAX_ATTEMPTS", "3"))
CARDS_POST_RETRY_SECONDS = int(os.getenv("SPOTIFY_CARDS_POST_RETRY_SECONDS", "30"))
WORLDWIDE_COLLECT_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_WORLDWIDE_COLLECT_MAX_ATTEMPTS", "3"))
WORLDWIDE_COLLECT_RETRY_SECONDS = int(os.getenv("SPOTIFY_WORLDWIDE_COLLECT_RETRY_SECONDS", "60"))
REGIONAL_POST_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_REGIONAL_POST_MAX_ATTEMPTS", "3"))
REGIONAL_POST_RETRY_SECONDS = int(os.getenv("SPOTIFY_REGIONAL_POST_RETRY_SECONDS", "30"))
PLAYWRIGHT_LAUNCH_TIMEOUT_MS = int(os.getenv("SPOTIFY_PLAYWRIGHT_LAUNCH_TIMEOUT_MS", "15000"))
PLAYWRIGHT_GOTO_TIMEOUT_MS = int(os.getenv("SPOTIFY_PLAYWRIGHT_GOTO_TIMEOUT_MS", "15000"))
PLAYWRIGHT_TOKEN_WAIT_SECONDS = int(os.getenv("SPOTIFY_PLAYWRIGHT_TOKEN_WAIT_SECONDS", "10"))
USE_PLAYWRIGHT_TOKEN = os.getenv("SPOTIFY_USE_PLAYWRIGHT_TOKEN", "0").strip().lower() in {"1", "true", "yes", "on"}
SPOTIFY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

# artists_global tourne desormais entierement de son cote (task scheduler
# dediee, collecte + posts de base + filtres female/T/Taylor/US/UK — voir
# artist_global_daily.py) ; les charts regionaux sont geres via worldwide.
COLLECT_RUNNERS: list[tuple[str, Path, list[str]]] = [
    ("worldwide", CHARTS_ROOT / "worldwide" / "daily.py", ["--force"]),
]

SPOTIFY_HISTORY_BACKFILL = REPO_ROOT / "scripts" / "backfill_spotify_charts_history.py"

CHART_AVAILABILITY: dict[str, str] = {
    "worldwide": "regional-global-daily",  # probe via le chart global
}

def _region_lock(name: str, target: date, lock_name: str) -> Path:
    return spotify_chart_dir(name, target) / lock_name


def _legacy_region_lock(name: str, target: date, lock_name: str) -> Path:
    return legacy_spotify_chart_dir(name, target) / lock_name


def _region_lock_exists(name: str, target: date, lock_name: str) -> bool:
    return _region_lock(name, target, lock_name).exists() or _legacy_region_lock(name, target, lock_name).exists()


def _r2_export_lock(target: date) -> Path:
    return run_all_charts_root(target) / "r2_exported.lock"


def _r2_export_done(target: date) -> bool:
    return _r2_export_lock(target).exists()


def _r2_export_is_fresh(target: date) -> bool:
    lock = _r2_export_lock(target)
    if not lock.exists():
        return False
    try:
        lock_mtime = lock.stat().st_mtime
    except OSError:
        return False

    watched_paths = [
        _worldwide_snapshot_path(target),
        spotify_chart_dir("global", target) / f"ts_chart_{target}.json",
        spotify_chart_dir("fr", target) / f"ts_chart_{target}.json",
        WEB_EXPORT_DATA_DIR / "charts_worldwide.json",
    ]
    watched_paths.extend((REPO_ROOT / "db").glob("charts_history_*.csv"))
    watched_paths.extend((WEB_EXPORT_DATA_DIR / "charts_discography").glob("*.json"))
    for path in watched_paths:
        try:
            if path.exists() and path.stat().st_mtime > lock_mtime:
                print(f"[INFO] R2 export stale: {path} plus recent que {lock}")
                return False
        except OSError:
            continue
    return True


def _mark_r2_exported(target: date) -> None:
    lock = _r2_export_lock(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()


def _region_data_exists(name: str, target: date) -> bool:
    day_dirs = [spotify_chart_dir(name, target), legacy_spotify_chart_dir(name, target)]
    for day_dir in day_dirs:
        if name == "worldwide":
            if (day_dir / f"ts_worldwide_{target}.json").exists():
                return True
        elif (day_dir / "ts_all_songs.csv").exists() or (day_dir / f"ts_chart_{target}.json").exists():
            return True
    return False


def _regional_chart_json_exists(name: str, target: date) -> bool:
    return any(
        (day_dir / f"ts_chart_{target}.json").exists()
        for day_dir in (spotify_chart_dir(name, target), legacy_spotify_chart_dir(name, target))
    )


def _worldwide_json_path() -> Path:
    return first_existing(WEB_EXPORT_DATA_DIR / "charts_worldwide.json", LEGACY_WEBSITE_DATA_DIR / "charts_worldwide.json")


def _worldwide_json_date() -> str | None:
    try:
        return str(json.loads(_worldwide_json_path().read_text(encoding="utf-8-sig")).get("date") or "")
    except Exception:
        return None


def _worldwide_data_ready(target: date) -> bool:
    if not _region_lock_exists("worldwide", target, "updated.lock"):
        print(f"[FAIL] worldwide updated.lock absent pour {target}: fetch incomplet, cards non postées")
        return False
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
    counts, total, _ = _worldwide_metrics(current)
    if not counts:
        return False, "snapshot mondial vide"
    return True, f"{len(counts)} songs, {total} appearances"


def _runner_args_for_run_all(name: str, fixed: list[str], forwarded: list[str], target_date: date, explicit_target_date: bool) -> list[str]:
    args = [*fixed, *forwarded]
    if not explicit_target_date:
        args.append(str(target_date))
    return args


def _already_done(
    runners: list[tuple[str, Path, list[str]]],
    target: date,
    post_parts: set[str],
) -> bool:
    return not _filter_pending_runners(runners, target, post_parts)


def _runner_done(name: str, target: date, post_parts: set[str]) -> bool:
    updated = _region_lock_exists(name, target, "updated.lock")
    data_exists = _region_data_exists(name, target)
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


def _to_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError, OverflowError):
        return None


def _build_env() -> dict[str, str]:
    load_dotenv(REPO_ENV_FILE, override=True)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["UPLOAD_TO_R2"] = "0"  # individual scripts must not upload; only export_for_web.py does
    env["CHARTS_RUN_ALL"] = "1"  # individual scripts must not git commit; run_all does it
    # Priorite de post X : les tweets charts (chart du jour, cards NEW/RE) passent
    # devant les posts streams finalize (albums, sweep) quand les deux pipelines
    # tournent en meme temps. Voir core.twitter._twitter_account_slot.
    env.setdefault("TWITTER_POST_PRIORITY", "1")
    missing = [k for k in R2_ENV_VARS if not env.get(k, "").strip()]
    if missing:
        print(f"[WARN] R2 vars manquantes: {', '.join(missing)}")
    return env


def _build_backfill_env(env: dict[str, str]) -> dict[str, str]:
    backfill_env = env.copy()
    backfill_env.setdefault("SPOTIFY_WORLDWIDE_TOTAL_CONCURRENCY", "1")
    backfill_env.setdefault("SPOTIFY_WORLDWIDE_RATE_LIMIT_MIN_SECONDS", "20")
    backfill_env.setdefault("SPOTIFY_WORLDWIDE_REQUEST_INTERVAL_SECONDS", "2.0")
    backfill_env.setdefault("SPOTIFY_WORLDWIDE_ADAPTIVE_MIN", "5")
    backfill_env.setdefault("SPOTIFY_WORLDWIDE_ADAPTIVE_MAX", "25")
    backfill_env.setdefault("SPOTIFY_WORLDWIDE_ADAPTIVE_STEP_SUCCESSES", "20")
    backfill_env.setdefault("SPOTIFY_SKIP_LATEST_FALLBACK_ON_404", "1")
    return backfill_env


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
    session_files = sorted(SPOTIFY_SESSION.parent.glob("spotify_session*.json"))
    if not session_files:
        return None
    for sf in session_files:
        cookies: dict[str, str] = {}
        try:
            session_data = json.loads(sf.read_text(encoding="utf-8-sig"))
            for cookie in session_data.get("cookies", []):
                n = str(cookie.get("name") or "")
                v = str(cookie.get("value") or "")
                if n and v:
                    cookies[n] = v
        except Exception:
            continue
        if not cookies:
            continue
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
            continue
        if token:
            label = f" ({sf.name})" if sf != SPOTIFY_SESSION else ""
            print(f"[CHECK] token Spotify recupere via HTTP direct{label}.")
            _save_bearer_to_caches(token, names)
            return token
    return None


def _load_extra_tokens_via_playwright(primary_token: str) -> list[str]:
    """Load bearer tokens from extra session files (spotify_session_2.json, etc.) via Playwright."""
    extra: list[str] = []
    api_host = SPOTIFY_API_BASE.split("//", 1)[1].split("/", 1)[0]
    for sf in sorted(SPOTIFY_SESSION.parent.glob("spotify_session*.json")):
        if sf == SPOTIFY_SESSION:
            continue
        token_holder: list[str] = []

        def _on_req(req, _th=token_holder, _host=api_host) -> None:
            if _host in req.url and not _th:
                auth = req.headers.get("authorization", "")
                if auth.startswith("Bearer "):
                    _th.append(auth[7:])

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=HEADLESS,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                    timeout=PLAYWRIGHT_LAUNCH_TIMEOUT_MS,
                )
                try:
                    ctx = browser.new_context(
                        storage_state=str(sf),
                        user_agent=SPOTIFY_UA,
                        viewport={"width": 1280, "height": 800},
                    )
                    page = ctx.new_page()
                    page.on("request", _on_req)
                    page.goto(SPOTIFY_CHARTS_URL, wait_until="networkidle", timeout=45_000)
                    deadline = time.time() + 15
                    while not token_holder and time.time() < deadline:
                        page.wait_for_timeout(300)
                finally:
                    browser.close()
        except Exception as exc:
            print(f"[CHECK] token extra {sf.name} indisponible ({str(exc).split(chr(10))[0][:80]})")
            continue

        if token_holder and token_holder[0] != primary_token:
            extra.append(token_holder[0])
            print(f"[CHECK] token extra chargé depuis {sf.name}")
    return extra


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
                headless=HEADLESS,
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


def _latest_worldwide_snapshot_date() -> date | None:
    latest: date | None = None
    for path in spotify_chart_snapshot_files("worldwide", "ts_worldwide_*.json"):
        match = re.search(r"ts_worldwide_(\d{4}-\d{2}-\d{2})\.json$", path.name)
        if not match:
            continue
        try:
            chart_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if latest is None or chart_date > latest:
            latest = chart_date
    return latest


def _default_target_date() -> date:
    yesterday = date.today() - timedelta(days=1)
    latest = _latest_worldwide_snapshot_date()
    if latest is None:
        return yesterday
    return min(latest + timedelta(days=1), yesterday)


def _extract_target_date(forwarded: list[str]) -> tuple[date, bool]:
    for value in forwarded:
        if value.startswith("--"):
            continue
        try:
            return datetime.strptime(value, "%Y-%m-%d").date(), True
        except ValueError:
            continue
    return _default_target_date(), False


def _parse_cli_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"date invalide {value!r}, attendu YYYY-MM-DD") from exc


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
                headless=HEADLESS,
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
        if resp.status_code in {401, 403}:
            return False, f"auth=HTTP {resp.status_code}", retry_after_seconds, None
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

    if resp.status_code in {401, 403}:
        retry_after = resp.headers.get("Retry-After")
        retry_after_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
        return False, f"auth=HTTP {resp.status_code}", retry_after_seconds, None

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
                try:
                    detected_date = date.fromisoformat(detected)
                except ValueError:
                    detected_date = None
                if detected_date is not None and detected_date > target and entries:
                    return True, f"HTTP 404 date, latest={detected} ({len(entries)} lignes)", None, detected_date
                return False, f"HTTP 404 date, latest pointe vers {detected}", None, None
            page_detected = _latest_chart_page_date()
            if page_detected == str(target) and entries:
                return True, f"HTTP 404 date, page latest={page_detected} ({len(entries)} lignes)", None, target
            if page_detected:
                try:
                    page_detected_date = date.fromisoformat(page_detected)
                except ValueError:
                    page_detected_date = None
                if page_detected_date is not None and page_detected_date > target and entries:
                    return True, f"HTTP 404 date, page latest={page_detected} ({len(entries)} lignes)", None, page_detected_date
                return False, f"HTTP 404 date, page latest pointe vers {page_detected}", None, None
            return False, f"HTTP 404 date, latest sans date ({len(entries)} lignes)", None, None
        retry_after = latest_resp.headers.get("Retry-After")
        retry_after_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
        if latest_resp.status_code in {401, 403}:
            return False, f"auth=HTTP {latest_resp.status_code}", retry_after_seconds, None
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
    if detail.startswith("reseau:") or detail.startswith("auth=HTTP") or detail in {"token indisponible", "HTTP 401", "HTTP 403", "HTTP 429"}:
        return error_seconds
    if elapsed > 2 * 60 * 60:
        return max(late_seconds, base_seconds)
    return base_seconds


def _wait_for_charts_available(
    runners: list[tuple[str, Path, list[str]]],
    *,
    target: date | None,
    dry_run: bool,
    allow_latest_resolution: bool = True,
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

    probe = next((n for n in ("global", "fr", "worldwide") if n in names), names[0])
    probe_chart = CHART_AVAILABILITY[probe]

    attempt = 1
    refresh_token = False
    warp_active = initial_warp_active
    consecutive_429 = 0
    started = time.monotonic()
    mode = "watch-release" if watch_release else "check"
    target_label = str(target) if target is not None else "latest"
    print(f"\n[CHECK] disponibilite Spotify pour {target_label} (via {probe}, mode {mode})")

    # Token pool : primary + tokens des sessions supplémentaires
    check_tokens: list[str] = []
    check_token_idx = 0
    check_token_exhausted = 0

    def _current_check_token() -> str:
        return check_tokens[check_token_idx] if check_tokens else ""

    def _rotate_check_token() -> bool:
        """Rotate to next token. Returns False if all exhausted."""
        nonlocal check_token_idx, check_token_exhausted
        check_token_exhausted += 1
        if check_token_exhausted >= len(check_tokens):
            return False
        check_token_idx = (check_token_idx + 1) % len(check_tokens)
        print(f"[CHECK] rotation token → {check_token_idx + 1}/{len(check_tokens)}", flush=True)
        return True

    def _reset_check_token_cycle() -> None:
        nonlocal check_token_exhausted
        check_token_exhausted = 0

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

        if warp_active and warp_on_token_fail and not _ensure_warp_still_connected():
            wait = 5
            print(f"[WARP] tunnel toujours indisponible - retry dans {wait}s", flush=True)
            time.sleep(wait)
            attempt += 1
            continue

        try:
            primary = _acquire_bearer_token(names, refresh=refresh_token, allow_stale=watch_release)
            refresh_token = False
            if not check_tokens:
                check_tokens.append(primary)
                extra = _load_extra_tokens_via_playwright(primary)
                check_tokens.extend(extra)
                print(f"[CHECK] pool de {len(check_tokens)} token(s) pour le check")
            else:
                check_tokens[0] = primary
            token = _current_check_token()
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
            if (
                target is not None
                and resolved_target is not None
                and resolved_target != target
                and not allow_latest_resolution
            ):
                raise TimeoutError(
                    f"Spotify chart indisponible pour la date explicite {target}; "
                    f"latest pointe vers {resolved_target}."
                )
            _reset_check_token_cycle()
            print(f"[CHECK] charts disponibles pour {resolved_target or target_label}")
            return warp_active, resolved_target or target
        is_network_err = detail.startswith("reseau:")
        is_rate_limited = "HTTP 429" in detail
        is_auth_err = detail.startswith("auth=HTTP")
        if is_auth_err:
            consecutive_429 = 0
            if _rotate_check_token():
                attempt += 1
                continue
            _reset_check_token_cycle()
            refresh_token = True
            check_tokens.clear()
            check_token_idx = 0
            wait = 5
            print(f"[CHECK] token Spotify refuse ({detail}) - refresh et retry dans {wait}s")
            time.sleep(wait)
            attempt += 1
            continue
        if is_rate_limited:
            consecutive_429 += 1
            if _rotate_check_token():
                attempt += 1
                continue  # retry immédiatement avec le token suivant
            _reset_check_token_cycle()
        else:
            consecutive_429 = 0
            _reset_check_token_cycle()
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
        label = "reseau" if is_network_err else ("rate limited (429)" if is_rate_limited else "chart indisponible")
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


def _run_swift_top_charts_if_ready(target_date: date, *, env: dict[str, str], verbose: bool) -> bool:
    gate_status = check_swift_top_gate(target_date, source="charts")
    if gate_status == "not_thursday":
        return False
    if gate_status == "done":
        print(f"[Swift Top] deja genere pour {target_date}")
        return False
    if gate_status == "waiting":
        print(f"[Swift Top] attente des streams pour {target_date}")
        return False

    print(f"[Swift Top] streams + charts prets pour {target_date}, generation...")
    swift_env = env.copy()
    swift_env["UPLOAD_TO_R2"] = "1"
    swift_top_100_script = REPO_ROOT / "collectors" / "billboard" / "swift_top_100.py"
    rc = _run(
        "swift-top",
        swift_top_100_script,
        ["--date", target_date.isoformat(), "--variant", "all"],
        dry_run=False,
        env=swift_env,
        verbose=verbose,
    )
    if rc != 0:
        print(f"[Swift Top] generation echouee ({rc})")
        return False
    mark_swift_top_done(target_date, source="charts")
    return True


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


def _probe_latest_available_date(
    runners: list[tuple[str, Path, list[str]]],
    *,
    allow_stale: bool,
) -> date | None:
    names = [name for name, _, _ in runners if name in CHART_AVAILABILITY]
    if not names:
        return None
    probe = next((n for n in ("global", "fr", "worldwide") if n in names), names[0])
    try:
        token = _acquire_bearer_token(names, allow_stale=allow_stale)
        ok, detail, _retry_after, resolved = _chart_available(CHART_AVAILABILITY[probe], None, token)
    except Exception as exc:
        short = str(exc).split("\n")[0][:120]
        print(f"[CHECK] latest Spotify indisponible ({short})")
        return None
    print(f"[CHECK] latest Spotify: {probe}={detail}")
    return resolved if ok else None


def _date_span(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _collect_data_only_dates(
    dates: list[date],
    *,
    force: bool,
    dry_run: bool,
    env: dict[str, str],
    verbose: bool,
) -> list[tuple[str, int]]:
    failures: list[tuple[str, int]] = []
    runners = [
        (name, script, fixed + ["--no-post"])
        for name, script, fixed in COLLECT_RUNNERS
        if name == "worldwide"
    ]
    for target in dates:
        pending = runners if force or dry_run else _filter_pending_runners(runners, target, set())
        if not pending:
            print(f"[CATCHUP] {target}: deja collecte")
            continue
        print(f"\n[CATCHUP] collecte data-only pour {target}")
        failures_for_date = _run_parallel(
            pending,
            forwarded=[],
            target_date=target,
            explicit_target_date=False,
            dry_run=dry_run,
            env=env,
            verbose=verbose,
        )
        failures.extend((f"{target}/{name}", rc) for name, rc in failures_for_date)
        if failures_for_date:
            continue
        if not dry_run:
            ok, detail = _validate_worldwide_snapshot(target)
            print(f"[CHECK] validation worldwide {target}: {detail}")
            if not ok:
                failures.append((f"{target}/worldwide-validation", 1))
    return failures


def _verify_regional_posts(
    target_date: date,
    post_parts: set[str],
    *,
    force: bool,
    env: dict[str, str],
    verbose: bool,
) -> list[tuple[str, int]]:
    failures: list[tuple[str, int]] = []
    scripts = {
        "global": CHARTS_ROOT / "global" / "daily.py",
        "fr": CHARTS_ROOT / "fr" / "daily.py",
        "us": CHARTS_ROOT / "us" / "daily.py",
    }
    requested = [name for name in ("global", "fr", "us") if name in post_parts]
    if not requested:
        return failures

    print("\n[CHECK] verification posts Global/FR/US...")
    for name in requested:
        if _region_lock_exists(name, target_date, "posted.lock") and not force:
            print(f"[SKIP] post {name} deja fait pour {target_date}")
            continue
        if not _regional_chart_json_exists(name, target_date):
            print(f"[FAIL] post {name}: ts_chart_{target_date}.json absent")
            failures.append((f"{name}-post-data", 1))
            continue
        script = scripts[name]
        args = ["--post-only", str(target_date)]
        if force:
            args.append("--force")
        rc = _run(
            f"{name}-post",
            script,
            args,
            dry_run=False,
            env=env,
            verbose=verbose,
        )
        for attempt in range(2, REGIONAL_POST_MAX_ATTEMPTS + 1):
            if rc == 0:
                break
            print(
                f"[WARN] {name}-post en echec, nouvelle tentative {attempt}/{REGIONAL_POST_MAX_ATTEMPTS} "
                f"dans {REGIONAL_POST_RETRY_SECONDS}s..."
            )
            time.sleep(REGIONAL_POST_RETRY_SECONDS)
            rc = _run(
                f"{name}-post",
                script,
                args,
                dry_run=False,
                env=env,
                verbose=verbose,
            )
        if rc != 0:
            failures.append((f"{name}-post", rc))
    return failures


def _missing_regional_chart_jsons(names: set[str], target: date) -> list[str]:
    return sorted(name for name in names if not _regional_chart_json_exists(name, target))


def _global_chart_json_path(target_date: date) -> Path | None:
    for day_dir in (spotify_chart_dir("global", target_date), legacy_spotify_chart_dir("global", target_date)):
        path = day_dir / f"ts_chart_{target_date}.json"
        if path.exists():
            return path
    return None


def _require_global_chart_data_before_cards(target_date: date) -> bool:
    if _global_chart_json_path(target_date) is not None:
        return True
    print(f"[SKIP] cards bloquees: Global chart pas encore collecte pour {target_date} (global/ts_chart_{target_date}.json absent)")
    return False


def _load_json_file(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(f"[WARN] JSON illisible {path}: {exc}")
        return None


def _notify_spcharts_topic(env: dict[str, str]) -> str:
    return env.get("NTFY_TOPIC_SPCHARTS", "").strip() or NTFY_TOPIC_SPCHARTS_DEFAULT


def _song_key(row: dict) -> str:
    track_id = str(row.get("track_id") or row.get("_raw_track_id") or "").strip()
    if track_id:
        return f"track:{track_id}"
    title = re.sub(r"[^a-z0-9]+", " ", str(row.get("song_name") or "").lower()).strip()
    return f"title:{title}" if title else ""


def _song_title(row: dict) -> str:
    return str(row.get("song_name") or row.get("title") or row.get("track_name") or "Unknown song").strip()


def _load_discography_region(path: Path) -> dict | None:
    data = _load_json_file(path)
    if isinstance(data, dict) and isinstance(data.get("songs"), list):
        return data
    return None


def _format_spcharts_alerts(alerts: list[str], *, limit: int = 12) -> str:
    shown = alerts[:limit]
    if len(alerts) > limit:
        shown.append(f"... +{len(alerts) - limit} more")
    return "\n".join(shown)


def _notify_spcharts_log(
    env: dict[str, str],
    message: str,
    *,
    title: str,
    tags: str = "spotify,information_source",
    priority: str = "default",
) -> None:
    _notify(_notify_spcharts_topic(env), message, title=title, tags=tags, priority=priority)


def _snapshot_chart_rows(path: Path) -> list[dict]:
    data = _load_json_file(path)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("entries") or data.get("tracks") or data.get("songs") or []
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _spcharts_snapshot_song_counts(target_date: date) -> tuple[int, int, int] | None:
    worldwide = _load_worldwide_snapshot(target_date)
    by_track = worldwide.get("by_track", {}) if isinstance(worldwide, dict) else {}
    if isinstance(by_track, dict) and by_track:
        region_song_rows = 0
        regions = set()
        for entries in by_track.values():
            if not isinstance(entries, list):
                continue
            region_song_rows += len([entry for entry in entries if isinstance(entry, dict)])
            for entry in entries:
                if isinstance(entry, dict) and entry.get("country"):
                    regions.add(str(entry.get("country")))
        return len(by_track), region_song_rows, len(regions)

    unique_song_keys: set[str] = set()
    region_song_rows = 0
    region_count = 0
    root = run_all_charts_root(target_date)
    if root.exists():
        for region_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            if region_dir.name == "worldwide":
                continue
            chart_path = region_dir / f"ts_chart_{target_date}.json"
            if not chart_path.exists():
                continue
            rows = _snapshot_chart_rows(chart_path)
            if not rows:
                continue
            region_count += 1
            for row in rows:
                key = _song_key(row)
                if key:
                    unique_song_keys.add(key)
                region_song_rows += 1

    if region_count == 0:
        return None
    return len(unique_song_keys), region_song_rows, region_count


def _spcharts_finished_message(target_date: date, total: str, failures: list[tuple[str, int]]) -> str:
    lines = [
        f"Date: {target_date.isoformat()}",
        f"Duration: {total}",
    ]
    counts = _spcharts_snapshot_song_counts(target_date)
    if counts is not None:
        unique_songs, region_song_rows, region_count = counts
        lines.append(f"Songs charted in snapshot: {unique_songs:,}")
        lines.append(f"Region-song entries: {region_song_rows:,} across {region_count} regions")
    if failures:
        lines.append("Failures: " + ", ".join(f"{name} ({rc})" for name, rc in failures))
    return "\n".join(lines)


def _collect_spcharts_total_days_overtakes(current_rows: list[dict], previous_rows: list[dict]) -> list[str]:
    previous_by_key = {
        key: row
        for row in previous_rows
        if isinstance(row, dict)
        for key in [_song_key(row)]
        if key
    }
    current = []
    for row in current_rows:
        if not isinstance(row, dict):
            continue
        key = _song_key(row)
        previous = previous_by_key.get(key)
        current_days = _to_int(row.get("total_days"))
        previous_days = _to_int(previous.get("total_days")) if previous else None
        if not key or current_days is None or previous_days is None:
            continue
        current.append((key, row, current_days, previous_days))

    alerts = []
    for a_key, a_row, a_days, a_previous_days in current:
        for b_key, b_row, b_days, b_previous_days in current:
            if a_key == b_key:
                continue
            if a_previous_days <= b_previous_days and a_days > b_days:
                alerts.append(
                    f"{_song_title(a_row)} passed {_song_title(b_row)} in total chart days: "
                    f"{a_days}d vs {b_days}d"
                )
    return sorted(set(alerts))


def _collect_spcharts_streak_deactivations(current_rows: list[dict], previous_rows: list[dict]) -> list[str]:
    current_by_key = {
        key: row
        for row in current_rows
        if isinstance(row, dict)
        for key in [_song_key(row)]
        if key
    }
    alerts = []
    for previous in previous_rows:
        if not isinstance(previous, dict) or not previous.get("longest_streak_active"):
            continue
        key = _song_key(previous)
        current = current_by_key.get(key)
        if not current or current.get("longest_streak_active"):
            continue
        previous_streak = _to_int(previous.get("longest_streak")) or 0
        current_streak = _to_int(current.get("longest_streak")) or previous_streak
        if previous_streak <= 0:
            continue
        alerts.append(
            f"{_song_title(current or previous)} streak became inactive "
            f"(longest streak: {current_streak}d)"
        )
    return sorted(set(alerts))


def _collect_spcharts_peak_rank_records(current_rows: list[dict], previous_rows: list[dict]) -> list[str]:
    previous_by_key = {
        key: row
        for row in previous_rows
        if isinstance(row, dict)
        for key in [_song_key(row)]
        if key
    }
    alerts = []
    for row in current_rows:
        if not isinstance(row, dict):
            continue
        previous = previous_by_key.get(_song_key(row))
        if not previous:
            continue
        current_peak = _to_int(row.get("peak_rank"))
        previous_peak = _to_int(previous.get("peak_rank"))
        if current_peak is None or previous_peak is None or current_peak >= previous_peak:
            continue
        alerts.append(f"{_song_title(row)} reached a new peak rank: #{current_peak} (was #{previous_peak})")
    return sorted(set(alerts))


# NOTE: a "new peak streams" alert used to live here, but our chart history does
# not reach back to 2017, so for catalog songs the stored peak is only a
# "best since tracking started" value, not an all-time record. Removed 2026-08-27.


def _notify_spcharts_events(env: dict[str, str]) -> None:
    discography_dir = WEB_EXPORT_DATA_DIR / "charts_discography"
    if not discography_dir.exists():
        print(f"[spcharts_notify] skip: {discography_dir} absent")
        return

    total_days_alerts: list[str] = []
    streak_alerts: list[str] = []
    peak_rank_alerts: list[str] = []
    for current_path in sorted(discography_dir.glob("*.json")):
        region = current_path.stem
        if region == "index" or region.endswith("_previous"):
            continue
        previous_path = current_path.with_name(f"{region}_previous.json")
        if not previous_path.exists():
            continue
        current = _load_discography_region(current_path)
        previous = _load_discography_region(previous_path)
        if not current or not previous:
            continue
        current_rows = [row for row in current.get("songs", []) if isinstance(row, dict)]
        previous_rows = [row for row in previous.get("songs", []) if isinstance(row, dict)]
        region_label = region.upper()
        # total-days overtakes and streak deactivations rely on a full continuous
        # history, which we only have for the curated regions.
        if region in SPCHARTS_RANKED_HISTORY_REGIONS:
            total_days_alerts.extend(
                f"[{region_label}] {line}"
                for line in _collect_spcharts_total_days_overtakes(current_rows, previous_rows)
            )
            streak_alerts.extend(
                f"[{region_label}] {line}"
                for line in _collect_spcharts_streak_deactivations(current_rows, previous_rows)
            )
        peak_rank_alerts.extend(
            f"[{region_label}] {line}"
            for line in _collect_spcharts_peak_rank_records(current_rows, previous_rows)
        )

    topic = _notify_spcharts_topic(env)
    if total_days_alerts:
        _notify(
            topic,
            _format_spcharts_alerts(total_days_alerts),
            title="Spotify Charts - total days passed",
            tags="spotify,chart_with_upwards_trend",
        )
        print(f"[spcharts_notify] total-days alerts: {len(total_days_alerts)}")
    if streak_alerts:
        _notify(
            topic,
            _format_spcharts_alerts(streak_alerts),
            title="Spotify Charts - streak inactive",
            tags="spotify,warning",
            priority="high",
        )
        print(f"[spcharts_notify] streak alerts: {len(streak_alerts)}")
    if peak_rank_alerts:
        _notify(
            topic,
            _format_spcharts_alerts(peak_rank_alerts),
            title="Spotify Charts - new peak rank",
            tags="spotify,trophy",
        )
        print(f"[spcharts_notify] peak-rank alerts: {len(peak_rank_alerts)}")
    if not total_days_alerts and not streak_alerts and not peak_rank_alerts:
        print("[spcharts_notify] no alerts")


def _global_rows_for_cards(target_date: date) -> list[dict]:
    path = _global_chart_json_path(target_date)
    if path is None:
        return []
    data = _load_json_file(path)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("entries") or data.get("tracks") or []
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _global_worldwide_entry(row: dict) -> dict | None:
    track_id = str(row.get("track_id") or row.get("_track_id_uri") or "").strip()
    if not track_id:
        return None
    return {
        "country": "global",
        "country_name": "Global",
        "rank": row.get("rank"),
        "previous_rank": row.get("previous_rank"),
        "rank_change": row.get("rank_change"),
        "streams": row.get("streams"),
        "peak_rank": row.get("peak_rank"),
        "total_days": row.get("total_days"),
        "streak": row.get("streak"),
        "is_new": bool(row.get("is_new")),
        "is_re_entry": bool(row.get("is_re_entry")),
        "movement": row.get("movement"),
        "stream_change": row.get("stream_change"),
        "stream_change_pct": row.get("stream_change_pct"),
        "weekly_stream_change": row.get("weekly_stream_change"),
        "weekly_stream_change_pct": row.get("weekly_stream_change_pct"),
    }


def _worldwide_source_for_global_enrichment(target_date: date) -> tuple[Path, dict] | None:
    candidates = [
        _worldwide_snapshot_path(target_date),
        legacy_spotify_chart_dir("worldwide", target_date) / f"ts_worldwide_{target_date}.json",
        WEB_EXPORT_DATA_DIR / "charts_worldwide.json",
        LEGACY_WEBSITE_DATA_DIR / "charts_worldwide.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        data = _load_json_file(path)
        if isinstance(data, dict) and str(data.get("date") or "") == str(target_date) and isinstance(data.get("by_track"), dict):
            return path, data
    return None


def _ensure_global_entries_in_worldwide_snapshot(target_date: date) -> bool:
    source = _worldwide_source_for_global_enrichment(target_date)
    if source is None:
        print(f"[FAIL] snapshot worldwide invalide/absent pour enrichissement Global: {target_date}")
        return False
    source_path, data = source

    changed = 0
    by_track = data["by_track"]
    for row in _global_rows_for_cards(target_date):
        track_id = str(row.get("track_id") or row.get("_track_id_uri") or "").strip()
        entry = _global_worldwide_entry(row)
        if not track_id or entry is None:
            continue
        entries = by_track.setdefault(track_id, [])
        if not isinstance(entries, list):
            print(f"[FAIL] snapshot worldwide invalide pour track {track_id}: entries non-liste")
            return False
        previous_global = [
            item for item in entries
            if isinstance(item, dict) and str(item.get("country") or "").lower() in {"global", "glob"}
        ]
        entries[:] = [
            item for item in entries
            if not (isinstance(item, dict) and str(item.get("country") or "").lower() in {"global", "glob"})
        ]
        entries.insert(0, entry)
        if previous_global != [entry]:
            changed += 1

    if changed:
        snapshot_path = _worldwide_snapshot_path(target_date)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_path = WEB_EXPORT_DATA_DIR / "charts_worldwide.json"
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        latest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"[INFO] snapshot worldwide enrichi avec Global pour {target_date}: "
            f"{changed} track(s) depuis {source_path.name}"
        )
    return True


def _ensure_card_regional_data(target_date: date, *, env: dict[str, str], verbose: bool) -> bool:
    required_regions = {"global", "us"}
    missing = _missing_regional_chart_jsons(required_regions, target_date)
    if not missing:
        return True

    print(
        f"[CHECK] donnees regionales requises pour cards absentes ({', '.join(missing)}) "
        f"pour {target_date}; regeneration data-only via worldwide..."
    )
    rc_worldwide = _run(
        "worldwide-regional-data",
        CHARTS_ROOT / "worldwide" / "daily.py",
        ["--no-post", str(target_date)],
        dry_run=False,
        env=env,
        verbose=verbose,
    )
    if rc_worldwide != 0:
        print(f"[FAIL] worldwide data-only requis avant cards a echoue ({rc_worldwide})")
        return False

    missing = _missing_regional_chart_jsons(required_regions, target_date)
    if missing:
        print(
            f"[FAIL] cards bloquees: ts_chart_{target_date}.json absent pour "
            f"{', '.join(missing)} apres regeneration worldwide"
        )
        return False
    return True


def _post_priority_global_cards(
    target_date: date,
    *,
    force: bool,
    env: dict[str, str],
    verbose: bool,
) -> tuple[str, int] | None:
    args = [str(target_date), "--post-worldwide"]
    if force:
        args.append("--force")
    rc = _run(
        "priority-global-highlights-worldwide",
        CHARTS_ROOT / "worldwide" / "tools" / "scripts" / "post_global_new_releases.py",
        args,
        dry_run=False,
        env=env,
        verbose=verbose,
    )
    return None if rc == 0 else ("priority-global-highlights-worldwide", rc)


def _verify_multi_song_regional_posts(
    target_date: date,
    post_parts: set[str],
    *,
    force: bool,
    env: dict[str, str],
    verbose: bool,
) -> list[tuple[str, int]]:
    if "regions" not in post_parts:
        return []
    if not _load_worldwide_snapshot(target_date):
        print(f"[FAIL] posts multi-regions: snapshot worldwide absent pour {target_date}")
        return [("regions-post-data", 1)]

    print("\n[CHECK] verification posts multi-regions...")
    args = [str(target_date), "--post-multi-song-regions-only"]
    if force:
        args.append("--force")
    rc = _run(
        "regions-post",
        CHARTS_ROOT / "worldwide" / "daily.py",
        args,
        dry_run=False,
        env=env,
        verbose=verbose,
    )
    return [] if rc == 0 else [("regions-post", rc)]


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
    return ok, 0


_ALL_POST_PARTS = {"global", "fr", "us", "cards"}
_PAUSED_POST_PARTS = {"fr"}
_DEFAULT_POST_PARTS = _ALL_POST_PARTS - _PAUSED_POST_PARTS
_EXTRA_POST_PARTS = {"best-day-since", "regions"}  # non inclus dans le défaut, à passer explicitement via --post


def _streams_history_path() -> Path:
    return STREAMS_HISTORY_CSV if STREAMS_HISTORY_CSV.exists() else ARCHIVED_STREAMS_HISTORY_CSV


def _streams_history_has_date(target: date) -> bool:
    path = _streams_history_path()
    if not path.exists():
        return False
    target_key = target.isoformat()
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if (row.get("date") or "").strip() == target_key:
                    return True
    except Exception as exc:
        print(f"[WARN] streams_history illisible pour best-day-since: {exc}")
    return False


def _run_best_day_since_post(
    target_date: date,
    post_parts: set[str],
    *,
    force: bool,
    env: dict[str, str],
    verbose: bool,
) -> tuple[str, int] | None:
    if "best-day-since" not in post_parts:
        return None
    if not _streams_history_has_date(target_date):
        print(f"\n[SKIP] best-day-since: streams_history ne contient pas {target_date}")
        return None

    print("\n[PHASE4] best-day-since...")
    script = REPO_ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts" / "post_best_day_since_twitter.py"
    args = [str(target_date), "--limit", "3"]
    if force:
        args.append("--force")
    rc = _run(
        "best-day",
        script,
        args,
        dry_run=False,
        env=env,
        verbose=verbose,
    )
    if rc != 0:
        return ("best-day-since", rc)
    return None


def _run_backfill(args, env: dict[str, str]) -> int:
    env = _build_backfill_env(env)
    start = args.backfill_from or "2017-01-01"
    end = args.backfill_to or str(date.today() - timedelta(days=1))
    cmd = [
        sys.executable,
        str(SPOTIFY_HISTORY_BACKFILL),
        "--start",
        start,
        "--end",
        end,
        "--workers",
        str(args.backfill_workers),
    ]
    if args.force:
        cmd.append("--refetch-done")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.backfill_upload_r2:
        cmd.append("--upload-r2")
    print(f"[BACKFILL] historique Spotify Charts no-post: {start} -> {end}")
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env).returncode



def main() -> int:
    parser = argparse.ArgumentParser(description="Run all Spotify chart daily scripts.")
    parser.add_argument("--date", type=_parse_cli_date, metavar="YYYY-MM-DD", help="Date Spotify Charts a traiter.")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--post",
        nargs="+",
        choices=sorted(_ALL_POST_PARTS | _EXTRA_POST_PARTS),
        metavar="PART",
        default=None,
        help=(
            "Parties à poster sur Twitter: cards, fr, global, us (défaut: toutes sauf fr, en pause). "
            "Extras non inclus par défaut (à passer explicitement): best-day-since, regions. "
            "Exemple: --post global fr"
        ),
    )
    parser.add_argument("--no-post", action="store_true", help="Désactive tout le posting Twitter.")
    parser.add_argument("--force", action="store_true", help="Relance la collecte meme si les donnees existent deja.")
    parser.add_argument("--force-cards", action="store_true", help="Force la regeneration des cards worldwide.")
    parser.add_argument("--skip-uk", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-verbose", dest="verbose", action="store_false", help="Masque la sortie complète des scripts.")
    parser.set_defaults(verbose=True)
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
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Mode rattrapage historique newest-first: hier -> 2017-01-01 par defaut, sans poster, avec reprise JSON.",
    )
    parser.add_argument(
        "--backfill-all",
        action="store_true",
        help="Alias tolere: le backfill complet est deja le comportement par defaut en newest-first.",
    )
    parser.add_argument("--backfill-from", metavar="DATE", help="Borne ancienne du rattrapage (YYYY-MM-DD, defaut: 2017-01-01).")
    parser.add_argument("--backfill-to", "--to-date", "--from-date", dest="backfill_to", metavar="DATE", help="Point de depart recent du rattrapage newest-first (YYYY-MM-DD, defaut: hier).")
    parser.add_argument("--backfill-workers", type=int, default=1, help="Nombre de dates Spotify Charts a collecter en parallele (defaut: 1).")
    parser.add_argument(
        "--backfill-upload-r2",
        action="store_true",
        help=(
            "Apres le backfill et le sync local, uploader les donnees charts vers R2 "
            "(scripts/r2.py --charts-only) pour que la prod reflete le rattrapage. "
            "Off par defaut: ecriture reseau/prod, opt-in explicite."
        ),
    )
    args, passthrough = parser.parse_known_args()

    forwarded = list(passthrough)
    if args.date is not None:
        forwarded = ["--date", args.date.isoformat(), *forwarded]

    if args.backfill:
        return _run_backfill(args, _build_env())

    # Détermine quelles parties postent sur Twitter
    if args.no_post:
        post_parts: set[str] = set()
        collect_parts = set(args.post) if args.post is not None else set(_DEFAULT_POST_PARTS)
    elif args.post is not None:
        post_parts = set(args.post)
        collect_parts = set(post_parts)
    else:
        post_parts = set(_DEFAULT_POST_PARTS)
        collect_parts = set(post_parts)

    if "cards" in post_parts:
        # Poste la card highlight NEW/RE du chart Global (badge via comp/chart_card.py) en
        # tache de fond des que worldwide/daily.py a fetch la region "global" — sans ce flag
        # elle ne se declenche jamais dans un run quotidien normal (le filet "catchup" plus
        # bas exige not ran_collect, jamais vrai un jour ou la collecte tourne).
        forwarded.append("--post-priority-global-new")

    if "us" in post_parts:
        # US poste des que sa collecte prioritaire est ecrite (Phase 1 de worldwide/daily.py),
        # sans attendre la fin des ~75 autres regions worldwide (Phase 2) — meme mecanisme deja
        # cable pour "global" ci-dessus, demande explicite (2026-08-17) pour ne plus faire
        # attendre le post US derriere tout le reste de la collecte worldwide. daily.py accepte
        # deja --post-priority-region {global,fr,us} pour ca (etait juste jamais forwarde ici).
        # _verify_regional_posts plus bas reste le filet de secours (retry) si ce post anticipe
        # echoue — protege par le meme posted.lock, donc pas de double-post possible.
        forwarded.extend(["--post-priority-region", "us"])

    paused_post_parts = post_parts & _PAUSED_POST_PARTS
    if paused_post_parts:
        paused = ", ".join(sorted(paused_post_parts))
        print(f"[SKIP] posts en pause dans run_all_charts: {paused}")
        post_parts -= paused_post_parts
        collect_parts -= paused_post_parts

    started = time.perf_counter()
    env = _build_env()

    collect_runners = []
    for name, script, fixed in COLLECT_RUNNERS:
        if name == "worldwide":
            # run_all garde la collecte et le posting separes pour eviter les doublons:
            # worldwide ecrit les donnees, puis les phases ci-dessous postent une seule fois.
            extra = ["--no-post"]
        else:
            extra = []
        collect_runners.append((name, script, fixed + extra))

    target_date, _explicit_target_date = _extract_target_date(forwarded)

    # Si on ne poste que les cards / best-day-since, pas besoin de collecter.
    # Les cards lisent le snapshot worldwide existant; best-day-since lit streams_history.csv.
    needs_collect = bool(collect_parts - {"cards", "best-day-since"}) and bool(collect_runners)

    failures: list[tuple[str, int]] = []
    ran_collect = False
    worldwide_ready_for_final_sync = False

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
                    allow_latest_resolution=not _explicit_target_date,
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
                _notify_spcharts_log(
                    env,
                    f"Spotify Charts page available for {target_date.isoformat()}",
                    title="Spotify Charts - page available",
                    tags="spotify,white_check_mark",
                )
            except TimeoutError as e:
                print(f"[FAIL] {e}")
                _notify_spcharts_log(
                    env,
                    f"Spotify Charts availability check failed for {target_date.isoformat()}:\n{e}",
                    title="Spotify Charts - script error",
                    tags="spotify,x",
                    priority="high",
                )
                if warp_active:
                    _warp_disconnect()
                return 1

            if not _explicit_target_date and not args.dry_run:
                latest_available = _probe_latest_available_date(
                    collect_runners,
                    allow_stale=args.watch_release,
                )
                if latest_available is not None:
                    latest_available = min(latest_available, date.today() - timedelta(days=1))
                if latest_available is not None and latest_available > target_date:
                    catchup_dates = _date_span(target_date, latest_available - timedelta(days=1))
                    print(
                        "[CATCHUP] Spotify a deja publie "
                        f"{latest_available}; rattrapage no-post: "
                        f"{', '.join(str(d) for d in catchup_dates)}"
                    )
                    catchup_failures = _collect_data_only_dates(
                        catchup_dates,
                        force=args.force,
                        dry_run=args.dry_run,
                        env=env,
                        verbose=args.verbose,
                    )
                    if catchup_failures:
                        failures.extend(catchup_failures)
                        if args.stop_on_error:
                            print(f"[FAIL] stop-on-error — {_fmt(time.perf_counter() - started)}")
                            total = _fmt(time.perf_counter() - started)
                            _notify_spcharts_log(
                                env,
                                _spcharts_finished_message(target_date, total, failures),
                                title="Spotify Charts - script error",
                                tags="spotify,x",
                                priority="high",
                            )
                            if warp_active:
                                _warp_disconnect()
                            return 1
                    target_date = latest_available
                    collect_runners = _filter_pending_runners(original_collect_runners, target_date, post_parts)
                    if collect_runners:
                        print(
                            f"[CHECK] collecte finale avec posts pour {target_date}: "
                            f"{', '.join(n for n, _, _ in collect_runners)}"
                        )
                    else:
                        _print_already_done(original_collect_runners, target_date, post_parts)
                        collect_runners = original_collect_runners

            names_str = ", ".join(n for n, _, _ in collect_runners)
            print(f"\n[PHASE1] collecte en parallèle: {names_str}")
            t_phase1 = time.perf_counter()
            phase1_failures = _run_parallel(
                collect_runners,
                forwarded=forwarded,
                target_date=target_date,
                explicit_target_date=_explicit_target_date,
                dry_run=args.dry_run,
                env=env,
                verbose=args.verbose,
            )
            failures.extend(phase1_failures)
            ran_collect = True
            print(f"[PHASE1] collecte terminée ({_fmt(time.perf_counter() - t_phase1)})")

            if not args.dry_run and "worldwide" in {n for n, _ in phase1_failures}:
                worldwide_runner = [r for r in collect_runners if r[0] == "worldwide"]
                for attempt in range(2, WORLDWIDE_COLLECT_MAX_ATTEMPTS + 1):
                    print(
                        f"[WARN] worldwide: collecte plantee, nouvelle tentative "
                        f"{attempt}/{WORLDWIDE_COLLECT_MAX_ATTEMPTS} dans {WORLDWIDE_COLLECT_RETRY_SECONDS}s..."
                    )
                    time.sleep(WORLDWIDE_COLLECT_RETRY_SECONDS)
                    retry_failures = _run_parallel(
                        worldwide_runner,
                        forwarded=forwarded,
                        target_date=target_date,
                        explicit_target_date=_explicit_target_date,
                        dry_run=args.dry_run,
                        env=env,
                        verbose=args.verbose,
                    )
                    if not retry_failures:
                        phase1_failures = [f for f in phase1_failures if f[0] != "worldwide"]
                        failures = [f for f in failures if f[0] != "worldwide"]
                        break
                    print(f"[WARN] worldwide: toujours en echec (tentative {attempt})")

            if phase1_failures:
                failed_names = {n for n, _ in phase1_failures}
                print(f"[WARN] Echecs collecte: {', '.join(failed_names)}")
                if args.stop_on_error:
                    print(f"[FAIL] stop-on-error — {_fmt(time.perf_counter() - started)}")
                    total = _fmt(time.perf_counter() - started)
                    _notify_spcharts_log(
                        env,
                        _spcharts_finished_message(target_date, total, failures),
                        title="Spotify Charts - script error",
                        tags="spotify,x",
                        priority="high",
                    )
                    if warp_active:
                        _warp_disconnect()
                    return 1
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
                else:
                    worldwide_ready_for_final_sync = True
            if warp_active:
                _warp_disconnect()

    should_generate_cards = "cards" in post_parts or args.force_cards or (args.no_post and args.force)
    should_post_cards = "cards" in post_parts
    regional_post_failures: list[tuple[str, int]] = []
    priority_global_cards_done = False

    if not args.dry_run and should_generate_cards and not failures:
        if not _worldwide_data_ready(target_date):
            failures.append(("cards-data", 1))
        elif not _ensure_card_regional_data(target_date, env=env, verbose=args.verbose):
            failures.append(("cards-regional-data", 1))
        elif not _require_global_chart_data_before_cards(target_date):
            should_generate_cards = False
            should_post_cards = False
        elif not _ensure_global_entries_in_worldwide_snapshot(target_date):
            failures.append(("cards-global-data", 1))

    # Les priority cards Global (NEW/RE) restent prioritaires pour le post,
    # mais seulement apres collecte effective du chart Global.
    if not args.dry_run and not args.no_post and should_post_cards and "global" in post_parts and not failures:
        priority_failure = _post_priority_global_cards(
            target_date,
            force=args.force_cards or args.force,
            env=env,
            verbose=args.verbose,
        )
        priority_global_cards_done = True
        if priority_failure:
            failures.append(priority_failure)

    if not args.dry_run and not args.no_post:
        regional_post_failures = _verify_regional_posts(
            target_date,
            post_parts,
            force=args.force,
            env=env,
            verbose=args.verbose,
        )
        regional_post_failures.extend(_verify_multi_song_regional_posts(
            target_date,
            post_parts,
            force=args.force,
            env=env,
            verbose=args.verbose,
        ))
        if regional_post_failures:
            failed_names = ", ".join(n for n, _ in regional_post_failures)
            print(f"[WARN] posts regionaux echoues, suite du run maintenue: {failed_names}")

    if not args.dry_run and should_generate_cards and not failures:
        if should_post_cards:
            if priority_global_cards_done:
                print("\n[PHASE3] worldwide cards priority Global highlights deja postees plus haut, skip")
            else:
                print("\n[PHASE3] publication des worldwide cards priority Global highlights...")
                priority_failure = _post_priority_global_cards(
                    target_date,
                    force=args.force_cards or args.force,
                    env=env,
                    verbose=args.verbose,
                )
                priority_global_cards_done = True
                if priority_failure:
                    failures.append(priority_failure)

            if not failures and not ran_collect:
                global_new_args = [str(target_date), "--post"]
                if args.force_cards or args.force:
                    global_new_args.append("--force")
                rc_global_new = _run(
                    "priority-global-highlights-catchup",
                    CHARTS_ROOT / "worldwide" / "tools" / "scripts" / "post_global_new_releases.py",
                    global_new_args,
                    dry_run=False,
                    env=env,
                    verbose=args.verbose,
                )
                if rc_global_new != 0:
                    failures.append(("priority-global-highlights-catchup", rc_global_new))

    if not args.dry_run and should_generate_cards and not failures:
        if should_post_cards:
            print("\n[PHASE3] generation et publication des card images worldwide...")
        else:
            print("\n[PHASE3] generation des card images worldwide (no-post)...")
        # Include every worldwide charting song; the card script posts them as one thread.
        cards_args = [str(target_date), "--min-countries", "1"]
        if should_post_cards:
            cards_args.append("--post")
        if args.force_cards or args.force:
            cards_args.append("--force")
        cards_script = CHARTS_ROOT / "worldwide" / "tools" / "scripts" / "generate_card_images.py"
        rc_cards = _run("cards", cards_script, cards_args, dry_run=False, env=env, verbose=args.verbose)
        for attempt in range(2, CARDS_POST_MAX_ATTEMPTS + 1):
            if rc_cards == 0:
                break
            print(f"[WARN] cards en echec, nouvelle tentative {attempt}/{CARDS_POST_MAX_ATTEMPTS} dans {CARDS_POST_RETRY_SECONDS}s...")
            time.sleep(CARDS_POST_RETRY_SECONDS)
            retry_args = cards_args if attempt < CARDS_POST_MAX_ATTEMPTS else [*cards_args, "--force"]
            rc_cards = _run("cards", cards_script, retry_args, dry_run=False, env=env, verbose=args.verbose)
        if rc_cards != 0:
            failures.append(("cards", rc_cards))

    if not args.dry_run and not args.no_post and not failures:
        best_day_failure = _run_best_day_since_post(
            target_date,
            post_parts,
            force=args.force,
            env=env,
            verbose=args.verbose,
        )
        if best_day_failure:
            failures.append(best_day_failure)

    if not args.dry_run and needs_collect and worldwide_ready_for_final_sync:
        print("\n[FINAL] sync Spotify country chart history for all worldwide regions...")
        rc_sync = _run(
            "sync-country-charts",
            REPO_ROOT / "scripts" / "sync_spotify_country_charts_from_worldwide.py",
            [],
            dry_run=False,
            env=env,
            verbose=args.verbose,
        )
        if rc_sync != 0:
            failures.append(("sync-country-charts", rc_sync))
        else:
            rc_discography = _run(
                "build-country-discography",
                REPO_ROOT / "scripts" / "build_spotify_chart_discography.py",
                [],
                dry_run=False,
                env=env,
                verbose=args.verbose,
            )
            if rc_discography != 0:
                failures.append(("build-country-discography", rc_discography))
            else:
                try:
                    _notify_spcharts_events(env)
                except Exception as exc:
                    print(f"[spcharts_notify] failed: {exc}")

        if not failures:
            if _r2_export_is_fresh(target_date) and not args.force:
                print(f"\n[FINAL] upload R2 charts-only deja fait pour {target_date} (r2_exported.lock), skip")
            else:
                print("\n[FINAL] upload R2 charts-only...")
                rc_export = _run(
                    "r2-charts",
                    REPO_ROOT / "scripts" / "r2.py",
                    [
                        "--skip-history-upload",
                        "--skip-db-upload",
                        "--skip-images-upload",
                        "--charts-only",
                        *(["--worldwide-snapshot-only"] if _explicit_target_date else []),
                        "--new-date",
                        str(target_date),
                    ],
                    dry_run=False,
                    env={**env, "UPLOAD_TO_R2": "1"},
                    verbose=args.verbose,
                )
                if rc_export != 0:
                    failures.append(("export", rc_export))
                else:
                    _mark_r2_exported(target_date)

    total = _fmt(time.perf_counter() - started)
    if failures:
        print(f"[FAIL] {', '.join(n for n, _ in failures)} — {total}")
        _notify_spcharts_log(
            env,
            _spcharts_finished_message(target_date, total, failures),
            title="Spotify Charts - script error",
            tags="spotify,x",
            priority="high",
        )
        return 1
    if regional_post_failures:
        print(f"[ OK ] tout termine — {total} (posts a retenter: {', '.join(n for n, _ in regional_post_failures)})")
    else:
        print(f"[ OK ] tout termine — {total}")
    if not args.dry_run and ran_collect:
        cleanup_generated_artifacts()
        git_commit_and_push(REPO_ROOT, f"charts run all {target_date.isoformat()}")
        if _run_swift_top_charts_if_ready(target_date, env=env, verbose=args.verbose):
            git_commit_and_push(REPO_ROOT, f"charts swift top 100 and albums {target_date.isoformat()}")
        # Best-effort: refresh the Charts Gallery highlights/version R2 cache
        # from the freshly exported chart data. Never blocks the pipeline —
        # on failure the frontend's own cache-on-miss fallback recomputes it.
        _run(
            "home-highlights-cache",
            REPO_ROOT / "scripts" / "generate_home_highlights.py",
            ["--quiet"],
            dry_run=False,
            env=env,
            verbose=args.verbose,
        )
    if not args.dry_run:
        total = _fmt(time.perf_counter() - started)
        _notify_spcharts_log(
            env,
            _spcharts_finished_message(target_date, total, []),
            title="Spotify Charts - script finished",
            tags="spotify,white_check_mark",
        )
    return 0


if __name__ == "__main__":
    from core.run_logging import CollectorRunLog

    _log_target_date, _ = _extract_target_date(sys.argv[1:])
    try:
        with CollectorRunLog("spotify_charts", "spcharts", _log_target_date):
            raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C — arrêt en cours...")
        _stop_event.set()
        _kill_all()
        sys.exit(130)
    except Exception as exc:
        print(f"[FAIL] exception non geree: {exc}", flush=True)
        try:
            details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            _notify_spcharts_log(
                _build_env(),
                f"Unhandled exception in Spotify Charts runner:\n{details}",
                title="Spotify Charts - script error",
                tags="spotify,x",
                priority="high",
            )
        except Exception:
            pass
        raise
