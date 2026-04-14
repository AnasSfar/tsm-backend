#!/usr/bin/env python3
"""
post_albums_twitter.py — génère et poste l'image "Albums on Spotify" sur Twitter.

Usage:
  python post_albums_twitter.py               # hier par défaut
  python post_albums_twitter.py 2026-04-13    # date spécifique
  python post_albums_twitter.py 2026-04-13 --no-post
"""
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR      = Path(__file__).resolve().parent          # streams/tools/scripts/
ROOT            = SCRIPT_DIR.parents[1]                    # streams/
TWITTER_SESSION = SCRIPT_DIR.parents[2] / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(SCRIPT_DIR.parents[2]))             # collectors/spotify/
from core.twitter import post_with_image

import generate_albums_image

TWITTER_MAX = 280


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"


def _fmt(n: int) -> str:
    """Format number with regular commas for tweet text."""
    return f"{n:,}"


def _pct(daily: int, yest: int) -> str:
    if not yest:
        return ""
    p = (daily - yest) / yest * 100
    return f"{p:+.1f}%"


def build_tweet(rows: list[dict], target_date: str) -> str:
    d = date.fromisoformat(target_date)
    weekday   = d.strftime("%A").lower()          # "monday"
    month     = d.strftime("%B")                  # "April"
    day_ord   = _ordinal(d.day)                   # "13th"
    year      = d.year

    # Most streamed = rows[0] (already sorted by daily_streams desc)
    top = rows[0]
    top_daily = top["daily_streams"]
    top_yest  = top["yest_daily"]

    # Biggest gainer = highest absolute daily gain
    gainer = max(rows, key=lambda r: r["daily_streams"] - r["yest_daily"])
    gain_delta = gainer["daily_streams"] - gainer["yest_daily"]
    gain_daily = gainer["daily_streams"]
    gain_yest  = gainer["yest_daily"]

    header = (
        f"📈 | Taylor Swift's albums on Spotify, yesterday, "
        f"{weekday} {month} {day_ord} {year}."
    )
    most_streamed = (
        f'"{top["album"]}" was the most streamed album with '
        f'{_fmt(top_daily)} streams [{_pct(top_daily, top_yest)}].'
    )
    biggest_gainer = (
        f'"{gainer["album"]}" was the biggest daily gainer, '
        f'up {_fmt(gain_delta)} to {_fmt(gain_daily)} streams [{_pct(gain_daily, gain_yest)}].'
    )

    full_tweet = f"{header}\n{most_streamed}\n{biggest_gainer}"
    if len(full_tweet) <= TWITTER_MAX:
        return full_tweet

    # Fallback: drop most-streamed line
    short_tweet = f"{header}\n{biggest_gainer}"
    return short_tweet


def main():
    no_post = "--no-post" in sys.argv
    args    = [a for a in sys.argv[1:] if not a.startswith("-")]
    target_date = args[0] if args else str(date.today() - timedelta(days=1))

    # Anti double-post lock
    d        = date.fromisoformat(target_date)
    day_dir  = ROOT / "history" / str(d.year) / f"{d.month:02d}" / target_date
    day_dir.mkdir(parents=True, exist_ok=True)
    lock     = day_dir / "albums_posted.lock"

    if lock.exists() and not no_post:
        print(f"Albums image already posted for {target_date}, skipping.")
        return

    if not no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    # Generate image
    print(f"[albums_post] Generating image for {target_date}...")
    image_path = generate_albums_image.generate(target_date)

    # Load data to build tweet text
    covers    = generate_albums_image.load_covers()
    track_map = generate_albums_image.load_album_track_map()
    today, yest = generate_albums_image.load_history(target_date)
    rows      = generate_albums_image.build_album_rows(today, yest, track_map, covers)

    tweet = build_tweet(rows, target_date)
    print(f"[albums_post] Tweet ({len(tweet)} chars):\n{tweet}")
    print(f"[albums_post] Image: {image_path}")

    if no_post:
        print("[albums_post] Twitter post skipped (--no-post).")
        return

    success = post_with_image(tweet, image_path, TWITTER_SESSION)
    if not success:
        print(f"[albums_post] Failed to post for {target_date}.")
        sys.exit(1)

    lock.touch()
    print(f"[albums_post] Posted successfully for {target_date}.")


if __name__ == "__main__":
    main()
