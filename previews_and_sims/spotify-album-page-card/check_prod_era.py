"""Read-only check of the 2026-10-13 prod fixes in generate_album_update_image:
TOTAL ERA = Top Eras scope for every album, and no "of the year"/"of the month" label.
Real cards are rendered into this folder (album_update_out_dir patched), never snapshots/.

  python previews_and_sims/spotify-album-page-card/check_prod_era.py [DATE] [ALBUM...]
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "collectors/spotify/streams/tools/scripts"))

import generate_album_update_image as g  # noqa: E402
import generate_albums_image as ga  # noqa: E402

OUT = HERE / "prod_cards"
g.album_update_out_dir = lambda target_date: OUT  # never the real snapshots/ folder


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else g.get_latest_date()
    render = sys.argv[2:]
    today, yest, week = ga.load_history(target)
    top = {r["album"]: r for r in ga.build_album_rows(today, yest, week, ga.load_album_track_map(),
                                                       ga.load_covers(), target_date=target)}
    for album in g.all_album_names():
        sections, canonical = g.load_album_sections(album, target)
        hist = g.load_history_for_album(sections, target)
        g.drop_merged_era_extras(sections, hist)
        daily = lambda ts: sum((hist.get(t["track_id"]) or {}).get("daily") or 0 for t in ts)
        era = daily(g._display_total_tracks(sections))
        row = top.get(canonical)
        top_daily = row["daily_streams"] if row else None
        tv = "(Taylor's Version)" in canonical  # Top Eras merges TV into the original era
        flag = "" if tv or top_daily is None or top_daily == era else "  <-- differs"
        print(f"{canonical:38} album={daily(g._album_total_tracks(sections)):>11,} "
              f"era={era:>11,} topEras={top_daily if top_daily is not None else '-':>11}{flag}")
    for album in render:
        text = g._build_album_post_text(album, target)
        print(f"\n--- tweet {album}\n{text}")
        assert "of the year" not in text.replace("BIGGEST DAY of the year and BEST DAY since", "") \
            and "of the month" not in text, "label without date in tweet"
        out = g.generate(album, target, style="table-dark")
        print("card:", out)


if __name__ == "__main__":
    main()
