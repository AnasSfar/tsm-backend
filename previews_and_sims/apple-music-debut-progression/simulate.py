"""Simulation of The Encore's release (2026-09-25) for
post_new_release_progression.py — generates real card PNGs with --no-post,
touches ONLY files under this folder (fake CSVs, fake state/locks/cards),
never the real db/snapshots/tools/json state used by the production pipeline.

Convention: skill `previews-and-sims`. Re-run this file (adjust the fake
cycles below) instead of creating a new sim folder if
post_new_release_progression.py changes again.
"""
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "collectors" / "apple_music"))

import post_new_release_progression as m  # noqa: E402
import generate_snapshot_images as snap  # noqa: E402

SCRATCH = Path(__file__).resolve().parent
m.OUT_DIR = SCRATCH / "cards"
m.LOCKS_DIR = SCRATCH / "locks"
m.STATE_DIR = SCRATCH  # per-platform state files since 2026-09-24
for _f in SCRATCH.glob("new_release_progression_state_*.json"):
    _f.unlink()

# Album-filtered Global snapshot card (generate_snapshot_images --album) reads
# per-day folders through apple_music_charts_dir -> point it at fixtures/<date>/.
# fixtures/2026-09-24/ = copy of the REAL 09-24 Global CSV (the "vs yesterday"
# baseline, read-only copy); fixtures/2026-09-25/ = fake release-day Global.
FIXTURES = SCRATCH / "fixtures"
_real_charts_dir = snap.apple_music_charts_dir
# fixture day if we have one, else the REAL day folder (read-only: the Peak
# column scans every day since 2026-06-05 for Ophelia/Opalite "peak since")
snap.apple_music_charts_dir = lambda d: FIXTURES / d if (FIXTURES / d).exists() else _real_charts_dir(d)
_real_prev = REPO_ROOT / "snapshots" / "apple_music_charts" / "2026" / "09" / "2026-09-24" / "apple_music_global.csv"
(FIXTURES / "2026-09-24").mkdir(parents=True, exist_ok=True)
(FIXTURES / "2026-09-24" / "apple_music_global.csv").write_bytes(_real_prev.read_bytes())

SHOWGIRL_COVER = "https://is1-ssl.mzstatic.com/image/thumb/Music211/v4/2d/46/e0/2d46e0bc-8ab9-85dd-4b56-ee6951351034/25UM1IM19577.rgb.jpg/300x300bb.jpg"
# older Showgirl songs already charting (real 09-24 ranks: Ophelia #20, Opalite #83)
CATALOG_GLOBAL = [
    {"Fate": 18, "Opalite": 80},
    {"Fate": 19, "Opalite": 88},
    {"Fate": 22, "Opalite": 95},
]

TRACKS = ["Patient Zero", "Cleveland!", "Pink Clouding", "Babylon"]
COUNTRIES = ["us", "gb", "fr", "ca", "au"]

# cycle 1 = release moment (00:00), cycle 2 = +2h with movement
CYCLES = [
    {
        "scraped_at": "2026-09-25T00:00:00+02:00",
        "global_ranks": {"Patient Zero": 42, "Cleveland!": 58, "Pink Clouding": 71, "Babylon": 33},
        "us_ranks": {"Patient Zero": 12, "Cleveland!": 20, "Pink Clouding": 35, "Babylon": 9},
        "itunes_us_ranks": {"Patient Zero": 5, "Cleveland!": 8, "Pink Clouding": 15, "Babylon": 3},
    },
    {
        "scraped_at": "2026-09-25T02:00:00+02:00",
        "global_ranks": {"Patient Zero": 30, "Cleveland!": 58, "Pink Clouding": 60, "Babylon": 14},
        "us_ranks": {"Patient Zero": 8, "Cleveland!": 20, "Pink Clouding": 22, "Babylon": 4},
        "itunes_us_ranks": {"Patient Zero": 2, "Cleveland!": 8, "Pink Clouding": 10, "Babylon": 1},
    },
]

CYCLES.append({
    # cycle 3 = +4h, some tracks fall back -> PEAK column differs from current rank
    "scraped_at": "2026-09-25T04:00:00+02:00",
    "global_ranks": {"Patient Zero": 35, "Cleveland!": 50, "Pink Clouding": 60, "Babylon": 21},
    "us_ranks": {"Patient Zero": 11, "Cleveland!": 18, "Pink Clouding": 22, "Babylon": 6},
    "itunes_us_ranks": {"Patient Zero": 4, "Cleveland!": 6, "Pink Clouding": 10, "Babylon": 2},
})

FAKE_NOW = datetime(2026, 9, 25, 2, 5, 0, tzinfo=timezone.utc)


