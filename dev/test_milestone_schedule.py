"""Milestone anticipation: date bounds and integrity gates (no production writes)."""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from contextlib import ExitStack, redirect_stdout
import copy
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "collectors" / "spotify" / "streams" / "tools" / "scripts"))
import milestone_schedule as m
import schedule_stream_milestones as schedule


def series(values=None):
    values = values if values is not None else [100] * 116
    total = 1_000
    result = []
    for i, value in enumerate(values):
        total += value
        result.append({"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(), "streams": total, "daily_streams": value})
    return result


class ForecastTests(unittest.TestCase):
    def test_separates_next_day_and_following_day(self):
        rows = series()
        total = rows[-1]["streams"]
        self.assertTrue(m.crossing_window(rows, total + 90, 1)["eligible"])
        self.assertFalse(m.crossing_window(rows, total + 110, 1)["eligible"])
        self.assertTrue(m.crossing_window(rows, total + 190, 2)["eligible"])
        self.assertFalse(m.crossing_window(rows, total + 90, 2)["eligible"])

    def test_decline_reduces_eligibility(self):
        rows = series([100] * 109 + [70] * 7)
        total = rows[-1]["streams"]
        self.assertFalse(m.crossing_window(rows, total + 90, 1)["eligible"])

    def test_5k_remaining_with_150k_daily_survives_recent_worst_day(self):
        rows = series([150_000] * 14 + [20_000] + [150_000] * 13)
        result = m.crossing_window(rows, rows[-1]["streams"] + 5_000, 1)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["observed"]["last_daily"], 150_000)
        self.assertEqual(result["observed"]["recent_worst_daily"], 20_000)
        self.assertEqual(result["lower_gain"], 20_000)
        self.assertTrue(all(result["scenario_passed"].values()))
        self.assertNotIn("nominal_coverage", result)

    def test_average_cannot_hide_worst_day_short_of_milestone(self):
        rows = series([150_000] * 14 + [20_000] + [150_000] * 13)
        result = m.crossing_window(rows, rows[-1]["streams"] + 25_000, 1)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["assessment_reason"], "pessimistic_scenario_short_of_milestone")

    def test_weekly_minus_5_percent_is_applied_to_the_daily(self):
        rows = series([200_000] * 7 + [190_000] * 7)
        result = m.crossing_window(rows, rows[-1]["streams"] + 180_000, 1)
        self.assertEqual(result["observed"]["weekly_change_pct"], -5.0)
        self.assertEqual(result["observed"]["stress_decline_factor"], 0.95)
        self.assertEqual(result["scenario_gains"]["last_daily_weekly_decline"], 180_500)
        self.assertTrue(result["eligible"])
        self.assertFalse(m.crossing_window(rows, rows[-1]["streams"] + 181_000, 1)["eligible"])

    def test_worst_weekly_decline_over_last_7_days_is_stressed(self):
        rows = series([100] * 7 + [50] + [100] * 6)
        result = m.crossing_window(rows, rows[-1]["streams"] + 30, 1)
        self.assertEqual(result["observed"]["worst_recent_weekly_change_pct"], -50.0)
        self.assertEqual(result["lower_gain"], 25)
        self.assertFalse(result["eligible"])

    def test_growth_never_boosts_pessimistic_gain(self):
        rows = series([100] * 7 + [200] * 7)
        result = m.crossing_window(rows, rows[-1]["streams"] + 101, 1)
        self.assertEqual(result["observed"]["stress_decline_factor"], 1.0)
        self.assertEqual(result["lower_gain"], 100)
        self.assertFalse(result["eligible"])

    def test_rational_rounding_cannot_promote_a_shortfall(self):
        rows = series([3] * 7 + [2] * 7)
        result = m.crossing_window(rows, rows[-1]["streams"] + 2, 1)
        self.assertEqual(result["lower_gain"], 1)
        self.assertFalse(result["eligible"])

    def test_zero_worst_day_keeps_it_pending(self):
        rows = series([0] + [150_000] * 27)
        result = m.crossing_window(rows, rows[-1]["streams"] + 5_000, 1)
        self.assertEqual(result["lower_gain"], 0)
        self.assertFalse(result["eligible"])

    def test_14_exact_days_can_qualify_without_months_of_calibration(self):
        rows = series([150_000] * 14)
        self.assertTrue(m.crossing_window(rows, rows[-1]["streams"] + 5_000, 1)["eligible"])

    def test_no_lookahead(self):
        rows = series()
        cutoff = rows[:100]
        before = m.crossing_window(cutoff, cutoff[-1]["streams"] + 90, 1)
        rows[101]["daily_streams"] = 1_000_000
        self.assertEqual(before, m.crossing_window(rows[:100], cutoff[-1]["streams"] + 90, 1))

    def test_two_complete_weeks_required(self):
        with self.assertRaises(m.PendingForecast):
            m.crossing_window(series()[:13], 100_000, 1)

    def test_duplicate_even_equal_blocks(self):
        rows = series()
        history = SimpleNamespace(points_by_track={"a": rows + [dict(rows[-1])]})
        with self.assertRaisesRegex(m.PendingForecast, "duplicate"):
            m.exact_series(history, {"a"}, rows[-1]["date"])

    def test_estimated_or_inconsistent_daily_blocks(self):
        for change in ({"estimated": True}, {"daily_streams": 101}, {"daily_streams": None}):
            rows = series()
            rows[-1].update(change)
            with self.assertRaises(m.PendingForecast):
                m.exact_series(SimpleNamespace(points_by_track={"a": rows}), {"a"}, rows[-1]["date"])

    def test_album_requires_every_member(self):
        rows = series()
        with self.assertRaisesRegex(m.PendingForecast, "missing current"):
            m.exact_series(SimpleNamespace(points_by_track={"a": rows, "b": rows[:-1]}), {"a", "b"}, rows[-1]["date"])

    def test_gap_is_not_filled(self):
        rows = series()
        del rows[-6]
        exact = m.exact_series(SimpleNamespace(points_by_track={"a": rows}), {"a"}, rows[-1]["date"])
        self.assertLess(len(exact), 6)
        with self.assertRaises(m.PendingForecast):
            m.crossing_window(exact, exact[-1]["streams"] + 90, 1)

    def test_aggregates_exact_album_totals_and_dailies(self):
        rows = series()
        exact = m.exact_series(SimpleNamespace(points_by_track={"a": rows, "b": rows}), {"a", "b"}, rows[-1]["date"])
        self.assertEqual(exact[-1]["streams"], rows[-1]["streams"] * 2)
        self.assertEqual(exact[-1]["daily_streams"], 200)

    def test_no_post_or_plan_only_does_not_persist_audit(self):
        ledger = {"events": {"a__100": {"status": "scheduled", "kind": "song", "track_id": "a", "target_date": "2026-10-14", "milestone": 100}}}
        history = SimpleNamespace(get_total_for_date=lambda tid, day: 110 if day == "2026-10-14" else 90)
        with patch.object(m, "save_ledger") as save:
            schedule.reconcile(ledger, history, "2026-10-14", persist=False)
        save.assert_not_called()
        self.assertEqual(ledger["events"]["a__100"]["outcome"], "confirmed")

    def test_wrong_day_is_audited_without_reposting(self):
        ledger = {"events": {"a__100": {"status": "scheduled", "kind": "song", "track_id": "a", "target_date": "2026-10-14", "milestone": 100}}}
        history = SimpleNamespace(get_total_for_date=lambda *a: 90)
        schedule.reconcile(ledger, history, "2026-10-14", persist=False)
        self.assertEqual(ledger["events"]["a__100"]["outcome"], "wrong_day")

    def test_anticipated_copy_has_no_unverified_rank(self):
        text = schedule.posts.stream_milestone_tweet(title="Example", milestone_streams=100_000_000, milestone_rank=None, prefix="X |")
        self.assertIn("100,000,000", text)
        self.assertNotIn("song to do so", text)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.event = {"track_id": "a", "title": "Example", "kind": "song", "milestone": 100,
                      "target_date": "2026-10-15", "base_date": "2026-10-14", "assessment": "robust_crossing"}
        self.ledger = {"events": {}}
        self.saved = []

    def run_planner(self, *, success=True, no_post=False):
        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 15, 12, tzinfo=schedule.MADRID).astimezone(tz)
        with ExitStack() as stack:
            stack.enter_context(patch.object(schedule, "datetime", FrozenDatetime))
            stack.enter_context(patch.object(schedule.posts.history_store.HistoryIndex, "load", return_value=object()))
            stack.enter_context(patch.object(m, "load_ledger", return_value=self.ledger))
            stack.enter_context(patch.object(m, "save_ledger", side_effect=lambda p: self.saved.append(copy.deepcopy(p))))
            stack.enter_context(patch.object(schedule, "candidates", return_value=[self.event]))
            render = stack.enter_context(patch.object(schedule, "render", return_value=("tweet", Path("isolated.png")) ))
            stack.enter_context(patch.object(schedule.posts, "TWITTER_SESSION", SimpleNamespace(exists=lambda: True)))
            stack.enter_context(patch.object(schedule.twitter_core, "_account_key", return_value="isolated-account"))
            native = stack.enter_context(patch.object(schedule.twitter_core, "schedule_post", return_value=success))
            direct = stack.enter_context(patch.object(schedule.posts, "post_with_image"))
            stack.enter_context(patch.dict(schedule.os.environ, {}, clear=False))
            stack.enter_context(patch.object(schedule.twitter_core, "scheduled_entries", side_effect=lambda account=None: [{
                "source": "milestone-advance:a__100", "at": datetime(2026, 10, 15, 16, 30, tzinfo=schedule.MADRID),
            }] if account is None else []))
            args = SimpleNamespace(date="2026-10-14", no_post=no_post, plan_only=False)
            if not success and not no_post:
                with self.assertRaisesRegex(SystemExit, "not confirmed"):
                    schedule.run(args)
            else:
                self.assertEqual(schedule.run(args), 0)
            direct.assert_not_called()
            return native, render

    def test_success_reserves_before_submission_and_records_receipt(self):
        native, _ = self.run_planner()
        native.assert_called_once()
        self.assertEqual(self.saved[0]["events"]["a__100"]["status"], "submitting")
        self.assertEqual(self.saved[-1]["events"]["a__100"]["status"], "scheduled")
        self.assertEqual(native.call_args.kwargs["not_after"].date(), date(2026, 10, 15))
        self.assertEqual(native.call_args.args[1].hour, 16)
        self.assertEqual(self.saved[-1]["events"]["a__100"]["timing"]["activity_source_date"], "2026-10-07")
        # The callback, used under the native account lock, must reassess the
        # best slot if a concurrent run occupied the peak after the first plan.
        reselect = native.call_args.kwargs["select_slot"]
        now = datetime(2026, 10, 15, 17, 50, tzinfo=schedule.MADRID)
        chosen = reselect(now, [])
        self.assertGreater(chosen, now)
        self.assertEqual(chosen.date(), date(2026, 10, 15))

    def test_failure_is_schedule_unconfirmed_never_live_fallback(self):
        self.run_planner(success=False)
        self.assertEqual(self.saved[-1]["events"]["a__100"]["status"], "schedule_unconfirmed")
        self.assertTrue(self.saved[-1]["events"]["a__100"]["scheduling_error"])
        self.assertEqual(self.saved[-1]["events"]["a__100"]["assessment"], "robust_crossing")

    def test_retry_of_reserved_event_never_submits_again(self):
        self.ledger["events"]["a__100"] = {**self.event, "status": "uncertain"}
        native, render = self.run_planner()
        native.assert_not_called()
        render.assert_not_called()
        self.assertEqual(self.saved, [])

    def test_no_post_does_not_write_reservations(self):
        native, render = self.run_planner(no_post=True)
        native.assert_not_called()
        render.assert_called_once()
        self.assertEqual(self.saved, [])

    def test_force_does_not_duplicate_scheduled_milestone(self):
        posts = schedule.posts
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["post_stream_milestones.py", "2026-10-15", "--force", "--count-units"]))
            stack.enter_context(patch.object(posts.history_store.HistoryIndex, "load", return_value=object()))
            stack.enter_context(patch.object(posts, "_load_posted_keys", return_value=set()))
            stack.enter_context(patch.object(posts, "find_milestone_events", return_value=[self.event]))
            stack.enter_context(patch.object(posts, "find_album_milestone_events", return_value=[]))
            stack.enter_context(patch.object(m, "reserved_keys", return_value={"a__100"}))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(posts.main(), 0)
            self.assertIn('"units": 0', output.getvalue())

    def test_corrupt_ledger_blocks_instead_of_resetting(self):
        path = SimpleNamespace(exists=lambda: True, read_text=lambda **kwargs: '{"events": []}')
        with patch.object(m, "LEDGER_PATH", path), self.assertRaisesRegex(SystemExit, "invalid schedule ledger"):
            m.load_ledger()


