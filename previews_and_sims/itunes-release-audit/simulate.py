"""iTunes-side readiness audit (2026-09-24) of post_new_release_progression.py.

FAKE DATA: every CSV row below is fabricated (ranks invented) to exercise the
real code path. Nothing outside this folder is read for chart data or written:
sources/state/locks/out dir are monkeypatched, no network, no render, no post.
"""
import csv, json, shutil, sys, traceback
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "collectors" / "apple_music"))
import post_new_release_progression as m  # noqa: E402

W = Path(__file__).resolve().parent / "work"
if W.exists():
    shutil.rmtree(W)
W.mkdir()
m.OUT_DIR = W / "cards"; m.LOCKS_DIR = W / "locks"; m.STATE_DIR = W  # per-platform state files since 2026-09-24
m._render_card_png = lambda html, out, scale=3: (out.parent.mkdir(parents=True, exist_ok=True), out.write_bytes(b""))
m._remote_data_uri = lambda url: ""
m._album_snapshot_post = lambda album, today, state: None
EMPTY = W / "empty.csv"; ITUNES = W / "itunes.csv"
IF = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank",
      "image_url", "url", "artist_name", "album_name", "genre_names", "release_date", "explicitness"]
_orig = m.build_sources
def fake_sources(today):
    s = _orig(today)
    for x in s:
        x["path"] = ITUNES if x["platform"] == "itunes" else EMPTY
        x["path_for"] = (lambda _d, _p=x["path"]: _p)
    return s
m.build_sources = fake_sources
class Args: platform="all"; scraped_at=None; window_hours=72; no_post=True; dry_run=False
m.parse_args = lambda: Args()
TWEETS = []
_orig_build = m._build_card_and_tweet
def spy(**kw):
    t, p = _orig_build(**kw); TWEETS.append(t); print("TWEET >>>", repr(t), "len", len(t)); return t, p
m._build_card_and_tweet = spy

def write(cycles):
    EMPTY.write_text("")
    with ITUNES.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=IF); w.writeheader()
        for sa, data in cycles:
            for cc, ranks in data.items():
                for title, rank in ranks.items():
                    w.writerow({"date": sa[:10], "scraped_at": sa, "country": cc, "chart_type": "itunes_country",
                                "song_name": title, "rank": rank, "artist_name": "Taylor Swift",
                                "album_name": "The Life of a Showgirl: The Encore"})

def run(label, cycles, now_utc, reset=False, dry=False):
    print("=" * 90); print(label)
    if reset:
        for _f in W.glob("new_release_progression_state_*.json"):
            _f.unlink()
    Args.dry_run = dry
    write(cycles); m._now_paris = lambda: now_utc
    try:
        m.main()
    except Exception:
        print("!!! CRASH:"); traceback.print_exc(limit=2)
    Args.dry_run = False

P = "2026-09-25T{:02d}:00:00+02:00"
# A: staggered — FR store gets Babylon at 00:00 Paris, before the script window opens (release_date 00:00Z = 02:00 Paris)
cA = [(P.format(0), {"fr": {"Babylon": 4}}), (P.format(1), {"fr": {"Babylon": 4}}), (P.format(2), {"fr": {"Babylon": 4}})]
run("A — FR had Babylon at 00:00/01:00 (pre-window), window opens 02:00, only FR present, unchanged", cA,
    datetime(2026, 9, 25, 0, 30, tzinfo=timezone.utc), reset=True)
# B: simultaneous 06:00 Paris release; 07:00 cycle sees all 4 in US/GB/FR/CA
T = ["Patient Zero", "Cleveland!", "Pink Clouding", "Babylon"]
c7 = (P.format(7), {"us": dict(zip(T, [2, 5, 7, 1])), "gb": dict(zip(T, [3, 4, 9, 1])),
                    "fr": dict(zip(T, [2, 6, 8, 1])), "ca": dict(zip(T, [1, 3, 6, 2]))})
run("B — first iTunes observation 07:00 (US/GB/FR/CA)", [c7], datetime(2026, 9, 25, 5, 30, tzinfo=timezone.utc), reset=True)
# C: 08:00 AU appears + US moves
c8 = (P.format(8), {**c7[1], "us": dict(zip(T, [1, 5, 7, 2])), "au": dict(zip(T, [2, 3, 4, 1]))})
run("C — 08:00: AU debut + US moves", [c7, c8], datetime(2026, 9, 25, 6, 30, tzinfo=timezone.utc))
# D: 09:00 only Pink Clouding moves in GB
c9 = (P.format(9), {**c8[1], "gb": {**c8[1]["gb"], "Pink Clouding": 6}})
run("D — 09:00: single move", [c7, c8, c9], datetime(2026, 9, 25, 7, 30, tzinfo=timezone.utc))
# E: dry-run at 10:00 persists state? then real run at same cycle posts nothing
c10 = (P.format(10), {**c9[1], "fr": {**c9[1]["fr"], "Cleveland!": 2}})
_sp = m.state_path("itunes")
before = _sp.read_text() if _sp.exists() else ""
run("E1 — --dry-run on 10:00 cycle", [c7, c8, c9, c10], datetime(2026, 9, 25, 8, 30, tzinfo=timezone.utc), dry=True)
print("state changed by dry-run:", before != (_sp.read_text() if _sp.exists() else ""))
n = len(TWEETS)
run("E2 — real run, same 10:00 cycle, after the dry-run", [c7, c8, c9, c10], datetime(2026, 9, 25, 8, 35, tzinfo=timezone.utc))
print("tweets produced by E2:", len(TWEETS) - n)
# F: title variants
cF = (P.format(7), {"us": {"Cleveland": 3, "Babylon (feat. Someone)": 1, "Pink Clouding": 5, "Patient Zero (Encore)": 8}})
run("F — title variants from Apple", [cF], datetime(2026, 9, 25, 5, 30, tzinfo=timezone.utc), reset=True)
