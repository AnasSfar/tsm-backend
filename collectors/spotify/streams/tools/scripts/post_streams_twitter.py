#!/usr/bin/env python3
"""
post_streams_twitter.py — génère et poste l'image des top streams daily sur Twitter.

Usage:
  python post_streams_twitter.py               # stats_date (hier par défaut)
  python post_streams_twitter.py 2026-03-15    # date spécifique
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR      = Path(__file__).resolve().parent          # streams/tools/scripts/
_TOOLS          = SCRIPT_DIR.parent                        # streams/tools/
ROOT            = SCRIPT_DIR.parents[1]                    # streams/
REPO_ROOT       = SCRIPT_DIR.parents[4]                    # repo root
TWITTER_SESSION = SCRIPT_DIR.parents[2] / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(SCRIPT_DIR.parents[2]))             # collectors/spotify/
from core.twitter import post_with_image

import generate_streams_image
import generate_albums_image


def main():
    no_post = "--no-post" in sys.argv[1:]
    args = [a for a in sys.argv[1:] if a != "--no-post"]
    target_date = args[0] if args else str(date.today() - timedelta(days=1))

    # Guard against double-posting
    d = date.fromisoformat(target_date)
    day_dir     = ROOT / "history" / str(d.year) / f"{d.month:02d}" / target_date
    day_dir.mkdir(parents=True, exist_ok=True)
    posted_lock = day_dir / "posted.lock"

    if posted_lock.exists():
        print(f"Already posted for {target_date}, skipping.")
        return

    if not no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    # Generate images
    print(f"Generating streams image for {target_date}...")
    image_path = generate_streams_image.generate(target_date)

    print(f"Generating albums image for {target_date}...")
    try:
        generate_albums_image.generate(target_date)
    except Exception as e:
        print(f"Warning: albums image generation failed: {e}")

    # Build tweet text
    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    tweet    = f"Taylor Swift's most streamed songs yesterday ({date_fmt}) :"

    print(f"Tweet: {tweet}")
    print(f"Image: {image_path}")

    if no_post:
        print("Twitter post skipped (--no-post).")
        success = True
    else:
        success = post_with_image(tweet, image_path, TWITTER_SESSION)

    if success:
        posted_lock.touch()
        print(f"Posted successfully for {target_date}.")
    else:
        print(f"Failed to post for {target_date}.")
        sys.exit(1)


if __name__ == "__main__":
    main()
