"""Preview of the Spotify-app-page streams cards (live since 2026-10-13).

The renderers live in collectors/spotify/streams/tools/scripts/spotify_app_cards.py (the code the
pipeline posts); this only calls them on REAL data (read-only) and writes the PNGs to cards/ here.
Nothing goes to snapshots/, nothing is posted, no state file is touched.

  python previews_and_sims/spotify-app-cards/simulate.py [DATE] [recap|songs|eras|gainers|billion|watch=900M ...]
  --theme=YYYY-MM-DD : real stats of DATE, theme of the day of another date (timeline themes preview)
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "collectors/spotify/streams/tools/scripts"))

import spotify_app_cards as app  # noqa: E402

OUT = HERE / "cards"


def main() -> None:
    args = sys.argv[1:]
    theme_d = next((a.split("=", 1)[1] for a in args if a.startswith("--theme=")), None)
    args = [a for a in args if not a.startswith("--")]
    d = next((a for a in args if a[:2] == "20"), None) or app.gs.get_latest_date()
    which = [a for a in args if a[:2] != "20"] or ["recap", "songs", "eras", "gainers", "billion"]
    sfx = d
    if theme_d:
        real = app.day_theme.theme_for
        app.day_theme.theme_for = lambda _d, **k: real(theme_d, **k)
        sfx = f"{d}_theme-{theme_d}"
    if "recap" in which:
        app.render_recap(d, OUT / f"1_recap_artist_page_{sfx}.png")
    if "songs" in which:
        app.render_songs(d, OUT / f"2_top_songs_playlist_{sfx}.png")
    if "eras" in which:
        app.render_eras(d, OUT / f"3_top_eras_discography_{sfx}.png")
    if "gainers" in which:
        for period, days in (("daily", 1), ("weekly", 7)):
            picked = app.pg._pick_gainers(d, compare_days=days, limit=10, min_baseline=1000)
            app.render_gainers(d, period, picked, OUT / f"4_gainers_{period}_charts_{sfx}.png")
    if "billion" in which:
        app.render_billion(d, OUT / f"5_billion_watch_{sfx}.png")
    for w in [a for a in which if a.startswith("watch=")]:  # watch=900M / watch=2B
        v = w.split("=", 1)[1].upper()
        m = int(float(v[:-1]) * (1_000_000_000 if v.endswith("B") else 1_000_000))
        app.render_watch(d, OUT / f"5_watch_{app.milestone_label(m)}_{sfx}.png", milestone=m)


if __name__ == "__main__":
    main()
