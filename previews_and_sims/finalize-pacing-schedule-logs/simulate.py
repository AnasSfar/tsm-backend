"""Sim: finalize paced X scheduling + resume + live log (2026-10-04, plan 2026-10-07).

No browser, no post, no real state:
- finalize_update.update_streams_dir -> this folder (pacing state file)
- core.twitter.TWITTER_COORD_DIR -> this folder (markers + schedule registry)
- fake "now" = future dates (registry pruning uses the real clock).

A "programmed post" = what core.twitter.post_with_image does once the browser
part succeeded: _env_schedule_at() -> free_schedule_slot vs registry ->
_register_scheduled + _mark_account_scheduled + in-process counters.

Checks:
1. live_log lines (unchanged since 2026-10-04).
2. Plans for real-looking days (availability hour x number of posts), heatmap
   weights, compression to the window.
3. Multi-post step: each post takes the next planned slot (curve advances per
   post, no flat 2-min gap), resume after interruption keeps the chain.
4. Two updates: J+1 lands 40 min after J -> no slot within the collision gap of
   J's, and a live post right on a J slot waits.
5. finalize _run_subprocess piping.
"""
from __future__ import annotations

import os
import shutil
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
import post_pacing  # noqa: E402
from core import twitter as tw  # noqa: E402
from core.x_active_times import engagement_weight  # noqa: E402
from reporting import ProgressLogger  # noqa: E402

day_dir = HERE / "day"
shutil.rmtree(day_dir, ignore_errors=True)


def _sim_day_dir(stats_date, *_a, **_k):
    path = day_dir / str(stats_date)
    path.mkdir(parents=True, exist_ok=True)
    return path


fu.update_streams_dir = _sim_day_dir
shutil.rmtree(HERE / "coord", ignore_errors=True)
tw.TWITTER_COORD_DIR = HERE / "coord"
ACCOUNT = "acct"

FAKE_NOW = [datetime(2026, 10, 14, 0, 36)]


class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return FAKE_NOW[0]


fu.datetime = FakeDT


def program_step(slots: list[datetime], source: str, posts: int) -> list[datetime]:
    """Child process of one finalize step programming `posts` posts."""
    env = {
        "TWITTER_SCHEDULE_AT": slots[0].isoformat(timespec="minutes"),
        "TWITTER_SCHEDULE_SLOTS": ",".join(s.isoformat(timespec="minutes") for s in slots),
        "TWITTER_SCHEDULE_GAP_SECONDS": str(post_pacing.MIN_GAP_SECONDS),
        "TWITTER_SCHEDULE_SOURCE": source,
    }
    os.environ.update(env)
    tw._SCHEDULED_IN_PROCESS = 0
    tw._LAST_SCHEDULED_IN_PROCESS = None
    out = []
    for _ in range(posts):
        at = tw._env_schedule_at()
        assert at is not None, "slot unexpectedly too close"
        free = tw.free_schedule_slot(at, [e["at"] for e in tw.scheduled_entries(ACCOUNT)])
        tw._register_scheduled(ACCOUNT, free, tw._schedule_source())
        tw._mark_account_scheduled(ACCOUNT, free)
        tw._LAST_SCHEDULED_IN_PROCESS = free
        tw._SCHEDULED_IN_PROCESS += 1
        out.append(free)
    for key in env:
        os.environ.pop(key, None)
    return out


def run_update(stats_date: str, available_at: datetime, step_posts: list[int], *, stop_after: int | None = None):
    """Drive the paced phase like _guarded_post_step: plan, program, count."""
    source = f"streams-finalize:{stats_date}"
    state = fu._load_pacing_state(stats_date)
    FAKE_NOW[0] = available_at
    all_slots = []
    for index, posts in enumerate(step_posts):
        if stop_after is not None and index >= stop_after:
            break
        remaining = max(1.0, sum(step_posts[index:]))
        slots, scale, deadline = fu._plan_paced_slots(state, remaining)
        fu._SCHEDULE_SOURCE = source
        before = fu._my_scheduled_keys()
        got = program_step(slots, source, posts) if posts else []
        new = sorted(s for s, _c in fu._my_scheduled_keys() - before)
        assert len(new) == posts, (len(new), posts)
        if new:
            state["done"] += len(new)
            state["last_slot_at"] = new[-1].isoformat(timespec="minutes")
        fu._save_pacing_state(stats_date, state)
        all_slots += got
        # the run itself moves on ~40 s per step (image + browser)
        FAKE_NOW[0] += timedelta(seconds=40)
    return all_slots, state