def write_am_global_csv(path: Path, cycles):
    # same columns/order as collectors/apple_music/global.py FIELDNAMES
    fieldnames = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank",
                  "image_url", "url", "artist_name", "album_name", "duration_ms", "release_date", "isrc",
                  "content_rating", "genre_names"]
    rows = []
    for i, cyc in enumerate(cycles):
        catalog = CATALOG_GLOBAL[i]
        entries = [(t, r, "", "https://i.scdn.co/image/ab67616d0000b2733c9ea57c5fce6677a860a6e2", "2026-09-25")
                   for t, r in cyc["global_ranks"].items()]
        entries += [
            ("The Fate of Ophelia", catalog["Fate"], "1833328840", SHOWGIRL_COVER, "2025-10-03"),
            ("Opalite", catalog["Opalite"], "1833328845", SHOWGIRL_COVER, "2025-10-03"),
        ]
        for title, rank, am_id, image, release in entries:
            rows.append({
                "date": "2026-09-25", "scraped_at": cyc["scraped_at"], "country": "fr", "chart_type": "global",
                "song_name": title, "apple_music_id": am_id, "rank": rank, "previous_rank": "",
                "image_url": image, "url": "", "artist_name": "Taylor Swift",
                "album_name": "The Life of a Showgirl", "duration_ms": "", "release_date": release,
                "isrc": "", "content_rating": "", "genre_names": "",
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_am_country_csv(path: Path, cycles, key="us_ranks"):
    fieldnames = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank", "image_url", "url", "artist_name"]
    rows = []
    for cyc in cycles:
        for country in COUNTRIES:
            ranks = cyc[key] if country == "us" else {t: r + 15 for t, r in cyc[key].items()}
            for title, rank in ranks.items():
                rows.append({
                    "date": "2026-09-25", "scraped_at": cyc["scraped_at"], "country": country, "chart_type": "songs",
                    "song_name": title, "apple_music_id": "", "rank": rank, "previous_rank": "",
                    "image_url": "https://i.scdn.co/image/ab67616d0000b2733c9ea57c5fce6677a860a6e2",
                    "url": "", "artist_name": "Taylor Swift",
                })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_itunes_csv(path: Path, cycles, key="itunes_us_ranks"):
    fieldnames = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank", "image_url", "url", "artist_name", "album_name", "genre_names", "release_date"]
    rows = []
    for cyc in cycles:
        for country in COUNTRIES:
            ranks = cyc[key] if country == "us" else {t: r + 10 for t, r in cyc[key].items()}
            for title, rank in ranks.items():
                rows.append({
                    "date": "2026-09-25", "scraped_at": cyc["scraped_at"], "country": country, "chart_type": "songs",
                    "song_name": title, "apple_music_id": "", "rank": rank, "previous_rank": "",
                    "image_url": "https://i.scdn.co/image/ab67616d0000b2733c9ea57c5fce6677a860a6e2",
                    "url": "", "artist_name": "Taylor Swift", "album_name": "The Life of a Showgirl",
                    "genre_names": "", "release_date": "2026-09-25",
                })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


am_global_csv = FIXTURES / "2026-09-25" / "apple_music_global.csv"
am_country_csv = SCRATCH / "apple_music_country_charts.csv"
itunes_csv = SCRATCH / "itunes_top_songs.csv"

write_am_global_csv(am_global_csv, CYCLES)
write_am_country_csv(am_country_csv, CYCLES)
write_itunes_csv(itunes_csv, CYCLES)

orig_build_sources = m.build_sources


def fake_build_sources(today):
    sources = orig_build_sources(today)
    for s in sources:
        if s["source_key"] == "am_global":
            s["path"] = am_global_csv
        elif s["source_key"].startswith("am_"):
            s["path"] = am_country_csv
        elif s["source_key"].startswith("itunes_"):
            s["path"] = itunes_csv
        # peak lookup reads every day since release: point all days at the fake CSV
        s["path_for"] = (lambda _d, _p=s["path"]: _p)
    return sources


m.build_sources = fake_build_sources


class Args:
    platform = "all"
    scraped_at = None
    window_hours = 72
    no_post = True
    dry_run = False


m.parse_args = lambda: Args()

# --- cycle 1: release moment, everything is a DEBUT ---
print("=" * 80)
print("CYCLE 1 — 2026-09-25T00:00:00+02:00 (release)")
print("=" * 80)
m._now_paris = lambda: datetime(2026, 9, 25, 0, 5, 0, tzinfo=timezone.utc)
# restrict CSVs to cycle 1 only rows for this pass
write_am_global_csv(am_global_csv, CYCLES[:1])
write_am_country_csv(am_country_csv, CYCLES[:1])
write_itunes_csv(itunes_csv, CYCLES[:1])
m.main()

print()
print("=" * 80)
print("CYCLE 2 — 2026-09-25T02:00:00+02:00 (movement)")
print("=" * 80)
m._now_paris = lambda: FAKE_NOW
write_am_global_csv(am_global_csv, CYCLES[:2])
write_am_country_csv(am_country_csv, CYCLES[:2])
write_itunes_csv(itunes_csv, CYCLES[:2])
m.main()

print()
print("=" * 80)
print("CYCLE 3 — 2026-09-25T04:00:00+02:00 (falls -> peak < rank)")
print("=" * 80)
m._now_paris = lambda: datetime(2026, 9, 25, 4, 5, 0, tzinfo=timezone.utc)
write_am_global_csv(am_global_csv, CYCLES)
write_am_country_csv(am_country_csv, CYCLES)
write_itunes_csv(itunes_csv, CYCLES)
m.main()

print()
print("=" * 80)
print("CYCLE 3 RERUN — same data -> album snapshot must NOT be re-posted")
print("=" * 80)
m.main()

print()
print("=" * 80)
print("Cards generated:")
for p in sorted((SCRATCH / "cards").glob("*.png")):
    print(" -", p)
