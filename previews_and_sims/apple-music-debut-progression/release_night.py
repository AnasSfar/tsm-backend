"""Release night simulation (The Encore, Fri 2026-09-25) of the REAL
post_new_release_progression.py after the 2026-09-24 night fixes: automatic
release detection, match by Apple id, retry after a failed post, dry-run that
writes nothing, volume control, new tweet texts, crash isolation, alerts.

FAKE DATA: every CSV row, the Apple availability answer, the X post results
and the alerts are fabricated. State/locks/cards/CSVs all live in ./night/;
no network, no render, no real post (post_with_image is replaced by a fake).
Run: python release_night.py > release_night.log
"""
import csv
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "collectors" / "apple_music"))
import post_new_release_progression as m  # noqa: E402
import collectors.spotify.core.twitter as tw  # noqa: E402

W = Path(__file__).resolve().parent / "night"
if W.exists():
    shutil.rmtree(W)
W.mkdir()
m.OUT_DIR, m.LOCKS_DIR, m.STATE_DIR = W / "cards", W / "locks", W
m._render_card_png = lambda html, out, scale=3: (out.parent.mkdir(parents=True, exist_ok=True), out.write_bytes(b""))
m._remote_data_uri = lambda url: ""
m.POST_RETRY_WAIT = 0  # post retries are instant in the sim

ITUNES, EMPTY, ALBUMS = W / "itunes_top_songs.csv", W / "empty.csv", W / "itunes_top_albums.csv"
EMPTY.write_text("")
m.itunes_daily_csv = lambda d, name: ALBUMS if "albums" in name else ITUNES   # also used by _worldwide_stats
m.apple_music_daily_csv = lambda d, name: EMPTY
_orig_sources = m.build_sources


def fake_sources(today, express=None):
    out = _orig_sources(today, express)
    for s in out:
        s["path"] = ALBUMS if s.get("kind") == "album" else ITUNES if s["platform"] == "itunes" else EMPTY
        s["path_for"] = (lambda _d, _p=s["path"]: _p)   # history lookups too (skill pitfall)
    return out


m.build_sources = fake_sources

AVAILABLE = {"now": False}
m._apple_availability = lambda ids, countries: {i: AVAILABLE["now"] for i in ids}
ALERTS, POSTS, FAIL_TITLES = [], [], set()
m.alert = lambda msg, **kw: (ALERTS.append(msg), print(f"   ALERT >>> {msg}"))


FAIL_ONCE, UNCONFIRMED = set(), set()
tw.get_last_post_error = lambda: LAST_ERR["e"]
LAST_ERR = {"e": ""}


def fake_post(tweet, image, session, **kw):
    if kw.get("skip_if") and kw["skip_if"]():
        return True
    title = tweet.split('"')[1] if '"' in tweet else tweet[:40]
    LAST_ERR["e"] = ""
    if title in FAIL_ONCE:
        FAIL_ONCE.discard(title)
        print(f"   X POST FAILED ONCE (fake, browser error) for {title}")
        LAST_ERR["e"] = "browser crashed"
        return False
    if title in UNCONFIRMED:
        UNCONFIRMED.discard(title)
        print(f"   X POST UNCONFIRMED (fake) for {title}")
        LAST_ERR["e"] = "post non confirme apres clic"
        return False
    if title in FAIL_TITLES:
        print(f"   X POST FAILED (fake) for {title}")
        return False
    POSTS.append(tweet)
    print("   POSTED >>>\n      " + tweet.replace("\n", "\n      "))
    return True


tw.post_with_image = fake_post

IF = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank",
      "image_url", "url", "artist_name", "album_name", "genre_names", "release_date", "explicitness"]
IDS = {t: next(iter(v)) for t, v in m.KNOWN_APPLE_IDS.items()}
CYCLES: list = []


