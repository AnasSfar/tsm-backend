"""Card layout variants (2026-09-25, owner: "on ne voit rien si on ne zoom pas"):
the same all-regions card rendered with 1 / 2 / 3 / 4 table columns, on REAL
data of the day (The Fate of Ophelia: ~60 iTunes countries, Global + ~70 Apple
Music countries). Read-only; output ./card_sizes/*.png; nothing posted."""
import struct
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "collectors" / "apple_music"))
import post_new_release_progression as m  # noqa: E402

OUT = Path(__file__).resolve().parent / "card_sizes"
OUT.mkdir(exist_ok=True)
m.OUT_DIR = OUT
m.CARD_WIDTH_BY_COLS = {1: 780, 2: 1000, 3: 1500, 4: 2000}
today = datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d")
track = {"title": "The Fate of Ophelia", "key": "sizes", "album": "The Life of a Showgirl",
         "keys": set(m.song_key_candidates("The Fate of Ophelia")), "apple_ids": set(),
         "image_url": m._album_cover_url("The Life of a Showgirl", ""),
         "release_date": datetime(2026, 9, 25, tzinfo=ZoneInfo("Europe/Paris"))}
for platform in ("itunes", "apple_music"):
    sources = [s for s in m.build_sources(today) if s["platform"] == platform and s.get("kind", "song") == "song"]
    placements = m._collect_track_placements(track, sources, {}, {}, today)
    for cols in (1, 2, 3, 4):
        m._column_count = lambda n, c=cols: c
        path = m.render_card(track=track, placements=placements, max_scraped_at=placements[0]["scraped_at"],
                             platform=platform)
        target = OUT / f"{platform}_{len(placements)}rows_{cols}col.png"
        path.replace(target)
        w, h = struct.unpack(">II", target.read_bytes()[16:24])
        print(f"{target.name}: {w}x{h} px, ratio {w / h:.2f}, {target.stat().st_size / 1e6:.2f} MB")
