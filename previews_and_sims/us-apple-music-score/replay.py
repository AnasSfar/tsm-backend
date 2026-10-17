"""Replay the US Apple Music score card (post_new_release_progression
._post_score_card rules) over the REAL US history, read-only.

    python previews_and_sims/us-apple-music-score/replay.py [--min 50] [--cap 2] [--gap 6] [--from 2026-03-22] [--show N]

For every distinct US snapshot: the real region_update() score vs the card's
reference snapshot, then the production gate (no OUT, score >= min, max `cap`
per Paris day, `gap` hours between posts). Prints the score distribution,
posts per threshold, and the would-be posts with their captions. Writes
nothing outside this folder (scores cached in scores.json).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
AM = REPO / "collectors" / "apple_music"
for p in (REPO, AM):
    sys.path.insert(0, str(p))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import generate_snapshot_images as snap  # noqa: E402
import post_new_release_progression as nrp  # noqa: E402
from core.region_update_score import UpdateScore  # noqa: E402

CACHE = HERE / "scores.json"


def snapshots(start: date, end: date):
    """(day, scraped_at, rows) of every distinct, fetched US snapshot."""
    prev_sig = None
    day = start
    while day <= end:
        d = day.isoformat()
        path = snap._region_csv_path(d, "us", None)
        if path.exists():
            by_at: dict[str, list[dict]] = {}
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    if str(r.get("date") or "").strip() == d and str(r.get("country") or "").lower() == "us":
                        by_at.setdefault(r.get("scraped_at") or "", []).append(r)
            cycles = snap._day_cycles(d, "us", None)
            for at in sorted(cycles):
                sig = cycles[at]
                if sig == prev_sig:
                    continue
                prev_sig = sig
                yield d, at, sorted(by_at.get(at, []), key=lambda r: snap._rank_int(r.get("rank")) or 999)
        day += timedelta(days=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=nrp.SCORE_CARD_MIN_SCORE)
    ap.add_argument("--cap", type=int, default=nrp.SCORE_CARD_MAX_PER_DAY)
    ap.add_argument("--gap", type=float, default=nrp.SCORE_CARD_GAP_HOURS, help="hours")
    ap.add_argument("--gap-runs", type=int, default=nrp.SCORE_CARD_GAP_RUNS)
    ap.add_argument("--from", dest="start", default="2026-03-22")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--list", default="", help="print every snapshot's score from this day on")
    args = ap.parse_args()

    cache = {} if args.rescore or not CACHE.exists() else json.loads(CACHE.read_text(encoding="utf-8"))
    results = []
    for d, at, rows in snapshots(date.fromisoformat(args.start), date(2026, 10, 16)):
        if at not in cache:
            update, prev_at = nrp.region_update(d, "us", rows, at)
            cache[at] = None if update is None else {"score": update.score, "veto": update.veto,
                                                     "events": update.events, "prev": prev_at}
        if cache[at]:
            results.append((at, cache[at]))
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    days = {at[:10] for at, _ in results}
    vetoed = sum(1 for _, r in results if r["veto"])
    print(f"{len(results)} distinct US snapshots over {len(days)} days, {vetoed} with an OUT (vetoed)")
    clean = sorted(r["score"] for _, r in results if not r["veto"])
    for q in (0.5, 0.75, 0.9, 0.95, 0.98):
        print(f"  p{int(q * 100)} score (no OUT) = {clean[int(q * (len(clean) - 1))]:g}")
    print("threshold -> snapshots over it (no OUT) / posts after cap+gap / days with a post")
    for t in (30, 40, 50, 60, 70, 80, 100):
        print(f"  {t:>4}: {sum(1 for s in clean if s >= t):>4} / {len(gate(results, t, args.cap, args.gap, args.gap_runs)):>4}"
              f" / {len({a[:10] for a, _ in gate(results, t, args.cap, args.gap, args.gap_runs)})}")
    posts = gate(results, args.min, args.cap, args.gap, args.gap_runs)
    by_hour: dict[str, list[float]] = {}
    for at, r in results:
        if at[:10] >= "2026-10-05":  # 4-hour runs (02/06/10/14/18/22)
            by_hour.setdefault(at[11:13], []).append(r["score"] if not r["veto"] else None)
    print("\nby run hour since 2026-10-05 (4 h runs): mean score without OUT / OUT share / best")
    for h, vals in sorted(by_hour.items()):
        ok = [v for v in vals if v is not None]
        print(f"  {h}:00  n={len(vals):>2}  mean {sum(ok) / max(len(ok), 1):>6.1f}  OUT {len(vals) - len(ok):>2}"
              f"  best {max(ok, default=0):g}")
    print("posts by hour (all history):", dict(sorted(Counter(a[11:13] for a, _ in posts).items())))
    if args.list:
        posted = {a for a, _ in posts}
        for at, r in results:
            if at[:10] >= args.list:
                mark = "POST" if at in posted else ("OUT " if r["veto"] else "    ")
                print(f"{mark} {at[:16]} {r['score']:>6g}  " + ", ".join(
                    f'{e["title"][:22]} {e["kind"]}{"" if e["rank"] is None else " #" + str(e["rank"])} {e["points"]:+g}'
                    for e in r["events"] if e["points"] or e["kind"] == "out"))
    print(f"\n=== min {args.min:g}, cap {args.cap}/day, gap {args.gap_runs} runs or {args.gap:g}h: {len(posts)} posts in {len(days)} days"
          f" ({len(posts) / max(len(days), 1):.2f}/day) ===")
    per_month = Counter(a[:7] for a, _ in posts)
    print("per month:", dict(sorted(per_month.items())))
    for at, r in posts[-args.show:]:
        upd = UpdateScore(score=r["score"], veto=r["veto"], events=r["events"])
        print(f"\n{at}  score {r['score']:g}  (vs {r['prev']})")
        print("   " + ", ".join(f'{e["title"]} {e["kind"]} {e["points"]:+g}' for e in r["events"] if e["points"]))
        print("   " + nrp.score_card_caption("us", upd, "https://x/amcharts").replace("\n\n", " / "))


def gate(results, minimum, cap, gap, gap_runs=0):
    """Production gate; gap = gap_runs distinct snapshots OR gap hours."""
    posts: list[tuple[str, dict]] = []
    last_i = None
    for i, (at, r) in enumerate(results):
        if r["veto"] or r["score"] < minimum:
            continue
        when = datetime.fromisoformat(at[:19])
        today = [p for p, _ in posts if p[:10] == at[:10]]
        if len(today) >= cap:
            continue
        if posts and (i - last_i) < gap_runs and                 (when - datetime.fromisoformat(posts[-1][0][:19])).total_seconds() < gap * 3600:
            continue
        posts.append((at, r))
        last_i = i
    return posts


if __name__ == "__main__":
    main()
