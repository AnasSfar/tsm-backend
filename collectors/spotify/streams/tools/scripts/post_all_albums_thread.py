#!/usr/bin/env python3
"""Post every album (excluding Misc/standalone) as a single Twitter thread.

Intended for Mondays and Fridays only — separate from the individual
ALBUM_UPDATE_TARGETS updates, which keep posting independently.

Usage:
  python post_all_albums_thread.py 2026-07-06
  python post_all_albums_thread.py 2026-07-06 --no-post
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # streams/tools/scripts/
TWITTER_SESSION = SCRIPT_DIR.parents[2] / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(SCRIPT_DIR.parents[2]))        # collectors/spotify/
from core.twitter import post_image_thread  # noqa: E402
from core.data_paths import update_streams_dir  # noqa: E402

import generate_album_update_image  # noqa: E402
import history_store  # noqa: E402
from post_locks import mark_posted, should_skip_post  # noqa: E402


def _is_misc_album(name: str) -> bool:
    norm = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    parts = set(norm.split("_"))
    if "misc" in parts or "standalone" in parts:
        return True
    return norm in {"miscellaneous", "standalone_extras", "standalone_and_extras"}


def all_album_names() -> list[str]:
    seen: list[str] = []
    for section in history_store.load_album_sections_flat():
        album = (section.get("album") or "").strip()
        if not album or album in seen or _is_misc_album(album):
            continue
        seen.append(album)
    return seen


def main() -> None:
    parser = argparse.ArgumentParser(description="Post all albums as a single thread.")
    parser.add_argument("date", help="Stats date YYYY-MM-DD.")
    parser.add_argument("--no-post", action="store_true", help="Generate images but skip Twitter post.")
    args = parser.parse_args()

    target_date = args.date
    day_dir = update_streams_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    lock = day_dir / "all_albums_thread_posted.lock"

    if should_skip_post(lock, target_date=target_date, label="All-albums thread", no_post=args.no_post):
        return

    if not args.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    albums = all_album_names()
    if not albums:
        print("[all_albums_thread] No albums found.")
        return

    thread_posts: list[tuple[str, Path]] = []
    for album in albums:
        if not history_store.album_tracks_done_for(album, target_date):
            print(f"[all_albums_thread] Skipping incomplete album: {album}")
            continue
        try:
            image_path = generate_album_update_image.generate(album, target_date)
            tweet = generate_album_update_image._build_album_post_text(album, target_date)
        except Exception as exc:
            print(f"[all_albums_thread] Failed to build {album}: {exc}")
            continue
        thread_posts.append((tweet, image_path))

    if not thread_posts:
        print("[all_albums_thread] Nothing to post (no complete albums).")
        return

    print(f"[all_albums_thread] Prepared {len(thread_posts)}/{len(albums)} album(s) for the thread.")
    for tweet, image_path in thread_posts:
        print(f"[all_albums_thread] Tweet ({len(tweet)} chars):\n{tweet}")
        print(f"[all_albums_thread] Image: {image_path}")

    if args.no_post:
        print("[all_albums_thread] Twitter post skipped (--no-post).")
        return

    success = post_image_thread(thread_posts, TWITTER_SESSION)
    if not success:
        print(f"[all_albums_thread] Failed to post thread for {target_date}.")
        sys.exit(1)

    mark_posted(lock)
    print(f"[all_albums_thread] Posted successfully for {target_date} ({len(thread_posts)} album(s)).")


if __name__ == "__main__":
    main()
