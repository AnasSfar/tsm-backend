"""How interesting one Apple Music chart update is (owner 2026-10-16: post the
US Apple Music chart "quand c'est intéressant", score >= a minimum, max 2 per
day; an OUT is eliminatory, a RE is good, a top 10 is good...).

Pure scoring: the caller resolves, vs the SAME snapshot the card's arrows
compare to (previous distinct snapshot), every current Taylor row as a
SongMove + the titles that left (dropped_songs). Calibrated on the real US
history (Mar 22 -> Oct 16, 2026): previews_and_sims/us-apple-music-score/.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SongMove:
    title: str
    rank: int
    prev: int | None  # None = was not on the previous snapshot
    badge: str = ""  # card Peak badge: "NEW", "NEW PEAK", "RE-PEAK", ""


@dataclass
class UpdateScore:
    score: float
    veto: str | None  # why the update can never post (an OUT), else None
    events: list[dict] = field(default_factory=list)  # best first: {title, kind, rank, prev, points}

    @property
    def interesting(self) -> bool:
        return self.veto is None and self.score > 0


# Where a rank sits: entries and climbs weigh more the higher they land.
def _zone(rank: int) -> int:
    for i, top in enumerate((10, 25, 50, 100)):
        if rank <= top:
            return i
    return 4


# Entry by zone (top 10 / 25 / 50 / 100 / 200). A debut (first time ever on
# the chart) is always news. A RE
# at the bottom of the chart is mostly a song flickering around #190-200,
# worth little.
ENTRY_POINTS = {"debut": (80, 65, 55, 45, 40), "reentry": (45, 35, 25, 15, 6)}
NUMBER_ONE_POINTS = 50
TOP10_ENTRY_POINTS = 20
PEAK_POINTS = {"NEW PEAK": 12, "RE-PEAK": 6}
CLIMB_PER_PLACE = (3.0, 1.5, 0.8, 0.4, 0.2)  # by the zone of the NEW rank
CLIMB_CAP = 20
DROP_FACTOR = 0.5  # a drop costs half a climb of the same size...
DROP_CAP = 10  # ...and at most this much per song


def score_update(moves: list[SongMove], out_titles: list[str]) -> UpdateScore:
    events: list[dict] = []
    for m in moves:
        if m.prev is None:
            # Debut = first appearance ever on this chart (the card's Peak badge
            # NEW), not "recent release": Cleveland! back at #181 three weeks
            # after its release is a re-entry.
            kind = "debut" if m.badge == "NEW" else "reentry"
            pts = ENTRY_POINTS[kind][_zone(m.rank)]
            if m.rank == 1:
                pts += NUMBER_ONE_POINTS
            events.append({"title": m.title, "kind": kind, "rank": m.rank, "prev": None, "points": pts})
            continue
        delta = m.prev - m.rank
        if delta > 0:
            pts = min(delta * CLIMB_PER_PLACE[_zone(m.rank)], CLIMB_CAP)
            kind = "climb"
            if m.rank == 1:
                kind, pts = "number_one", pts + NUMBER_ONE_POINTS
            elif m.rank <= 10 < m.prev:
                kind, pts = "top10_entry", pts + TOP10_ENTRY_POINTS
            pts += PEAK_POINTS.get(m.badge, 0)
            events.append({"title": m.title, "kind": kind, "rank": m.rank, "prev": m.prev, "points": round(pts, 1)})
        elif delta < 0:
            pts = -min(-delta * CLIMB_PER_PLACE[_zone(m.prev)] * DROP_FACTOR, DROP_CAP)
            events.append({"title": m.title, "kind": "drop", "rank": m.rank, "prev": m.prev, "points": round(pts, 1)})
    for title in out_titles:
        events.append({"title": title, "kind": "out", "rank": None, "prev": None, "points": 0})
    veto = ("OUT: " + ", ".join(out_titles)) if out_titles else None
    score = round(sum(e["points"] for e in events), 1)
    # Biggest news first (the caption says the first ones), OUTs last.
    events.sort(key=lambda e: (e["kind"] == "out", -e["points"], e["rank"] or 999))
    return UpdateScore(score=score, veto=veto, events=events)
