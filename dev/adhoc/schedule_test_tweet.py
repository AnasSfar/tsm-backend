#!/usr/bin/env python3
"""Schedule a single X/Twitter post through the native X scheduler.

Usage:
    python dev/adhoc/schedule_test_tweet.py --text "test" --at "2026-06-28 18:00" --yes
    python dev/adhoc/schedule_test_tweet.py --text "test" --image path/to/card.png --at "2026-06-28T18:00:00" --yes
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
    / "worldwide"
    / "tools"
    / "json"
    / "twitter_session.json"
)

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from twitter import schedule_post  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Schedule a single X/Twitter post.")
    parser.add_argument("--text", default="test", help="Post text.")
    parser.add_argument("--image", type=Path, help="Optional image path.")
    parser.add_argument("--at", required=True, help="Local schedule datetime: ISO or 'YYYY-MM-DD HH:MM'.")
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION, help="Path to twitter_session.json")
    parser.add_argument("--yes", action="store_true", help="Actually schedule the post.")
    args = parser.parse_args()

    text = str(args.text or "").strip()
    image = args.image.resolve() if args.image else None
    session = args.session.resolve()

    print(f"Text: {text!r}")
    print(f"Image: {image or '(none)'}")
    print(f"At: {args.at}")
    print(f"Session: {session}")

    if not text and image is None:
        print("Refusing to schedule an empty post.")
        return 2
    if not session.exists():
        print(f"Twitter session not found: {session}")
        return 1
    if image is not None and not image.exists():
        print(f"Image not found: {image}")
        return 1

    if not args.yes:
        print("Dry run only. Re-run with --yes to schedule in X.")
        return 0

    return 0 if schedule_post(text, args.at, session, image) else 1


if __name__ == "__main__":
    raise SystemExit(main())