def show(label: str, slots: list[datetime]) -> list[int]:
    gaps = [int((b - a).total_seconds() // 60) for a, b in zip(slots, slots[1:])]
    print(f"{label}: {len(slots)} posts {slots[0]:%a %H:%M} -> {slots[-1]:%a %H:%M} "
          f"({(slots[-1] - slots[0]).total_seconds() / 3600:.1f} h)")
    print("   slots:", " ".join(f"{s:%H:%M}" for s in slots))
    print("   gaps (min):", gaps)
    return gaps


# 1. live log + progress
print("== 1. live log")
live_log.set_phase("COLLECTE", detail="sim")
prog = ProgressLogger("normal")
for i in range(1, 41):
    prog(i, 40, f"Track {i}", {"status": "updated" if i % 7 else "pending", "streams": 1, "previous_streams": 1})
    time.sleep(0.02)
live_log.set_phase("SONDE Spotify", detail="attente")
time.sleep(2.5)

# 2. real-looking days. Weekday finalize after the urgent phase: ~13 album
# cards alternating with best-day / overtakes (2+2) / milestone, tables (2).
WEEKDAY = [1, 1, 1, 1, 1, 2, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 2]  # 20 posts
print("== 2. plans")
results = {}
for name, at, steps in [
    ("Wed 00:36 (like 2026-10-07)", datetime(2026, 10, 14, 0, 36), WEEKDAY),
    ("Tue 18:00", datetime(2026, 10, 20, 18, 0), WEEKDAY),
    ("Thu 22:30", datetime(2026, 10, 22, 22, 30), WEEKDAY),
    ("Sat 16:30 weekend (6 posts)", datetime(2026, 10, 24, 16, 30), [1, 1, 1, 1, 2]),
    ("Fri 03:30 catch-up, 30 posts", datetime(2026, 10, 30, 3, 30), [1] * 26 + [2, 2]),
]:
    stats = f"sim-{at:%m%d%H%M}"
    slots, state = run_update(stats, at, steps)
    gaps = show(name, slots)
    results[name] = (slots, gaps, state)
    assert all(g >= 3 for g in gaps), "min gap 3 min"
    assert slots[-1] <= datetime.fromisoformat(state["deadline"]) or min(gaps) == 3, "fits the window"
    shutil.rmtree(HERE / "coord", ignore_errors=True)

# 3. multi-post step + resume
print("== 3. multi-post + resume")
first, st = run_update("sim-resume", datetime(2026, 10, 15, 21, 0), [1, 3, 1, 1, 1, 1], stop_after=3)
print("before stop:", " ".join(f"{s:%H:%M}" for s in first), "state", st)
again, st2 = run_update("sim-resume", FAKE_NOW[0] + timedelta(minutes=5), [1, 1, 1])
print("after resume:", " ".join(f"{s:%H:%M}" for s in again), "state", st2)
seq = first + again
gaps = [(b - a).total_seconds() / 60 for a, b in zip(seq, seq[1:])]
print("gaps (min):", [int(g) for g in gaps])
assert gaps[1] > gaps[0] - 1 and gaps[2] > gaps[1] - 1, "overtake posts follow the curve, not a flat gap"
assert again[0] > first[-1], "resume chains after the last slot"
assert st2["done"] == 8

# 4. two updates
print("== 4. collision between two updates")
shutil.rmtree(HERE / "coord", ignore_errors=True)
j_slots, _ = run_update("sim-J", datetime(2026, 10, 16, 19, 0), WEEKDAY)
k_slots, _ = run_update("sim-J1", datetime(2026, 10, 16, 19, 40), WEEKDAY)
show("J", j_slots)
show("J+1", k_slots)
closest = min(abs((a - b).total_seconds()) for a in j_slots for b in k_slots)
print("closest J / J+1 slots:", int(closest), "s")
assert closest >= tw.TWITTER_SCHEDULE_COLLISION_SECONDS
# live post at a J slot: the guard computes a wait (sleep stubbed)
waits = []
real_sleep = tw.time.sleep
tw.time.sleep = lambda s: waits.append(s) or FAKE_CLOCK.__setitem__(0, FAKE_CLOCK[0] + timedelta(seconds=s))
FAKE_CLOCK = [j_slots[3] - timedelta(seconds=30)]


class ClockDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return FAKE_CLOCK[0]


real_dt = tw.datetime
tw.datetime = ClockDT
tw._wait_scheduled_collision(ACCOUNT)
tw.datetime = real_dt
tw.time.sleep = real_sleep
print(f"live post at {j_slots[3] - timedelta(seconds=30):%H:%M:%S} next to J slot {j_slots[3]:%H:%M}: waited {[int(w) for w in waits]} s")
assert waits and sum(waits) >= 60

# 5. subprocess piping
print("== 5. _run_subprocess piping")
res = fu._run_subprocess([sys.executable, "-c", "print('child line 1'); print('child line 2')"], check=False)
print("child rc", res.returncode)
print("weights Wed 00h/04h/16h/21h:", [engagement_weight(datetime(2026, 10, 14, h)) for h in (0, 4, 16, 21)])
print("SIM OK")
