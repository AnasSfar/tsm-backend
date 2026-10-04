"""Sim: finalize paced X scheduling + resume + live log (2026-10-04).

No browser, no post, no real state:
- finalize_update.update_streams_dir -> this folder (pacing state file)
- core.twitter.TWITTER_COORD_DIR -> this folder (last_post / last_schedule markers)

Checks:
1. live_log: [HH:MM:SS] prefix, [PHASE] lines, [STATUS] heartbeat, ProgressLogger
   feeding progress/ETA.
2. Slot chain: 8 paced posts scheduled; interruption after 4 (state reloaded
   from disk) must continue the same curve, never restart at +60 s.
3. core.twitter._env_schedule_at: a process programming several posts with the
   same TWITTER_SCHEDULE_AT spaces them by TWITTER_SCHEDULE_GAP_SECONDS; a slot
   too close falls back to live.
4. finalize _run_subprocess: child output re-printed through the live log.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STREAMS = REPO / "collectors" / "spotify" / "streams"
sys.path[:0] = [str(REPO / "collectors" / "spotify"), str(STREAMS), str(STREAMS / "tools" / "scripts")]

import live_log  # noqa: E402

live_log.STATUS_EVERY_SECONDS = 2.0
live_log.install("sim", "2026-10-03", own_file=True, log_dir=HERE)

import finalize_update as fu  # noqa: E402
from core import twitter as tw  # noqa: E402
from reporting import ProgressLogger  # noqa: E402

day_dir = HERE / "day"
day_dir.mkdir(exist_ok=True)
fu.update_streams_dir = lambda *_a, **_k: day_dir
tw.TWITTER_COORD_DIR = HERE / "coord"
(day_dir / fu.PACING_STATE_FILENAME).unlink(missing_ok=True)

# 1. live log + progress
print("== 1. live log")
live_log.set_phase("COLLECTE", detail="sim")
prog = ProgressLogger("normal")
for i in range(1, 41):
    prog(i, 40, f"Track {i}", {"status": "updated" if i % 7 else "pending", "streams": 1, "previous_streams": 1})
    time.sleep(0.05)
print("status:", live_log.status_text(bar=False))
live_log.set_phase("SONDE Spotify", detail="attente")
time.sleep(5.5)  # expect >= 1 [STATUS] heartbeat line

# 2. slot chain + resume
print("== 2. slot chain + resume")
state = {"active": True, **fu._load_pacing_state("2026-10-03")}
slots = []
for k in range(8):
    if k == 4:
        print("-- simulated interruption: reload state from disk --")
        state = {"active": True, **fu._load_pacing_state("2026-10-03")}
        print("reloaded:", state)
    slot = fu._next_schedule_slot(state)
    tw._mark_account_scheduled("acct", slot)
    at, scheduled = fu._account_last_schedule_marker()
    state["done"] += 1
    state["last_slot_at"] = scheduled
    fu._save_pacing_state("2026-10-03", state)
    slots.append(slot)
    print(f"paced #{k + 1}: {slot:%H:%M} (spacing k={k}: {fu._paced_spacing_seconds(k)}s)")
gaps = [int((b - a).total_seconds()) for a, b in zip(slots, slots[1:])]
print("gaps (s):", gaps)
assert all(g >= 60 for g in gaps), "slots must be at least a minute apart"
assert gaps[3] >= gaps[2] - 60, "resume must not restart the curve"

# 3. in-process repeat + too-close fallback
print("== 3. core.twitter schedule env")
os.environ["TWITTER_SCHEDULE_GAP_SECONDS"] = "200"
target = (datetime.now() + timedelta(minutes=10)).replace(second=0, microsecond=0)
os.environ["TWITTER_SCHEDULE_AT"] = target.isoformat(timespec="minutes")
tw.TWITTER_SCHEDULE_GAP_SECONDS = 200
first = tw._env_schedule_at()
tw._LAST_SCHEDULED_IN_PROCESS = first
second = tw._env_schedule_at()
print("first", first, "second", second)
assert second > first
os.environ["TWITTER_SCHEDULE_AT"] = (datetime.now() + timedelta(seconds=30)).isoformat(timespec="minutes")
tw._LAST_SCHEDULED_IN_PROCESS = None
assert tw._env_schedule_at() is None, "too close -> live"
os.environ.pop("TWITTER_SCHEDULE_AT")

# 4. subprocess piping
print("== 4. _run_subprocess piping")
res = fu._run_subprocess([sys.executable, "-c", "print('child line 1'); print('child line 2')"], check=False)
print("child rc", res.returncode)
print("SIM OK")
