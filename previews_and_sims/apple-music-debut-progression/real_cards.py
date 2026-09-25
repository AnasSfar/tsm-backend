"""REAL card renders (Playwright, same code as production) for the debut posts:
iTunes song, iTunes album, Apple Music song with Global. The other sims stub
the renderer with empty files (logic only) — this one is the visual check.

FAKE DATA: ranks are invented. Output: ./real_cards/*.png. Nothing posted,
no state written.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "collectors" / "apple_music"))
import post_new_release_progression as m  # noqa: E402

OUT = Path(__file__).resolve().parent / "real_cards"
OUT.mkdir(exist_ok=True)
m.OUT_DIR = OUT
SA = "2026-09-25T06:00:41+02:00"


def P(region, rank, new=True, delta=None, peak=False):
    return {"region": region, "label": "Global" if region == "global" else m.country_label(region),
            "rank": rank, "is_new": new, "delta": delta, "new_peak": peak, "re_peak": False, "moved": True,
            "peak": rank, "prev_scraped_at": None, "scraped_at": SA, "platform": "", "state_key": ""}


cover = m._album_cover_url("The Life of a Showgirl", "")
song = {"title": "Babylon", "key": "babylon", "album": "The Life of a Showgirl", "image_url": cover}
album = {"title": "The Life of a Showgirl: The Encore", "key": "album|encore", "album": "The Life of a Showgirl",
         "image_url": cover, "subtitle": "Taylor Swift · Album", "is_album": True}
cases = [
    ("itunes_song_debut", song, "itunes",
     [P("us", 1), P("gb", 1), P("fr", 2), P("ca", 1), P("au", 3)]),
    ("itunes_album_debut", album, "itunes",
     [P("us", 1), P("gb", 1), P("fr", 1), P("ca", 1), P("au", 2)]),
    ("apple_music_song_debut", song, "apple_music",
     [P("global", 3), P("us", 1), P("gb", 2), P("fr", 4), P("ca", 1), P("au", 2)]),
    ("apple_music_new_peak", song, "apple_music",
     [P("global", 2, new=False, delta=1, peak=True), P("us", 1, new=False, delta=0),
      P("gb", 1, new=False, delta=1, peak=True)]),
]
for name, item, platform, placements in cases:
    path = m.render_card(track={**item, "key": name}, placements=placements, max_scraped_at=SA, platform=platform)
    target = OUT / f"{name}.png"
    path.replace(target)
    print(f"{target.name}: {target.stat().st_size // 1024} KB")
