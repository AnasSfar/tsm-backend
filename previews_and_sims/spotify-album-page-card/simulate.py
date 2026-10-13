"""Preview of the album update card (Spotify album page design, live since 2026-10-13).

Calls the REAL renderer (collectors/spotify/streams/tools/scripts/album_page_card.py)
on real data, read-only; PNGs go to cards/ here, never snapshots/, nothing is posted.

  python previews_and_sims/spotify-album-page-card/simulate.py ["Album" | all] [DATE] [--weekly|--daily] [--overtake]

Sat/Sun dates render the weekend "vs last week" card unless --daily (same rule as finalize).
--overtake = same-album overtake list (fails if the album had none that day).
Each PNG gets a _phone.png copy (310 px, X mobile timeline) to check readability.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "collectors/spotify/streams/tools/scripts"))

import generate_album_update_image as g  # noqa: E402  (also puts collectors/ on sys.path)
import album_page_card  # noqa: E402
from PIL import Image  # noqa: E402


def render(album: str, target: str, *, weekly: bool = False, overtake: bool = False) -> None:
    suffix = ("_overtake" if overtake else "") + ("_weekly" if weekly else "")
    out = HERE / "cards" / f"{g.album_update_slug(album)}_{target}{suffix}.png"
    try:
        album_page_card.render_png(album, target, out, weekly=weekly, overtake=overtake)
    except ValueError as exc:
        print(f"skip {album}: {exc}")
        return
    im = Image.open(out)
    im.resize((310, round(im.height * 310 / im.width)), Image.LANCZOS).save(out.with_name(out.stem + "_phone.png"))
    print(out, im.size)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    album = args[0] if args else "The Life of a Showgirl"
    target = args[1] if len(args) > 1 else g.get_latest_date()
    weekly = "--weekly" in flags or ("--daily" not in flags and date.fromisoformat(target).weekday() >= 5)
    overtake = "--overtake" in flags
    for name in (g.all_album_names() if album == "all" else [album]):
        render(name, target, weekly=weekly, overtake=overtake)


if __name__ == "__main__":
    main()
