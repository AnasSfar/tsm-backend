#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "billboard"))
from live_trigger import trigger_live_projection  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from collectors.spotify.core import run_guard  # noqa: E402

# 2026-09-25: phone alert on any failure + single-instance lock (the scheduler
# can't prevent overlaps, see run_guard). EXIT_SKIPPED -> run_apple_music.bat
# skips the rest of the chain.
LOCK_PATH = HERE / "tools" / "locks" / "run_apple_music.lock"
NTFY_TOPIC = os.getenv("NTFY_TOPIC_APPLE_MUSIC", "taylormuseum-apple-music")
POST_SCRIPT = HERE / "post_new_release_progression.py"
POST_INPUTS = {"global.py", "country_all.py"}  # what post_new_release_progression reads
POST_LOG = HERE / "post_new_release_progression.log"


def alert(message: str, priority: str = "high") -> None:
    run_guard.alert(NTFY_TOPIC, "Apple Music collector", message, priority)


def start_new_release_posts() -> subprocess.Popen | None:
    """New-release posts right after the collectors (2026-09-25, "be the first
    to post"): they only read the collectors' CSVs, so they no longer wait
    ~15 min for export / upload / images / highlights. Runs in parallel with
    them; waited for at the end of the run. No-op outside a release window."""
    try:
        log_fh = open(POST_LOG, "a", encoding="utf-8")
        return subprocess.Popen(
            [sys.executable, "-u", str(POST_SCRIPT), "--platform", "apple_music"],
            cwd=REPO_ROOT, env=child_env(), stdout=log_fh, stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        alert(f"Could not start the new-release posts: {type(exc).__name__}: {exc}")
        return None

# The .bat redirects stdout to a file -> cp1252 by default. Relaying a child's
# output containing e.g. U+FFFD then raised UnicodeEncodeError at the end of
# the run (2026-09-24 15h-17h), which skipped the home highlights refresh.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPTS = [
    HERE / "global.py",
    HERE / "ts_page.py",
    HERE / "ts_page_all.py",
    HERE / "finalize_ts_top_songs_daily.py",
    HERE / "ts_top_songs_live.py",
    HERE / "country_all.py",
    HERE / "genre_all.py",
]

# The collectors write disjoint CSVs, so independent groups run in parallel
# (was fully sequential, ~3.5 min/cycle). Order is kept inside a group:
# finalize_ts_top_songs_daily.py reads ts_page_all.py's raw CSV. genre_all is
# the long pole (~1.3k requests); everything else runs alongside it.
# APPLE_MUSIC_PARALLEL_SCRIPTS=0 goes back to one-by-one.
SCRIPT_GROUPS = [
    [HERE / "genre_all.py"],
    # live reads today's raw cycles + yesterday's final -> after both.
    [HERE / "ts_page_all.py", HERE / "finalize_ts_top_songs_daily.py", HERE / "ts_top_songs_live.py"],
    [HERE / "country_all.py"],
    [HERE / "global.py", HERE / "ts_page.py"],
]
assert sorted(p.name for g in SCRIPT_GROUPS for p in g) == sorted(p.name for p in SCRIPTS)

_PRINT_LOCK = threading.Lock()

# Their output is never read by the hourly export (raw TS composite cycles /
# idempotent next-day finalization, retried every run), and ts_page_all.py
# already refuses to write a partial composite. A failure there (e.g. a DNS
# blip on 12/168 storefronts, 2026-09-24 12h) must not drop the whole hour's
# global/country/genre data from the site.
NON_BLOCKING_SCRIPTS = {"ts_page_all.py", "finalize_ts_top_songs_daily.py", "ts_top_songs_live.py"}


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _snapshot_hours() -> list[int]:
    raw = os.getenv("APPLE_MUSIC_SNAPSHOT_HOURS", "0,2,4,6,8,10,12,14,16,18,20,22")
    hours: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour = int(part)
        except ValueError as exc:
            raise ValueError(f"Invalid APPLE_MUSIC_SNAPSHOT_HOURS value: {part!r}") from exc
        if hour < 0 or hour > 23:
            raise ValueError(f"Invalid APPLE_MUSIC_SNAPSHOT_HOURS hour: {hour}")
        hours.append(hour)
    if not hours:
        raise ValueError("APPLE_MUSIC_SNAPSHOT_HOURS must contain at least one hour")
    return sorted(set(hours))


