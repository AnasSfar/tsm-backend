#!/usr/bin/env python3
"""Post weekend song gainers with the shared song_card component."""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # streams/tools/scripts/
ROOT = SCRIPT_DIR.parents[1]                          # streams/
REPO_ROOT = SCRIPT_DIR.parents[4]                     # repo root
COLLECTORS_ROOT = REPO_ROOT / "collectors"
COVERS_PATH = REPO_ROOT / "db" / "discography" / "covers.json"

sys.path.insert(0, str(COLLECTORS_ROOT))              # collectors/
sys.path.insert(0, str(ROOT))                         # collectors/spotify/streams/
sys.path.insert(0, str(ROOT.parent))                  # collectors/spotify/
sys.path.insert(0, str(SCRIPT_DIR))                   # streams/tools/scripts/

from comp.song_card_chart_sheet import format_change_html, render_chart_sheet_card, slugify, write_chart_sheet_card_png  # noqa: E402
from twitter.albums import album_emoji  # noqa: E402
from twitter.sessions import default_twitter_session  # noqa: E402
TWITTER_SESSION = default_twitter_session(REPO_ROOT)
from twitter.text import track_history_line  # noqa: E402
from core.data_paths import update_streams_dir  # noqa: E402
from core.twitter import post_with_image  # noqa: E402
import history_store  # noqa: E402
import best_day_since  # noqa: E402


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


MIN_GAIN_PCT = 10.0
DEFAULT_LIMIT = 5
DEFAULT_MIN_BASELINE = 1_000
CHARTS_HISTORY_GLOBAL = REPO_ROOT / "db" / "charts_history_global.csv"

_EXCLUDED_TITLE_MARKERS = (
    "commentary",
    "track by track",
    "karaoke",
    "remix",
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


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{int(value):,}"


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value):.1f}%"


def _fmt_pct_abs(value: float) -> str:
    return f"{abs(value):.1f}%"


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _is_postable_song_title(title: str) -> bool:
    normalized = title.casefold()
    normalized = normalized.replace("taylor's version", "")
    normalized = normalized.replace("taylorâ€™s version", "")
    if " version" in normalized:
        return False
    for marker in _EXCLUDED_TITLE_MARKERS:
        if re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", normalized):
            return False
    return True


def _badge_class(text: str) -> str:
    if text.startswith("+"):
        return "up"
    if text.startswith("-"):
        return "down"
    return "flat"


def _date_text(target_date: str) -> str:
    d = datetime.strptime(target_date, "%Y-%m-%d").date()
    return f"{d.strftime('%A')} ({d.strftime('%b')} {d.day}, {d.year})"


def _date_phrase(target_date: str) -> str:
    target = date.fromisoformat(target_date)
    if target == date.today() - timedelta(days=1):
        return f"yesterday, {_date_text(target_date)}"
    return f"on {_date_text(target_date)}"


def _load_album_tracks() -> list[dict]:
    album_ids = history_store.load_album_track_ids()
    tracks: list[dict] = []
    seen: set[str] = set()
    for section in history_store.load_album_sections_flat():
        album = str(section.get("album") or "").strip()
        for item in section.get("tracks", []):
            if not isinstance(item, dict):
                continue
            track_id = history_store.extract_track_id(item.get("url") or item.get("spotify_url") or "")
            if not track_id or track_id in seen or track_id not in album_ids:
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            seen.add(track_id)
            artists = item.get("artists") or []
            tracks.append({
                "track_id": track_id,
                "title": title,
                "spotify_url": f"https://open.spotify.com/track/{track_id}",
                "image_url": str(item.get("image_url") or "").strip(),
                "type": str(item.get("type") or "album").strip(),
                "single_image": str(item.get("single_image") or "").strip(),
                "song_family": str(item.get("song_family") or "").strip(),
                "album": album,
                "artist": item.get("primary_artist") or (artists[0] if artists else "Taylor Swift"),
            })
    tracks.sort(key=lambda track: str(track.get("title") or "").casefold())
    return tracks


def _song_family_single_image_map() -> dict[str, str]:
    family_map: dict[str, str] = {}
    for section in history_store.load_album_sections_flat():
        for item in section.get("tracks", []):
            if not isinstance(item, dict):
                continue
            family = str(item.get("song_family") or "").strip()
            image = str(item.get("single_image") or "").strip()
            if family and image.startswith("http"):
                family_map[family] = image
    return family_map