def add_cycle(hour, ranks, worldwide_no1=None, names=None):
    """ranks = {title: {country: rank}}; worldwide_no1 = {title: n extra #1 countries}."""
    sa = f"2026-09-25T{hour:02d}:00:00+02:00"
    rows = []
    for title, per in ranks.items():
        for cc, rank in per.items():
            rows.append((sa, cc, (names or {}).get(title, title), IDS[title], rank))
    extra = ["de", "it", "es", "nl", "br", "mx", "se", "no", "dk", "fi", "pl", "pt", "ie", "nz", "at", "ch", "be",
             "jp", "kr", "ph", "sg", "my", "id", "th", "tr", "za", "ar", "cl", "co", "pe", "gr", "cz", "hu", "ro"]
    for title, n in (worldwide_no1 or {}).items():
        for cc in extra[:n]:
            rows.append((sa, cc, title, IDS[title], 1))
    # every other title: a few top-10 and lower placements in extra countries
    for title in ranks:
        if title in (worldwide_no1 or {}):
            continue
        for i, cc in enumerate(extra[:18]):
            rows.append((sa, cc, title, IDS[title], 1 if i < 2 else (4 + i) if i < 7 else 20 + i))
    CYCLES.append(rows)
    with ITUNES.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=IF)
        w.writeheader()
        for cyc in CYCLES:
            for sa_, cc, name, aid, rank in cyc:
                w.writerow({"date": "2026-09-25", "scraped_at": sa_, "country": cc, "chart_type": "itunes_country",
                            "song_name": name, "apple_music_id": aid, "rank": rank, "artist_name": "Taylor Swift",
                            "album_name": "The Life of a Showgirl: The Encore"})


AF = ["date", "scraped_at", "country", "chart_type", "album_name", "apple_music_id", "rank", "previous_rank",
      "image_url", "url", "artist_name", "genre_names", "release_date"]
ALBUM_CYCLES: list = []


def add_album_cycle(hour, per_country, extra_no1=0):
    sa = f"2026-09-25T{hour:02d}:00:00+02:00"
    rows = [(sa, cc, r) for cc, r in per_country.items()]
    rows += [(sa, cc, 1) for cc in ["de", "it", "es", "nl", "br", "mx", "se", "no", "dk", "fi", "pl", "pt", "ie",
                                     "nz", "at", "ch", "be", "jp", "kr", "ph"][:extra_no1]]
    ALBUM_CYCLES.append(rows)
    with ALBUMS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=AF)
        w.writeheader()
        for cyc in ALBUM_CYCLES:
            for sa_, cc, rank in cyc:
                w.writerow({"date": "2026-09-25", "scraped_at": sa_, "country": cc, "chart_type": "itunes_country",
                            "album_name": "The Life of a Showgirl: The Encore", "apple_music_id": "6814997249",
                            "rank": rank, "artist_name": "Taylor Swift"})
            # an OLD Showgirl edition that must never match the Encore item
            w.writerow({"date": "2026-09-25", "scraped_at": cyc[0][0], "country": "us", "chart_type": "itunes_country",
                        "album_name": "The Life of a Showgirl", "apple_music_id": "1833328839", "rank": 4,
                        "artist_name": "Taylor Swift"})


class Args:
    platform = "itunes"
    scraped_at = None
    window_hours = 72
    no_post = False
    dry_run = False


m.parse_args = lambda: Args()


def run(label, hour, minute=3, dry=False):
    print("=" * 100)
    print(f"{label}   (fake now = Fri {hour:02d}:{minute:02d} Paris, iTunes chain)")
    Args.dry_run = dry
    m._now_paris = lambda: datetime(2026, 9, 25, hour, minute, tzinfo=ZoneInfo("Europe/Paris"))
    n_posts = len(POSTS)
    m.main()
    Args.dry_run = False
    print(f"   -> {len(POSTS) - n_posts} post(s) this cycle")


state_file = lambda: m.state_path("itunes")  # noqa: E731

# 1. 05:03 — not out yet: Apple says not available, nothing on the charts.
run("1. before release", 5)

# 2. 06:03 — out. Debuts everywhere; Cleveland!'s X post fails.
AVAILABLE["now"] = True
FAIL_TITLES.add("Cleveland!")      # fails all 3 attempts -> alert, retried next cycle
FAIL_ONCE.add("Pink Clouding")     # fails once -> posted on the immediate retry
UNCONFIRMED.add("Patient Zero")    # unconfirmed -> counted as posted, alert, no repost
add_cycle(6, {
    "Babylon": {"us": 1, "gb": 1, "fr": 1, "ca": 2, "au": 3},
    "Patient Zero": {"us": 5, "gb": 4, "fr": 7, "ca": 6, "au": 9},
    "Cleveland!": {"us": 3, "gb": 2},
    "Pink Clouding": {"us": 8},
}, worldwide_no1={"Babylon": 34})
add_album_cycle(6, {"us": 1, "gb": 1, "fr": 2, "ca": 1, "au": 1}, extra_no1=18)
run("2. release: debuts + a failed post", 6)

