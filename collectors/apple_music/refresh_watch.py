#!/usr/bin/env python3
"""Apple Music refresh watch (2026-09-26, owner: "it's been two hours since we
posted apple music... fix it for once").

Apple refreshes its country charts a few times a day, at an unpredictable
minute (seen 2026-09-25/26: ~12, 14, 17, 20, 22, 00:xx). The collector only
runs hourly at HH:00, so a refresh at HH:05 waited ~55 min to be collected and
posted. This watch fills the gap: started DETACHED by run_apple_music.py at the
end of a successful hourly run, it polls the live Taylor rows of a few key
countries' Top Songs (1 request per country) every
APPLE_MUSIC_WATCH_INTERVAL_SECONDS (120). As soon as they differ from the last
stored snapshot, it starts a full run_apple_music.py (same HH:00 slot) and
exits — the posts land a few minutes after Apple's refresh.

Guards:
- Stops at HH:APPLE_MUSIC_WATCH_DEADLINE_MINUTE (40): a full run takes ~17 min
  and the scheduler skips its HH:00 trigger while a run holds the lock.
- Exits at once when this hour's run already captured a refresh (the HH:00
  snapshot differs from the one before): re-running the same slot would
  overwrite it after its posts were made (locks are per scraped_at).
- Single instance (tools/locks/refresh_watch.lock); the run it starts gets
  APPLE_MUSIC_REFRESH_WATCH=0 so it doesn't start another watch.
- Never alerts: a failed poll just waits for the next one; the hourly run
  stays the source of truth.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for p in (str(REPO_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from collectors.spotify.core import run_guard  # noqa: E402

LOCK_PATH = HERE / "tools" / "locks" / "refresh_watch.lock"
RUN_LOG = HERE / "run_apple_music.log"
INTERVAL = float(os.getenv("APPLE_MUSIC_WATCH_INTERVAL_SECONDS", "120"))
DEADLINE_MINUTE = int(os.getenv("APPLE_MUSIC_WATCH_DEADLINE_MINUTE", "40"))
COUNTRIES = [c.strip().lower() for c in os.getenv("APPLE_MUSIC_WATCH_COUNTRIES", "us,gb").split(",") if c.strip()]


def log(msg: str) -> None:
    print(f"[refresh_watch {datetime.now():%H:%M:%S}] {msg}", flush=True)


def _signature(rows: list[dict]) -> list[tuple[str, str]]:
    return sorted((str(r.get("apple_music_id") or r.get("song_name") or ""), str(r.get("rank") or "")) for r in rows)


def _stored(country: str) -> tuple[list[tuple[str, str]], bool] | None:
    """(signature of the latest stored snapshot, whether that snapshot is
    already a refresh of THIS hour), None when nothing is stored."""
    import generate_snapshot_images as snap

    now = datetime.now()
    cycles: dict[str, dict[str, int]] = {}
    for day in sorted({now.strftime("%Y-%m-%d"), (now.fromordinal(now.toordinal() - 1)).strftime("%Y-%m-%d")}):
        cycles.update(snap._day_cycles(day, country, None))
    if not cycles:
        return None
    keys = sorted(cycles)
    latest = keys[-1]
    rows, _at = snap.get_region_rows(latest[:10], country, None)
    this_hour = latest[:13] == now.strftime("%Y-%m-%dT%H")
    fresh = this_hour and len(keys) > 1 and cycles[latest] != cycles[keys[-2]]
    return _signature(rows), fresh


def _live(country: str, session, manager) -> list[tuple[str, str]]:
    import country_all

    songs, _albums = country_all.fetch_country(session, manager, country)
    return _signature(songs)


def main() -> int:
    if os.getenv("APPLE_MUSIC_REFRESH_WATCH", "0") != "1":
        return 0
    if not run_guard.acquire_lock(LOCK_PATH):
        log("another watch is running — exit")
        return 0
    try:
        return _watch()
    finally:
        run_guard.release_lock(LOCK_PATH)


def _watch() -> int:
    from core.http import build_session
    from core.token import TokenManager

    started_hour = datetime.now().strftime("%Y-%m-%dT%H")
    baseline: dict[str, list[tuple[str, str]]] = {}
    for country in COUNTRIES:
        stored = _stored(country)
        if stored is None:
            continue
        signature, fresh = stored
        if fresh:
            log(f"{country}: this hour's run already captured a refresh — nothing to watch")
            return 0
        baseline[country] = signature
    if not baseline:
        log("no stored snapshot — exit")
        return 0
    session = build_session()
    manager = TokenManager(session)
    log(f"watching {', '.join(baseline)} every {INTERVAL:g}s until HH:{DEADLINE_MINUTE:02d}")
    while True:
        now = datetime.now()
        if now.strftime("%Y-%m-%dT%H") != started_hour or now.minute >= DEADLINE_MINUTE:
            log("deadline reached — the next hourly run takes over")
            return 0
        for country, signature in baseline.items():
            try:
                live = _live(country, session, manager)
            except Exception as exc:  # network blip: next poll
                log(f"{country}: poll failed ({type(exc).__name__}: {exc})")
                continue
            if live and live != signature:
                log(f"{country}: Apple refreshed its chart — starting a full run now")
                return _start_run()
        time.sleep(INTERVAL)


def _start_run() -> int:
    env = os.environ.copy()
    env["APPLE_MUSIC_REFRESH_WATCH"] = "0"  # the triggered run doesn't watch again
    # Its OWN snapshot time (e.g. 10:39:30), not the HH:00 slot (2026-09-26):
    # with HH:00 it shared the post locks (per scraped_at) of the hourly run —
    # the 10:39 refresh found the 10:00 thread lock taken and posted nothing,
    # its moves only went out at 11:04.
    env["APPLE_MUSIC_ROUND_SCRAPED_AT"] = "0"
    env["PYTHONUNBUFFERED"] = "1"
    with open(RUN_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"[Apple Music] Refresh watch: Apple refreshed at {datetime.now():%H:%M:%S}, triggered run\n")
        fh.flush()
        return subprocess.call([sys.executable, "-u", str(HERE / "run_apple_music.py")],
                               cwd=REPO_ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT)


if __name__ == "__main__":
    sys.exit(main())
