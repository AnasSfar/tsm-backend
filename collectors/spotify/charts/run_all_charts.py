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
REPO_ENV_FILE = REPO_ROOT / ".env"
R2_ENV_VARS = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)

POSTING_RUNNERS = [
    ("global", CHARTS_ROOT / "global" / "daily.py", []),
    ("fr", CHARTS_ROOT / "fr" / "daily.py", []),
]

NO_POST_RUNNERS = [
    ("us-no-post", CHARTS_ROOT / "us" / "daily.py", ["--no-post"]),
    ("uk-no-post", CHARTS_ROOT / "uk" / "daily.py", ["--no-post"]),
    ("worldwide", CHARTS_ROOT / "worldwide" / "daily.py", []),
]


def _format_seconds(value: float) -> str:
    mins = int(value // 60)
    secs = int(value % 60)
    return f"{mins}m {secs:02d}s"


def _build_child_env() -> dict[str, str]:
    # Force .env values to override stale/empty inherited environment values.
    load_dotenv(REPO_ENV_FILE, override=True)
    child_env = os.environ.copy()

    missing = [key for key in R2_ENV_VARS if not child_env.get(key, "").strip()]
    if missing:
        print(
            "[WARN] Missing R2 variables in environment after loading .env: "
            + ", ".join(missing)
        )
        print(f"[INFO] Checked .env at: {REPO_ENV_FILE}")

    return child_env


def _run_region(
    region: str,
    script_path: Path,
    merged_args: list[str],
    *,
    dry_run: bool,
    repo_root: Path,
    child_env: dict[str, str],
) -> int:
    if not script_path.exists():
        print(f"[FAIL] {region}: missing script {script_path}")
        return 127

    cmd = [sys.executable, str(script_path), *merged_args]
    print(f"[RUN ] {region}: {' '.join(cmd)}")

    if dry_run:
        print(f"[SKIP] {region}: dry-run mode")
        return 0

    t0 = time.perf_counter()
    result = subprocess.run(cmd, cwd=str(repo_root), check=False, env=child_env)
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
            "Any extra arguments are forwarded to each daily script."
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
        "--skip-uk",
        action="store_true",
        help="Skip UK no-post chart runner (UK runs by default).",
    )
    parser.add_argument(
        "--include-uk-no-post",
        action="store_true",
        help=(
            "(Deprecated) Also run UK no-post script after global/fr/us-no-post. "
            "UK now runs by default; use --skip-uk to disable."
        ),
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
    child_env = _build_child_env()

    run_uk = (not args.skip_uk) or args.include_uk_no_post

    parallel_runners = list(POSTING_RUNNERS)
    sequential_runners = [NO_POST_RUNNERS[0]]
    if run_uk:
        sequential_runners.append(NO_POST_RUNNERS[1])
    sequential_runners.append(NO_POST_RUNNERS[2])   # worldwide — always last

    print("Running Spotify chart dailies for: global + fr (parallel), then us-no-post, uk-no-post, worldwide")
    if not run_uk:
        print("UK disabled: --skip-uk")
    if args.no_post:
        print("Twitter posting disabled: --no-post")
    if forwarded:
        print(f"Forwarded args: {' '.join(forwarded)}")
    print()

    print("[PHASE] Parallel: global + fr")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(parallel_runners)) as executor:
        future_to_region = {}
        for region, script_path, runner_args in parallel_runners:
            merged_args = list(dict.fromkeys([*runner_args, *forwarded]))
            future = executor.submit(
                _run_region,
                region,
                script_path,
                merged_args,
                dry_run=args.dry_run,
                repo_root=REPO_ROOT,
                child_env=child_env,
            )
            future_to_region[future] = region

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
        print("[PHASE] Sequential: us-no-post, uk-no-post, worldwide")
        for region, script_path, runner_args in sequential_runners:
            merged_args = list(dict.fromkeys([*runner_args, *forwarded]))
            code = _run_region(
                region,
                script_path,
                merged_args,
                dry_run=args.dry_run,
                repo_root=REPO_ROOT,
                child_env=child_env,
            )
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
    raise SystemExit(main())
