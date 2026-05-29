#!/usr/bin/env python3
"""Post unique stream highlight tweets combining daily, weekly and best-day notes."""
from __future__ import annotations

import argparse
import sys
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
import best_day_since  # noqa: E402
import post_gainer_thread  # noqa: E402
import spotlight  # noqa: E402


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{int(value):,}"


def _fmt_pct(value: float) -> str:
    return f"+{value:.1f}%"


def _best_day_rows(target_date: str, *, limit: int, min_days: int) -> list[dict]:
    tracks = best_day_since.load_tracks(include_extras=False)
    history = best_day_since.load_history()
    target = date.fromisoformat(target_date)

    rows: list[dict] = []
    for _track_id, track in tracks.items():
        title = track.title
        if not post_gainer_thread._is_postable_song_title(title):
            continue
        points = history.get(track.track_id)
        if not points:
            continue
        row = best_day_since.compute_best_day_since(track, points, target)
        if row and row.get("kind") == "since" and best_day_since.passes_filters(row, min_days=min_days):
            rows.append(row)

    rows.sort(key=best_day_since.sort_key, reverse=True)
    return rows[:limit]


def _collect_highlights(
    target_date: str,
    *,
    limit: int,
    best_limit: int,
    min_baseline: int,
    min_days: int,
) -> list[dict]:
    daily = post_gainer_thread._pick_gainers(
        target_date,
        compare_days=1,
        limit=limit,
        min_baseline=min_baseline,
    )
    weekly = post_gainer_thread._pick_gainers(
        target_date,
        compare_days=7,
        limit=limit,
        min_baseline=min_baseline,
    )
    best_rows = _best_day_rows(target_date, limit=best_limit, min_days=min_days)

    by_id: dict[str, dict] = {}
    order: list[str] = []

    def ensure(track_id: str, track: dict, source_order: int) -> dict:
        if track_id not in by_id:
            by_id[track_id] = {"track_id": track_id, "track": track, "source_order": source_order}
            order.append(track_id)
        return by_id[track_id]

    for idx, row in enumerate(daily):
        item = ensure(row["track_id"], row["track"], idx)
        item["daily"] = row

    for idx, row in enumerate(weekly):
        item = ensure(row["track_id"], row["track"], limit + idx)
        item["weekly"] = row

    tracks_by_id = {track["track_id"]: track for track in spotlight.load_all_tracks()}
    for idx, row in enumerate(best_rows):
        track_id = row["track_id"]
        track = tracks_by_id.get(track_id) or {"track_id": track_id, "title": row.get("title") or track_id}
        item = ensure(track_id, track, limit * 2 + idx)
        item["best_day"] = row

    return [by_id[track_id] for track_id in order]


def _build_tweet(item: dict, target_date: str) -> str:
    track = item["track"]
    title = track.get("title") or item["track_id"]
    emoji = album_emoji(track.get("album"))
    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    lines = [f'{emoji} "{title}" was a Taylor Swift stream highlight yesterday ({date_fmt}).']

    if "daily" in item:
        row = item["daily"]
        lines.append(
            f"Daily gainer: {_fmt_pct(row['pct'])} vs yesterday, "
            f"with {_fmt_int(row['daily_today'])} streams (+{_fmt_int(row['gain'])})."
        )

    if "weekly" in item:
        row = item["weekly"]
        lines.append(
            f"Weekly gainer: {_fmt_pct(row['pct'])} vs last week, "
            f"with {_fmt_int(row['daily_today'])} streams (+{_fmt_int(row['gain'])})."
        )

    if "best_day" in item:
        lines.append(f"The song earned its {best_day_since.row_label(item['best_day'])}.")

    lines.append(f"See full track's history here : https://thetsmuseum.app/songs/{item['track_id']}")
    return "\n\n".join(lines)


def _image_for_item(item: dict, target_date: str, covers: dict) -> Path:
    track = item["track"]
    track_id = item["track_id"]
    total_today, total_yesterday, _daily_today, daily_yesterday, daily_last_week = (
        spotlight.load_history_for_tracks([track_id], target_date)
    )
    if total_today is None:
        raise RuntimeError(f"Missing total streams for {track.get('title') or track_id} on {target_date}")

    if "weekly" in item and "daily" not in item:
        comparison_daily = daily_last_week
        comparison_label = "Last Week"
    else:
        comparison_daily = daily_yesterday
        comparison_label = "Yesterday"

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
    parser = argparse.ArgumentParser(description="Post combined stream highlights without duplicate songs.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--limit", type=int, default=5, help="Top N daily and weekly gainers.")
    parser.add_argument("--best-limit", type=int, default=3, help="Top N best-day-since notes.")
    parser.add_argument("--min-baseline", type=int, default=1000)
    parser.add_argument("--min-days", type=int, default=14)
    parser.add_argument("--no-post", action="store_true")
    args = parser.parse_args()

    target_date = args.date or str(date.today() - timedelta(days=1))
    day_dir = post_gainer_thread.history_store.update_streams_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    lock = day_dir / "stream_highlights_posted.lock"
    if lock.exists() and not args.no_post:
        print(f"[stream_highlights] Already posted for {target_date}, skipping.")
        return 0

    if not args.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return 1

    items = _collect_highlights(
        target_date,
        limit=max(0, int(args.limit)),
        best_limit=max(0, int(args.best_limit)),
        min_baseline=max(0, int(args.min_baseline)),
        min_days=max(0, int(args.min_days)),
    )
    if not items:
        print(f"[stream_highlights] No highlights found for {target_date}.")
        return 0

    covers = spotlight.load_covers()
    posts: list[tuple[str, Path]] = []
    for idx, item in enumerate(items, 1):
        tweet = _build_tweet(item, target_date)
        image_path = _image_for_item(item, target_date, covers)
        tags = ", ".join(k for k in ("daily", "weekly", "best_day") if k in item)
        print(f"[stream_highlights] {idx}/{len(items)} {item['track'].get('title')} [{tags}]")
        print(f"[stream_highlights] Tweet ({len(tweet)} chars):\n{tweet}")
        print(f"[stream_highlights] Image: {image_path}")
        posts.append((tweet, image_path))

    if args.no_post:
        print("[stream_highlights] Twitter post skipped (--no-post).")
        return 0

    if not post_image_thread(posts, TWITTER_SESSION):
        print("[stream_highlights] Failed to post highlight thread.")
        return 1
    lock.touch()
    print(f"[stream_highlights] Posted {len(posts)} unique highlight song(s) for {target_date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