class EngagementSlotTests(unittest.TestCase):
    def slot(self, day, *, hour=8, minute=0, busy=None):
        return schedule.choose_slot(day, datetime.combine(day, datetime.min.time(), schedule.MADRID).replace(hour=hour, minute=minute), busy or [])

    def test_uses_recorded_peak_every_weekday(self):
        for offset in range(7):
            day = date(2026, 10, 12) + timedelta(days=offset)
            chosen = self.slot(day)
            self.assertEqual((chosen.hour, chosen.minute), (16, 30))

    def test_saturday_uses_second_peak_after_first_passed(self):
        chosen = self.slot(date(2026, 10, 17), hour=18)
        self.assertEqual((chosen.hour, chosen.minute), (20, 30))

    def test_friday_uses_evening_activity_after_afternoon_passed(self):
        chosen = self.slot(date(2026, 10, 16), hour=19)
        self.assertEqual((chosen.hour, chosen.minute), (22, 30))

    def test_occupied_peak_minute_keeps_gap_and_high_activity(self):
        day = date(2026, 10, 16)
        busy = datetime(2026, 10, 16, 16, 30, tzinfo=schedule.MADRID)
        chosen = self.slot(day, busy=[busy.astimezone(timezone.utc)])
        self.assertEqual(chosen.hour, 16)
        self.assertGreaterEqual(abs((chosen - busy).total_seconds()), schedule.twitter_core.TWITTER_SCHEDULE_COLLISION_SECONDS)

    def test_entire_peak_occupied_reselects_next_best_window(self):
        day = date(2026, 10, 16)
        busy = [datetime(2026, 10, 16, 16, i, tzinfo=schedule.MADRID) for i in range(60)]
        chosen = self.slot(day, busy=busy)
        self.assertEqual((chosen.hour, chosen.minute), (17, 30))

    def test_multiple_milestones_get_distinct_spaced_slots(self):
        day = date(2026, 10, 16)
        first = self.slot(day)
        second = self.slot(day, busy=[first])
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(abs((second - first).total_seconds()), schedule.twitter_core.TWITTER_SCHEDULE_COLLISION_SECONDS)

    def test_end_of_day_never_moves_to_tomorrow(self):
        self.assertIsNone(self.slot(date(2026, 10, 16), hour=23, minute=58))

    def test_timezone_and_daylight_saving(self):
        day = date(2026, 10, 25)  # 25-hour audience day.
        now = datetime(2026, 10, 25, 6, tzinfo=timezone.utc)
        chosen = schedule.choose_slot(day, now, [])
        self.assertEqual((chosen.hour, chosen.minute), (16, 30))
        self.assertEqual(chosen.utcoffset(), timedelta(hours=1))

    def test_naive_current_time_is_rejected(self):
        with self.assertRaises(ValueError):
            schedule.choose_slot(date(2026, 10, 16), datetime(2026, 10, 16, 8), [])

    def test_missing_heatmap_blocks_instead_of_using_neutral_weight(self):
        with patch.dict(schedule.x_active_times.LEVELS, {4: []}), self.assertRaisesRegex(ValueError, "heatmap"):
            self.slot(date(2026, 10, 16))


if __name__ == "__main__":
    unittest.main()
