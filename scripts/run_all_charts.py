#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CHARTS_ROOT = REPO_ROOT / "collectors" / "spotify" / "charts"

_WARP_CLI = Path(r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe")


def _warp_connect() -> None:
    cli = str(_WARP_CLI) if _WARP_CLI.exists() else "warp-cli"
    try:
        subprocess.run([cli, "connect"], timeout=15, check=False, capture_output=True)
        time.sleep(2)
        print("[WARP] connecté")
    except Exception as e:
        print(f"[WARP] impossible de connecter ({e})")


def _warp_disconnect() -> None:
    cli = str(_WARP_CLI) if _WARP_CLI.exists() else "warp-cli"
    try:
        subprocess.run([cli, "disconnect"], timeout=10, check=False, capture_output=True)
        print("[WARP] déconnecté")
    except Exception:
        pass

CHART_RUNNERS = [
    ("global",    CHARTS_ROOT / "global"    / "daily.py"),
    ("fr",        CHARTS_ROOT / "fr"        / "daily.py"),
    ("us",        CHARTS_ROOT / "us"        / "daily.py"),
    ("uk",        CHARTS_ROOT / "uk"        / "daily.py"),
    ("worldwide", CHARTS_ROOT / "worldwide" / "daily.py"),
]


def _format_seconds(value: float) -> str:
    mins = int(value // 60)
    secs = int(value % 60)
    return f"{mins}m {secs:02d}s"


def _run_region(region: str, script_path: Path, forwarded: list[str], dry_run: bool) -> int:
    if not script_path.exists():
        print(f"[FAIL] {region}: missing script {script_path}")
        return 127

    cmd = [sys.executable, str(script_path), *forwarded]
    print(f"[RUN ] {region}: {' '.join(cmd)}")

    if dry_run:
        print(f"[SKIP] {region}: dry-run mode")
        return 0

    t0 = time.perf_counter()
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    dt = _format_seconds(time.perf_counter() - t0)

    if result.returncode == 0:
        print(f"[ OK ] {region}: completed in {dt}")
    else:
        print(f"[FAIL] {region}: exit code {result.returncode} after {dt}")

    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run all Spotify chart daily scripts (global, fr, us, uk) in sequence. "
            "Any extra arguments are forwarded to each daily script, e.g. --force or YYYY-MM-DD."
        )
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately when one region fails.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )
    parser.add_argument(
        "--no-post",
        action="store_true",
        help="Run full chart pipeline but skip Twitter posting where supported.",
    )

    args, passthrough = parser.parse_known_args()

    forwarded = list(passthrough)
    if args.no_post and "--no-post" not in forwarded:
        forwarded.append("--no-post")

    started = time.perf_counter()
    failures: list[tuple[str, int]] = []

    print("Running Spotify chart dailies for: global + fr (parallel), then us, uk, worldwide")
    if args.no_post:
        print("Twitter posting disabled: --no-post")
    if forwarded:
        print(f"Forwarded args: {' '.join(forwarded)}")
    print()

    parallel_runners = [CHART_RUNNERS[0], CHART_RUNNERS[1]]
    sequential_runners = CHART_RUNNERS[2:]

    print("[PHASE] Parallel: global + fr")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_to_region = {
            executor.submit(_run_region, region, script_path, forwarded, args.dry_run): region
            for region, script_path in parallel_runners
        }
        for future in concurrent.futures.as_completed(future_to_region):
            region = future_to_region[future]
            try:
                code = future.result()
            except Exception as e:
                print(f"[FAIL] {region}: runner crashed ({e})")
                code = 1
            if code != 0:
                failures.append((region, code))
    print()

    if failures and args.stop_on_error:
        print("Stopping due to --stop-on-error")
    else:
        print("[PHASE] Sequential: us, uk, worldwide")
        for region, script_path in sequential_runners:
            code = _run_region(region, script_path, forwarded, args.dry_run)
            if code != 0:
                failures.append((region, code))
                if args.stop_on_error:
                    print("Stopping due to --stop-on-error")
                    print()
                    break
            print()

    total = _format_seconds(time.perf_counter() - started)
    print(f"Total runtime: {total}")

    if failures:
        print("Failed regions:")
        for region, code in failures:
            print(f"- {region} (exit {code})")
        return 1

    print("All chart scripts completed successfully.")
    return 0


if __name__ == "__main__":
    _warp_connect()
    try:
        raise SystemExit(main())
    finally:
        _warp_disconnect()
