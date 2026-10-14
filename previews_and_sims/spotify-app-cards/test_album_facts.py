"""Selection and exact-data regression checks; no posting or data writes."""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "collectors/spotify/streams/tools/scripts"))
import album_page_facts as f
import post_priority


class FactsTests(unittest.TestCase):
    def test_complementary_selection_and_no_padding(self):
        milestone = f.Fact("new", 80, "", "", "milestone", "a")
        redundant = f.Fact("gain", 70, "", "", "growth", "a")
        other = f.Fact("gain", 60, "", "", "growth", "b")
        filler = f.Fact("club", 30, "", "", event=False)
        self.assertEqual(f.select_facts([filler, other, redundant, milestone]), [milestone, other])
        self.assertEqual(f.select_facts([milestone, filler]), [milestone])
        self.assertEqual(f.select_facts([filler]), [filler])
        self.assertEqual(f.select_facts([f.Fact("weak", 20, "", "")]), [])

    def test_milestone_score_and_exact_remaining_without_daily(self):
        tracks = [{"track_id": "a", "title": "A"}]
        crossed = f.f_milestones(tracks, {"a": {"streams": 200_000_001, "daily": 2}})[0]
        self.assertEqual(crossed.priority, post_priority.song_milestone_importance(200_000_000, None))
        near = f.f_milestones(tracks, {"a": {"streams": 199_967_298}})[0]
        self.assertIn("32,702 streams", near.text)
        self.assertNotIn("day", near.text)

    def test_top10_entry_requires_exact_previous_chart(self):
        tracks = [{"track_id": "a", "title": "A"}]
        with patch.object(f, "global_chart", side_effect=[{"a": 8}, {"a": 11}]):
            self.assertEqual(f.f_chart(tracks, "2026-10-06")[0].priority, 85)
        with patch.object(f, "global_chart", side_effect=[{"a": 8}, {}]):
            self.assertFalse(f.f_chart(tracks, "2026-10-06")[0].event)

    def test_gain_requires_complete_matching_history(self):
        target = date(2026, 10, 6)
        tracks = [{"track_id": "a", "title": "A"}, {"track_id": "b", "title": "B"}]
        hist = {"a": {"daily": 27_163, "change": 1_987}, "b": {"daily": 100, "change": -1}}
        points = [SimpleNamespace(day=target - timedelta(days=i), daily=25_176 if i else 27_163) for i in range(31)]
        with patch.object(f, "history", return_value={"a": points}):
            self.assertEqual(len(f.f_songs_up(tracks, hist, target)), 1)
        with patch.object(f, "history", return_value={"a": points[:-1]}):
            self.assertEqual(f.f_songs_up(tracks, hist, target), [])


if __name__ == "__main__":
    unittest.main()
