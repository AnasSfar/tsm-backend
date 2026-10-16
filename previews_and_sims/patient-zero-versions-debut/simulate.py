"""Preview of the debut card + tweet for new versions of an existing song.

Case: "Patient Zero - Acoustic Version" + "Patient Zero - Piano Version" (out 2026-10-16),
grouped with the main "Patient Zero" (The Life of a Showgirl). Runs the REAL
post_debut_releases._build_post_threads on the real catalogue, with:
  - streams history rows FAKED (Spotify had not counted the new versions yet):
    Patient Zero up to 2026-10-14 is real, 10-15 / 10-16 / 10-17 are invented;
  - output dir redirected into cards/ here (never snapshots/), posting never called.

Usage (repo root):
  python previews_and_sims/patient-zero-versions-debut/simulate.py [day1|day2|swap|single|all]
    day1 = release day (2026-10-16), day2 = 2026-10-17,
    swap = day 1 where the acoustic version out-streams the main version (order check)
Each PNG gets a _phone.png copy (310 px wide = X mobile timeline).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPTS = REPO / "collectors" / "spotify" / "streams" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO))

import post_debut_releases as pdr  # noqa: E402
from PIL import Image  # noqa: E402

PZ = "11hcBLPtbMp4aQI6zGQLub"
AC = "2a5MkKmuigi8mqJNzGpRQv"
PI = "021C3nvL4td0oFnURYbyCm"

# FAKE numbers (except Patient Zero 2026-10-14, real).
SCENARIOS = {
    "day1": ("2026-10-16", {
        "2026-10-15": {PZ: (101_090_517, 2_880_293)},
        "2026-10-16": {PZ: (104_212_904, 3_122_387), AC: (1_204_118, 1_204_118), PI: (687_432, 687_432)},
    }),
    "day2": ("2026-10-17", {
        "2026-10-16": {PZ: (104_212_904, 3_122_387), AC: (1_204_118, 1_204_118), PI: (687_432, 687_432)},
        "2026-10-17": {PZ: (107_171_022, 2_958_118), AC: (2_102_530, 898_412), PI: (1_209_973, 522_541)},
    }),
    "swap": ("2026-10-16", {
        "2026-10-15": {PZ: (101_090_517, 2_880_293)},
        "2026-10-16": {PZ: (104_212_904, 3_122_387), AC: (3_504_118, 3_504_118), PI: (687_432, 687_432)},
    }),
}


def run_single() -> None:
    """A lone single (one version, no main version): rendered straight through the card module."""
    import debut_page_card
    out = HERE / "cards" / "single" / "FAKE_single.png"
    debut_page_card.render_png(out, "2026-10-16", title="Patient Zero - Acoustic Version",
                               rows=[{"title": "Patient Zero - Acoustic Version", "album": "", "total": 1_204_118,
                                      "daily": 1_204_118, "change": None, "new": True, "debut": True, "day": 1}],
                               cover_url="https://i.scdn.co/image/ab67616d0000b27316bc19366517944ef91c6654",
                               daily=1_204_118, change=None, total=1_204_118)
    im = Image.open(out)
    im.resize((310, round(im.height * 310 / im.width)), Image.LANCZOS).save(out.with_name(out.stem + "_phone.png"))
    print(f"\n=== single : {out}")


def run(name: str) -> None:
    target, days = SCENARIOS[name]
    out = HERE / "cards" / name
    out.mkdir(parents=True, exist_ok=True)

    def fake_rows(day: str) -> dict[str, dict]:
        return {tid: {"date": day, "streams": t, "daily_streams": d} for tid, (t, d) in days.get(day, {}).items()}

    pdr._load_rows_for_date = fake_rows
    pdr.update_streams_dir = lambda *_a, **_k: out
    pdr.post_with_image = pdr.post_image_thread = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no post in sim"))

    threads = pdr._build_post_threads(target)
    print(f"\n=== {name} ({target}) : {len(threads)} thread(s)")
    for thread in threads:
        for slug, text, img in thread:
            print(f"--- {slug} ({len(text)} chars)\n{text}\n[img] {img}")
            if img:
                im = Image.open(img)
                im.resize((310, round(im.height * 310 / im.width)), Image.LANCZOS).save(
                    Path(img).with_name(Path(img).stem + "_phone.png"))
            (out / f"{slug.replace(':', '_')}.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    for key in (list(SCENARIOS) + ["single"] if arg == "all" else [arg]):
        run_single() if key == "single" else run(key)
