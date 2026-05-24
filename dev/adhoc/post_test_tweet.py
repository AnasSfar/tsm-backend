#!/usr/bin/env python3
"""Post a tiny X/Twitter smoke-test tweet.

Usage:
    python dev/adhoc/post_test_tweet.py --yes
    python dev/adhoc/post_test_tweet.py --text "test" --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "collectors" / "spotify" / "core"
DEFAULT_SESSION = (
    REPO_ROOT
    / "collectors"
    / "spotify"
    / "charts"
    / "fr"
    / "tools"
    / "json"
    / "twitter_session.json"
)

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from twitter import post_thread  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Post a test tweet to the configured X/Twitter account.")
    parser.add_argument("--text", default="test", help="Text to post. Default: test")
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION, help="Path to twitter_session.json")
    parser.add_argument("--yes", action="store_true", help="Actually post the tweet.")
    args = parser.parse_args()

    text = str(args.text or "").strip()
    if not text:
        print("Refusing to post an empty tweet.")
        return 2

    session = args.session.resolve()
    print(f"Tweet text: {text!r}")
    print(f"Session: {session}")

    if not session.exists():
        print(f"Twitter session not found: {session}")
        return 1

    if not args.yes:
        print("Dry run only. Re-run with --yes to post.")
        return 0

    return 0 if post_thread([text], session) else 1


if __name__ == "__main__":
    raise SystemExit(main())