def _now_for_snapshot() -> datetime:
    tz_name = os.getenv("APPLE_MUSIC_SNAPSHOT_TZ", "Europe/Paris")
    if ZoneInfo is not None and tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception as exc:
            print(f"[Apple Music] Could not load timezone {tz_name!r}; using local time ({exc})")
    return datetime.now()


def _iso_seconds(dt: datetime) -> str:
    """ISO-8601 to the second. Keeps the UTC offset when `dt` is timezone-aware
    (it is, unless the Paris zone failed to load) so the frontend can render the
    snapshot in each visitor's own timezone instead of assuming Paris."""
    return dt.isoformat(timespec="seconds")


def build_scraped_at() -> str:
    now = _now_for_snapshot()
    if not _truthy_env("APPLE_MUSIC_ROUND_SCRAPED_AT", default=True):
        return _iso_seconds(now.replace(microsecond=0))

    hours = _snapshot_hours()
    candidates = [
        now.replace(hour=hour, minute=0, second=0, microsecond=0)
        for hour in hours
    ]
    past = [candidate for candidate in candidates if candidate <= now]
    if past:
        slot = past[-1]
    else:
        slot = (now - timedelta(days=1)).replace(hour=hours[-1], minute=0, second=0, microsecond=0)

    print(
        "[Apple Music] Rounded scraped_at "
        f"{now.strftime('%Y-%m-%dT%H:%M:%S')} -> {_iso_seconds(slot)}"
    )
    return _iso_seconds(slot)

def child_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_parts = [str(REPO_ROOT), str(HERE)]
    existing = env.get("PYTHONPATH")
    if existing:
        pythonpath_parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    # The .bat's -u only covers this process; without this, children's output
    # sits in a block buffer and a hung child leaves the log silent.
    env["PYTHONUNBUFFERED"] = "1"
    # Children decode as UTF-8 in run_child_captured: make them emit UTF-8.
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_child(cmd: list[str], timeout: int, env: dict[str, str] | None = None) -> int:
    # Hard cap per step: a hung child must not hold the hourly cycle past the
    # next trigger (Task Scheduler IgnoreNew silently skips it -> missing hour).
    try:
        return subprocess.run(cmd, cwd=REPO_ROOT, env=env or child_env(), check=False, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"[Apple Music] TIMEOUT after {timeout}s, killed: {' '.join(str(c) for c in cmd[1:])}")
        return 124


def run_child_captured(cmd: list[str], timeout: int) -> tuple[int, str]:
    # Same cap as run_child, but output comes back as one block so children
    # running in parallel don't interleave line by line in the log.
    try:
        result = subprocess.run(
            cmd, cwd=REPO_ROOT, env=child_env(), check=False, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
        )
        return result.returncode, result.stdout or ""
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, f"{partial}\n[Apple Music] TIMEOUT after {timeout}s, killed: {' '.join(str(c) for c in cmd[1:])}"


def export_apple_music(scraped_at: str) -> int:
    """Generate JSON files from CSV data."""
    export_script = REPO_ROOT / "scripts" / "export_apple_music.py"
    if not export_script.exists():
        print(f"[Apple Music] Export script missing: {export_script}")
        return 1

    print("[Apple Music] Exporting CSV to JSON...")
    env = child_env()
    env["APPLE_MUSIC_RUN_SCRAPED_AT"] = scraped_at
    returncode = run_child([sys.executable, str(export_script)], timeout=900, env=env)
    if returncode == 0:
        for rel_path in (
            Path("runtime/exports/web/site/data/applemusic.json"),
            Path("runtime/exports/web/site/data/applemusic_history.json"),
        ):
            path = REPO_ROOT / rel_path
            if path.exists():
                print(f"[Apple Music] Exported {rel_path} ({path.stat().st_size} bytes)")
            else:
                print(f"[Apple Music] Export missing after export: {rel_path}")
    return returncode


