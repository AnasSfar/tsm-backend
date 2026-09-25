"""Audit (2026-09-24): does the album Global snapshot post fire at the first
in-window cycle (Fri 02:xx Paris) before any Encore song charts?
Fixture = real 09-24 00:00 Global rows relabelled 2026-09-25T00:00 (scratch copy).
--dry-run, state/locks/cards redirected to audit_album/."""
import csv, shutil, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT)); sys.path.insert(0, str(REPO_ROOT / "collectors" / "apple_music"))
import post_new_release_progression as m
import generate_snapshot_images as snap
W = Path(__file__).resolve().parent / "audit_album"
shutil.rmtree(W, ignore_errors=True); (W / "2026-09-25").mkdir(parents=True)
m.OUT_DIR, m.LOCKS_DIR, m.STATE_DIR = W / "cards", W / "locks", W  # per-platform state files since 2026-09-24
src = REPO_ROOT / "snapshots/apple_music_charts/2026/09/2026-09-24/apple_music_global.csv"
rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
latest = max(r["scraped_at"] for r in rows)
out = [dict(r, date="2026-09-25", scraped_at="2026-09-25T00:00:00+02:00") for r in rows if r["scraped_at"] == latest]
with open(W / "2026-09-25" / "apple_music_global.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(out)
real = snap.apple_music_charts_dir
snap.apple_music_charts_dir = lambda d: (W / d) if (W / d).exists() else real(d)
_orig = m.build_sources
def fs(today):
    s = _orig(today)
    for x in s:
        x["path"] = W / "none.csv"; x["path_for"] = lambda _d: W / "none.csv"
    return s
m.build_sources = fs
class A: platform="all"; scraped_at=None; window_hours=72; no_post=False; dry_run=True
m.parse_args = lambda: A()
m._now_paris = lambda: datetime(2026, 9, 25, 2, 20, tzinfo=ZoneInfo("Europe/Paris"))
print("fixture global rows:", [(r["song_name"], r["rank"]) for r in out])
m.main()
