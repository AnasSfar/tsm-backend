"""Preview of the LIVE post card (post_new_release_progression._build_progression_card_html,
rank-groups design, 2026-09-26) on REAL data: Patient Zero on Apple Music, latest collected
cycle of today vs the previous cycle (per-country rank, delta, NEW = absent from the previous
cycle). Writes only into this folder; never touches state/locks/snapshots."""
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "collectors" / "apple_music")]
import post_new_release_progression as prog  # noqa: E402
from core.card_theme import country_label  # noqa: E402

DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-09-26"
SONG = "Patient Zero"
csv_path = REPO / "snapshots" / "apple_music_charts" / DAY[:4] / DAY[5:7] / DAY / "apple_music_country_charts.csv"
cycles: dict[str, dict[str, int]] = {}
for r in csv.DictReader(csv_path.open(encoding="utf-8")):
    if r["song_name"] == SONG:
        cycles.setdefault(r["scraped_at"], {})[r["country"]] = int(r["rank"])
at = sorted(cycles)
now, prev = cycles[at[-1]], cycles[at[-2]]
placements = [{
    "region": cc, "label": country_label(cc), "rank": rank,
    "delta": (prev[cc] - rank) if cc in prev else None, "is_new": cc not in prev,
    "new_peak": False, "prev_scraped_at": at[-2],
} for cc, rank in now.items()]
track = {"title": SONG, "subtitle": "The Life of a Showgirl: The Encore",
         "image_url": prog._album_cover_url("The Life of a Showgirl", "")}
html = prog._build_progression_card_html(track=track, placements=placements, scraped_at=at[-1], platform="apple_music")
out = Path(__file__).parent / "live_patient_zero_apple_music.png"
prog._render_card_png(html, out, scale=2)
print(f"{len(placements)} countries, {at[-2]} -> {at[-1]}: {out}")
