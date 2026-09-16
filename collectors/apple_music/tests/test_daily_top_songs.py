from __future__ import annotations

import json
import unittest

from collectors.apple_music.daily_top_songs import IncompleteDailyChart, aggregate_daily_chart


def row(hour, song, isrc, score, rank, us_rank):
    return {
        "date": "2026-09-16",
        "scraped_at": f"2026-09-16T{hour:02d}:00:00+02:00",
        "song_name": song,
        "apple_music_id": f"id-{isrc}",
        "isrc": isrc,
        "rank": rank,
        "composite_score": score,
        "storefront_ranks": json.dumps({"us": {"rank": us_rank}}),
    }


class TestDailyTopSongs(unittest.TestCase):
    def test_aggregates_scores_and_compares_final_daily_ranks(self):
        snapshots = [
            row(0, "Alpha", "AAA", 100, 1, 1),
            row(0, "Beta", "BBB", 80, 2, 2),
            row(2, "Alpha", "AAA", 20, 2, 2),
            row(2, "Beta", "BBB", 90, 1, 1),
        ]
        previous = [
            {
                "song_name": "Alpha",
                "apple_music_id": "old-a",
                "isrc": "AAA",
                "rank": 2,
                "storefront_ranks": json.dumps({"us": {"rank": 2}}),
            },
            {
                "song_name": "Beta",
                "apple_music_id": "old-b",
                "isrc": "BBB",
                "rank": 1,
                "storefront_ranks": json.dumps({"us": {"rank": 1}}),
            },
        ]

        daily = aggregate_daily_chart(
            snapshots,
            previous,
            target_day="2026-09-16",
            expected_hours=[0, 2],
            important_storefronts=["us"],
        )

        self.assertEqual([item["song_name"] for item in daily], ["Beta", "Alpha"])
        self.assertEqual([item["previous_rank"] for item in daily], [1, 2])
        self.assertEqual([item["snapshot_count"] for item in daily], [2, 2])
        self.assertEqual(
            json.loads(daily[0]["storefront_ranks"])["us"],
            {"rank": 1, "previous_rank": 1},
        )

    def test_absent_song_contributes_zero_for_that_snapshot(self):
        snapshots = [
            row(0, "Alpha", "AAA", 100, 1, 1),
            row(0, "Beta", "BBB", 60, 2, 2),
            row(2, "Beta", "BBB", 60, 1, 1),
        ]

        daily = aggregate_daily_chart(
            snapshots,
            [],
            target_day="2026-09-16",
            expected_hours=[0, 2],
            important_storefronts=["us"],
        )

        self.assertEqual([item["song_name"] for item in daily], ["Beta", "Alpha"])
        self.assertEqual(float(daily[1]["composite_score"]), 50.0)

    def test_blocks_when_an_expected_snapshot_is_missing(self):
        with self.assertRaisesRegex(IncompleteDailyChart, "02:00"):
            aggregate_daily_chart(
                [row(0, "Alpha", "AAA", 100, 1, 1)],
                [],
                target_day="2026-09-16",
                expected_hours=[0, 2],
                important_storefronts=["us"],
            )

    def test_blocks_when_composite_score_is_missing(self):
        broken = row(0, "Alpha", "AAA", 100, 1, 1)
        broken["composite_score"] = ""
        with self.assertRaisesRegex(IncompleteDailyChart, "composite_score"):
            aggregate_daily_chart(
                [broken],
                [],
                target_day="2026-09-16",
                expected_hours=[0],
                important_storefronts=["us"],
            )


if __name__ == "__main__":
    unittest.main()
