"""Express path of collectors/itunes/release_watch.py (2026-09-25): the REAL
run_platform(express=...) on key-country rows, then the normal hourly run on
the full snapshot — the debut must be posted once (express), the hourly run
must only post what moved since.

FAKE DATA: all CSV rows, the Apple availability answer and the X posts are
fabricated; state/locks/cards/CSVs live in ./express_sim/. No network, no post.
"""
import csv
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

W = Path(__file__).resolve().parent / "express_sim"
if W.exists():
    shutil.rmtree(W)
W.mkdir()
m.OUT_DIR, m.LOCKS_DIR, m.STATE_DIR = W / "cards", W / "locks", W
m._render_card_png = lambda html, out, scale=3: (out.parent.mkdir(parents=True, exist_ok=True), out.write_bytes(b""))
m._remote_data_uri = lambda url: ""
m._apple_availability = lambda ids, countries: {i: True for i in ids}
m.alert = lambda msg, **kw: print(f"   ALERT >>> {msg}")
POSTS = []


def fake_post(tweet, image, session, **kw):
    POSTS.append(tweet)
    print("   POSTED >>> " + tweet.split("\n")[0])
    if "🌍" in tweet:
        print("              " + [l for l in tweet.split("\n") if "🌍" in l][0])
    return True


tw.post_with_image = fake_post
IDS = {t: next(iter(v)) for t, v in m.KNOWN_APPLE_IDS.items()}
SF = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank",
      "image_url", "url", "artist_name", "album_name", "genre_names", "release_date", "explicitness"]
AF = ["date", "scraped_at", "country", "chart_type", "album_name", "apple_music_id", "rank", "previous_rank",
      "image_url", "url", "artist_name", "genre_names", "release_date"]
DEBUT = {"Babylon": {"us": 1, "gb": 1, "fr": 2}, "Patient Zero": {"us": 4, "gb": 3},
         "Cleveland!": {"us": 2}, "Pink Clouding": {"au": 7}}
ALBUM = {"us": 1, "gb": 1, "ca": 1}


def write(path, fields, sa, rows, extra_countries=()):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for name, aid, per in rows:
            for cc, rank in list(per.items()) + [(c, 1) for c in extra_countries]:
                w.writerow({"date": sa[:10], "scraped_at": sa, "country": cc, "chart_type": "itunes_country",
                            "song_name": name, "album_name": name, "apple_music_id": aid, "rank": rank,
                            "artist_name": "Taylor Swift"})


class Args:
    platform = "itunes"
    scraped_at = None
    window_hours = 72
    no_post = False
    dry_run = False


# 1. express at 06:00:41 on key-country rows only
sa = "2026-09-25T06:00:41+02:00"
ex = {"song": W / "express_songs.csv", "album": W / "express_albums.csv"}
write(ex["song"], SF, sa, [(t, IDS[t], per) for t, per in DEBUT.items()])
write(ex["album"], AF, sa, [("The Life of a Showgirl: The Encore", "6814997249", ALBUM)])
now = datetime(2026, 9, 25, 6, 0, 41, tzinfo=ZoneInfo("Europe/Paris"))
print("=" * 90 + "\n1. EXPRESS (key countries only)")
m.run_platform("itunes", Args(), now, "2026-09-25", express=ex)
n_express = len(POSTS)

# 2. full hourly snapshot (06:00 slot) with the SAME key ranks + 30 other countries
full_s, full_a = W / "itunes_top_songs.csv", W / "itunes_top_albums.csv"
sa2 = "2026-09-25T06:00:00+02:00"
write(full_s, SF, sa2, [(t, IDS[t], per) for t, per in DEBUT.items()], extra_countries=["de", "it", "es"])
write(full_a, AF, sa2, [("The Life of a Showgirl: The Encore", "6814997249", ALBUM)], extra_countries=["de", "it"])
m.itunes_daily_csv = lambda d, name: full_a if "albums" in name else full_s
m.apple_music_daily_csv = lambda d, name: W / "empty.csv"
(W / "empty.csv").write_text("")
print("=" * 90 + "\n2. FULL hourly run, same key ranks -> expect 0 posts")
m.run_platform("itunes", Args(), datetime(2026, 9, 25, 6, 3, tzinfo=ZoneInfo("Europe/Paris")), "2026-09-25")
n_full = len(POSTS) - n_express

# 3. next hour: Patient Zero hits #1 in the UK -> posted with the worldwide line
sa3 = "2026-09-25T07:00:00+02:00"
moved = dict(DEBUT, **{"Patient Zero": {"us": 4, "gb": 1}})
rows_prev = list(csv.DictReader(full_s.open(encoding="utf-8")))
write(full_s, SF, sa3, [(t, IDS[t], per) for t, per in moved.items()], extra_countries=["de", "it", "es"])
with full_s.open("a", newline="", encoding="utf-8") as fh:
    csv.DictWriter(fh, fieldnames=SF, extrasaction="ignore").writerows(rows_prev)
print("=" * 90 + "\n3. 07:00 hourly run: Patient Zero #1 UK")
m.run_platform("itunes", Args(), datetime(2026, 9, 25, 7, 3, tzinfo=ZoneInfo("Europe/Paris")), "2026-09-25")

print("=" * 90)
print(f"express posts: {n_express} | full-run posts right after: {n_full} | total: {len(POSTS)}")
