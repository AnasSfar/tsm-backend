#!/usr/bin/env python3
"""
post_streams_twitter.py — génère et poste l'image des top streams daily sur Twitter.

Usage:
  python post_streams_twitter.py               # stats_date (hier par défaut)
  python post_streams_twitter.py 2026-03-15    # date spécifique
    python post_streams_twitter.py 2026-03-15 --top-n 20
    python post_streams_twitter.py 2026-03-15 --no-post --top-n 20
"""
import sys
import argparse
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR      = Path(__file__).resolve().parent          # streams/tools/scripts/
_TOOLS          = SCRIPT_DIR.parent                        # streams/tools/
ROOT            = SCRIPT_DIR.parents[1]                    # streams/
REPO_ROOT       = SCRIPT_DIR.parents[4]                    # repo root
TWITTER_SESSION = SCRIPT_DIR.parents[2] / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(REPO_ROOT / 'collectors'))             # collectors/
sys.path.insert(0, str(SCRIPT_DIR.parents[2]))             # collectors/spotify/
from core.twitter import post_image_thread
from twitter.text import streams_update_tweet
from core.data_paths import update_streams_dir

import generate_streams_image
import post_best_day_since_twitter as bds_post
from post_locks import mark_posted, should_skip_post



def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("date", nargs="?", help="Stats date (YYYY-MM-DD). Defaults to yesterday.")
    parser.add_argument("--no-post", action="store_true", help="Generate images but skip Twitter post.")
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Number of tracks in the single top songs image (default: 20).",
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

    if should_skip_post(
        posted_lock,
        target_date=target_date,
        label="Streams image",
        no_post=no_post,
    ):
        return

    if not no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    # Generate images
    print(f"Generating top {top_n} streams image for {target_date}...")
    image_paths = generate_streams_image.generate_thread_images(
        target_date,
        top_n=top_n,
        pages=1,
    )

    # Build tweet text
    tweet = streams_update_tweet(top_n=top_n, stats_date=target_date)
    thread_posts = [
        (tweet, image_paths[0]),
    ]

    print(f"Tweet: {tweet}")
    for image_path in image_paths:
        print(f"Image: {image_path}")

    # Best-day-since recap + per-era recap cards ride as replies in this same
    # thread (decision 2026-09-04), instead of posting standalone later in
    # finalize. A failure building them must never block the Top Songs post
    # itself; finalize's "best-day-since recap (fallback)" step (--limit 0)
    # covers the standalone case if this thread post fails outright.
    try:
        recap_posts, era_groups, has_global_recap = bds_post.build_recap_thread_posts(target_date)
    except Exception as exc:
        print(f"Best-day-since recap unavailable, posting Top Songs alone: {exc}")
        recap_posts, era_groups, has_global_recap = [], [], False
    thread_posts += recap_posts
    for tweet_text, images in recap_posts:
        print(f"Recap thread post ({len(images)} image(s)): {tweet_text}")

    if no_post:
        print("Twitter post skipped (--no-post).")
        return

    success = post_image_thread(thread_posts, TWITTER_SESSION)

    if not success:
        print(f"Failed to post for {target_date}.")
        sys.exit(1)

    mark_posted(posted_lock)
    if era_groups or has_global_recap:
        bds_post.mark_recap_thread_posted(target_date, era_groups, has_global_recap)
    print(f"Posted successfully for {target_date}.")


if __name__ == "__main__":
    main()
