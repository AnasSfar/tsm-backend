"""Preview harness for the stream milestone cards (comp/milestone_card.py).

Two modes, both write ONLY inside this folder:

  python previews_and_sims/milestone-cards/simulate.py real [DATE ...]
      Runs the real post_stream_milestones.main() with --force --no-post on real
      dates (default: 2026-10-07 = Cover Story + Climb, 2026-09-26 = Plaque Lover 15B).
      update_streams_dir is redirected here, so cards, temp HTML and any lock land in
      real/<date>/ — never in snapshots/. post_with_image is stubbed as a safety net.

  python previews_and_sims/milestone-cards/simulate.py edge
      Renders layout edge cases straight through milestone_card with FAKE values
      (long titles, 1B song, long album name, first 100M, no next-expected). Titles, albums and
      covers are the real catalog ones; the numbers are FAKE (files prefixed FAKE_).

Every PNG also gets a *_phone.png copy at 310 px wide (X timeline on a phone).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPTS = REPO / "collectors" / "spotify" / "streams" / "tools" / "scripts"
for p in (SCRIPTS, REPO / "collectors" / "spotify" / "streams", REPO / "collectors" / "spotify", REPO / "collectors", REPO):
    sys.path.insert(0, str(p))

from PIL import Image  # noqa: E402


def phone_copy(png: Path) -> None:
    img = Image.open(png)
    w = 310
    img.resize((w, round(img.height * w / img.width)), Image.LANCZOS).save(png.with_name(png.stem + "_phone.png"))


def run_real(dates: list[str]) -> None:
    import post_stream_milestones as psm

    out_root = HERE / "real"
    psm.update_streams_dir = lambda d: out_root / str(d)  # cards, temp html, locks -> sim folder

    def no_post(*a, **k):
        raise RuntimeError("simulate.py: posting is disabled")

    psm.post_with_image = no_post
    for d in dates:
        sys.argv = ["post_stream_milestones.py", d, "--force", "--no-post"]
        psm.main()
        for png in sorted((out_root / d / "milestones").glob("*.png")):
            if not png.stem.endswith("_phone"):
                phone_copy(png)


def _fake_points(start: date, end: date, start_total: int, end_total: int, gap: tuple[int, int] | None = None) -> list[dict]:
    days = (end - start).days
    pts = []
    for i in range(days + 1):
        if gap and gap[0] <= i < gap[1]:
            continue
        frac = i / days
        pts.append({"date": (start + timedelta(days=i)).isoformat(), "streams": int(start_total + (end_total - start_total) * frac ** 1.3)})
    return pts


def run_edge() -> None:
    from comp import milestone_card as mc

    out = HERE / "edge"
    import spotlight

    tracks = spotlight.load_all_tracks()
    album_covers = spotlight.load_covers()

    def song(title: str) -> tuple[str, str | None, str]:
        """Real catalog title/cover/album (only the numbers below are fake)."""
        t = next(t for t in tracks if t["title"].casefold() == title.casefold())
        return t["title"], spotlight.get_cover_url(t, album_covers), t["album"]

    def album_cover(name: str) -> str | None:
        return album_covers.get(spotlight._norm(name))

    d = "2026-10-07"
    atw = song("All Too Well (10 Minute Version) (Taylor's Version) (From The Vault)")
    clara = song("Clara Bow")
    iion = song("Is It Over Now? (Taylor's Version) (From The Vault)")
    cs = song("Cruel Summer")
    cases = {
        "FAKE_cover_long_title": mc.render_song_cover_story(
            title=atw[0], cover_url=atw[1], milestone=1_000_000_000, total=1_000_123_456, daily=512_345,
            rank=12, stats_date=d, prev_step=(900_000_000, 211)),
        "FAKE_cover_first_100M": mc.render_song_cover_story(
            title=clara[0], cover_url=clara[1], milestone=100_000_000, total=100_042_000,
            daily=88_000, rank=250, stats_date=d, prev_step=None),
        "FAKE_climb_1B_no_next": mc.render_song_climb(
            title=iion[0], album=iion[2], cover_url=iion[1], milestone=1_000_000_000, total=1_000_200_000, daily=950_000, rank=40,
            stats_date=d, history_points=_fake_points(date(2023, 10, 27), date(2026, 10, 7), 4_000_000, 1_000_200_000, gap=(300, 360)),
            next_expected=None),
        "FAKE_climb_3_2B": mc.render_song_climb(
            title=cs[0], album=cs[2], cover_url=cs[1], milestone=3_200_000_000,
            total=3_200_400_000, daily=1_900_000, rank=1, stats_date=d,
            history_points=_fake_points(date(2019, 8, 23), date(2026, 10, 7), 9_000_000, 3_200_400_000),
            next_expected=("The Fate of Ophelia", "2027-03-02")),
        "FAKE_plaque_long_album": mc.render_album_plaque(
            album="THE TORTURED POETS DEPARTMENT", cover_url=album_cover("THE TORTURED POETS DEPARTMENT"), milestone=11_000_000_000,
            rank=6, stats_date=d),
    }
    for name, html in cases.items():
        png = mc.write_milestone_png(html, out / f"{name}.png", out / f"_{name}.html")
        phone_copy(png)
        print(f"[sim] {png}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "real"
    if mode == "edge":
        run_edge()
    else:
        run_real(sys.argv[2:] or ["2026-10-07", "2026-09-26"])
