#!/usr/bin/env python3
"""iTunes Store purchase charts — full run.

Order: charts.py -> scripts/export_itunes.py -> scripts/upload_itunes_r2.py.
This runner never posts to X and never commits git (run_itunes.bat posts the
new-release progression right after it). A failed collector aborts the run (no
export / upload). scraped_at rounding mirrors run_apple_music.py so the two
Apple pipelines share a snapshot cadence.

Since 2026-09-25: phone alert (ntfy) on any failure, and a single-instance
lock — Task Scheduler cannot prevent overlaps (the .vbs doesn't wait for the
.bat), so a run that finds the previous one still alive skips its hour and
exits with EXIT_SKIPPED, which run_itunes.bat uses to skip the post step too.
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

LOCK_PATH = HERE / "tools" / "locks" / "run_itunes.lock"
NTFY_TOPIC = os.getenv("NTFY_TOPIC_ITUNES") or os.getenv("NTFY_TOPIC_APPLE_MUSIC", "taylormuseum-apple-music")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from collectors.spotify.core import run_guard  # noqa: E402

EXIT_SKIPPED = run_guard.EXIT_SKIPPED  # previous run still alive (checked by run_itunes.bat)


def alert(message: str, priority: str = "high") -> None:
    run_guard.alert(NTFY_TOPIC, "iTunes collector", message, priority)


def acquire_lock() -> bool:
    return run_guard.acquire_lock(LOCK_PATH)


def release_lock() -> None:
    run_guard.release_lock(LOCK_PATH)


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
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_child(cmd: list[str], timeout: int) -> int:
    # Hard cap per step: the scheduler does NOT stop a hung run (the .vbs
    # launches the .bat without waiting, so IgnoreNew / time limits never
    # apply). Worst case = 3 steps x 900 s, under the 1 h cadence; the lock
    # in main() covers anything longer (e.g. the laptop slept mid-run).
    try:
        return subprocess.run(cmd, cwd=REPO_ROOT, env=child_env(), check=False, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"[iTunes] TIMEOUT after {timeout}s, killed: {' '.join(str(c) for c in cmd[1:])}")
        return 124


def run_script(script_path: Path, scraped_at: str) -> int:
    if not script_path.exists():
        print(f"[ERROR] Missing script: {script_path}")
        return 1

    print(f"\n{'=' * 80}\nRunning: {script_path.relative_to(REPO_ROOT)}\n{'=' * 80}")
    chart_date = scraped_at.split("T", 1)[0]
    returncode = run_child(
        [sys.executable, str(script_path), "--date", chart_date, "--scraped-at", scraped_at],
        timeout=900,
    )
    print(f"[OK] {script_path.name}" if returncode == 0
          else f"[ERROR] {script_path.name} failed with code {returncode}")
    return returncode


def _run_repo_script(rel: str) -> int:
    script = REPO_ROOT / rel
    if not script.exists():
        print(f"[iTunes] Script missing: {rel}")
        return 1
    return run_child([sys.executable, str(script)], timeout=900)


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
    if not acquire_lock():
        alert("Previous iTunes run still running — this hour was skipped.", priority="default")
        sys.exit(EXIT_SKIPPED)
    code = 0
    try:
        _main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        alert(f"iTunes run crashed: {type(exc).__name__}: {exc}")
        code = 1
    finally:
        release_lock()
    release_watch_hook()
    # Every non-zero exit above has already sent its phone alert: exit
    # EXIT_ALERTED so the .bat only alerts for crashes that never reached
    # Python's alert (import error, broken interpreter...).
    sys.exit(run_guard.EXIT_ALERTED if code else 0)


def release_watch_hook() -> None:
    """After the lock is released: returns at once (date check) unless a
    catalog release is due within ~70 min — then this run waits for it and
    polls every second (release_watch.py, owner 2026-09-25: "retry chaque
    seconde jusqu'à avoir la data")."""
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import release_watch

        release_watch.run_if_release_due()
    except Exception as exc:
        alert(f"Release watch failed to start: {type(exc).__name__}: {exc}")


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-post", action="store_true", help="No effect (this pipeline never posts to X).")
    args, _unknown = parser.parse_known_args()

    scraped_at = build_scraped_at()
    print(f"[iTunes] Starting full run - scraped_at={scraped_at}")

    os.environ["ITUNES_SKIP_EXPORT"] = "1"

    failures: list[tuple[str, int]] = []
    for script in SCRIPTS:
        code = run_guard.retry_step(script.name, lambda s=script: run_script(s, scraped_at))
        if code != 0:
            failures.append((script.name, code))

    print(f"\n{'=' * 80}")
    if failures:
        print("[iTunes] Finished with errors:")
        for name, code in failures:
            print(f" - {name}: {code}")
        alert("iTunes collection failed ("
              + ", ".join(f"{n}: {'timeout' if c == 124 else f'code {c}'}" for n, c in failures)
              + f") for {scraped_at}. No export/upload, no fresh data for the posts this hour.")
        sys.exit(1)

    print("[iTunes] All scripts completed successfully")
    if run_guard.retry_step("export_itunes", export_itunes, waits=(10, 30)) != 0:
        print("[iTunes] Export failed, skipping R2 upload")
        alert(f"iTunes export failed for {scraped_at} (site not updated this hour).")
        sys.exit(1)

    upload_code = run_guard.retry_step("upload_itunes_r2", maybe_upload_to_r2, waits=(15, 45))
    if upload_code != 0:
        print("[iTunes] R2 upload failed")
        alert(f"iTunes R2 upload failed for {scraped_at} (site not updated this hour).", priority="default")
        sys.exit(upload_code)
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
