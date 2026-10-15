"""One native X thread: recap, songs, eras, daily gainers, weekly gainers.

Exact secondary records can follow as one grouped reply. All images and text
are prepared and validated before publishing; locks are written only on success.
Use --no-post --output-dir PATH for isolated real-data previews.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[4]
sys.path.insert(0, str(REPO_ROOT / "collectors"))
sys.path.insert(0, str(SCRIPT_DIR.parents[2]))
from core.data_paths import update_streams_dir
from core.twitter import TWITTER_TEXT_LIMIT, post_image_thread
from twitter.sessions import default_twitter_session
from twitter.text import daily_streams_thread_tweet, streams_update_tweet, stream_gainers_table_tweet
import history_store
import post_albums_twitter as albums
import post_best_day_since_twitter as records
import post_gainer_thread as gainers
import spotify_app_cards as cards
from post_locks import mark_posted

TWITTER_SESSION = default_twitter_session(REPO_ROOT)
LOCKS = {
    "recap": "weekend_streams_posted.lock",
    "top20": "posted.lock",
    "top-eras": "albums_posted.lock",
    "daily": "stream_highlights_daily_gainers_posted.lock",
    "weekly": "stream_highlights_weekly_gainers_posted.lock",
}


@dataclass
class ThreadPlan:
    posts: list[tuple[str, Path | list[Path]]]
    components: list[str]
    grouped_record_ids: list[str]
    evaluated_components: list[str] = field(default_factory=list)
    era_groups: list[dict] = field(default_factory=list)


def build_plan(target_date: str, *, output_dir: Path | None = None,
               skip: set[str] | None = None, preview: bool = False) -> ThreadPlan:
    skip = set(skip or ())
    day_dir = update_streams_dir(target_date)
    out = (output_dir or day_dir).resolve()
    d = date.fromisoformat(target_date)
    history_store.validate_released_active_history_complete(
        target_date, comparison_dates=(str(d - timedelta(days=1)), str(d - timedelta(days=7))),
        label="daily Spotify streams thread",
    )
    posts = []
    components = []

    def needed(key):
        return key not in skip and (preview or not (day_dir / LOCKS[key]).exists())

    if needed("recap"):
        posts.append((daily_streams_thread_tweet(stats_date=target_date),
                      cards.render_recap(target_date, out / "weekend_streams_image.png")))
        components.append("recap")
    if needed("top20"):
        posts.append((streams_update_tweet(top_n=20, stats_date=target_date),
                      cards.render_songs(target_date, out / "streams_image_1_20.png", n=20)))
        components.append("top20")
    if needed("top-eras"):
        tmap = cards.ga.load_album_track_map()
        today, yesterday, week = cards.ga.load_history(target_date)
        era_rows = cards.ga.build_album_rows(today, yesterday, week, tmap, cards.ga.load_covers(), target_date=target_date)
        ids = {tid for tid, info in tmap.items() if not info.get("chart_extra")}
        span = albums._max_days_covered_fast(ids, target_date)
        text = albums.build_tweet_with_best_day(era_rows, target_date, tmap, max_days=span)
        posts.append((text, cards.render_eras(target_date, out / "albums_image.png")))
        components.append("top-eras")
    for period, compare_days in (("daily", 1), ("weekly", 7)):
        if not needed(period):
            continue
        picked = gainers._pick_gainers(target_date, compare_days=compare_days, limit=10, min_baseline=1000)
        if not picked:
            print(f"[daily-thread] No qualifying {period} gainers for {target_date}.")
            continue
        image = cards.render_gainers(target_date, period, picked, out / f"stream_highlights_{period}_gainers_{target_date}.png")
        posts.append((stream_gainers_table_tweet(period=period, count=len(picked), stats_date=target_date), image))
        components.append(period)

    grouped_ids = []
    era_groups = []
    if "best-day-since" not in skip and (preview or not (day_dir / "best_day_since_recap_posted.lock").exists()):
        rec_posts, grouped_ids = records.build_grouped_record_reply(target_date, output_dir=out, preview=preview)
        posts.extend(rec_posts)
    if "best-day-since" not in skip:
        era_posts, era_groups = records.build_era_record_replies(target_date, output_dir=out, preview=preview)
        posts.extend(era_posts)
    for text, images in posts:
        if len(text) > TWITTER_TEXT_LIMIT:
            raise ValueError(f"Daily thread blocked: text exceeds {TWITTER_TEXT_LIMIT} characters ({len(text)}).")
        paths = images if isinstance(images, list) else [images]
        if not 1 <= len(paths) <= 4 or any(not p.is_file() for p in paths):
            raise ValueError("Daily thread blocked: invalid or missing image attachments.")
    return ThreadPlan(posts, components, grouped_ids, [key for key in LOCKS if key not in skip], era_groups)


def publish_plan(plan: ThreadPlan, target_date: str) -> bool:
    if not plan.posts:
        print(f"[daily-thread] All requested components already posted for {target_date}.")
        return True
    day_dir = update_streams_dir(target_date)
    receipt = day_dir / "daily_streams_thread_posted.json"

    def already_posted():
        # Recheck after acquiring the account slot: concurrent runs must not duplicate a thread.
        if not receipt.exists():
            return False
        data = json.loads(receipt.read_text(encoding="utf-8"))
        return (set(plan.components) <= set(data["components"])
                and set(plan.grouped_record_ids) <= set(data["grouped_record_ids"])
                and {g["era_key"] for g in plan.era_groups} <= set(data.get("era_keys", [])))

    if already_posted():
        return True
    if (day_dir / LOCKS["recap"]).exists():
        raise RuntimeError("Thread pending: a recap was published while this thread was being prepared.")
    day_dir.mkdir(parents=True, exist_ok=True)
    pending = day_dir / "daily_streams_thread_pending.json"
    # A missing X confirmation is ambiguous: never retry an entire thread blindly.
    try:
        with pending.open("x", encoding="utf-8") as fh:
            json.dump({"date": target_date, "components": plan.components,
                       "reason": "Publication started; awaiting X confirmation."}, fh)
    except FileExistsError:
        raise RuntimeError(f"Thread pending: check the previous X publication before retrying ({pending}).")
    if not post_image_thread(plan.posts, TWITTER_SESSION, skip_if=already_posted):
        print(f"[daily-thread] X confirmation missing; component locks remain pending. Check {pending} before retrying.")
        return False
    if already_posted():
        return True
    for key in plan.components:
        mark_posted(day_dir / LOCKS[key])
    if plan.grouped_record_ids:
        records.mark_grouped_record_reply_posted(target_date, plan.grouped_record_ids)
    for group in plan.era_groups:
        records._write_era_recap_lock(group["era_key"], target_date, group)
    receipt.write_text(json.dumps({"date": target_date, "components": plan.components,
                                   "grouped_record_ids": plan.grouped_record_ids,
                                   "era_keys": [g["era_key"] for g in plan.era_groups],
                                   "evaluated_components": plan.evaluated_components}, indent=2), encoding="utf-8")
    pending.unlink()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date")
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--output-dir", type=Path, help="Isolated preview output; requires --no-post.")
    parser.add_argument("--skip", default="", help="Comma-separated component names.")
    args = parser.parse_args()
    if args.output_dir and not args.no_post:
        parser.error("--output-dir requires --no-post")
    day_dir = update_streams_dir(args.date)
    skip = {x.strip() for x in args.skip.split(",") if x.strip()}
    unknown = skip - set(LOCKS) - {"best-day-since"}
    if unknown:
        parser.error(f"Unknown components: {sorted(unknown)}")
    pending = day_dir / "daily_streams_thread_pending.json"
    if not args.no_post and pending.exists() and not (day_dir / "daily_streams_thread_posted.json").exists():
        raise RuntimeError(f"Thread pending: previous X publication needs verification ({pending}).")
    if not args.no_post and (day_dir / LOCKS['recap']).exists():
        receipt = day_dir / "daily_streams_thread_posted.json"
        if receipt.exists():
            data = json.loads(receipt.read_text(encoding="utf-8"))
            requested = {key for key in LOCKS if key not in skip}
            if requested <= set(data["evaluated_components"]):
                if "best-day-since" not in skip:
                    missing_eras = [g["album"] for g in records._era_recap_groups(args.date)
                                    if not records._era_recap_lock_path(g["era_key"], args.date).exists()]
                    if missing_eras:
                        raise RuntimeError(f"Thread pending: era replies {missing_eras} need the existing thread's X URL.")
                print(f"[daily-thread] Thread already posted for {args.date}; no new root.")
                return 0
            raise RuntimeError("Thread pending: additional components require the existing thread's X URL.")
        missing = [key for key, name in LOCKS.items() if key not in skip and not (day_dir / name).exists()]
        if missing:
            raise RuntimeError(f"Thread pending: legacy recap already posted; cannot attach {missing} without its X URL.")
        print(f"[daily-thread] Legacy components already posted for {args.date}; no new root.")
        return 0
    plan = build_plan(args.date, output_dir=args.output_dir, skip=skip, preview=args.no_post)
    for i, (text, images) in enumerate(plan.posts, 1):
        print(f"[daily-thread] {'ROOT' if i == 1 else 'REPLY'} {i}: {text}")
        print(f"[daily-thread] Images: {images}")
    if args.no_post:
        print(f"[daily-thread] Preview only: {len(plan.posts)} posts, no publication or lock writes.")
        return 0
    return 0 if publish_plan(plan, args.date) else 1


if __name__ == "__main__":
    raise SystemExit(main())
