from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
APPLE_MUSIC_DIR = REPO_ROOT / "collectors" / "apple_music"

if str(APPLE_MUSIC_DIR) not in sys.path:
    sys.path.insert(0, str(APPLE_MUSIC_DIR))

from core.config import HEADERS, RETRY_STATUS_FORCELIST  # noqa: E402
from core.http import AppleMusicSession, build_session  # noqa: E402
from requests import Session  # noqa: E402


class TestAppleMusicHttp(unittest.TestCase):
    def test_request_uses_default_timeout(self) -> None:
        session = AppleMusicSession()
        session.default_timeout = 17

        with patch.object(Session, "request", autospec=True, return_value=object()) as request_mock:
            session.request("GET", "https://example.com")

        _, kwargs = request_mock.call_args
        self.assertEqual(kwargs["timeout"], 17)

    def test_request_keeps_explicit_timeout(self) -> None:
        session = AppleMusicSession()
        session.default_timeout = 17

        with patch.object(Session, "request", autospec=True, return_value=object()) as request_mock:
            session.request("GET", "https://example.com", timeout=3)

        _, kwargs = request_mock.call_args
        self.assertEqual(kwargs["timeout"], 3)

    def test_build_session_configures_retries_headers_and_timeout(self) -> None:
        session = build_session(retry_total=6, retry_backoff=0.25, timeout=13)

        self.assertEqual(session.default_timeout, 13)
        self.assertEqual(session.headers.get("User-Agent"), HEADERS["User-Agent"])

        https_adapter = session.adapters["https://"]
        retries = https_adapter.max_retries

        self.assertEqual(retries.total, 6)
        self.assertEqual(retries.read, 6)
        self.assertEqual(retries.connect, 6)
        self.assertEqual(retries.backoff_factor, 0.25)
        self.assertEqual(tuple(retries.status_forcelist), RETRY_STATUS_FORCELIST)


if __name__ == "__main__":
    unittest.main()
