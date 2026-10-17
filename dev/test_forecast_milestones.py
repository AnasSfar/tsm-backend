from __future__ import annotations

import sys
import unittest
import copy
from datetime import date, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import forecast_milestones as fm  # noqa: E402


def make_series(start: str, dailies: list[int], start_streams: int = 1_000_000) -> list[dict]:
    day = fm.parse_iso_date(start)
    total = start_streams
    rows = []
    for offset, daily in enumerate(dailies):
        total += daily
        row_date = day + timedelta(days=offset)
        rows.append(
            {
                "date": row_date.isoformat(),
                "date_obj": row_date,
                "streams": total,
                "daily_streams": daily,
            }
        )
    return rows


class ForecastMilestonesTest(unittest.TestCase):
    def next_step_rows(self, daily=1_000_000, total=400_000_000):
        return make_series("2026-01-01", [daily] * 8, start_streams=total - 8 * daily)

    def test_same_song_forecast_targets_500m_after_400m(self):
        result = fm.next_song_milestone_at_recent_pace(self.next_step_rows(), reached_milestone=400_000_000, as_of_date="2026-01-08")
        self.assertEqual(result["milestone"], 500_000_000)
        self.assertEqual(result["expected_date"], (date(2026, 1, 8) + timedelta(days=100)).isoformat())
        self.assertEqual(result["basis"], "current_7_day_pace")

    def test_anticipatory_date_starts_from_verified_total_below_milestone(self):
        rows = self.next_step_rows(total=399_500_000)
        result = fm.next_song_milestone_at_recent_pace(rows, reached_milestone=400_000_000, as_of_date="2026-01-08")
        self.assertEqual(result["days_from_snapshot"], 101)
        self.assertEqual(result["milestone"], 500_000_000)

    def test_next_step_forecast_rounds_up_days(self):
        result = fm.next_song_milestone_at_recent_pace(self.next_step_rows(daily=300_000), reached_milestone=400_000_000, as_of_date="2026-01-08")
        self.assertEqual(result["days_from_snapshot"], 334)

    def test_next_step_forecast_never_reads_future_rows(self):
        rows = self.next_step_rows()
        before = fm.next_song_milestone_at_recent_pace(rows, reached_milestone=400_000_000, as_of_date="2026-01-08")
        rows.append({"date": "2026-01-09", "streams": 900_000_000, "daily_streams": 500_000_000})
        self.assertEqual(before, fm.next_song_milestone_at_recent_pace(rows, reached_milestone=400_000_000, as_of_date="2026-01-08"))

    def test_next_step_missing_estimated_duplicate_or_inconsistent_data_has_no_date(self):
        valid = self.next_step_rows()
        cases = [valid[:-1], valid + [dict(valid[-1])]]
        for change in ({"estimated": True}, {"daily_streams": None}, {"daily_streams": -1}, {"streams": 400_000_001}):
            rows = copy.deepcopy(valid)
            rows[-1].update(change)
            cases.append(rows)
        for rows in cases:
            result = fm.next_song_milestone_at_recent_pace(rows, reached_milestone=400_000_000, as_of_date="2026-01-08")
            self.assertIsNone(result["expected_date"])
            self.assertTrue(result["pending_reason"])

    def test_zero_week_is_pending(self):
        result = fm.next_song_milestone_at_recent_pace(self.next_step_rows(daily=0), reached_milestone=400_000_000, as_of_date="2026-01-08")
        self.assertIsNone(result["expected_date"])
        self.assertIn("zero", result["pending_reason"])

    def test_next_step_is_above_actual_total_and_does_not_mutate_history(self):
        rows = self.next_step_rows(total=450_000_000)
        original = copy.deepcopy(rows)
        result = fm.next_song_milestone_at_recent_pace(rows, reached_milestone=100_000_000, as_of_date="2026-01-08")
        self.assertEqual(result["milestone"], 500_000_000)
        self.assertEqual(rows, original)

    def test_next_milestone_uses_100m_steps_after_1b(self) -> None:
        self.assertEqual(fm.next_milestone(1_045_000_000), 1_100_000_000)
        self.assertEqual(fm.next_milestone(5_050_000_000), 5_100_000_000)

    def test_projection_uses_supplied_last_track_date(self) -> None:
        projected = fm.project_milestone_date(
            current_streams=990_000_000,
            last_date="2026-01-10",
            start_daily=1_000_000,
            decay_factor=1.0,
            milestone=1_000_000_000,
        )
        self.assertIsNotNone(projected)
        self.assertEqual(projected["expected_date"], "2026-01-20")
        self.assertEqual(projected["days_left"], 10)

    def test_advanced_estimator_reacts_to_growth_trend(self) -> None:
        dailies = [100_000 + i * 2_000 for i in range(90)]
        series = make_series("2026-01-01", dailies)
        estimate = fm.estimate_future_daily_streams(series)
        self.assertGreater(estimate["projected_next_daily"], dailies[-14])
        self.assertGreater(estimate["decay_factor"], 1.0)
        self.assertIn("forecast_model", estimate)
        self.assertIn("backtest", estimate)
        self.assertIn("confidence", estimate)

    def test_scenario_dates_are_ordered_when_available(self) -> None:
        confidence = {"score": 0.8}
        scenarios = fm.scenario_dates(
            current_streams=950_000_000,
            last_date="2026-01-01",
            start_daily=1_000_000,
            decay_factor=1.0,
            milestone=1_000_000_000,
            weekday_factors={i: 1.0 for i in range(7)},
            confidence=confidence,
        )
        self.assertLess(
            fm.parse_iso_date(scenarios["optimistic"]["expected_date"]),
            fm.parse_iso_date(scenarios["expected"]["expected_date"]),
        )
        self.assertGreater(
            fm.parse_iso_date(scenarios["conservative"]["expected_date"]),
            fm.parse_iso_date(scenarios["expected"]["expected_date"]),
        )


if __name__ == "__main__":
    unittest.main()
