#!/usr/bin/env python3
"""
post_streams_twitter.py — génère et poste l'image des top streams daily sur Twitter.

Usage:
  python post_streams_twitter.py               # stats_date (hier par défaut)
  python post_streams_twitter.py 2026-03-15    # date spécifique
    python post_streams_twitter.py 2026-03-15 --top-n 10
    python post_streams_twitter.py 2026-03-15 --no-post --top-n 10
"""
import sys
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR      = Path(__file__).resolve().parent          # streams/tools/scripts/
_TOOLS          = SCRIPT_DIR.parent                        # streams/tools/
ROOT            = SCRIPT_DIR.parents[1]                    # streams/
REPO_ROOT       = SCRIPT_DIR.parents[4]                    # repo root
TWITTER_SESSION = SCRIPT_DIR.parents[2] / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(SCRIPT_DIR.parents[2]))             # collectors/spotify/
from core.twitter import post_image_thread
from core.data_paths import update_streams_dir

import generate_streams_image


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("date", nargs="?", help="Stats date (YYYY-MM-DD). Defaults to yesterday.")
    parser.add_argument("--no-post", action="store_true", help="Generate images but skip Twitter post.")
    parser.add_argument(
        "--top-n",
        type=int,
        default=15,
        help="Number of tracks per thread image (default: 15).",
    )
    ns = parser.parse_args()

    no_post = bool(ns.no_post)
    target_date = ns.date or str(date.today() - timedelta(days=1))
    top_n = int(ns.top_n)
    if top_n <= 0:
        print("ERROR: --top-n must be > 0")
        sys.exit(2)

    # Guard against double-posting
    d = date.fromisoformat(target_date)
    day_dir = update_streams_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    posted_lock = day_dir / "posted.lock"

    if posted_lock.exists() and not no_post:
        print(f"Already posted for {target_date}, skipping.")
        return
    if posted_lock.exists() and no_post:
        print(f"Already posted for {target_date}, regenerating images only (--no-post).")

    if not no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    # Generate images
    print(f"Generating streams thread images for {target_date}...")
    image_paths = [
        generate_streams_image.generate(target_date, top_n=top_n, start_rank=1),
        generate_streams_image.generate(target_date, top_n=top_n, start_rank=top_n + 1),
        generate_streams_image.generate(target_date, top_n=top_n, start_rank=top_n * 2 + 1),
    ]

    # Build tweet text
    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    tweet = (
            f"Taylor Swift's most streamed songs yesterday ({date_fmt}) :\n\n"
            f"See full update here : https://thetsmuseum.app/streams/latest ❤️‍🔥"
        )

    thread_posts = [
        (tweet, image_paths[0]),
        (f"Taylor Swift's most streamed songs yesterday ({date_fmt}) — #{top_n + 1}-{top_n * 2} :", image_paths[1]),
        (f"Taylor Swift's most streamed songs yesterday ({date_fmt}) — #{top_n * 2 + 1}-{top_n * 3} :", image_paths[2]),
    ]

    print(f"Tweet: {tweet}")
    for image_path in image_paths:
        print(f"Image: {image_path}")

    if no_post:
        print("Twitter post skipped (--no-post).")
        return

    success = post_image_thread(thread_posts, TWITTER_SESSION)

    if not success:
        print(f"Failed to post for {target_date}.")
        sys.exit(1)

    posted_lock.touch()
    print(f"Posted successfully for {target_date}.")


if __name__ == "__main__":
    main()
