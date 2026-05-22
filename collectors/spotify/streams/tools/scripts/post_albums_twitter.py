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
from core.album_emoji import album_emoji
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


def _era_daily_series(era: str, track_map: dict) -> dict[str, int]:
    # Tous les albums qui appartiennent à cette ère (OG + TV le cas échéant).
    era_albums = {
        album
        for album in {info.get("album") for info in track_map.values()}
        if generate_albums_image.ERA_MAP.get(album, album) == era
    }
    album_tracks = [
        (track_id, info)
        for track_id, info in track_map.items()
        if info.get("album") in era_albums
    ]
    if not album_tracks:
        return {}

    wanted_ids = {track_id for track_id, _ in album_tracks}
    rows_by_date: dict[str, dict[str, dict]] = {}
    with open(generate_albums_image.HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            track_id = row.get("track_id")
            if track_id not in wanted_ids:
                continue
            d = (row.get("date") or "").strip()
            if not d:
                continue
            try:
                streams = int(row.get("streams") or 0)
            except Exception:
                streams = 0
            try:
                daily = int(row.get("daily_streams") or 0)
            except Exception:
                daily = 0
            rows_by_date.setdefault(d, {})[track_id] = {
                "streams": streams,
                "daily_streams": daily,
            }

    def best_key(entry: dict | None) -> tuple[int, int]:
        if not entry:
            return (-1, -1)
        return (int(entry.get("daily_streams") or 0), int(entry.get("streams") or 0))

    series: dict[str, int] = {}
    for d, day_rows in rows_by_date.items():
        # Dédup intra-album uniquement : clé = (album, song_family, display_section)
        best_by_group: dict[tuple[str, str, str], str] = {}
        for track_id, info in album_tracks:
            entry = day_rows.get(track_id)
            if entry is None:
                continue
            alb = (info.get("album") or "").strip()
            fam = (info.get("song_family") or "").strip() or track_id
            sec = (info.get("display_section") or "").strip().lower()
            key = (alb, fam, sec)
            prev_id = best_by_group.get(key)
            if prev_id is None or best_key(entry) > best_key(day_rows.get(prev_id)):
                best_by_group[key] = track_id
        series[d] = sum(
            int(day_rows[track_id].get("daily_streams") or 0)
            for track_id in best_by_group.values()
            if track_id in day_rows
        )
    return series


def _era_best_day_label(era: str, target_date: str, current_daily: int, track_map: dict, *, min_days: int = 14) -> str:
    if current_daily <= 0:
        return ""

    target = date.fromisoformat(target_date)
    series = _era_daily_series(era, track_map)
    previous_dates = [d for d in sorted(series) if d < target_date]
    if not previous_dates:
        return ""

    first_available = None
    last_at_or_above = None
    for d in previous_dates:
        daily = series.get(d)
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
        f"📊 | Taylor Swift's eras on Spotify yesterday, "
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
    track_map = generate_albums_image.load_album_track_map()
    label = _era_best_day_label(
        row["album"],
        target_date,
        int(row.get("daily_streams") or 0),
        track_map,
    )
    if not label:
        return tweet

    album = row["album"]
    return f'{tweet}\n\n{album_emoji(album)} "{_short_album(album)}" was the biggest gainer and earned its {label}.'


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
