import unittest
from unittest.mock import patch
import notify_song_ranks as ranks


class NotificationBridgeTests(unittest.TestCase):
    def test_delegates_only_to_top2026(self):
        with patch.object(ranks.subprocess,'run') as run:
            ranks.notify_for_date('2026-10-12',dry_run=True)
        cmd=run.call_args.args[0]
        self.assertEqual(cmd[-2:],['2026-10-12','--dry-run'])
        self.assertTrue(cmd[1].endswith('notify_top2026_taylor.py'))


if __name__=='__main__':unittest.main()