def maybe_upload_to_r2() -> int:
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        print("[Apple Music] R2 upload skipped (UPLOAD_TO_R2 explicitly disabled)")
        return 0

    upload_script = REPO_ROOT / "scripts" / "upload_ap_r2.py"
    if not upload_script.exists():
        print(f"[Apple Music] R2 upload script missing: {upload_script}")
        return 1

    print("[Apple Music] Uploading to R2...")
    # Critical files (main JSON, snapshots, history-by-date) go first; the cap
    # only ever cuts the per-song phase, which the next run catches up on.
    return run_child([sys.executable, str(upload_script)], timeout=1800)


def regenerate_home_highlights_cache() -> None:
    """Best-effort refresh of the Charts Gallery highlights/version R2 cache.

    Never blocks the run: on failure, tsm-frontend/api's own cache-on-miss
    fallback recomputes it live instead.
    """
    script = REPO_ROOT / "scripts" / "generate_home_highlights.py"
    if not script.exists():
        print(f"[Apple Music] Home highlights cache script missing: {script}")
        return

    print("[Apple Music] Refreshing home highlights/version R2 cache...")
    returncode = run_child([sys.executable, str(script), "--quiet"], timeout=600)
    if returncode != 0:
        print(f"[Apple Music] Home highlights cache refresh exited with code {returncode}")


def generate_country_cards(scraped_at: str, force: bool = False) -> int:
    script = HERE / "generate_country_card_images.py"
    if not script.exists():
        print(f"[Apple Music] Card image script missing: {script}")
        return 1

    chart_date = scraped_at.split("T", 1)[0]
    args = [sys.executable, str(script), chart_date, "--min-countries", "1"]
    if force:
        args.append("--force")

    return _run_logged_block("Generating country chart card images", args, timeout=900)


def generate_snapshot_images(scraped_at: str) -> int:
    script = HERE / "generate_snapshot_images.py"
    if not script.exists():
        print(f"[Apple Music] Snapshot image script missing: {script}")
        return 1

    chart_date = scraped_at.split("T", 1)[0]
    args = [sys.executable, str(script), "--date", chart_date]

    return _run_logged_block(
        "Generating snapshot images (Global, US, US Pop, US Country, US Alternative)", args, timeout=900
    )


def _run_logged_block(title: str, args: list[str], timeout: int) -> int:
    # Images render while the R2 upload runs: print each as one block.
    returncode, output = run_child_captured(args, timeout=timeout)
    with _PRINT_LOCK:
        print(f"[Apple Music] {title}...")
        print(output.rstrip(), flush=True)
    return returncode


def notify_global_update(scraped_at: str) -> int:
    script = HERE / "generate_snapshot_images.py"
    if not script.exists():
        print(f"[Apple Music] Snapshot image script missing: {script}")
        return 1

    chart_date = scraped_at.split("T", 1)[0]
    args = [sys.executable, str(script), "--date", chart_date, "--notify-global-only"]

    print("[Apple Music] Sending Global Apple Music notification after data export...")
    return run_child(args, timeout=300)


def run_script(script_path: Path, scraped_at: str, extra_args: list[str] | None = None) -> int:
    if not script_path.exists():
        with _PRINT_LOCK:
            print(f"[ERROR] Missing script: {script_path}")
        return 1

    chart_date = scraped_at.split("T", 1)[0]
    cmd = [sys.executable, str(script_path), "--date", chart_date, "--scraped-at", scraped_at]
    if extra_args:
        cmd.extend(extra_args)

    started = datetime.now()
    returncode, output = run_child_captured(cmd, timeout=1200)
    elapsed = (datetime.now() - started).total_seconds()

    with _PRINT_LOCK:
        print(f"\n{'=' * 80}")
        print(f"Running: {script_path.relative_to(REPO_ROOT)} (started {started:%H:%M:%S}, took {elapsed:.0f}s)")
        print(f"{'=' * 80}")
        print(output.rstrip())
        if returncode == 0:
            print(f"[OK] {script_path.name}", flush=True)
        else:
            print(f"[ERROR] {script_path.name} failed with code {returncode}", flush=True)

    return returncode


