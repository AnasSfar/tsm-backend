#!/usr/bin/env python3
"""Post top percentage stream gainers as an image thread."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # streams/tools/scripts/
ROOT = SCRIPT_DIR.parents[1]                          # streams/
TWITTER_SESSION = ROOT.parent / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(ROOT))                         # collectors/spotify/streams/
sys.path.insert(0, str(ROOT.parent))                  # collectors/spotify/
sys.path.insert(0, str(SCRIPT_DIR))                   # streams/tools/scripts/

from core.album_emoji import album_emoji  # noqa: E402
from core.twitter import post_image_thread  # noqa: E402
import history_store  # noqa: E402
import spotlight  # noqa: E402


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{int(value):,}"


def _fmt_pct(value: float) -> str:
    return f"+{value:.1f}%"


_EXCLUDED_TITLE_MARKERS = (
    "commentary",
    "track by track",
    "karaoke",
    "remix",
    "live",
    "instrumental",
    "arr.",
    "arr ",
    "concert",
    "demo",
    "voice memo",
    "piano/vocal",
    "acoustic",
    "stripped",
    "album version",
    "international version",
    "pop version",
    "radio edit",
    "single version",
    "u.s. version",
    "us version",
)


def _is_postable_song_title(title: str) -> bool:
    normalized = title.casefold()
    if "taylor's version" in normalized:
        normalized = normalized.replace("taylor's version", "")
    if "taylor’s version" in normalized:
        normalized = normalized.replace("taylor’s version", "")
    if " version" in normalized:
        return False
    return not any(marker in normalized for marker in _EXCLUDED_TITLE_MARKERS)


def _pick_gainers(target_date: str, *, compare_days: int, limit: int, min_baseline: int) -> list[dict]:
    history = history_store.HistoryIndex.load()
    album_ids = history_store.load_album_track_ids()
    tracks = [
        track for track in history_store.load_tracks_from_discography(album_ids)
        if track["track_id"] in album_ids
    ]
    baseline_date = str(date.fromisoformat(target_date) - timedelta(days=compare_days))

    rows: list[dict] = []
    for track in tracks:
        track_id = track["track_id"]
        daily_today = history_store._daily_for_spotlight(history, track_id, target_date)
        daily_baseline = history_store._daily_for_spotlight(history, track_id, baseline_date)
        if daily_today is None or daily_baseline is None or daily_baseline <= 0:
            continue
        gain = daily_today - daily_baseline
        if gain <= 0:
            continue
        if daily_baseline < min_baseline:
            continue
        if not _is_postable_song_title(track.get("title") or ""):
            continue
        pct = gain / daily_baseline * 100
        rows.append({
            "track": track,
            "track_id": track_id,
            "daily_today": daily_today,
            "daily_baseline": daily_baseline,
            "gain": gain,
            "pct": pct,
            "baseline_date": baseline_date,
        })

    rows.sort(key=lambda row: (row["pct"], row["gain"], row["daily_today"]), reverse=True)
    return rows[:limit]


def _build_tweet(row: dict, *, rank: int, target_date: str, period: str) -> str:
    track = row["track"]
    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    emoji = album_emoji(track.get("album"))
    title = track["title"]
    compare_label = "yesterday" if period == "daily" else "last week"
    song_url = f"https://thetsmuseum.app/songs/{row['track_id']}"
    return (
        f'{emoji} #{rank} "{title}" was one of Taylor Swift\'s biggest {period} gainers '
        f"by % yesterday ({date_fmt}).\n\n"
        f"It rose {_fmt_pct(row['pct'])} vs {compare_label}, with {_fmt_int(row['daily_today'])} streams "
        f"(+{_fmt_int(row['gain'])}).\n\n"
        f"See full track's history here : {song_url}"
    )


def _image_for_row(row: dict, *, target_date: str, period: str, covers: dict) -> Path:
    track = row["track"]
    total_today, total_yesterday, _daily_today, daily_yesterday, daily_last_week = (
        spotlight.load_history_for_tracks([row["track_id"]], target_date)
    )
    if total_today is None:
        raise RuntimeError(f"Missing total streams for {track['title']} on {target_date}")
    comparison_daily = daily_last_week if period == "weekly" else daily_yesterday
    comparison_label = "Last Week" if period == "weekly" else "Yesterday"
    return spotlight.generate_spotlight_image(
        track=track,
        total_scraped=total_today,
        total_yesterday=total_yesterday,
        comparison_daily=comparison_daily,
        comparison_label=comparison_label,
        cover_url=spotlight.get_cover_url(track, covers),
        stats_date=target_date,
        handle="@tsmuseum13",
        combined=False,
        highlight="vs",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Post top percentage stream gainers as a Twitter/X thread.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--period", choices=("daily", "weekly"), required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--min-baseline", type=int, default=1000)
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--post-spacing-seconds", type=int, default=0)
    args = parser.parse_args()

    target_date = args.date or str(date.today() - timedelta(days=1))
    limit = max(0, int(args.limit))
    if limit == 0:
        print("[gainer_thread] Limit is 0, nothing to do.")
        return 0

    day_dir = history_store.update_streams_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    lock = day_dir / f"gainers_{args.period}_posted.lock"
    if lock.exists() and not args.no_post:
        print(f"[gainer_thread] {args.period} gainers already posted for {target_date}, skipping.")
        return 0

    if not args.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return 1

    compare_days = 1 if args.period == "daily" else 7
    rows = _pick_gainers(
        target_date,
        compare_days=compare_days,
        limit=limit,
        min_baseline=max(0, int(args.min_baseline)),
    )
    if not rows:
        print(f"[gainer_thread] No positive {args.period} gainers found for {target_date}.")
        return 0

    covers = spotlight.load_covers()
    posts: list[tuple[str, Path]] = []
    for rank, row in enumerate(rows, 1):
        image_path = _image_for_row(row, target_date=target_date, period=args.period, covers=covers)
        tweet = _build_tweet(row, rank=rank, target_date=target_date, period=args.period)
        print(f"[gainer_thread] {args.period} #{rank}: {row['track']['title']} {_fmt_pct(row['pct'])}")
        print(f"[gainer_thread] Tweet ({len(tweet)} chars):\n{tweet}")
        print(f"[gainer_thread] Image: {image_path}")
        posts.append((tweet, image_path))

    if args.no_post:
        print("[gainer_thread] Twitter post skipped (--no-post).")
        return 0

    if not post_image_thread(posts, TWITTER_SESSION):
        print(f"[gainer_thread] Failed to post {args.period} gainer thread.")
        return 1
    lock.touch()

    if args.post_spacing_seconds > 0:
        time.sleep(args.post_spacing_seconds)
    print(f"[gainer_thread] Posted {len(posts)} {args.period} gainer(s) for {target_date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
