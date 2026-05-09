#!/usr/bin/env python3
"""
post_albums_twitter.py - generate and post the "Albums on Spotify" image.

Usage:
  python post_albums_twitter.py
  python post_albums_twitter.py 2026-04-13
  python post_albums_twitter.py 2026-04-13 --no-post
"""
import sys
import csv
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # streams/tools/scripts/
ROOT = SCRIPT_DIR.parents[1]                          # streams/
TWITTER_SESSION = SCRIPT_DIR.parents[2] / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(SCRIPT_DIR.parents[2]))        # collectors/spotify/
sys.path.insert(0, str(ROOT))                         # collectors/spotify/streams/
from core.twitter import post_with_image

import generate_albums_image

TWITTER_MAX = 280


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"


def _fmt(n: int) -> str:
    return f"{n:,}"


def _pct(daily: int, yest: int) -> str:
    if not yest:
        return ""
    p = (daily - yest) / yest * 100
    return f"{p:+.1f}%"


def _short_album(name: str, *, limit: int = 42) -> str:
    name = (name or "").strip()
    if len(name) <= limit:
        return name
    return name[: limit - 1].rstrip() + "..."


def _history_dates() -> list[str]:
    dates = set()
    with open(generate_albums_image.HISTORY_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = (row.get("date") or "").strip()
            if d:
                dates.add(d)
    return sorted(dates)


def _album_daily_for_date(album: str, target_date: str, track_map: dict, covers: dict) -> int | None:
    try:
        today, yest, week = generate_albums_image.load_history(target_date)
        rows = generate_albums_image.build_album_rows(today, yest, week, track_map, covers)
    except Exception:
        return None
    row = next((r for r in rows if r.get("album") == album), None)
    if not row:
        return None
    return int(row.get("daily_streams") or 0)


def _album_best_day_label(album: str, target_date: str, current_daily: int, track_map: dict, covers: dict, *, min_days: int = 14) -> str:
    if current_daily <= 0:
        return ""

    target = date.fromisoformat(target_date)
    previous_dates = [d for d in _history_dates() if d < target_date]
    if not previous_dates:
        return ""

    first_available = None
    last_at_or_above = None
    for d in previous_dates:
        daily = _album_daily_for_date(album, d, track_map, covers)
        if daily is None:
            continue
        point_date = date.fromisoformat(d)
        if first_available is None:
            first_available = point_date
        if daily >= current_daily:
            last_at_or_above = point_date

    if last_at_or_above is None:
        if first_available and first_available > date(2025, 1, 1):
            return "best day ever"
        return "best day since before 2025"

    best_since = last_at_or_above + timedelta(days=1)
    if best_since >= target:
        return ""
    days_since = (target - best_since).days + 1
    if days_since < min_days:
        return ""
    return f"best day since {best_since.strftime('%B')} {_ordinal(best_since.day)}, {best_since.year}"


def build_tweet(rows: list[dict], target_date: str) -> str:
    d = date.fromisoformat(target_date)
    weekday = d.strftime("%A")
    month = d.strftime("%B")
    day_ord = _ordinal(d.day)
    year = d.year

    return (
        f"📊 | Taylor Swift's albums on Spotify yesterday, "
        f"{weekday}, {month} {day_ord}, {year}.\n\n"
        "See the combined version here :\n"
        "🔗 : https://thetsmuseum.app/albums/date/latest"
    )


def build_tweet_with_best_day(rows: list[dict], target_date: str) -> str:
    tweet = build_tweet(rows, target_date)

    biggest_gain = None
    for row in rows:
        gain = int(row.get("daily_streams") or 0) - int(row.get("yest_daily") or 0)
        if gain <= 0:
            continue
        if biggest_gain is None or gain > biggest_gain[0]:
            biggest_gain = (gain, row)

    if not biggest_gain:
        return tweet

    _, row = biggest_gain
    covers = generate_albums_image.load_covers()
    track_map = generate_albums_image.load_album_track_map()
    label = _album_best_day_label(
        row["album"],
        target_date,
        int(row.get("daily_streams") or 0),
        track_map,
        covers,
    )
    if not label:
        return tweet

    return f'{tweet}\n\n"{_short_album(row["album"])}" was the biggest gainer and earned its {label}.'


def main():
    no_post = "--no-post" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    target_date = args[0] if args else str(date.today() - timedelta(days=1))

    d = date.fromisoformat(target_date)
    day_dir = ROOT / "history" / str(d.year) / f"{d.month:02d}" / target_date
    day_dir.mkdir(parents=True, exist_ok=True)
    lock = day_dir / "albums_posted.lock"

    if lock.exists() and not no_post:
        print(f"Albums image already posted for {target_date}, skipping.")
        return

    if not no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    print(f"[albums_post] Generating image for {target_date}...")
    image_path = generate_albums_image.generate(target_date)

    covers = generate_albums_image.load_covers()
    track_map = generate_albums_image.load_album_track_map()
    today, yest, week = generate_albums_image.load_history(target_date)
    rows = generate_albums_image.build_album_rows(today, yest, week, track_map, covers)

    tweet = build_tweet_with_best_day(rows, target_date)
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
