#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

CHARTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CHARTS_ROOT.parents[2]

POSTING_RUNNERS = [
    ("global", CHARTS_ROOT / "global" / "daily.py", []),
    ("fr", CHARTS_ROOT / "fr" / "daily.py", []),
]

NO_POST_RUNNERS = [
    ("us-no-post", CHARTS_ROOT / "us" / "daily.py", ["--no-post"]),
    ("uk-no-post", CHARTS_ROOT / "uk" / "daily_no_post.py", []),
    ("worldwide", CHARTS_ROOT / "worldwide" / "daily.py", []),
]


def _format_seconds(value: float) -> str:
    mins = int(value // 60)
    secs = int(value % 60)
    return f"{mins}m {secs:02d}s"


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
        "--include-uk-no-post",
        action="store_true",
        help="Also run UK no-post script after global/fr/us-no-post.",
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

    runners = list(POSTING_RUNNERS)
    runners.append(NO_POST_RUNNERS[0])
    if args.include_uk_no_post:
        runners.append(NO_POST_RUNNERS[1])
    runners.append(NO_POST_RUNNERS[2])   # worldwide — always last

    print("Running Spotify chart dailies for: global, fr, us-no-post, worldwide")
    if args.include_uk_no_post:
        print("Also running: uk-no-post")
    if args.no_post:
        print("Twitter posting disabled: --no-post")
    if forwarded:
        print(f"Forwarded args: {' '.join(forwarded)}")
    print()

    for region, script_path, runner_args in runners:
        if not script_path.exists():
            print(f"[FAIL] {region}: missing script {script_path}")
            failures.append((region, 127))
            if args.stop_on_error:
                break
            continue

        merged_args = list(dict.fromkeys([*runner_args, *forwarded]))
        cmd = [sys.executable, str(script_path), *merged_args]
        print(f"[RUN ] {region}: {' '.join(cmd)}")

        if args.dry_run:
            print(f"[SKIP] {region}: dry-run mode")
            print()
            continue

        t0 = time.perf_counter()
        result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
        dt = _format_seconds(time.perf_counter() - t0)

        if result.returncode == 0:
            print(f"[ OK ] {region}: completed in {dt}")
        else:
            print(f"[FAIL] {region}: exit code {result.returncode} after {dt}")
            failures.append((region, result.returncode))
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