# 3. dry-run of the next cycle must not change the state.
before = state_file().read_text()
FAIL_TITLES.clear()
add_cycle(7, {
    "Babylon": {"us": 1, "gb": 1, "fr": 2, "ca": 1, "au": 2},
    "Patient Zero": {"us": 3, "gb": 4, "fr": 7, "ca": 6, "au": 9},
    "Cleveland!": {"us": 2, "gb": 2},
    "Pink Clouding": {"us": 11},
}, worldwide_no1={"Babylon": 36})
add_album_cycle(7, {"us": 1, "gb": 1, "fr": 1, "ca": 1, "au": 1}, extra_no1=20)
run("3a. --dry-run on the 07:00 cycle", 7, dry=True)
print("   state changed by dry-run:", before != state_file().read_text())

# 3b. real 07:03 run: Cleveland! retried as a debut, Babylon new #1 in Canada,
#     Patient Zero new peak #3 US, Pink Clouding only dropped (not posted).
run("3b. real 07:00 cycle", 7)

# 4. 08:03 — minor climb only (Patient Zero AU 9->8), last post 1h ago -> throttled.
#    Apple renames Babylon with a feat. — still matched by id.
add_cycle(8, {
    "Babylon": {"us": 1, "gb": 1, "fr": 2, "ca": 1, "au": 2},
    "Patient Zero": {"us": 3, "gb": 4, "fr": 7, "ca": 6, "au": 8},
    "Cleveland!": {"us": 2, "gb": 2},
    "Pink Clouding": {"us": 11},
}, worldwide_no1={"Babylon": 36}, names={"Babylon": "Babylon (feat. Someone)"})
run("4. minor move 1h after last post + renamed title", 8)

# 5. 10:03 — same minor gap now 3h -> posted.
add_cycle(10, {
    "Babylon": {"us": 1, "gb": 1, "fr": 2, "ca": 1, "au": 2},
    "Patient Zero": {"us": 3, "gb": 4, "fr": 7, "ca": 6, "au": 8},
    "Cleveland!": {"us": 2, "gb": 2},
    "Pink Clouding": {"us": 11},
}, worldwide_no1={"Babylon": 36})
run("5. same minor move 3h after last post", 10)

# 6. crash isolation: one track blows up, the others still run + alert.
_orig_ww = m._worldwide_stats
m._worldwide_stats = lambda t, *a, **k: (_ for _ in ()).throw(RuntimeError("boom")) if t["title"] == "Babylon" else _orig_ww(t, *a, **k)
add_cycle(11, {
    "Babylon": {"us": 2, "gb": 1, "fr": 1, "ca": 1, "au": 1},
    "Patient Zero": {"us": 2, "gb": 4, "fr": 7, "ca": 6, "au": 8},
    "Cleveland!": {"us": 1, "gb": 2},
    "Pink Clouding": {"us": 11},
})
run("6. one track crashes", 11)
m._worldwide_stats = _orig_ww

# 7. Apple Music chain: normal Global card + album Global card.
import generate_snapshot_images as snap  # noqa: E402
GLOBAL = {"rows": [], "sa": ""}
snap.get_region_rows = lambda today, region, genre: (list(GLOBAL["rows"]), GLOBAL["sa"])


def fake_generate(today, region, genre, out_dir, album=None):
    out = Path(out_dir) / f"global_{'album' if album else 'all'}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"")
    return out


snap.generate = fake_generate
Args.platform = "apple_music"
GLOBAL.update(sa="2026-09-24T11:00:00+02:00", rows=[{"song_name": "The Fate of Ophelia", "rank": "20"},
                                                    {"song_name": "Opalite", "rank": "83"}])
run("7a. first Apple Music run: Global not updated yet -> baseline only", 12, 20)
run("7b. same Global an hour later -> nothing", 13, 20)
GLOBAL.update(sa="2026-09-25T14:00:00+02:00", rows=[{"song_name": "Babylon", "rank": "3"},
                                                    {"song_name": "The Fate of Ophelia", "rank": "25"},
                                                    {"song_name": "Opalite", "rank": "90"}])
run("7c. Global updated with an Encore song -> normal card + album card", 14, 20)

print("=" * 100)
print(f"TOTAL posts: {len(POSTS)}   alerts: {len(ALERTS)}   longest tweet: {max(len(p) for p in POSTS)} chars")
print("final state keys:", sorted(json.loads(state_file().read_text()))[:6], "...")
