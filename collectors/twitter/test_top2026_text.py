import unittest
from .text import compact_streams, top2026_taylor_ranks_tweet


class Top2026TextTests(unittest.TestCase):
    def test_rounding_units_and_boundaries(self):
        for counter, expected in [(214541027, '214.5M'), (1250000, '1.3M'),
                                  (1200000, '1.2M'), (1000000000, '1B'),
                                  (100000, '100k'), (999949, '999.9k'),
                                  (999950, '1M'), (999950000, '1B'), (0, '0'), (999, '999')]:
            with self.subTest(counter=counter):
                self.assertEqual(compact_streams(counter), expected)

    def test_signed_moves_glued_separator_and_raw_values_preserved(self):
        rows=[dict(rank=49,move=2,title='Song',streams=214541027,daily_streams=1200000),
              dict(rank=155,move=-1,title='Other',streams=1000000000,daily_streams=100000),
              dict(rank=259,move=0,title='Third',streams=42209539,daily_streams=0)]
        self.assertEqual(top2026_taylor_ranks_tweet(rows),
                         "Most streamed songs of 2026 (Taylor's Version)* :\n"
                         '*only songs that charted at least one day on the global spotify charts.\n\n'
                         '#49 (+2) Song--214.5M streams (+1.2M)\n'
                         '#155 (-1) Other--1B streams (+100k)\n'
                         '#259 (+0) Third--42.2M streams (+0)')
        self.assertEqual(rows[0]['streams'],214541027)

    def test_unavailable_stats_are_not_formatted_as_zero(self):
        for field in ['move','daily_streams']:
            row=dict(rank=1,move=0,title='Song',streams=100,daily_streams=0)
            row[field]=None
            with self.assertRaises(ValueError):top2026_taylor_ranks_tweet([row])
        with self.assertRaises(ValueError):compact_streams(-1)

    def test_female_scope_is_named_and_unknown_scope_blocks(self):
        self.assertIn("(Taylor's Version) [Female]*", top2026_taylor_ranks_tweet([], scope='female'))
        with self.assertRaises(ValueError):top2026_taylor_ranks_tweet([], scope='unknown')


if __name__ == '__main__':
    unittest.main()
