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

_WARP_CLI = Path(r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe")


def _warp_connect() -> None:
    cli = str(_WARP_CLI) if _WARP_CLI.exists() else "warp-cli"
    try:
        t0 = time.perf_counter()
        print("[WARP] connexion en cours...")
        subprocess.run([cli, "connect"], timeout=15, check=False, capture_output=True)
        time.sleep(15)
        print(f"[WARP] connecté ({_fmt(time.perf_counter() - t0)})")
    except Exception as e:
        print(f"[WARP] impossible de connecter ({e})")


def _warp_disconnect() -> None:
    cli = str(_WARP_CLI) if _WARP_CLI.exists() else "warp-cli"
    try:
        subprocess.run([cli, "disconnect"], timeout=10, check=False, capture_output=True)
        print("[WARP] déconnecté")
    except Exception:
        pass


REPO_ENV_FILE = REPO_ROOT / ".env"
R2_ENV_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
SPOTIFY_API_BASE = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
SPOTIFY_CHARTS_URL = "https://charts.spotify.com/charts/view/regional-global-daily/latest"
SPOTIFY_SESSION = CHARTS_ROOT / "global" / "tools" / "json" / "spotify_session.json"
SPOTIFY_TOKEN_TTL = 50 * 60
AVAILABILITY_RETRY_SECONDS = 10
SPOTIFY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

# global et fr postent dès leur collecte terminée (pas d'attente de worldwide)
# us/uk/worldwide ne postent pas
COLLECT_RUNNERS: list[tuple[str, Path, list[str]]] = [
    ("global",    CHARTS_ROOT / "global"    / "daily.py", ["--force"]),
    ("fr",        CHARTS_ROOT / "fr"        / "daily.py", ["--force"]),
    ("us",        CHARTS_ROOT / "us"        / "daily.py", ["--no-post"]),
    ("uk",        CHARTS_ROOT / "uk"        / "daily.py", ["--no-post"]),
    ("worldwide", CHARTS_ROOT / "worldwide" / "daily.py", []),
]

CHART_AVAILABILITY: dict[str, str] = {
    "global": "regional-global-daily",
    "fr": "regional-fr-daily",
    "us": "regional-us-daily",
    "uk": "regional-gb-daily",
}

# posted.lock path mirrors each daily.py: {region}/history/YYYY/MM/date/posted.lock
def _posted_lock(name: str, target: date) -> Path:
    return CHARTS_ROOT / name / "history" / str(target.year) / f"{target.month:02d}" / str(target) / "posted.lock"


def _already_done(runners: list[tuple[str, Path, list[str]]], target: date) -> bool:
    names = [n for n, _, _ in runners if n in CHART_AVAILABILITY]
    if not names:
        return False
    done = [n for n in names if _posted_lock(n, target).exists()]
    missing = [n for n in names if n not in done]
    if missing:
        return False
    print(f"[SKIP] posted.lock présent pour toutes les régions ({', '.join(done)}) — déjà terminé pour {target}")
    return True


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


def _load_cached_bearer(name: str) -> str | None:
    try:
        data = json.loads(_bearer_cache_path(name).read_text(encoding="utf-8"))
        if time.time() - float(data.get("ts", 0)) < SPOTIFY_TOKEN_TTL:
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


def _acquire_bearer_token(names: list[str], *, refresh: bool = False) -> str:
    if not refresh:
        for name in names:
            token = _load_cached_bearer(name)
            if token:
                return token

    print("[CHECK] token Spotify absent/expire, recuperation via Playwright...")
    from playwright.sync_api import sync_playwright

    token_holder: list[str] = []
    api_host = SPOTIFY_API_BASE.split("//", 1)[1].split("/", 1)[0]

    def _on_request(req) -> None:
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
                storage_state=str(SPOTIFY_SESSION),
                user_agent=SPOTIFY_UA,
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.on("request", _on_request)
            page.goto(SPOTIFY_CHARTS_URL, wait_until="domcontentloaded", timeout=30_000)
            deadline = time.time() + 20
            while not token_holder and time.time() < deadline:
                page.wait_for_timeout(300)
        finally:
            browser.close()

    if not token_holder:
        raise RuntimeError(f"Bearer token introuvable avec {SPOTIFY_SESSION}")

    token = token_holder[0]
    _save_bearer_to_caches(token, names)
    return token


def _extract_target_date(forwarded: list[str]) -> date:
    for value in forwarded:
        if value.startswith("--"):
            continue
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            continue
    return date.today() - timedelta(days=1)


def _chart_available(chart_id: str, target: date, token: str) -> tuple[bool, str]:
    try:
        resp = requests.get(
            f"{SPOTIFY_API_BASE}/{chart_id}/{target}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Referer": "https://charts.spotify.com/",
                "User-Agent": SPOTIFY_UA,
            },
            timeout=15,
        )
    except Exception as e:
        short = str(e).split("\n")[0][:120]
        return False, f"réseau: {short}"

    if resp.status_code == 200:
        try:
            entries = resp.json().get("entries") or []
        except Exception:
            entries = []
        return bool(entries), f"HTTP 200 ({len(entries)} lignes)"
    return False, f"HTTP {resp.status_code}"