def _load_covers() -> dict[str, str]:
    if not COVERS_PATH.exists():
        return {}
    try:
        covers = json.loads(COVERS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(covers, dict):
        return {}
    return {
        _norm(value.get("title", "")): value["cover_url"]
        for value in covers.values()
        if isinstance(value, dict) and value.get("title") and value.get("cover_url")
    }


def _cover_url(track: dict, covers: dict[str, str], family_images: dict[str, str]) -> str:
    track_image = str(track.get("image_url") or "").strip()
    single_image = str(track.get("single_image") or "").strip()
    family = str(track.get("song_family") or "").strip()
    album = str(track.get("album") or "").strip()
    track_type = str(track.get("type") or "album").strip()

    if track_type in {"standalone", "alternate_version"}:
        family_image = family_images.get(family, "") if family else ""
        for image in (family_image, single_image, track_image):
            if image.startswith("http"):
                return image
        return ""

    if track_image.startswith("http"):
        return track_image
    cover = covers.get(_norm(album), "") if album else ""
    return cover if cover.startswith("http") else ""


def _image_for_row(row: dict, *, target_date: str, covers: dict, family_images: dict[str, str]) -> Path | None:
    track = row["track"]
    track_id = row["track_id"]
    total_today = row.get("total_today")
    daily_today = row.get("daily_today")
    if total_today is None:
        print(f"[weekend_song_gainers] Skipping {track['title']}: missing total streams on {target_date}.")
        return None
    if daily_today is None:
        print(f"[weekend_song_gainers] Skipping {track['title']}: missing daily streams on {target_date}.")
        return None

    target = date.fromisoformat(target_date)
    pct_text = _fmt_pct(row["pct"])
    daily_class = _badge_class(pct_text)

    points = best_day_since.load_history().get(track_id) or []
    weekly_point = next((p for p in points if p.day == target - timedelta(days=7)), None)
    weekly_daily = weekly_point.daily if weekly_point else None
    weekly_pct_text = _fmt_pct((daily_today - weekly_daily) / weekly_daily * 100) if weekly_daily else None
    weekly_class = _badge_class(weekly_pct_text) if weekly_pct_text else "flat"
    bars = best_day_since.build_chart_sheet_bars(points, target)

    release_track = best_day_since.load_tracks(include_extras=True).get(track_id)
    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%b %d, %Y").upper()
    footer_right = (
        f"Released {release_track.release_date.strftime('%d/%m/%Y')}"
        if release_track and release_track.release_date
        else date_text
    )

    html = render_chart_sheet_card(
        title=track.get("title") or track_id,
        album=track.get("album") or "",
        date_text=date_text,
        kicker_text="Weekend Gainer",
        bars=bars,
        daily_value_text=_fmt_int(daily_today),
        daily_class=daily_class,
        change_text=format_change_html(pct_text, daily_class, weekly_pct_text, weekly_class),
        total_value_text=_fmt_int(total_today),
        cover_url=_cover_url(track, covers, family_images),
        footer_left="@swiftiescharts",
        footer_right=footer_right,
    )
    out_dir = update_streams_dir(target_date) / "weekend_song_gainers"
    out_path = out_dir / f"weekend_gainer_{slugify(track.get('title') or track_id)}_{target_date}.png"
    tmp_path = out_dir / f"_weekend_gainer_{track_id}.html"
    return write_chart_sheet_card_png(html, out_path, tmp_path)


def _build_tweet(row: dict, *, target_date: str) -> str:
    track = row["track"]
    emoji = album_emoji(track.get("album"), fallback="")
    prefix = f"\U0001F4C8 | {emoji} " if emoji else "\U0001F4C8 | "
    title = track.get("title") or row["track_id"]
    song_history = track_history_line(row['track_id'])
    pct = float(row["pct"])
    date_phrase = _date_phrase(target_date)
    if random.choice(("bracket", "direction")) == "bracket":
        first_line = f'{prefix}"{title}" earned {_fmt_int(row["daily_today"])} streams [{_fmt_pct(pct)}] {date_phrase}'
    else:
        direction = "up" if pct >= 0 else "down"
        first_line = f'{prefix}"{title}" earned {_fmt_int(row["daily_today"])} streams, {direction} {_fmt_pct_abs(pct)}, {date_phrase}'
    return f"{first_line}\n\n{song_history}"


def _global_charted_track_ids(target_date: str) -> set[str]:
    """Track ids that appeared anywhere in the Spotify Global Top 200 on target_date."""
    charted: set[str] = set()
    if not CHARTS_HISTORY_GLOBAL.exists():
        return charted
    with CHARTS_HISTORY_GLOBAL.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if (row.get("date") or "").strip() != target_date:
                continue
            track_id = (row.get("track_id") or "").strip()
            if track_id:
                charted.add(track_id)
    return charted


def _best_day_since_track_ids(target_date: str) -> set[str]:
    """Track ids (including combined song-family members) with a best-day-since record on target_date."""
    tracks = best_day_since.load_tracks(include_extras=False)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    history = best_day_since.load_history()
    target = date.fromisoformat(target_date)

    track_ids: set[str] = set()
    seen_families: set[str] = set()
    for track_id, track in tracks.items():
        family = (track.song_family or track_id).strip()
        if family in seen_families:
            continue
        seen_families.add(family)
        row = best_day_since.compute_best_day_since_combined(
            track,
            best_day_since.combined_tracks_for(all_tracks.get(track_id, track), all_tracks),
            history,
            target,
        )
        if row and best_day_since.passes_filters(row, min_days=best_day_since.DEFAULT_MIN_DAYS):
            track_ids.update(row.get("combined_track_ids") or [track_id])
    return track_ids


def _pick_weekend_gainers(
    target_date: str,
    *,
    limit: int,
    min_baseline: int,
    min_pct: float,
) -> list[dict]:
    history = history_store.HistoryIndex.load()
    baseline_date = str(date.fromisoformat(target_date) - timedelta(days=1))
    best_day_track_ids = _best_day_since_track_ids(target_date)
    charted_track_ids = _global_charted_track_ids(target_date)

    rows: list[dict] = []
    for track in _load_album_tracks():
        title = str(track.get("title") or "")
        if not _is_postable_song_title(title):
            print(f"[weekend_song_gainers] Skipping {title}: not a postable song title.")
            continue

        track_id = track["track_id"]
        daily_today = history.get_daily_for_date(track_id, target_date)
        daily_baseline = history.get_daily_for_date(track_id, baseline_date)
        total_today = history.get_total_for_date(track_id, target_date)
        if daily_today is None or daily_baseline is None or total_today is None:
            continue
        if daily_baseline < min_baseline:
            continue

        gain = daily_today - daily_baseline
        if gain <= 0:
            continue
        pct = gain / daily_baseline * 100

        # Below the pct bar, a song still qualifies if it hit a best-day-since
        # record or charted on the Global Top 200 that day (chart placement
        # is only used as a qualifying signal here — never mentioned in copy).
        had_best_day = track_id in best_day_track_ids
        had_chart_entry = track_id in charted_track_ids
        if pct < min_pct and not had_best_day and not had_chart_entry:
            continue

        rows.append({
            "track": track,
            "track_id": track_id,
            "total_today": total_today,
            "daily_today": daily_today,
            "daily_baseline": daily_baseline,
            "gain": gain,
            "pct": pct,
            "baseline_date": baseline_date,
            "had_best_day": had_best_day,
            "had_chart_entry": had_chart_entry,
        })

    rows.sort(key=lambda row: (row["pct"], row["gain"], row["daily_today"]), reverse=True)
    return rows[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Post weekend Spotify song gainers with song_card images. A song qualifies if it "
            "gained at least --min-pct, or hit a best-day-since record, or charted on the "
            "Global Top 200 that day."
        )
    )
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--min-pct", type=float, default=MIN_GAIN_PCT)
    parser.add_argument("--min-baseline", type=int, default=DEFAULT_MIN_BASELINE)
    parser.add_argument("--force-weekday", action="store_true", help="Allow posting for a non-weekend stats date.")
    parser.add_argument("--force", action="store_true", help="Post again even if the lock exists.")
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--post-spacing-seconds", type=int, default=0)
    args = parser.parse_args()

    target_date = args.date or str(date.today() - timedelta(days=1))
    d = date.fromisoformat(target_date)
    if d.weekday() not in (5, 6) and not args.force_weekday:
        print(f"[weekend_song_gainers] {target_date} is not Saturday or Sunday; skipping.")
        return 0

    day_dir = update_streams_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    lock = day_dir / "weekend_song_gainers_posted.lock"
    if lock.exists() and not args.no_post and not args.force:
        print(f"[weekend_song_gainers] Already posted for {target_date}, skipping.")
        return 0

    limit = max(0, int(args.limit))
    if limit == 0:
        print("[weekend_song_gainers] Limit is 0, nothing to do.")
        return 0

    if not args.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return 1

    rows = _pick_weekend_gainers(
        target_date,
        limit=limit,
        min_baseline=max(0, int(args.min_baseline)),
        min_pct=float(args.min_pct),
    )
    if not rows:
        print(
            f"[weekend_song_gainers] No song gained at least +{float(args.min_pct):.1f}%, "
            f"hit a best-day-since record, or charted on the Global Top 200 on {target_date}."
        )
        return 0

    covers = _load_covers()
    family_images = _song_family_single_image_map()
    posted_count = 0
    for index, row in enumerate(rows, 1):
        image_path = _image_for_row(row, target_date=target_date, covers=covers, family_images=family_images)
        if image_path is None:
            continue
        tweet = _build_tweet(row, target_date=target_date)
        print(f"[weekend_song_gainers] Tweet {index}/{len(rows)} ({len(tweet)} chars):\n{tweet}")
        print(f"[weekend_song_gainers] Image: {image_path}")

        if args.no_post:
            continue
        if not post_with_image(tweet, image_path, TWITTER_SESSION):
            print(f"[weekend_song_gainers] Failed to post {row['track']['title']}.")
            return 1
        posted_count += 1
        if index < len(rows) and args.post_spacing_seconds > 0:
            print(f"[weekend_song_gainers] Waiting {args.post_spacing_seconds}s before next post...")
            time.sleep(args.post_spacing_seconds)

    if posted_count and not args.no_post:
        lock.touch()
    if args.no_post:
        print("[weekend_song_gainers] Twitter posts skipped (--no-post).")
    else:
        print(f"[weekend_song_gainers] Posted {posted_count} song(s) for {target_date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())