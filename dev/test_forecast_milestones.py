from __future__ import annotations

import sys
import unittest
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
