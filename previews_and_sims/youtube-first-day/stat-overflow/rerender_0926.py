"""Re-render the 2026-09-26 first-day cards that overflowed, with the current youtube_card code."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from collectors.comp.youtube_card import render_youtube_card, write_song_card_png  # noqa: E402

OUT = Path(__file__).resolve().parent / "out_0926"
OUT.mkdir(exist_ok=True)
CASES = [
    ("c0Y4PqvDvqc", "Taylor Swift - The Life of a Showgirl: The Encore STATION", 12_966_141, "September 24, 2026 · 11:35 PM UTC"),
    ("tZnNLoPKriU", "Taylor Swift - Babylon (Official Lyric Video)", 1_314_033, "September 26, 2026 · 12:01 AM UTC"),
    ("jfVVXYTZykw", "Taylor Swift - Cleveland! (Official Lyric Video)", 1_886_539, "September 26, 2026 · 12:01 AM UTC"),
    ("BpR280fXISA", "Taylor Swift - Patient Zero (Official Lyric Video)", 1_656_098, "September 26, 2026 · 12:01 AM UTC"),
    ("BpR280fXISA", "Stress test - 10 digits", 1_234_567_890, "September 26, 2026 · 12:01 AM UTC"),
]
for vid, title, views, released in CASES:
    html_text = render_youtube_card(
        title=title, stat_label="First 24 Hours", stat_value=f"+{views:,} views",
        cover_url=f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg", footer_left="@swiftiescharts",
        badge_text="NEW VIDEO", release_date_text=released,
    )
    print(write_song_card_png(html_text, OUT / f"{vid}_{views}.png", OUT / f"{vid}_{views}.html"))
