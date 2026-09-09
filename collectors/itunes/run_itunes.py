#!/usr/bin/env python3
"""iTunes Store purchase charts — full run.

Order: charts.py -> scripts/export_itunes.py -> scripts/upload_itunes_r2.py.
Never posts to X, never commits git. A failed collector aborts the run (no
export / upload). scraped_at rounding mirrors run_apple_music.py so the two
Apple pipelines share a snapshot cadence.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

SCRIPTS = [HERE / "charts.py"]


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _snapshot_hours() -> list[int]:
    raw = os.getenv("ITUNES_SNAPSHOT_HOURS", "0,2,4,6,8,10,12,14,16,18,20,22")
    hours: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        hour = int(part)
        if 0 <= hour <= 23:
            hours.append(hour)
    if not hours:
        raise ValueError("ITUNES_SNAPSHOT_HOURS must contain at least one hour")
    return sorted(set(hours))


def _now_for_snapshot() -> datetime:
    tz_name = os.getenv("ITUNES_SNAPSHOT_TZ", "Europe/Paris")
    if ZoneInfo is not None and tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception as exc:
            print(f"[iTunes] Could not load timezone {tz_name!r}; using local time ({exc})")
    return datetime.now()


def _iso_seconds(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def build_scraped_at() -> str:
    now = _now_for_snapshot()
    if not _truthy_env("ITUNES_ROUND_SCRAPED_AT", default=True):
        return _iso_seconds(now.replace(microsecond=0))

    hours = _snapshot_hours()
    candidates = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in hours]
    past = [c for c in candidates if c <= now]
    slot = past[-1] if past else (now - timedelta(days=1)).replace(
        hour=hours[-1], minute=0, second=0, microsecond=0
    )
    print(f"[iTunes] Rounded scraped_at {now.strftime('%Y-%m-%dT%H:%M:%S')} -> {_iso_seconds(slot)}")
    return _iso_seconds(slot)


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    parts = [str(REPO_ROOT), str(HERE)]
    existing = env.get("PYTHONPATH")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def run_script(script_path: Path, scraped_at: str) -> int:
    if not script_path.exists():
        print(f"[ERROR] Missing script: {script_path}")
        return 1

    print(f"\n{'=' * 80}\nRunning: {script_path.relative_to(REPO_ROOT)}\n{'=' * 80}")
    chart_date = scraped_at.split("T", 1)[0]
    result = subprocess.run(
        [sys.executable, str(script_path), "--date", chart_date, "--scraped-at", scraped_at],
        cwd=REPO_ROOT,
        env=child_env(),
        check=False,
    )
    print(f"[OK] {script_path.name}" if result.returncode == 0
          else f"[ERROR] {script_path.name} failed with code {result.returncode}")
    return result.returncode


def _run_repo_script(rel: str) -> int:
    script = REPO_ROOT / rel
    if not script.exists():
        print(f"[iTunes] Script missing: {rel}")
        return 1
    return subprocess.run(
        [sys.executable, str(script)], cwd=REPO_ROOT, env=child_env(), check=False
    ).returncode


def export_itunes() -> int:
    print("[iTunes] Exporting CSV to JSON...")
    return _run_repo_script("scripts/export_itunes.py")


def maybe_upload_to_r2() -> int:
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        print("[iTunes] R2 upload skipped (UPLOAD_TO_R2 explicitly disabled)")
        return 0
    print("[iTunes] Uploading to R2...")
    return _run_repo_script("scripts/upload_itunes_r2.py")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-post", action="store_true", help="No effect (this pipeline never posts to X).")
    args, _unknown = parser.parse_known_args()

    scraped_at = build_scraped_at()
    print(f"[iTunes] Starting full run - scraped_at={scraped_at}")

    os.environ["ITUNES_SKIP_EXPORT"] = "1"

    failures: list[tuple[str, int]] = []
    for script in SCRIPTS:
        code = run_script(script, scraped_at)
        if code != 0:
            failures.append((script.name, code))

    print(f"\n{'=' * 80}")
    if failures:
        print("[iTunes] Finished with errors:")
        for name, code in failures:
            print(f" - {name}: {code}")
        sys.exit(1)

    print("[iTunes] All scripts completed successfully")
    if export_itunes() != 0:
        print("[iTunes] Export failed, skipping R2 upload")
        sys.exit(1)

    upload_code = maybe_upload_to_r2()
    if upload_code != 0:
        print("[iTunes] R2 upload failed")
        sys.exit(upload_code)
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
