#!/usr/bin/env python3
"""
Post the top "best day since" songs to @tsmuseum13 with spotlight images.

Usage:
  python post_best_day_since_twitter.py 2026-05-07
  python post_best_day_since_twitter.py 2026-05-07 --no-post
  python post_best_day_since_twitter.py 2026-05-07 --limit 3
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # streams/tools/scripts/
ROOT = SCRIPT_DIR.parents[1]                          # streams/
REPO_ROOT = SCRIPT_DIR.parents[4]                     # repo root
TWITTER_SESSION = ROOT.parent / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(ROOT))                         # collectors/spotify/streams/
sys.path.insert(0, str(ROOT.parent))                  # collectors/spotify/

from core.twitter import post_with_image  # noqa: E402
from core.album_emoji import album_emoji  # noqa: E402
import best_day_since  # noqa: E402
import spotlight  # noqa: E402


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{int(value):,}"


def _fmt_pct(current: int | None, previous: int | None) -> str:
    if current is None or previous is None or previous <= 0:
        return "+0.0%"
    pct = (current - previous) / previous * 100
    return f"{pct:+.1f}%"


def _pick_rows(target_date: str, *, limit: int, min_days: int) -> list[dict]:
    tracks = best_day_since.load_tracks(include_extras=False)
    history = best_day_since.load_history()
    target = date.fromisoformat(target_date)

    rows: list[dict] = []
    for track_id, track in tracks.items():
        points = history.get(track_id)
        if not points:
            continue
        row = best_day_since.compute_best_day_since(track, points, target)
        if row and row.get("kind") == "since" and best_day_since.passes_filters(row, min_days=min_days):
            rows.append(row)

    rows.sort(key=best_day_since.sort_key, reverse=True)
    return rows[:limit]


def _build_tweet(row: dict, daily_yesterday: int | None) -> str:
    emoji = album_emoji(row.get("album"))
    title = row["title"]
    label = best_day_since.row_label(row)
    daily = int(row["daily_streams"])
    pct = _fmt_pct(daily, daily_yesterday)
    return f'{emoji} "{title}" earned its {label} with {_fmt_int(daily)} streams [{pct}].'


def _day_dir(target_date: str) -> Path:
    d = date.fromisoformat(target_date)
    return ROOT / "history" / str(d.year) / f"{d.month:02d}" / target_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Post top best-day-since songs to @tsmuseum13.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--no-post", action="store_true", help="Generate images but skip Twitter posts.")
    parser.add_argument("--limit", type=int, default=3, help="Number of songs to post (default: 3).")
    parser.add_argument("--min-days", type=int, default=14, help="Minimum days for best-day-since (default: 14).")
    parser.add_argument(
        "--post-spacing-seconds",
        type=int,
        default=0,
        help="Extra seconds to wait between Twitter posts; core.twitter enforces account spacing.",
    )
    args = parser.parse_args()

    target_date = args.date or str(date.today() - timedelta(days=1))
    limit = max(0, int(args.limit))
    if limit == 0:
        print("[best_day_since_post] Limit is 0, nothing to do.")
        return

    day_dir = _day_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    lock = day_dir / "best_day_since_posted.lock"

    if lock.exists() and not args.no_post:
        print(f"[best_day_since_post] Already posted for {target_date}, skipping.")
        return
    if lock.exists() and args.no_post:
        print(f"[best_day_since_post] Already posted for {target_date}, regenerating only (--no-post).")

    if not args.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    rows = _pick_rows(target_date, limit=limit, min_days=args.min_days)
    if not rows:
        print(f"[best_day_since_post] No best-day-since songs found for {target_date}.")
        return

    tracks_by_id = {track["track_id"]: track for track in spotlight.load_all_tracks()}
    covers = spotlight.load_covers()

    posted_count = 0
    for index, row in enumerate(rows, 1):
        track = tracks_by_id.get(row["track_id"])
        if not track:
            print(f"[best_day_since_post] Track missing in spotlight DB: {row['title']} [{row['track_id']}]")
            continue

        total_today, total_yesterday, daily_today, daily_yesterday, _daily_last_week = (
            spotlight.load_history_for_tracks([row["track_id"]], target_date)
        )
        if total_today is None:
            print(f"[best_day_since_post] Missing total streams for {row['title']} on {target_date}; skipping.")
            continue

        cover_url = spotlight.get_cover_url(track, covers)
        image_path = spotlight.generate_spotlight_image(
            track=track,
            total_scraped=total_today,
            total_yesterday=total_yesterday,
            comparison_daily=daily_yesterday,
            comparison_label="Yesterday",
            cover_url=cover_url,
            stats_date=target_date,
            handle="@tsmuseum13",
            combined=False,
            highlight="vs",
        )

        tweet = _build_tweet(row, daily_yesterday)
        print(f"[best_day_since_post] Tweet {index}/{len(rows)} ({len(tweet)} chars):\n{tweet}")
        print(f"[best_day_since_post] Image: {image_path}")

        if args.no_post:
            continue

        if not post_with_image(tweet, image_path, TWITTER_SESSION):
            print(f"[best_day_since_post] Failed to post {row['title']}.")
            sys.exit(1)
        posted_count += 1
        if index < len(rows) and args.post_spacing_seconds > 0:
            print(f"[best_day_since_post] Waiting {args.post_spacing_seconds}s before next post...")
            time.sleep(args.post_spacing_seconds)

    if args.no_post:
        print("[best_day_since_post] Twitter posts skipped (--no-post).")
        return

    if posted_count:
        lock.touch()
    print(f"[best_day_since_post] Posted {posted_count} song(s) for {target_date}.")


if __name__ == "__main__":
    main()
