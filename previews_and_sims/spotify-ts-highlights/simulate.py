"""Sim: Spotify Charts official highlights about Taylor added to chart posts.

Fakes: a worldwide snapshot with "ts_highlights" (written in this folder only),
Taylor highlight texts, chart rows and comment candidates. Real code exercised:
worldwide/daily.py::_parse_ts_highlights / _build_multi_song_region_tweet and
core/chart_comment.py::spotify_ts_highlight / build_chart_comment. Nothing is
posted; no real snapshot/db file is read or written except the real API
response sample (read-only) used for the "no Taylor highlight" case.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
sys.path.insert(0, str(ROOT))

from core import chart_comment  # noqa: E402

spec = importlib.util.spec_from_file_location("ww_daily_sim", ROOT / "collectors/spotify/charts/worldwide/daily.py")
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)

D = date(2026, 9, 26)
FIX = HERE / "fixtures"
FIX.mkdir(exist_ok=True)

TS_HL = [
    {"type": "GREATEST_GAINER", "text": "“Opalite” by Taylor Swift is the biggest gainer on Top Songs Global, up 41 spots at #12."},
    {"type": "ARTIST_WITH_MOST_ENTRIES", "text": "Taylor Swift has the most spots on Top Songs Global. “Babylon” is the highest at #1."},
]
payload = {"highlights": TS_HL + [
    {"type": "LONGEST_STREAK", "text": "“Sweater Weather” by The Neighbourhood has been on Top Songs Global the longest, at 1840 days straight."},
]}

print("1) _parse_ts_highlights keeps only Taylor:")
print("  ", daily._parse_ts_highlights(payload))
real = Path(r"C:/Users/sfara/AppData/Local/Temp/claude/c--Users-sfara-Documents-GitHub-tsm-backend/f1b1ce2e-5074-421e-b0a2-d2162926a89f/scratchpad/global_2025-01-01.json")
if real.exists():
    print("   real 2025-01-01 response (no Taylor highlight) ->", daily._parse_ts_highlights(json.loads(real.read_text(encoding="utf-8"))))

# fake worldwide snapshot, in the sim folder only
snap_dir = FIX / "worldwide"
snap_dir.mkdir(exist_ok=True)
(snap_dir / f"ts_worldwide_{D}.json").write_text(json.dumps({
    "date": str(D), "by_track": {},
    "ts_highlights": {"global": TS_HL, "gb": [{"type": "HIGHEST_NEW_ENTRY", "text": "“Cleveland” by Taylor Swift is the highest new entry on Top Songs United Kingdom at #4."}]},
}), encoding="utf-8")
chart_comment.spotify_chart_dir = lambda name, d, *a, **k: snap_dir
chart_comment.legacy_spotify_chart_dir = lambda name, d, *a, **k: snap_dir

print("\n2) spotify_ts_highlight (priority = most spots first, quotes straightened):")
for name in ("global", "uk", "us"):
    print(f"   {name}: {chart_comment.spotify_ts_highlight(name, D)}")

print("\n3) build_chart_comment + full post text (header + comment), length vs limit 500:")
chart_comment._load_ts_rows = lambda *a, **k: [{"track": "x"}]
chart_comment.load_ts_history = lambda *a, **k: {}
chart_comment._load_chart_history = lambda *a, **k: ({}, [])
cases = {
    "normal": [(5, "A", '"Babylon" climbs 3 spots to #1'), (4, "B", '"Opalite" reaches a new peak of #12')],
    "very long comments": [(5, "A", "x" * 260), (4, "B", "y" * 200), (3, "C", '"Wood" is up 9 spots')],
    "no comment candidate": [],
}
for label, cands in cases.items():
    chart_comment._candidates = lambda *a, _c=cands, **k: list(_c)
    comment = chart_comment.build_chart_comment("global", D, Path("unused"))
    header = f"🌍 | Taylor Swift on Spotify Global Charts on {D.strftime('%A, %B %d, %Y')} :"
    text = f"{header}\n\n{comment}" if comment else header
    print(f"   [{label}] {len(text)} chars (limit {daily.TWITTER_TEXT_LIMIT})")
    print("   " + text.replace("\n", "\n   "))

print("\n4) regional post (worldwide/daily.py) with in-run highlights:")
daily._TS_HIGHLIGHTS[(str(D), "ph")] = [{"type": "ARTIST_WITH_MOST_ENTRIES", "text": "Taylor Swift has the most spots on Top Songs Philippines. “The Fate of Ophelia” is the highest at #2."}]
daily.days_since_last_chart = lambda *a, **k: None
rows = [{"track_name": "The Fate of Ophelia", "rank": 2}, {"track_name": "Opalite", "rank": 9}]
t = daily._build_multi_song_region_tweet(str(D), "ph", "Philippines", rows)
print(f"   {len(t)} chars\n   " + t.replace("\n", "\n   "))
