"""Read-only: real overtakes / milestones / album scores over recent days (calibration)."""
import sys
from datetime import date, timedelta
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
STREAMS = REPO / "collectors" / "spotify" / "streams"
sys.path[:0] = [str(REPO / "collectors" / "spotify"), str(STREAMS), str(STREAMS / "tools" / "scripts")]
import post_song_overtakes as pso
import post_stream_milestones as psm
import history_store

hist = history_store.HistoryIndex.load()
end = date(2026, 10, 9)
for k in range(10, -1, -1):
    d = str(end - timedelta(days=k))
    ovs = pso.find_overtakes(d)
    print(f"== {d}")
    for e in ovs:
        o, p = e["overtaker"], e["passed"]
        print(f"  OVT #{o['rank']} {o.get('title')} ({int(o['streams'])/1e6:.0f}M, daily {int(o.get('daily_streams') or 0)/1e3:.0f}k) > {p.get('title')}")
    songs = psm.find_milestone_events(d)
    albums = psm.find_album_milestone_events(d, hist)
    for e in albums:
        print(f"  MIL album {e.get('album')} {int(e['milestone'])/1e9:.0f}B rank {e.get('rank')}")
    for e in songs:
        r = psm._milestone_rank(e, songs, hist, d)
        print(f"  MIL song {e.get('title')} {int(e['milestone'])/1e6:.0f}M rank {r} daily {int(e.get('daily_streams') or 0)/1e3:.0f}k")