def run_group(group: list[Path], scraped_at: str) -> list[tuple[str, int]]:
    return [(script.name, run_script(script, scraped_at)) for script in group]


def main() -> None:
    if not run_guard.acquire_lock(LOCK_PATH):
        alert("Previous Apple Music run still running — this hour was skipped.", priority="default")
        sys.exit(run_guard.EXIT_SKIPPED)
    code = 0
    try:
        _main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        alert(f"Apple Music run crashed: {type(exc).__name__}: {exc}")
        code = 1
    finally:
        _wait_posts()  # never leave the posts running past the lock
        run_guard.release_lock(LOCK_PATH)
    # Every non-zero exit above has already sent its phone alert: exit
    # EXIT_ALERTED so the .bat only alerts for crashes that never reached
    # Python's alert (import error, broken interpreter...).
    sys.exit(run_guard.EXIT_ALERTED if code else 0)


_POSTS: list = []


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--no-images", action="store_true", help="Skip Apple Music country chart card images.")
    parser.add_argument("--force-images", action="store_true", help="Regenerate Apple Music country chart card images.")
    args, _unknown = parser.parse_known_args()

    scraped_at = build_scraped_at()
    print(f"[Apple Music] Starting full run - scraped_at={scraped_at}")

    os.environ["APPLE_MUSIC_SKIP_EXPORT"] = "1"

    failures: list[tuple[str, int]] = []

    collect_started = datetime.now()
    if _truthy_env("APPLE_MUSIC_PARALLEL_SCRIPTS", default=True):
        with ThreadPoolExecutor(max_workers=len(SCRIPT_GROUPS)) as executor:
            futures = [executor.submit(run_group, group, scraped_at) for group in SCRIPT_GROUPS]
            # New-release posts only read global.py + country_all.py output:
            # start them as soon as those groups are done, while genre_all /
            # ts_page_all keep running (2026-09-25, "be the first to post").
            for fut, group in zip(futures, SCRIPT_GROUPS):
                if {p.name for p in group} & POST_INPUTS:
                    fut.result()
            print(f"[Apple Music] Post inputs ready in {(datetime.now() - collect_started).total_seconds():.0f}s")
            posts = start_new_release_posts()
            if posts is not None:
                _POSTS.append(posts)
            grouped = [fut.result() for fut in futures]
        outcomes = [outcome for group in grouped for outcome in group]
    else:
        outcomes = [(script.name, run_script(script, scraped_at)) for script in SCRIPTS]
    print(f"[Apple Music] Collectors done in {(datetime.now() - collect_started).total_seconds():.0f}s")

    for name, code in outcomes:
        if code != 0:
            if name in NON_BLOCKING_SCRIPTS:
                print(f"[Apple Music] WARN: {name} failed ({code}), continuing (not part of the hourly export)")
            else:
                failures.append((name, code))

    # Retry each failed blocking collector on its own (owner 2026-09-25: "si
    # quelque chose échoue on réessaie toujours") — alert only if still failing.
    if failures:
        by_name = {p.name: p for p in SCRIPTS}
        still: list[tuple[str, int]] = []
        for name, _code in failures:
            code = run_guard.retry_step(name, lambda n=name: run_script(by_name[n], scraped_at),
                                        attempts=2, waits=(30,))
            if code != 0:
                still.append((name, code))
            elif name in POST_INPUTS and _POSTS:
                # posts ran on the failed collector's stale data: run them again
                # on the fresh CSV (platform_lock serializes the two processes).
                again = start_new_release_posts()
                if again is not None:
                    _POSTS.append(again)
        failures = still

    # Sequential mode (APPLE_MUSIC_PARALLEL_SCRIPTS=0): posts start now. Either
    # way they start even if a collector failed (the post script only uses what
    # was written, and alerts itself).
    if not _POSTS:
        posts = start_new_release_posts()
        if posts is not None:
            _POSTS.append(posts)

    print(f"\n{'=' * 80}")
    if failures:
        print("[Apple Music] Finished with errors:")
        for name, code in failures:
            print(f" - {name}: {code}")
        alert("Apple Music collection failed ("
              + ", ".join(f"{n}: {'timeout' if c == 124 else f'code {c}'}" for n, c in failures)
              + f") for {scraped_at}. No export/upload this hour.")
        _wait_posts()
        sys.exit(1)
    else:
        print("[Apple Music] All scripts completed successfully")
        
        # Export CSV to JSON for API/website
        export_code = run_guard.retry_step("export_apple_music", lambda: export_apple_music(scraped_at), waits=(10, 30))
        if export_code != 0:
            print("[Apple Music] Export failed, skipping R2 upload")
            alert(f"Apple Music export failed for {scraped_at} (site not updated this hour).")
            _wait_posts()
            sys.exit(1)

        if not args.no_post:
            notify_code = notify_global_update(scraped_at)
            if notify_code != 0:
                # 2026-09-25: a notification problem must not keep the hour's
                # data off the site (it used to skip the R2 upload).
                print("[Apple Music] WARN: Global notification failed, continuing with the R2 upload")
                alert(f"Global Apple Music notification step failed for {scraped_at} (upload continues).",
                      priority="default")
        else:
            print("[Apple Music] Global notification skipped (--no-post)")

        # Images are cosmetic (a failure must not keep the hour's data off the
        # site) and the R2 upload never reads them: render both while uploading.
        image_pool = ThreadPoolExecutor(max_workers=2)
        image_jobs = {}
        if not args.no_images:
            image_jobs = {
                "Card": image_pool.submit(generate_country_cards, scraped_at, args.force_images),
                "Snapshot": image_pool.submit(generate_snapshot_images, scraped_at),
            }

        upload_code = run_guard.retry_step("upload_ap_r2", maybe_upload_to_r2, waits=(15, 45))
        if upload_code != 0:
            # Main JSON / history-by-date upload first, so the hour is usually
            # already live even when the per-song phase failed.
            print("[Apple Music] R2 upload failed")
            # Alert now, not after images/live projection/posts (which pushed
            # it to ~HH:40 on 2026-09-25). Exit code still set at the end.
            alert(f"Apple Music R2 upload failed for {scraped_at} (site not updated this hour).", priority="default")
        # Highlights only depend on the uploaded data, not on the images:
        # refresh them now so the Charts Gallery doesn't wait on rendering.
        regenerate_home_highlights_cache()

        for kind, job in image_jobs.items():
            try:
                image_code = job.result()
            except Exception as exc:
                image_code = 1
                print(f"[Apple Music] {kind} image generation crashed: {exc!r}")
            if image_code != 0:
                print(f"[Apple Music] {kind} image generation failed (data upload unaffected)")
        image_pool.shutdown()
        trigger_live_projection(log=print)
        _wait_posts()
        print(f"{'=' * 80}")
        if upload_code != 0:
            sys.exit(upload_code)


def _wait_posts(timeout: int = 1800) -> None:
    """The new-release posts started after the collectors: wait for them so
    the next hourly run never overlaps them."""
    for proc in _POSTS:
        try:
            code = proc.wait(timeout=timeout)
            print(f"[Apple Music] New-release posts finished (code {code})")
        except subprocess.TimeoutExpired:
            print("[Apple Music] New-release posts still running after timeout — killed")
            proc.kill()
            alert("New-release posts ran > 30 min and were killed.")
    _POSTS.clear()


if __name__ == "__main__":
    main()
