"""Render the first-day video card at several view counts to check the stat box never overflows."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from collectors.comp.youtube_card import render_youtube_card, write_song_card_png  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
for views in (8_412, 912_345, 4_567_890, 12_966_141, 48_888_888, 188_888_888):
    html_text = render_youtube_card(
        title="Taylor Swift - Patient Zero (Official Music Video)",
        stat_label="First 24 Hours",
        stat_value=f"+{views:,} views",
        cover_url="https://i.ytimg.com/vi/mw3kSNIxjqo/maxresdefault.jpg",
        footer_left="@swiftiescharts",
        badge_text="NEW VIDEO",
        release_date_text="September 29, 2026 · 10:00 PM UTC",
    )
    html_text = html_text.replace(
        "</body>",
        "<script>const v=document.querySelector('.stat-val'),b=document.querySelector('.stat');"
        "document.title=JSON.stringify({text:v.scrollWidth,box:b.clientWidth-52});</script></body>",
    )
    png = write_song_card_png(html_text, OUT / f"card_{views}.png", OUT / f"card_{views}.html", keep_html=True)
    print(views, png)
