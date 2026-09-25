"""All-regions card on REAL data (2026-09-25): The Fate of Ophelia, which charts
in dozens of countries today, run through the real placement code + the real
renderer. Read-only on real CSVs; state is an empty in-memory dict; nothing
written outside ./real_cards/, nothing posted."""
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "collectors" / "apple_music"))
import post_new_release_progression as m  # noqa: E402

OUT = Path(__file__).resolve().parent / "real_cards"
OUT.mkdir(exist_ok=True)
m.OUT_DIR = OUT
today = datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d")
track = {"title": "The Fate of Ophelia", "key": "ophelia_allregions", "album": "The Life of a Showgirl",
         "keys": set(m.song_key_candidates("The Fate of Ophelia")), "apple_ids": set(),
         "image_url": m._album_cover_url("The Life of a Showgirl", ""),
         "release_date": datetime(2026, 9, 25, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))}
for platform in ("itunes", "apple_music"):
    t0 = time.time()
    sources = [s for s in m.build_sources(today) if s["platform"] == platform and s.get("kind", "song") == "song"]
    cache: dict = {}
    placements = m._collect_track_placements(track, sources, cache, {}, today)
    t1 = time.time()
    path = m.render_card(track=track, placements=placements, max_scraped_at=placements[0]["scraped_at"], platform=platform)
    target = OUT / f"allregions_{platform}.png"
    path.replace(target)
    world = m._worldwide_stats(track, platform, cache, today)
    print(f"{platform}: {len(sources)} sources, {len(placements)} placements ({sum(p['key'] for p in placements)} key) "
          f"in {t1 - t0:.1f}s | render {time.time() - t1:.1f}s | {target.stat().st_size / 1e6:.2f} MB | worldwide {world}")
