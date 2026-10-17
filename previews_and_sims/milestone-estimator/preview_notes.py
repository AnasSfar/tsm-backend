"""Real-data Watch notes and annual-effect audit. No posts or production writes.

python previews_and_sims/milestone-estimator/preview_notes.py [--render]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "collectors/spotify/streams/tools/scripts"))
import forecast_milestones as fm
import spotify_app_cards as app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    hist = fm.load_history_bundle()
    tid = "3hUxzQpSfdDqwM3ZTFQY0K"
    replay = []
    for year in (2023, 2025, 2026):
        origin = f"{year}-07-31"
        dates = [d for d in hist["dates"] if d <= origin]
        past = fm.get_track_history_series(tid, hist["by_date"], dates)
        exact, reason = fm.estimator.exact_recent_series(past, origin)
        if reason:
            replay.append({"origin": origin, "pending_reason": reason})
            continue
        actual_series = fm.get_track_history_series(tid, hist["by_date"], [d for d in hist["dates"] if origin <= d <= f"{year}-08-31"])
        actual, reason = fm.estimator.exact_recent_series(actual_series, f"{year}-08-31")
        if reason or len(actual) != 31:
            replay.append({"origin": origin, "pending_reason": reason or "held-out month incomplete"})
            continue
        observed = sum(r["daily_streams"] for r in actual)
        case = {"origin": origin, "observed_august_streams": observed}
        for label, kwargs in (("seasonal", {"track_id": tid, "seasonal_history": past}), ("unadjusted", {})):
            est = fm.estimate_future_daily_streams(exact, **kwargs)
            if est.get("pending_reason"):
                case[label] = {"pending_reason": est["pending_reason"]}
                continue
            factors = {int(k): v for k, v in est["forecast_model"]["weekday_factors"].items()}
            predicted = sum(fm._daily_on_projection_day(h, est["projected_next_daily"], est["decay_factor"],
                date.fromisoformat(origin), factors, trend_damping=est["trend_damping"],
                annual_seasonality=est.get("annual_seasonality")) for h in range(1, 32))
            case[label] = {"estimated_august_streams": predicted, "smape": 2 * abs(predicted - observed) / (predicted + observed),
                           "selected_model": est["forecast_model"]["selected_model"], "annual_profile": est.get("annual_seasonality")}
        replay.append(case)
    d = hist["dates"][-1]
    watches = {}
    for target in (2_100_000_000, 3_000_000_000):
        label = app.milestone_label(target)
        rows = app.watch_rows(d, target)
        watches[label] = rows
        if args.render:
            out = HERE / "cards" / f"watch_{label}_{d}.png"
            app.render_watch(d, out, rows, target)
            with Image.open(out) as im:
                assert max(im.size) <= 4096, f"X size limit exceeded: {im.size}"
                im.resize((310, round(im.height * 310 / im.width)), Image.Resampling.LANCZOS).save(out.with_name(out.stem + "_phone.png"))
    output = {"stats_date": d, "seasonal_holdouts": replay, "watch_rows": watches}
    path = HERE / "notes_and_seasonality.json"
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"seasonal_holdouts": [{"origin": r["origin"], **{k: v["smape"] for k, v in r.items() if isinstance(v, dict) and "smape" in v}} for r in replay], "audit": str(path)}, indent=2))


if __name__ == "__main__":
    main()
