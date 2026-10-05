"""Backtest de core/estimate.py sur données RÉELLES (lecture seule, rien n'est écrit).

On cache de vraies lectures et on compare l'estimation à la réalité :
1. Patient Zero : marques first-week +48h / +72h cachées (fenêtre serrée puis large).
2. Run quotidien : un snapshot caché (fenêtre ~48h entre ses voisins), clips
   > 30k vues/jour ; 3 variantes : linéaire, décroissance, décroissance + jour
   de la semaine (variante retenue en prod).
"""
import csv
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from collectors.youtube.core import estimate  # noqa: E402
from collectors.youtube.core.config import CSV_PATH  # noqa: E402

with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
    ALL_ROWS = list(csv.DictReader(f))
FACTORS = estimate.weekday_factors(ALL_ROWS)
print("Facteurs jour de semaine (NY, lun..dim):", {k: round(v, 3) for k, v in sorted(FACTORS.items())})

state = json.loads((ROOT / "collectors/youtube/tools/json/first_week/mw3kSNIxjqo.json").read_text(encoding="utf-8"))
published = datetime.fromisoformat(state["published_at"])
marks = {int(n): (datetime.fromisoformat(m["captured_at"]), m["views"]) for n, m in state["marks"].items() if "views" in m}
daily_rows = [a for r in ALL_ROWS if r["video_id"] == "mw3kSNIxjqo" and (a := estimate.real_anchor(r))]
LIVE = (datetime.fromisoformat("2026-10-04T17:58:08+00:00"), 12293814)  # lecture API réelle pendant la session

print("\n== Patient Zero : marque cachée (fenêtre serrée, snapshot quotidien 6 h après)")
for n in (2, 3):
    at, real = marks[n]
    others = [v for m, v in marks.items() if m != n] + daily_rows
    est = estimate.estimate_views_at(at, others, published, FACTORS)
    day_real, day_est = real - marks[n - 1][1], est["views"] - marks[n - 1][1]
    print(f"  Day {n} réel {day_real:,} est {day_est:,} ({(day_est-day_real)/day_real*100:+.1f}%)")

print("\n== Patient Zero : fenêtre large (marque + snapshot proche cachés)")
for n, hide in ((2, "2026-10-01T04"), (3, "2026-10-03T04")):
    at, real = marks[n]
    others = [v for m, v in marks.items() if m != n] + [p for p in daily_rows if not p[0].isoformat().startswith(hide)] + [LIVE]
    est = estimate.estimate_views_at(at, others, published, FACTORS)
    day_real, day_est = real - marks[n - 1][1], est["views"] - marks[n - 1][1]
    print(f"  Day {n} réel {day_real:,} est {day_est:,} ({(day_est-day_real)/day_real*100:+.1f}%) fenêtre {est['anchors']}")

print("\n== Run quotidien : snapshot caché (clips > 30k vues/jour)")
by_vid, pub = defaultdict(list), {}
for r in ALL_ROWS:
    if a := estimate.real_anchor(r):
        by_vid[r["video_id"]].append(a)
        pub[r["video_id"]] = estimate._parse(r.get("published_at"))
VARIANTS = {
    "linéaire": lambda t, o, p: estimate.estimate_views_at(t, o, None, {}),
    "décroissance": lambda t, o, p: estimate.estimate_views_at(t, o, p, {}),
    "décroissance + jour sem.": lambda t, o, p: estimate.estimate_views_at(t, o, p, FACTORS),
}
errors = {name: {"old": [], "new": []} for name in VARIANTS}
for vid, pts in by_vid.items():
    pts = estimate._clean(pts)
    if len(pts) < 5 or pub.get(vid) is None:
        continue
    for i in range(1, len(pts) - 1):
        (a, va), (t, vt), (b, vb) = pts[i - 1], pts[i], pts[i + 1]
        h1, h2 = (t - a).total_seconds() / 3600, (b - t).total_seconds() / 3600
        if not (23.5 < h1 < 24.5 and 23.5 < h2 < 24.5) or vt - va < 30000:
            continue
        others = pts[:i] + pts[i + 1:]
        bucket = "new" if (t - pub[vid]).days < 10 else "old"
        for name, fn in VARIANTS.items():
            est = fn(t, others, pub[vid])
            errors[name][bucket].append(abs((est["views"] - vt) / (vt - va)))
for name, buckets in errors.items():
    parts = []
    for label, key in (("clips > 10 j", "old"), ("clips < 10 j", "new")):
        errs = sorted(buckets[key])
        if errs:
            parts.append(f"{label}: médiane {statistics.median(errs)*100:.1f}% p90 {errs[int(len(errs)*0.9)]*100:.1f}% (n={len(errs)})")
    print(f"  {name:<26} " + " | ".join(parts))
