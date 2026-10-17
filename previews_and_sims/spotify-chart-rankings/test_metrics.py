"""Regression checks using real local rows only; never write test data to source state."""
from collections import defaultdict
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "collectors/spotify/charts/full_charts"))
import generate_ranking_images as generator
import ranking_metrics as metrics
from common import TAYLOR_ARTIST_ID, read_rows, raw_dates, read_json, ARTIST_GENDERS_PATH
from stats import Blocked


class ExactRankingMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dates = raw_dates("global", "2026")
        if len(dates) < 2:
            raise unittest.SkipTest("Requires real local 2026 Global chart snapshots")
        cls.day, cls.previous = dates[-1], dates[-2]
        cls.rows = generator.verify_source("global", "2026", cls.day)
        cls.pools = {"songs": metrics.song_metrics(cls.rows, "2026"), "albums": metrics.album_metrics(cls.rows, "2026")}

    def build(self, kind, scope="taylor"):
        return metrics.build_rankings(self.rows, region="global", year="2026", as_of=self.day,
                                      compare_to=self.previous, kind=kind, scope=scope, pools=self.pools)

    def test_taylor_scope_has_no_top50_cutoff(self):
        expected = {key for key,item in self.pools["songs"].items() if TAYLOR_ARTIST_ID in item["artist_ids"]}
        result = self.build("songs")
        self.assertEqual({s["key"] for s in result["entries"]}, expected)
        self.assertTrue(any(s["rank"] > 50 for s in result["entries"]))

    def test_song_metrics_use_only_year_rows(self):
        for item in self.pools["songs"].values():
            value = metrics.summarize(item, self.day)
            rows = item["rows"]
            self.assertEqual(value["days_2026"], len({r["date"] for r in rows}))
            self.assertEqual(value["streams"], sum(r["streams"] for r in rows))
            self.assertEqual(value["peak_rank_2026"], min(r["rank"] for r in rows))
            self.assertEqual(value["peak_streams_2026"], max(r["streams"] for r in rows))
            self.assertEqual(value["still_charting"], any(r["date"] == self.day for r in rows))
            if value["debut_date"]:
                debut = next(r for r in rows if r["date"] == value["debut_date"])
                self.assertEqual(str(debut["days_on_chart"]), "1")
                self.assertEqual(value["debut_streams"], debut["streams"])
            else:
                self.assertIsNone(value["debut_streams"])

    def test_overalls_keep_top15_and_original_ranks(self):
        for kind in metrics.KINDS:
            result = self.build(kind, "all")
            if result.get("skipped"):
                continue
            self.assertTrue(any(s["is_taylor"] for s in result["entries"]))
            self.assertTrue(all(s["rank"] <= 15 for s in result["entries"]))
            all_taylor = self.build(kind)["entries"]
            expected = {s["key"]:s["rank"] for s in result["entries"] if s["is_taylor"]}
            self.assertEqual({s["key"]:s["rank"] for s in all_taylor if s["key"] in expected}, expected)

    def test_debuts_rank_first_day_streams_not_year_totals(self):
        result = self.build("female_debuts", "all")
        values = [s["debut_streams"] for s in result["entries"]]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertTrue(all(s["debut_date"].startswith("2026-") for s in result["entries"]))

    def test_albums_never_invent_official_chart_positions(self):
        for item in self.pools["albums"].values():
            value = metrics.summarize(item, self.day, album=True)
            self.assertIsNone(value["peak_rank_2026"])
            self.assertIsNone(value["debut_rank"])
            by_day = defaultdict(int)
            for row in item["rows"]:
                by_day[row["date"]] += row["streams"]
            self.assertEqual(value["peak_streams_2026"], max(by_day.values()))
            self.assertEqual(value["days_2026"], len(by_day))

    def test_unknown_that_affects_a_displayed_rank_blocks(self):
        genders = read_json(ARTIST_GENDERS_PATH, {})
        genders.pop(TAYLOR_ARTIST_ID)
        actual_read = metrics.read_json
        def missing_taylor(path, default):
            return genders if path == ARTIST_GENDERS_PATH else actual_read(path, default)
        with patch.object(metrics, "read_json", side_effect=missing_taylor):
            with self.assertRaises(Blocked):
                self.build("female", "all")

    def test_new_debut_is_new_and_pushes_others_down(self):
        # In memory only: a song debuting the next chart day above every 2026 debut.
        result = self.build("debuts", "all")
        top = result["entries"][0]
        nxt = f"{self.day[:8]}{int(self.day[8:]) + 1:02d}"
        new_row = {**self.rows[0], "date": nxt, "rank": 1, "previous_rank": "-1", "peak_rank": "1",
                   "peak_date": nxt, "entry_rank": "1", "entry_date": nxt, "days_on_chart": "1", "streak": "1",
                   "streams": top["debut_streams"] + 1, "entry_status": "NEW_ENTRY", "track_id": "SIMULATEDDEBUT",
                   "track_name": "Simulated debut", "artists": "Sim", "artist_ids": "SIMARTIST"}
        rows = self.rows + [new_row]
        after = metrics.build_rankings(rows, region="global", year="2026", as_of=nxt, compare_to=self.day,
                                       kind="debuts", scope="all", pools={"songs": metrics.song_metrics(rows, "2026")})
        first, second = after["entries"][:2]
        self.assertEqual((first["key"], first["rank"], first["previous_rank"]), ("SIMULATEDDEBUT", 1, None))
        self.assertEqual((second["key"], second["movement"]), (top["key"], -1))

    def test_female_debut_record_held_event(self):
        # In memory only: a female solo artist (Olivia Rodrigo's real ID) debuts at #3 the next day
        # below Taylor's record -> one female_debut_record event with exact numbers.
        import post_ranking_events as events
        from datetime import date, timedelta
        olivia = next(r for r in self.rows if r["artists"] == "Olivia Rodrigo")
        nxt = (date.fromisoformat(self.day) + timedelta(days=1)).isoformat()
        new_row = {**olivia, "date": nxt, "rank": 3, "previous_rank": "-1", "peak_rank": "3", "peak_date": nxt,
                   "entry_rank": "3", "entry_date": nxt, "days_on_chart": "1", "streak": "1", "streams": 5_600_000,
                   "entry_status": "NEW_ENTRY", "track_id": "SIMULATEDFEMALE", "track_name": "Simulated"}
        ctx = events.Context.__new__(events.Context)
        ctx.region, ctx.year, ctx.as_of, ctx.previous = "global", "2026", nxt, self.day
        ctx.rows = self.rows + [new_row]
        ctx.pools = {"songs": metrics.song_metrics(ctx.rows, "2026")}
        ctx.albums = {}
        found = events.female_debut_record(ctx, date.fromisoformat(nxt) + timedelta(days=1), 10)
        self.assertEqual(len(found), 1)
        text, (kind, scope, result, spot) = found[0]["posts"][0]
        self.assertEqual((kind, scope, spot), ("female_debuts", "all", ["SIMULATEDFEMALE"]))
        self.assertIn("is still the biggest female debut of 2026", text)
        self.assertIn('Olivia Rodrigo\'s "Simulated" debuted at #3 with 5,600,000 streams yesterday', text)
        self.assertIn("SIMULATEDFEMALE", {e["key"] for e in result["entries"]})

    def test_missing_or_duplicate_csv_row_blocks(self):
        full = read_rows("global", "2026")
        for modified in (full[1:], full + [full[0]]):
            with patch.object(generator, "read_rows", return_value=modified):
                with self.assertRaisesRegex(Blocked, "CSV differs"):
                    generator.verify_source("global", "2026", self.day)


if __name__ == "__main__":
    unittest.main()
