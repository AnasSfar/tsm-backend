"""Audit harness (2026-09-24, pre-release readiness audit) for
post_new_release_progression.py — tweet texts + edge cases, NO rendering,
NO posting. Touches only files under this folder (audit/ subfolder).

Scenarios:
  1. all 4 tracks debut on AM US + iTunes US at once (Global not yet updated)
  2. one track moves on exactly one chart
  3. a track debuts on Global while its other charts are unchanged
  4. mixed: one debut + one move on the same platform
  5. state lost / previous run crashed: CSV has an earlier cycle, state empty,
     exactly one chart changed -> delta None path
  6. Apple titles differ from catalog ("Cleveland" / "Babylon (feat. X)")
"""
import csv
import json
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "collectors" / "apple_music"))

import post_new_release_progression as m  # noqa: E402

W = Path(__file__).resolve().parent / "audit"
if W.exists():
    shutil.rmtree(W)
W.mkdir()
m.OUT_DIR = W / "cards"
m.LOCKS_DIR = W / "locks"
m.STATE_DIR = W  # per-platform state files since 2026-09-24
m._render_card_png = lambda html, out, scale=3: (out.parent.mkdir(parents=True, exist_ok=True), out.write_bytes(b""))
m._remote_data_uri = lambda url: ""  # no network
m._album_snapshot_post = lambda album, today, state: None  # tested separately

AM_GLOBAL = W / "am_global.csv"
AM_COUNTRY = W / "am_country.csv"
ITUNES = W / "itunes.csv"
GF = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank",
      "image_url", "url", "artist_name", "album_name", "duration_ms", "release_date", "isrc", "content_rating", "genre_names"]
CF = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank", "image_url", "url", "artist_name"]
IF = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank",
      "image_url", "url", "artist_name", "album_name", "genre_names", "release_date", "explicitness"]


def write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def rows_for(cycles, key, chart_type):
    out = []
    for sa, data in cycles:
        for country, ranks in data.get(key, {}).items():
            for title, rank in ranks.items():
                out.append({"date": sa[:10], "scraped_at": sa, "country": country, "chart_type": chart_type,
                            "song_name": title, "rank": rank, "artist_name": "Taylor Swift"})
    return out


orig_build_sources = m.build_sources


def fake_sources(today):
    s = orig_build_sources(today)
    for x in s:
        x["path"] = AM_GLOBAL if x["source_key"] == "am_global" else (AM_COUNTRY if x["source_key"].startswith("am_") else ITUNES)
        x["path_for"] = (lambda _d, _p=x["path"]: _p)
    return s


m.build_sources = fake_sources


class Args:
    platform = "all"
    scraped_at = None
    window_hours = 72
    no_post = True
    dry_run = False


m.parse_args = lambda: Args()


def run(label, cycles, now_utc, reset_state=False):
    print("=" * 90)
    print(label)
    if reset_state:
        for _f in W.glob("new_release_progression_state_*.json"):
            _f.unlink()
    write(AM_GLOBAL, GF, rows_for(cycles, "global", "global"))
    write(AM_COUNTRY, CF, rows_for(cycles, "am", "country"))
    write(ITUNES, IF, rows_for(cycles, "itunes", "itunes_country"))
    m._now_paris = lambda: now_utc
    try:
        m.main()
    except Exception:
        print("!!! CRASH:")
        traceback.print_exc(limit=3)


T = ["Patient Zero", "Cleveland!", "Pink Clouding", "Babylon"]
c1 = ("2026-09-25T06:00:00+02:00", {
    "am": {"us": {t: r for t, r in zip(T, [12, 20, 35, 9])}},
    "itunes": {"us": {t: r for t, r in zip(T, [5, 8, 15, 3])}, "gb": {t: r for t, r in zip(T, [6, 9, 14, 2])}},
})
run("SCENARIO 1 — all debut at once (06:00, AM US + iTunes US/GB, no Global yet)", [c1],
    datetime(2026, 9, 25, 4, 30, tzinfo=timezone.utc), reset_state=True)

c2 = ("2026-09-25T07:00:00+02:00", {
    "am": c1[1]["am"],
    "itunes": {"us": {**c1[1]["itunes"]["us"], "Babylon": 1}, "gb": c1[1]["itunes"]["gb"]},
})
run("SCENARIO 2 — Babylon moves on exactly one chart (iTunes US 3 -> 1)", [c1, c2],
    datetime(2026, 9, 25, 5, 30, tzinfo=timezone.utc))

c3 = ("2026-09-25T08:00:00+02:00", {
    "global": {"fr": {"Patient Zero": 14}},
    "am": c1[1]["am"], "itunes": c2[1]["itunes"],
})
# global rows are keyed country=fr in the real CSV, filter is chart_type only
run("SCENARIO 3 — Patient Zero debuts on Global, US unchanged", [c1, c2, c3],
    datetime(2026, 9, 25, 6, 30, tzinfo=timezone.utc))

c4 = ("2026-09-25T09:00:00+02:00", {
    "global": {"fr": {"Patient Zero": 14, "Cleveland!": 40}},
    "am": {"us": {**c1[1]["am"]["us"], "Cleveland!": 11}},
    "itunes": c2[1]["itunes"],
})
run("SCENARIO 4 — Cleveland! debuts on Global + moves on US (mixed)", [c1, c2, c3, c4],
    datetime(2026, 9, 25, 7, 30, tzinfo=timezone.utc))

c5 = ("2026-09-25T10:00:00+02:00", {
    "global": c4[1]["global"], "am": c4[1]["am"],
    "itunes": {"us": {**c2[1]["itunes"]["us"], "Pink Clouding": 7}, "gb": c1[1]["itunes"]["gb"]},
})
run("SCENARIO 5 — state LOST (e.g. earlier crash), CSV has prior cycles, Pink Clouding moves on 1 iTunes chart",
    [c1, c2, c3, c4, c5], datetime(2026, 9, 25, 8, 30, tzinfo=timezone.utc), reset_state=True)

c6 = ("2026-09-25T06:00:00+02:00", {
    "am": {"us": {"Patient Zero": 12, "Cleveland": 20, "Pink Clouding (Encore)": 35, "Babylon (feat. Someone)": 9,
                  "patient zero": 99}},
})
run("SCENARIO 6 — Apple titles differ (Cleveland / Pink Clouding (Encore) / Babylon (feat.))", [c6],
    datetime(2026, 9, 25, 4, 30, tzinfo=timezone.utc), reset_state=True)

# Album snapshot tweet text (real template, from main() lines 866-870)
import generate_snapshot_images as snap  # noqa: E402
from collectors.comp.discography import display_title_for_album  # noqa: E402
from collectors.twitter.links import amcharts_url  # noqa: E402
t = (f'🎧 | "{display_title_for_album("The Life of a Showgirl")}" songs on the Global Apple Music chart '
     f'({snap._format_snapshot_time("2026-09-25T08:00:00+02:00")}).' f"\n\n{amcharts_url('applemusic')}")
print("=" * 90)
print("ALBUM SNAPSHOT TWEET:\n" + t)
print("len =", len(t))
print("STATE:", json.dumps({f.name: json.loads(f.read_text(encoding="utf-8")) for f in W.glob("new_release_progression_state_*.json")}, indent=0)[:600])
