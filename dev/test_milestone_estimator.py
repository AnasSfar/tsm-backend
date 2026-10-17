from __future__ import annotations

import copy
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collectors/spotify/streams/tools/scripts"))
import forecast_milestones as fm
import milestone_estimator as model
from dev.test_forecast_milestones import make_series


class MilestoneEstimatorTest(unittest.TestCase):
    def test_decline_and_growth_both_taper_instead_of_compounding_forever(self):
        for trend in (0.99, 1.01):
            phi = model.DEFAULT_DAMPING
            terminal = 1_000_000 * trend ** (phi / (1 - phi))
            daily = model.projected_daily(1000, 1_000_000, trend, phi)
            self.assertAlmostEqual(daily, terminal, places=6)
            self.assertAlmostEqual(model.projected_daily(1001, 1_000_000, trend, phi), daily, places=6)

    def test_no_undamped_path_can_be_reintroduced(self):
        with self.assertRaises(ValueError):
            model.projected_daily(10, 1_000_000, 0.99, 1.0)

    def test_release_drop_followed_by_stable_weeks_does_not_become_lifetime_decline(self):
        values = [round(12_000_000 * 0.95 ** i) for i in range(56)] + [2_000_000] * 120
        series = make_series("2026-01-01", values)
        result = model.estimate(series)
        self.assertNotIn("pending_reason", result)
        self.assertEqual(result["projected_next_daily"], 2_000_000)
        self.assertEqual(result["decay_factor"], 1.0)
        self.assertIn("90", result["backtest"]["horizon_points"])

    def test_weekday_rises_and_falls_are_not_a_downward_trend(self):
        series = make_series("2026-01-01", [120, 115, 110, 105, 100, 80, 70] * 20)
        result = model.estimate(series)
        self.assertAlmostEqual(result["decay_factor"], 1.0)
        factors = model.weekday_factors(series)
        self.assertAlmostEqual(sum(factors.values()), 7)
        self.assertGreater(max(factors.values()), min(factors.values()))

    def test_new_release_uses_observed_pace_with_no_invented_decay_or_validation(self):
        series = make_series("2026-01-01", [120] * 7 + [100] * 7)
        result = model.estimate(series)
        self.assertEqual(result["projected_next_daily"], 100)
        self.assertEqual(result["decay_factor"], 1)
        self.assertEqual(result["backtest"]["points"], 0)
        self.assertIsNone(result["backtest"]["mean_smape"])
        self.assertIsNone(result["confidence"]["score"])
        self.assertTrue(result["forecast_model"]["validation_note"])

    def test_each_extra_100m_uses_the_same_trajectory(self):
        kwargs = dict(current_streams=1_735_000_000, last_date="2026-10-15",
                      start_daily=2_000_000, decay_factor=0.995, trend_damping=model.DEFAULT_DAMPING)
        lower = fm.project_milestone_date(**kwargs, milestone=2_000_000_000)
        higher = fm.project_milestone_date(**kwargs, milestone=2_100_000_000)
        self.assertGreater(higher["days_left"], lower["days_left"])
        self.assertLess(higher["days_left"] - lower["days_left"], 70)

    def test_recent_missing_estimated_merged_duplicate_or_bad_daily_is_pending(self):
        valid = make_series("2026-01-01", [100] * 60)
        cases = [valid[:-1], valid + [dict(valid[-1])], valid[:-3] + valid[-2:]]
        for change in ({"estimated": True}, {"merged": True}, {"daily_streams": None},
                       {"daily_streams": -1}, {"daily_streams": 101}, {"streams": 1.5}):
            rows = copy.deepcopy(valid)
            rows[-1].update(change)
            cases.append(rows)
        for rows in cases:
            with self.subTest(last=rows[-1]):
                exact, reason = model.exact_recent_series(rows, valid[-1]["date"])
                self.assertEqual(exact, [])
                self.assertTrue(reason)

    def test_old_discontinuity_ends_training_window_with_no_joined_gap(self):
        series = make_series("2026-01-01", [100] * 60)
        series = series[:10] + series[20:]
        exact, reason = model.exact_recent_series(series, series[-1]["date"])
        self.assertIsNone(reason)
        self.assertEqual(exact[0]["date"], "2026-01-22")
        self.assertEqual(len(exact), 39)

    def test_zero_daily_is_kept_and_missing_daily_is_not_inferred(self):
        series = make_series("2026-01-01", [100] * 30 + [0] + [100] * 29)
        exact, reason = model.exact_recent_series(series, series[-1]["date"])
        self.assertIsNone(reason)
        self.assertEqual(exact[29]["daily_streams"], 0)
        broken = copy.deepcopy(series)
        broken[-1]["daily_streams"] = None
        self.assertIsNotNone(model.exact_recent_series(broken, series[-1]["date"])[1])

    def test_future_rows_do_not_change_as_of_training(self):
        series = make_series("2026-01-01", [100] * 60)
        as_of = series[-1]["date"]
        future = make_series("2026-03-02", [9_000_000] * 30)
        before = copy.deepcopy(series)
        exact, _ = model.exact_recent_series(series + future, as_of)
        self.assertEqual(model.estimate(exact), model.estimate(series[1:]))
        self.assertEqual(series, before)

    def test_selection_backtests_train_only_before_origin(self):
        series = make_series("2026-01-01", [100 + i % 7 for i in range(365)])
        seen = []
        original = model.fit_candidate
        def observe(train, name):
            seen.append(len(train))
            return original(train, name)
        with patch.object(model, "fit_candidate", side_effect=observe):
            result = model.estimate(series)
        self.assertTrue(all(n <= len(series) - 7 for n in seen[:-1]))
        self.assertEqual(seen[-1], len(series))
        self.assertEqual(set(result["backtest"]["horizon_points"]), {"7", "28", "90", "180"})

    def test_build_forecasts_has_no_date_for_stale_or_inexact_data(self):
        series = make_series("2026-01-01", [100] * 60)
        days = [r["date"] for r in series]
        history = {r["date"]: {"track": {"s": r["streams"], "d": r["daily_streams"]}} for r in series}
        for change in ({"e": True}, {"m": True}, {"d": None}):
            data = copy.deepcopy(history)
            data[days[-1]]["track"].update(change)
            with patch.object(fm, "load_json", return_value={"songs": [{"track_id": "track", "title": "Test"}]}), \
                 patch.object(fm, "load_history_bundle", return_value={"dates": days, "by_date": data}):
                result = fm.build_forecasts()["forecasts"][0]
            self.assertIsNone(result["forecast"])
            self.assertTrue(result["pending_reason"])
            self.assertIsNone(result["estimated_next_daily"])

    def test_watch_and_site_share_the_same_trajectory_including_growth(self):
        import spotify_app_cards as app
        trends = [{"song": {"track_id": "track", "title": "Test"}, "streams": 90_000_000,
                   "daily": 1_000_000, "last": "2026-01-01", "start": 1_000_000,
                   "decay": 1.01, "damping": model.DEFAULT_DAMPING, "wf": {i: 1 for i in range(7)}}]
        with patch.object(app, "_song_trends", return_value=trends):
            watch = app.watch_rows("2026-01-01", 100_000_000)[0]
        expected = fm.project_milestone_date(90_000_000, "2026-01-01", 1_000_000, 1.01,
            100_000_000, trends[0]["wf"], trend_damping=model.DEFAULT_DAMPING)
        self.assertEqual(watch["expected_date"], expected["expected_date"])
        self.assertEqual(watch["days_left"], expected["days_left"])

    def test_watch_blocks_missing_stale_or_duplicate_catalog_data(self):
        import history_store
        import spotify_app_cards as app
        series = make_series("2026-01-01", [100] * 60)
        days = [r["date"] for r in series]
        data = {r["date"]: {"track": {"s": r["streams"], "d": r["daily_streams"]}} for r in series}
        cases = [([], data), ([{"track_id": "track"}] * 2, data)]
        stale = copy.deepcopy(data)
        stale[days[-1]] = {}
        cases.append(([{"track_id": "track"}], stale))
        for songs, history in cases:
            with patch.dict(app._TRENDS, {}, clear=True), \
                 patch.object(history_store, "load_album_track_ids", return_value={"track"}), \
                 patch.object(fm, "load_json", return_value={"songs": songs}), \
                 patch.object(fm, "load_history_bundle", return_value={"dates": days, "by_date": history}):
                with self.assertRaisesRegex(ValueError, "blocked"):
                    app.watch_rows(days[-1], 100_000_000)


if __name__ == "__main__":
    unittest.main()
