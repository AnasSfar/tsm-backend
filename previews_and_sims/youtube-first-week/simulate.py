"""Simulate a first week of daily-view posts (core/first_week.py) — isolated state.

Uses the REAL Patient Zero release record (Day 1 = real exact +24h capture)
copied into a temp registry; Days 2..7 use FAKE cumulative view counts.
Nothing is posted, no Scheduled Task is created, real tools/json is untouched.

Scenario A: every mark captured on time.
Scenario B: the +96h mark is missed (PC asleep) -> Day 4 and Day 5 are n/a.
Outputs: previews_and_sims/youtube-first-week/out/<scenario>/
"""
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import collectors.youtube.core.first_week as fw  # noqa: E402

REAL_RELEASE = ROOT / "collectors/youtube/tools/json/first_day/releases/mw3kSNIxjqo.json"
# FAKE cumulative views at +48h..+168h (Day 1 = real 4,497,126).
FAKE_CUMULATIVE = {2: 6_602_310, 3: 8_115_902, 4: 9_380_441, 5: 10_412_037, 6: 11_336_580, 7: 12_190_884}
OUT = Path(__file__).resolve().parent / "out"


def run(scenario: str, missed: set[int]) -> None:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "releases").mkdir()
    shutil.copy(REAL_RELEASE, tmp / "releases" / REAL_RELEASE.name)
    fw.RELEASES_DIR, fw.FIRST_WEEK_DIR = tmp / "releases", tmp / "first_week"
    tasks = []
    fw._register_one_off_task = lambda name, when, args, log=print: tasks.append((name, when)) or True
    fw.unregister_task = lambda name: None
    out_root = OUT / scenario
    posted = []
    poster = lambda posts: posted.extend(posts) or True  # noqa: E731

    state = None
    print(f"\n######## Scenario {scenario}")
    published = None
    fw.sync(fw.parse_utc("2026-09-30T22:15:20Z"), poster=poster)  # right after the first-day post
    state = fw.load_state("mw3kSNIxjqo")
    published = fw.parse_utc(state["published_at"])
    print("next task:", tasks[-1])
    for n in range(2, 8):
        due = published + n * fw.FIRST_DAY
        if n in missed:
            continue  # PC asleep: the task never ran; the next one settles it
        fetch = lambda ids, n=n: {"mw3kSNIxjqo": {"viewCount": str(FAKE_CUMULATIVE[n])}}
        fw.capture_and_post("mw3kSNIxjqo", due, fetch, poster=poster, out_root=out_root)
        print("next task:", tasks[-1] if tasks else None)
    print("\n".join(fw.status_lines(published + timedelta(days=8))))
    print("final status:", fw.load_state("mw3kSNIxjqo")["status"])
    for tweet, image in posted:
        print("-----\n" + tweet + "\n->", image)


run("A_all_on_time", set())
run("B_missed_96h", {4})