def _wait_for_charts_available(
    runners: list[tuple[str, Path, list[str]]],
    *,
    target: date,
    dry_run: bool,
) -> None:
    names = [name for name, _, _ in runners if name in CHART_AVAILABILITY]
    if dry_run or not names:
        return

    probe = next((n for n in ("global", "fr", "us", "uk") if n in names), names[0])
    probe_chart = CHART_AVAILABILITY[probe]

    attempt = 1
    refresh_token = False
    print(f"\n[CHECK] disponibilite Spotify pour {target} (via {probe})")
    while not _stop_event.is_set():
        try:
            token = _acquire_bearer_token(names, refresh=refresh_token)
            refresh_token = False
        except Exception as e:
            print(f"[CHECK] tentative #{attempt}: token indisponible ({e})")
            time.sleep(AVAILABILITY_RETRY_SECONDS)
            attempt += 1
            continue

        ok, detail = _chart_available(probe_chart, target, token)
        print(f"[CHECK] tentative #{attempt}: {probe}={detail}")
        if ok:
            print(f"[CHECK] charts disponibles pour {target}")
            return
        if "HTTP 401" in detail or "HTTP 403" in detail:
            refresh_token = True

        is_network_err = detail.startswith("réseau:")
        wait = 5 if is_network_err else AVAILABILITY_RETRY_SECONDS
        label = "tunnel WARP" if is_network_err else "chart indisponible"
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
                list(dict.fromkeys([*fixed, *forwarded])),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all Spotify chart daily scripts.")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-post", action="store_true", help="Skip Twitter posting.")
    parser.add_argument("--skip-uk", action="store_true", help="Skip UK chart.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Affiche la sortie complète des scripts.")
    args, passthrough = parser.parse_known_args()

    forwarded = list(passthrough)

    started = time.perf_counter()
    env = _build_env()

    # global et fr reçoivent --no-post si demandé, sinon ils postent dès leur collecte terminée
    collect_runners = []
    for name, script, fixed in COLLECT_RUNNERS:
        if args.skip_uk and name == "uk":
            continue
        extra = ["--no-post"] if args.no_post and name in {"global", "fr"} else []
        collect_runners.append((name, script, fixed + extra))

    target_date = _extract_target_date(forwarded)
    if not args.dry_run and _already_done(collect_runners, target_date):
        return 0
    _wait_for_charts_available(collect_runners, target=target_date, dry_run=args.dry_run)

    # Collecte de toutes les régions en parallèle (global+fr postent dès qu'ils sont prêts)
    names_str = ", ".join(n for n, _, _ in collect_runners)
    print(f"\n[PHASE1] collecte en parallèle: {names_str}")
    t_phase1 = time.perf_counter()
    failures = _run_parallel(collect_runners, forwarded=forwarded, dry_run=args.dry_run, env=env, verbose=args.verbose)
    print(f"[PHASE1] collecte terminée ({_fmt(time.perf_counter() - t_phase1)})")

    if failures:
        failed_names = {n for n, _ in failures}
        print(f"[WARN] Echecs collecte: {', '.join(failed_names)}")
        if args.stop_on_error:
            print(f"[FAIL] stop-on-error — {_fmt(time.perf_counter() - started)}")
            return 1

    if not args.dry_run:
        print("\n[PHASE2] export web + upload R2...")
        rc_export = _run(
            "export",
            REPO_ROOT / "scripts" / "export_for_web.py",
            [],
            dry_run=False,
            env={**env, "UPLOAD_TO_R2": "1"},
            verbose=args.verbose,
        )
        if rc_export != 0:
            failures.append(("export", rc_export))

    total = _fmt(time.perf_counter() - started)
    if failures:
        print(f"[FAIL] {', '.join(n for n, _ in failures)} — {total}")
        return 1
    print(f"[ OK ] tout terminé — {total}")
    return 0


if __name__ == "__main__":
    _warp_connect()
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C — arrêt en cours...")
        _stop_event.set()
        _kill_all()
        sys.exit(130)
    finally:
        _warp_disconnect()
