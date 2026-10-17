"""Read-only replay against exact historical totals; writes private audit JSON only.

python dev/audit_milestone_estimator.py --baseline runtime/tmp/estimator-audit/legacy_forecast_milestones.py
The optional baseline is a saved copy of the pre-change estimator, never code
loaded from the network. Historical dates are deliberately fixed for comparison.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors/spotify/streams/tools/scripts"))
import forecast_milestones as fm
import history_store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "previews_and_sims/milestone-estimator/replay.json")
    args = parser.parse_args()
    legacy = None
    if args.baseline:
        spec = importlib.util.spec_from_file_location("legacy_forecast", args.baseline)
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
    history = fm.load_history_bundle()
    dates = history["dates"]
    songs = fm.load_json(fm.SONGS_PATH)["songs"]
    rows, blocked = [], []
    for origin in ("2026-03-31", "2026-06-30", "2026-08-31"):
        ids = history_store.load_album_track_ids(origin)
        candidates = [s for s in songs if s["track_id"] in ids]
        past_dates = [d for d in dates if d <= origin]
        for index, song in enumerate(candidates):
            tid = song["track_id"]
            past = fm.get_track_history_series(tid, history["by_date"], past_dates)
            exact, reason = fm.estimator.exact_recent_series(past, origin)
            if reason:
                blocked.append({"track_id": tid, "origin": origin, "reason": reason})
                continue
            new = fm.estimate_future_daily_streams(exact, track_id=tid, seasonal_history=past,
                                                  release_date=song.get("release_date"))
            if new.get("pending_reason"):
                blocked.append({"track_id": tid, "origin": origin, "reason": new["pending_reason"]})
                continue
            current = exact[-1]["streams"]
            target = fm.next_milestone(current)
            new_projection = fm.project_milestone_date(current, origin, new["projected_next_daily"],
                new["decay_factor"], target, {int(k): v for k, v in new["forecast_model"]["weekday_factors"].items()},
                trend_damping=new["trend_damping"], annual_seasonality=new.get("annual_seasonality"))
            # Verify every held-out daily; stop at the first gap/defect. A later
            # recovered total is not proof of an earlier exact crossing date.
            future = []
            previous = current
            for n in range(1, 201):
                day = (fm.parse_iso_date(origin) + timedelta(days=n)).isoformat()
                if day > dates[-1]:
                    break
                row = history["by_date"].get(day, {}).get(tid, {})
                total, daily = row.get("s"), row.get("d")
                if row.get("e") or row.get("m") or type(total) is not int or type(daily) is not int or daily < 0 or total - previous != daily:
                    break
                future.append((day, total, daily))
                previous = total
            actual = next((d for d, total, _ in future if total >= target), None)
            old, old_projection = None, None
            if legacy:
                old_series = legacy.get_track_history_series(tid, history["by_date"], past_dates)
                old = legacy.estimate_future_daily_streams(old_series)
                old_projection = legacy.project_milestone_date(current, origin, old["projected_next_daily"],
                    min(old["decay_factor"], 1.0), target, legacy.estimate_weekday_factors(old_series))
            item = {"track_id": tid, "title": song["title"], "origin": origin, "target": target,
                    "actual_crossing": actual, "new_projection": new_projection, "old_projection": old_projection,
                    "selected_model": new["forecast_model"]["selected_model"], "cumulative_errors": {}}
            for h in (7, 28, 90, 180):
                if len(future) < h:
                    continue
                observed = sum(daily for _, _, daily in future[:h])
                def predict(module, est, damping=None):
                    kwargs = {"trend_damping": damping, "annual_seasonality": est.get("annual_seasonality")} if module is fm else {}
                    factors = {int(k): v for k, v in est["forecast_model"]["weekday_factors"].items()} if module is fm else module.estimate_weekday_factors(old_series)
                    return sum(module._daily_on_projection_day(n, est["projected_next_daily"],
                               est["decay_factor"] if module is fm else min(est["decay_factor"], 1.0),
                               fm.parse_iso_date(origin), factors, **kwargs) for n in range(1, h + 1))
                predicted = predict(fm, new, new["trend_damping"])
                metrics = {"observed": observed, "new_smape": 2 * abs(predicted-observed)/(predicted+observed) if predicted+observed else 0.0}
                if old:
                    predicted = predict(legacy, old)
                    metrics["old_smape"] = 2 * abs(predicted-observed)/(predicted+observed) if predicted+observed else 0.0
                item["cumulative_errors"][str(h)] = metrics
            rows.append(item)
            if index % 50 == 0:
                print(f"{origin}: {index + 1}/{len(candidates)} tracks", flush=True)
    summary = {"cases": len(rows), "blocked_cases": len(blocked), "horizons": {}, "crossings": {}}
    for h in (7, 28, 90, 180):
        points = [r["cumulative_errors"][str(h)] for r in rows if str(h) in r["cumulative_errors"]]
        summary["horizons"][str(h)] = {"cases": len(points), **{
            key: statistics.mean(p[key] for p in points if key in p) for key in ("new_smape", "old_smape")
            if any(key in p for p in points)}}
    reached = [r for r in rows if r["actual_crossing"]]
    summary["crossings"]["observed"] = len(reached)
    summary["crossings"]["paired_predictions"] = sum(bool(r["new_projection"] and r["old_projection"]) for r in reached)
    for name in ("new", "old"):
        errors = [abs((fm.parse_iso_date(r[f"{name}_projection"]["expected_date"]) - fm.parse_iso_date(r["actual_crossing"])).days)
                  for r in reached if r[f"{name}_projection"]]
        summary["crossings"][name] = {"predicted": len(errors), "no_date": len(reached) - len(errors),
            "median_absolute_days": statistics.median(errors) if errors else None,
            "mean_absolute_days": statistics.mean(errors) if errors else None}
    summary["model_counts"] = dict(Counter(r["selected_model"] for r in rows))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "cases": rows, "blocked": blocked}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
