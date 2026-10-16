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

    def test_all_milestones_match_single_milestone_projection(self) -> None:
        wf = {0: 1.0, 1: 1.04, 2: 1.05, 3: 1.06, 4: 1.04, 5: 0.95, 6: 0.86}
        args = dict(current_streams=872_000_000, last_date="2026-10-13", start_daily=280_000, decay_factor=0.998)
        allm = fm.project_all_milestones(weekday_factors=wf, **args)
        self.assertEqual([m["label"] for m in allm[:3]], ["900M", "1B", "1.1B"])
        for item in allm:
            single = fm.project_milestone_date(
                milestone=item["milestone"], weekday_factors=wf,
                decay_days=fm.LONG_DECAY_DAYS, max_days=fm.LONG_MAX_DAYS, **args,
            )
            self.assertEqual(single["expected_date"], item["expected_date"])
        self.assertLessEqual(allm[-1]["days_left"], fm.LONG_MAX_DAYS)

    def test_weekday_factors_follow_the_weekly_shape(self) -> None:
        shape = [1.0, 1.04, 1.05, 1.06, 1.04, 0.95, 0.86]  # Mon..Sun
        start = date(2026, 1, 5)  # a Monday
        dailies = [int(300_000 * (0.999 ** i) * shape[(start + timedelta(days=i)).weekday()]) for i in range(120)]
        wf = fm.estimate_weekday_factors(make_series(start.isoformat(), dailies))
        avg = sum(shape) / 7
        for day in range(7):
            self.assertAlmostEqual(wf[day], shape[day] / avg, delta=0.01)

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
