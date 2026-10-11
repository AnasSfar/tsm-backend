"""Sim: finalize post order by importance (decision 2026-10-10) on REAL past days.

Read-only, nothing posted, nothing written outside this folder:
- album importance = real score_album_update scores (cached module, read only);
  album queue approximated like _album_post_queue on weekdays (top 2 ->
  Showgirl/TTPD -> rest by score; daily-drop gate respected via drop_blocked);
  weekend album queue (weekly-only) not reproduced -> no albums on Sat/Sun here.
- overtakes / milestones = the real scripts with `--force --count-units`
  (force ignores the locks already written for those past days; --count-units
  returns before rendering or posting anything).
- best-day-since candidates are NOT reproduced (`--list-batch-candidates`
  writes its stash into the real snapshots/ day folder) — real days
  2026-10-05 -> 10-09 had no individual best-day card anyway.
- order = the real post_priority.order_by_importance + the urgent split used
  by finalize_update.

Usage: python simulate.py [YYYY-MM-DD ...]   (default: 2026-09-29 -> 2026-10-09)
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STREAMS = REPO / "collectors" / "spotify" / "streams"
SCRIPTS = STREAMS / "tools" / "scripts"
sys.path[:0] = [str(REPO / "collectors" / "spotify"), str(STREAMS), str(SCRIPTS)]

import post_priority  # noqa: E402
import score_album_update  # noqa: E402
from finalize_update import PRIMARY_ALBUM_UPDATE_TARGETS, URGENT_ALBUM_MAX, URGENT_ALBUM_SCORE_MIN  # noqa: E402


def units(script: str, day: str) -> list[tuple[str, float]]:
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / script), day, "--force", "--count-units"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    ).stdout
    for line in reversed(out.splitlines()):
        if line.startswith("UNITS_JSON:"):
            data = json.loads(line.split(":", 1)[1])
            return list(zip(data.get("labels") or [], data.get("scores") or []))
    return []


def album_queue(day: str) -> list[tuple[str, float]]:
    if date.fromisoformat(day).weekday() >= 5:
        return []
    import generate_album_update_image  # noqa: E402
    albums = [a for a in generate_album_update_image.all_album_names() if "misc" not in a.casefold()]
    scored = [
        item for item in score_album_update.score_albums(albums, date.fromisoformat(day))
        if item.get("status") == "scored" and not item.get("drop_blocked")
    ]
    scored.sort(key=lambda item: -float(item.get("score") or 0))
    names = [str(item["album"]) for item in scored]
    score = {str(item["album"]): float(item.get("score") or 0) for item in scored}
    head = names[:2]
    forced = [a for a in names if a.casefold() in {t.casefold() for t in PRIMARY_ALBUM_UPDATE_TARGETS} and a not in head]
    rest = [a for a in names if a not in head and a not in forced]
    return [(a, score[a]) for a in head + forced + rest]


def main() -> None:
    days = sys.argv[1:] or [str(date(2026, 9, 29) + timedelta(days=k)) for k in range(11)]
    for day in days:
        albums = album_queue(day)
        urgent_albums = sorted([a for a in albums if a[1] >= URGENT_ALBUM_SCORE_MIN], key=lambda a: -a[1])[:URGENT_ALBUM_MAX]
        albums = [a for a in albums if a not in urgent_albums]
        ovt = units("post_song_overtakes.py", day)
        mil = units("post_stream_milestones.py", day)

        def split(u):
            n = 0
            while n < len(u) and u[n][1] >= post_priority.URGENT_UNIT_SCORE_MIN:
                n += 1
            return u[:n], u[n:]

        u_ovt, ovt = split(ovt)
        u_mil, mil = split(mil)
        urgent = post_priority.order_by_importance({"album": urgent_albums, "milestone": u_mil, "overtake": u_ovt})
        paced = post_priority.order_by_importance({"album": albums, "milestone": mil, "overtake": ovt})
        print(f"\n== {day} ({date.fromisoformat(day):%a})")
        print("  urgent (before top eras):", " > ".join(f"[{k[:3].upper()}] {i} {s:.0f}" for k, i, s in urgent) or "-")
        print("  paced:")
        for n, (kind, item, score) in enumerate(paced, 1):
            print(f"    {n:2d}. [{kind[:3].upper()}] {item}  ({score:.0f})")
        # check: a non-album type never follows itself while another type was still waiting
        for n in range(1, len(paced)):
            kind = paced[n][0]
            if kind != "album" and paced[n - 1][0] == kind:
                assert all(k == kind for k, _i, _s in paced[n:]), f"{day}: {kind} back to back at {n + 1}"


if __name__ == "__main__":
    main()
