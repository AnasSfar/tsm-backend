#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

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
        time.sleep(8)
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

# global et fr postent dès leur collecte terminée (pas d'attente de worldwide)
# us/uk/worldwide ne postent pas
COLLECT_RUNNERS: list[tuple[str, Path, list[str]]] = [
    ("global",    CHARTS_ROOT / "global"    / "daily.py", ["--force"]),
    ("fr",        CHARTS_ROOT / "fr"        / "daily.py", ["--force"]),
    ("us",        CHARTS_ROOT / "us"        / "daily.py", ["--no-post"]),
    ("uk",        CHARTS_ROOT / "uk"        / "daily.py", ["--no-post"]),
    ("worldwide", CHARTS_ROOT / "worldwide" / "daily.py", []),
]


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
