#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
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


def _fmt(value: float) -> str:
    m, s = divmod(int(value), 60)
    return f"{m}m {s:02d}s"


def _build_env() -> dict[str, str]:
    load_dotenv(REPO_ENV_FILE, override=True)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    missing = [k for k in R2_ENV_VARS if not env.get(k, "").strip()]
    if missing:
        print(f"[WARN] R2 vars manquantes: {', '.join(missing)}")
    return env


def _run(
    name: str,
    script: Path,
    args: list[str],
    *,
    dry_run: bool,
    env: dict[str, str],
) -> int:
    if not script.exists():
        print(f"[FAIL] {name}: script introuvable")
        return 127
    if dry_run:
        print(f"[SKIP] {name}")
        return 0

    t0 = time.perf_counter()
    rc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(REPO_ROOT),
        check=False,
        env=env,
    ).returncode
    tag = "[ OK ]" if rc == 0 else "[FAIL]"
    print(f"{tag} {name} ({_fmt(time.perf_counter() - t0)})")
    return rc


def _run_parallel(
    runners: list[tuple[str, Path, list[str]]],
    *,
    forwarded: list[str],
    dry_run: bool,
    env: dict[str, str],
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
            ): name
            for name, script, fixed in runners
        }
        for f in concurrent.futures.as_completed(futures):
            name = futures[f]
            try:
                rc = f.result()
            except Exception as e:
                print(f"[FAIL] {name}: crash ({e})")
                rc = 1
            if rc != 0:
                failures.append((name, rc))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all Spotify chart daily scripts.")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-post", action="store_true", help="Skip Twitter posting.")
    parser.add_argument("--skip-uk", action="store_true", help="Skip UK chart.")
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
    t_phase1 = time.perf_counter()
    failures = _run_parallel(collect_runners, forwarded=forwarded, dry_run=args.dry_run, env=env)
    print(f"[PHASE1] collecte terminée ({_fmt(time.perf_counter() - t_phase1)})")

    if failures:
        failed_names = {n for n, _ in failures}
        print(f"[WARN] Echecs collecte: {', '.join(failed_names)}")
        if args.stop_on_error:
            print(f"[FAIL] stop-on-error — {_fmt(time.perf_counter() - started)}")
            return 1

    if not args.dry_run:
        t_export = time.perf_counter()
        export_env = {**env, "UPLOAD_TO_R2": "1"}
        rc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "export_for_web.py")],
            cwd=str(REPO_ROOT),
            check=False,
            env=export_env,
        ).returncode
        print(f"[EXPORT] export_for_web.py ({_fmt(time.perf_counter() - t_export)})" + (f" exit {rc}" if rc != 0 else ""))

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
    finally:
        _warp_disconnect()
