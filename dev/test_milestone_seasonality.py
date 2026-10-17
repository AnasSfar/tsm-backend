from __future__ import annotations

import copy
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collectors/spotify/streams/tools/scripts"))
import milestone_estimator as estimator
import milestone_seasonality as seasonal
import forecast_milestones as fm
from dev.test_forecast_milestones import make_series


def seasons(years=(2022, 2023, 2024)):
    rows = []
    for year in years:
        start, end = date(year, 6, 30), date(year, 8, 31)
        values = [200 if (start + timedelta(days=i)).month == 8 else 100
                  for i in range((end - start).days + 1)]
        rows.extend(make_series(start.isoformat(), values))
    return rows


RULE = {"month": 8, "label": "August", "first_year": 2022}


class MilestoneSeasonalityTest(unittest.TestCase):
    def test_requires_repeated_exact_seasons_and_held_out_improvement(self):
        evidence = seasonal.verified_seasons(seasons(), RULE, "2024-09-01")
        profile = seasonal.profile_at(evidence, "2024-09-01")
        self.assertTrue(profile["enabled"])
        self.assertEqual(profile["factor"], 2)
        self.assertEqual(profile["training_years"], [2022, 2023, 2024])
        self.assertEqual(len(profile["validation"]), 2)
        self.assertFalse(seasonal.profile_at(evidence, "2023-07-31")["enabled"])

    def test_current_and_future_seasons_never_train_earlier_origins(self):
        rows = seasons()
        evidence = seasonal.verified_seasons(rows, RULE, "2024-09-01")
        before = seasonal.profile_at(evidence, "2024-07-31")
        changed = copy.deepcopy(rows)
        for row in changed:
            if row["date"].startswith("2024-08"):
                row["daily_streams"] = 9999
        later = seasonal.verified_seasons(changed, RULE, "2024-09-01")
        self.assertEqual(before, seasonal.profile_at(later, "2024-07-31"))
        self.assertEqual(before["training_years"], [2022, 2023])

    def test_missing_estimated_duplicate_and_inconsistent_season_is_excluded_with_reason(self):
        valid = seasons()
        day = "2023-07-20"
        cases = [[r for r in valid if r["date"] != day], valid + [copy.deepcopy(next(r for r in valid if r["date"] == day))]]
        for change in ({"estimated": True}, {"merged": True}, {"daily_streams": None}, {"daily_streams": 101}):
            rows = copy.deepcopy(valid)
            next(r for r in rows if r["date"] == day).update(change)
            cases.append(rows)
        for rows in cases:
            evidence = seasonal.verified_seasons(rows, RULE, "2024-09-01")
            self.assertEqual([s["year"] for s in evidence["seasons"]], [2022, 2024])
            self.assertTrue(evidence["excluded_seasons"][0]["reason"])

    def test_note_depends_on_whether_projection_actually_spans_august(self):
        profile = seasonal.profile_at(seasonal.verified_seasons(seasons(), RULE, "2024-09-01"), "2024-09-01")
        self.assertEqual(seasonal.projection_note(profile, "2024-10-15", "2025-07-31")["text"], "Before next August boost")
        self.assertEqual(seasonal.projection_note(profile, "2024-10-15", "2025-08-01")["text"], "August boost included")
        self.assertIsNone(seasonal.projection_note({"enabled": False}, "2024-10-15", "2025-08-01"))

    def test_august_level_is_deseasonalized_before_future_boost_is_applied(self):
        profile = {"enabled": True, "month": 8, "factor": 2}
        rows = make_series("2024-08-01", [200] * 28)
        before = copy.deepcopy(rows)
        adjusted = seasonal.adjusted(rows, profile)
        self.assertEqual(adjusted[-1]["daily_streams"], 100)
        model = estimator.fit_candidate(adjusted, "level_7")
        august_daily = fm._daily_on_projection_day(1, model["start_daily"], 1, date(2024, 8, 28), None,
                                                   annual_seasonality=profile)
        september_daily = fm._daily_on_projection_day(4, model["start_daily"], 1, date(2024, 8, 28), None,
                                                      annual_seasonality=profile)
        self.assertEqual(august_daily, 200)
        self.assertEqual(september_daily, 100)
        self.assertEqual(rows, before)

    def test_no_automatic_seasonality_for_unmapped_track(self):
        rows = make_series("2024-01-01", [100] * 60)
        result = estimator.estimate(rows, track_id="another-track", seasonal_history=seasons())
        self.assertIsNone(result["annual_seasonality"])

    def test_notes_require_evidence_and_relate_to_selected_inputs(self):
        steady = make_series("2024-01-01", [100] * 28)
        self.assertEqual(estimator.observed_note(steady, {"name": "level_7", "decay_factor": 1}, True, None)["kind"], "steady_pace")
        self.assertEqual(estimator.observed_note(steady, {"name": "level_7", "decay_factor": 1}, False, None)["kind"], "limited_history")
        falling = make_series("2024-01-01", [200] * 7 + [180] * 7 + [150] * 7)
        self.assertEqual(estimator.observed_note(falling, {"name": "damped_28_7", "decay_factor": .99}, True, "2024-01-01")["kind"], "release_easing")
        self.assertEqual(estimator.observed_note(falling, {"name": "damped_28_7", "decay_factor": .99}, True, "2020-01-01")["kind"], "recent_decline")
        mixed = make_series("2024-01-01", [200] * 7 + [100] * 7 + [150] * 7)
        self.assertIsNone(estimator.observed_note(mixed, {"name": "level_7", "decay_factor": 1}, True, None))
        level = estimator.fit_candidate(mixed, "level_28")
        # A recent average isn't evidence that the observed weeks are steady.
        self.assertNotEqual((estimator.observed_note(mixed, level, True, None) or {}).get("kind"), "steady_pace")
        release_history_missing = falling[7:]
        note = estimator.observed_note(release_history_missing, {"name": "damped_28_7", "decay_factor": .99}, True, "2024-01-01")
        self.assertNotEqual((note or {}).get("kind"), "release_easing")


if __name__ == "__main__":
    unittest.main()
