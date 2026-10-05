"""Simulate a first week of daily-view posts (core/first_week.py) — isolated state.

Uses the REAL Patient Zero release record (Day 1 = real exact +24h capture)
copied into a temp registry; Days 2..7 use FAKE cumulative view counts.
Nothing is posted, no Scheduled Task is created, real tools/json is untouched.

Scenario A: every mark captured on time.
Scenario B: the +96h mark is missed (PC asleep) -> since 2026-10-04 Day 4 and
  Day 5 are estimated (core/estimate.py) between the real marks and posted "~X (est.)".
Scenario C (2026-10-04, "ce soir"): REAL current Patient Zero state (marks 1-3
  real, +96h really missed) + REAL daily-run snapshots as anchors; only the
  +120h reading is FAKE (12,780,000).
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
    # Fake marks must not be mixed with the REAL daily-run snapshots.
    fw.estimate.csv_anchors = lambda *a, **k: []
    fw.estimate.registry_anchors = lambda *a, **k: {}
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


def run_tonight() -> None:
    import json
    real_anchors = _real_csv_anchors
    tmp = Path(tempfile.mkdtemp())
    (tmp / "first_week").mkdir()
    shutil.copy(ROOT / "collectors/youtube/tools/json/first_week/mw3kSNIxjqo.json", tmp / "first_week")
    fw.FIRST_WEEK_DIR = tmp / "first_week"
    fw.estimate.csv_anchors = real_anchors
    fw.estimate.registry_anchors = _real_registry
    fw._register_one_off_task = lambda name, when, args, log=print: print("next task:", name, when) or True
    fw.unregister_task = lambda name: None
    posted = []
    state = fw.load_state("mw3kSNIxjqo")
    due = fw.mark_due(state, 5)
    fetch = lambda ids: {"mw3kSNIxjqo": {"viewCount": "12780000"}}  # FAKE +120h reading
    print("\n######## Scenario C_tonight")
    fw.capture_and_post("mw3kSNIxjqo", due, fetch, poster=lambda p: posted.extend(p) or True,
                        out_root=OUT / "C_tonight")
    print("\n".join(fw.status_lines(due)))
    for tweet, image in posted:
        print("-----\n" + tweet + "\n->", image)
    # Day 7 chart preview with the estimated days (marks 6-7 FAKE).
    state = fw.load_state("mw3kSNIxjqo")
    fw._record_mark(state, 6, 13_900_000, fw.mark_due(state, 6), "sim")
    fw._record_mark(state, 7, 14_950_000, fw.mark_due(state, 7), "sim")
    print("week chart:", fw.render_day(state, 7, OUT / "C_tonight"))


_real_csv_anchors = fw.estimate.csv_anchors
_real_registry = fw.estimate.registry_anchors
run("A_all_on_time", set())
run("B_missed_96h", {4})
fw.estimate.csv_anchors = _real_csv_anchors
run_tonight()
