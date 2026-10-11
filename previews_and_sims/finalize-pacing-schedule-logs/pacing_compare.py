"""Compare post_pacing curves (current vs tighter) on typical days. No state, no post."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
STREAMS = REPO / "collectors" / "spotify" / "streams"
sys.path[:0] = [str(REPO / "collectors" / "spotify"), str(STREAMS), str(STREAMS / "tools" / "scripts")]
import post_pacing as pp

VARIANTS = {
    "current": dict(BASE_GAP_SECONDS=pp.BASE_GAP_SECONDS, GROWTH=pp.GROWTH, MAX_GAP_SECONDS=pp.MAX_GAP_SECONDS, WINDOW_HOURS=pp.WINDOW_HOURS),
}
for spec in sys.argv[1:]:  # name:base:growth:maxmin:window
    n, b, g, m, w = spec.split(":")
    VARIANTS[n] = dict(BASE_GAP_SECONDS=float(b), GROWTH=float(g), MAX_GAP_SECONDS=int(m) * 60, WINDOW_HOURS=float(w))
DAYS = [("Wed 00:36", datetime(2026, 10, 14, 0, 36)), ("Fri 15:00", datetime(2026, 10, 16, 15, 0)),
        ("Tue 18:00", datetime(2026, 10, 20, 18, 0)), ("Thu 22:30", datetime(2026, 10, 22, 22, 30))]
N = 20
for name, params in VARIANTS.items():
    for k, v in params.items():
        setattr(pp, k, v)
    print(f"== {name} {params}")
    for label, start in DAYS:
        slots, scale = pp.plan_slots(start=start, first_index=0, count=N, deadline=pp.window_deadline(start), busy=[], now=start)
        gaps = [int((b - a).total_seconds() // 60) for a, b in zip(slots, slots[1:])]
        print(f"  {label}: {slots[0]:%H:%M} -> {slots[-1]:%H:%M} ({(slots[-1]-start).total_seconds()/3600:.1f} h, scale {scale:.2f}) gaps {gaps}")
