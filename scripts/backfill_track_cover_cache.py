#!/usr/bin/env python3
"""
One-off backfill: pre-warm db/discography/track_cover_cache.json for the whole
catalog by calling the live Spotify getTrack API (same call already used for
playcount in fetch_playcount_api) for every track_id, so per-version cover art
(e.g. "Style" original vs "Style (Taylor's Version)") is correct immediately
instead of waiting for the next daily update_streams.py run.

Usage:
  python scripts/backfill_track_cover_cache.py
  python scripts/backfill_track_cover_cache.py --from-discography-only
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))  # for core.*
sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "collectors" / "comp"))

from spotify_api import TokenManager, fetch_playcount_api  # noqa: E402
from history_store import load_tracks_from_discography  # noqa: E402
from track_cover_cache import load_track_cover_cache, merge_track_cover_cache  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-warm db/discography/track_cover_cache.json from discography images and/or Spotify."
    )
    parser.add_argument(
        "--from-discography-only",
        action="store_true",
        help="Only copy existing discography image_url values into track_cover_cache.json; do not call Spotify.",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="When calling Spotify, skip track IDs that are already present in track_cover_cache.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tracks = load_tracks_from_discography()
    print(f"Found {len(tracks)} track(s) in discography.")

    existing_cache = load_track_cover_cache()
    print(f"{len(existing_cache)} track_id(s) already cached.")

    discography_updates = {
        track["track_id"]: track["image_url"]
        for track in tracks
        if track.get("track_id") and track.get("image_url")
    }
    if discography_updates:
        cache = merge_track_cover_cache(discography_updates)
        print(
            f"Synced {len(discography_updates)} cover(s) from discography image_url. "
            f"Cache now has {len(cache)} track_id(s)."
        )
        existing_cache = cache

    if args.from_discography_only:
        missing_image_ids = [
            track["track_id"]
            for track in tracks
            if track.get("track_id") and not track.get("image_url")
        ]
        print(f"{len(missing_image_ids)} track(s) still have no discography image_url.")
        return

    print("Capturing tokens via TokenManager...")
    token_mgr = TokenManager()
    if not token_mgr.capture():
        print("FAILED to capture tokens — aborting.")
        sys.exit(1)

    session = requests.Session()
    cover_updates: dict[str, str] = {}
    misses = 0

    for i, track in enumerate(tracks, 1):
        track_id = track["track_id"]
        title = track["title"]
        if args.missing_only and track_id in existing_cache:
            continue
        metrics: dict = {}
        try:
            fetch_playcount_api(track_id, token_mgr, session, metrics=metrics)
        except Exception as e:
            print(f"  [{i}/{len(tracks)}] {title} [{track_id}] ERROR: {e}")
            continue

        cover_url = metrics.get("cover_url")
        if cover_url:
            cover_updates[track_id] = cover_url
            if i % 25 == 0 or i == len(tracks):
                print(f"  [{i}/{len(tracks)}] {len(cover_updates)} cover(s) fetched so far...")
        else:
            misses += 1
            print(f"  [{i}/{len(tracks)}] {title} [{track_id}] no cover_url in response")

        time.sleep(0.05)

    print(f"\nFetched {len(cover_updates)} cover(s), {misses} miss(es).")
    if cover_updates:
        cache = merge_track_cover_cache(cover_updates)
        print(f"Cache now has {len(cache)} track_id(s) total.")


if __name__ == "__main__":
    main()
