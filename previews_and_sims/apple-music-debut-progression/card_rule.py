"""The production column rule (_column_count) on REAL data (Ophelia today) +
two FAKE extremes (15 and 168 rows, padded with invented rows for size only).
Output ./card_rule/*.png; nothing posted."""
import struct
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "collectors" / "apple_music"))
import post_new_release_progression as m  # noqa: E402

OUT = Path(__file__).resolve().parent / "card_rule"
OUT.mkdir(exist_ok=True)
m.OUT_DIR = OUT
today = datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d")
track = {"title": "The Fate of Ophelia", "key": "rule", "album": "The Life of a Showgirl",
         "keys": set(m.song_key_candidates("The Fate of Ophelia")), "apple_ids": set(),
         "image_url": m._album_cover_url("The Life of a Showgirl", ""),
         "release_date": datetime(2026, 9, 25, tzinfo=ZoneInfo("Europe/Paris"))}


def render(name, placements, platform):
    path = m.render_card(track=track, placements=placements, max_scraped_at=placements[0]["scraped_at"],
                         platform=platform)
    target = OUT / f"{name}.png"
    path.replace(target)
    w, h = struct.unpack(">II", target.read_bytes()[16:24])
    print(f"{target.name}: {len(placements)} rows -> {m._column_count(len(placements))} col, "
          f"{w}x{h} px, ratio {w / h:.2f}, {target.stat().st_size / 1e6:.2f} MB")


real = {}
for platform in ("itunes", "apple_music"):
    sources = [s for s in m.build_sources(today) if s["platform"] == platform and s.get("kind", "song") == "song"]
    real[platform] = m._collect_track_placements(track, sources, {}, {}, today)
    render(f"real_{platform}", real[platform], platform)
codes = sorted(m.COUNTRY_NAMES) if hasattr(m, "COUNTRY_NAMES") else []
from core.card_theme import COUNTRY_NAMES  # noqa: E402
base = real["itunes"][0]
fake = [{**base, "region": c, "label": COUNTRY_NAMES[c], "rank": i + 1, "peak": i + 1}
        for i, c in enumerate(sorted(COUNTRY_NAMES)[:168])]
render("fake_15rows", fake[:15], "itunes")
render("fake_168rows", fake[:168], "itunes")
