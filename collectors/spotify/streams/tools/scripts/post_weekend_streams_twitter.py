#!/usr/bin/env python3
"""
Generate and post the weekend one-card Spotify streams update.

Usage:
  python post_weekend_streams_twitter.py
  python post_weekend_streams_twitter.py 2026-05-23
  python post_weekend_streams_twitter.py 2026-05-23 --no-post
  python post_weekend_streams_twitter.py 2026-05-23 --force-weekday
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
TWITTER_SESSION = SCRIPT_DIR.parents[2] / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(SCRIPT_DIR.parents[2]))
from core.twitter import post_with_image  # noqa: E402

import generate_weekend_streams_image


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"


def build_tweet(target_date: str) -> str:
    d = date.fromisoformat(target_date)
    return (
        "Taylor Swift's Spotify streams update for "
        f"{d.strftime('%A')}, {d.strftime('%B')} {_ordinal(d.day)}, {d.year}.\n\n"
        "Top 5 albums + top 5 songs in one card.\n"
        "See full update here : https://thetsmuseum.app/streams/latest"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", help="Stats date (YYYY-MM-DD). Defaults to yesterday.")
    parser.add_argument("--no-post", action="store_true", help="Generate image but skip Twitter post.")
    parser.add_argument("--force-weekday", action="store_true", help="Allow posting for a non-weekend stats date.")
    ns = parser.parse_args()

    target_date = ns.date or str(date.today() - timedelta(days=1))
    d = date.fromisoformat(target_date)
    is_weekend = d.weekday() in (5, 6)

    if not is_weekend and not ns.force_weekday:
        print(f"{target_date} is not Saturday or Sunday; skipping weekend streams post.")
        return

    day_dir = ROOT / "history" / str(d.year) / f"{d.month:02d}" / target_date
    day_dir.mkdir(parents=True, exist_ok=True)
    lock = day_dir / "weekend_streams_posted.lock"

    if lock.exists() and not ns.no_post:
        print(f"Weekend streams image already posted for {target_date}, skipping.")
        return
    if lock.exists() and ns.no_post:
        print(f"Weekend streams image already posted for {target_date}, regenerating only (--no-post).")

    if not ns.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    print(f"[weekend_streams_post] Generating image for {target_date}...")
    image_path = generate_weekend_streams_image.generate(target_date)
    tweet = build_tweet(target_date)

    print(f"[weekend_streams_post] Tweet ({len(tweet)} chars):\n{tweet}")
    print(f"[weekend_streams_post] Image: {image_path}")

    if ns.no_post:
        print("[weekend_streams_post] Twitter post skipped (--no-post).")
        return

    success = post_with_image(tweet, image_path, TWITTER_SESSION)
    if not success:
        print(f"[weekend_streams_post] Failed to post for {target_date}.")
        sys.exit(1)

    lock.touch()
    print(f"[weekend_streams_post] Posted successfully for {target_date}.")


if __name__ == "__main__":
    main()
