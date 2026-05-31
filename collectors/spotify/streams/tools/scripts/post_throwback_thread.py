#!/usr/bin/env python3
"""Generate and post a throwback Spotify streams thread."""
from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
TSM_TWITTER_SESSION = ROOT.parent / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json"
SWIFTIES_TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"
HANDLE = "@tsmuseum13"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(SCRIPT_DIR.parents[2]))
from core.data_paths import update_streams_dir  # noqa: E402
from core.twitter import post_image_thread  # noqa: E402

import generate_albums_image  # noqa: E402
import generate_album_update_image  # noqa: E402
import generate_streams_image  # noqa: E402
import generate_weekend_streams_image  # noqa: E402


BACK = "\U0001f519"


def _word_number(value: int) -> str:
    words = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
        10: "ten",
    }
    return words.get(value, str(value))


def _sentence_start_label(label: str) -> str:
    label = (label or "").strip()
    if not label:
        return "On this day"
    return f"{label[:1].upper()}{label[1:]} ago"


def _event_noun(action: str) -> str:
    return "announcement" if action == "announced" else "release"


def _fmt_streams(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{int(value):,}"


def _relative_label(target_date: str, today: date | None = None) -> str:
    current = today or date.today()
    target = date.fromisoformat(target_date)
    if target > current:
        raise ValueError("--throwback date cannot be in the future")

    year_diff = current.year - target.year
    if year_diff > 0 and (current.month, current.day) == (target.month, target.day):
        unit = "year" if year_diff == 1 else "years"
        return f"{_word_number(year_diff)} {unit}"

    month_diff = (current.year - target.year) * 12 + current.month - target.month
    if month_diff > 0 and current.day == target.day:
        unit = "month" if month_diff == 1 else "months"
        return f"{_word_number(month_diff)} {unit}"

    day_diff = (current - target).days
    if day_diff >= 7 and day_diff % 7 == 0:
        week_diff = day_diff // 7
        unit = "week" if week_diff == 1 else "weeks"
        return f"{_word_number(week_diff)} {unit}"

    unit = "day" if day_diff == 1 else "days"
    return f"{_word_number(day_diff)} {unit}"


def _date_fmt(target_date: str) -> str:
    return datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")


def _render_html_image(
    html_text: str,
    out_path: Path,
    tmp_name: str,
    *,
    width: int = 1000,
    force: bool = False,
) -> Path:
    if out_path.exists() and not force:
        print(f"[throwback] Reusing existing image: {out_path}")
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_html = out_path.parent / tmp_name
    tmp_html.write_text(html_text, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": width, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()
    return out_path


def _throwback_theme(top_album: str | None) -> tuple[str, str, str]:
    hdr_style, handle_color = generate_weekend_streams_image._header_style(top_album or "")
    body_style = generate_weekend_streams_image._theme_vars_from_color(handle_color)
    return hdr_style, handle_color, body_style


def _is_real_album_row(row: dict) -> bool:
    album = (row.get("album") or "").strip().casefold()
    if album in {
        "standalone & extras",
        "standalone",
        "extras",
        "the taylor swift holiday collection",
    }:
        return False
    return int(row.get("daily_streams") or 0) > 0


def _album_update_path(album_name: str, target_date: str) -> Path:
    album_slug = re.sub(r"[^a-z0-9]+", "_", album_name.lower()).strip("_")
    return ROOT / "history" / target_date[:4] / target_date[5:7] / target_date / f"{album_slug}_update.png"


def _album_update_image(album_name: str, target_date: str, *, force: bool) -> Path:
    image_path = _album_update_path(album_name, target_date)
    if image_path.exists() and not force:
        print(f"[throwback] Reusing existing album update: {image_path}")
        return image_path
    return generate_album_update_image.generate(album_name, target_date, sort_tracks_by_daily=True)


def _section_image(
    *,
    target_date: str,
    rows: list[dict],
    title: str,
    entity_label: str,
    kind: str,
    filename: str,
    header_album: str,
    force: bool,
    album_cache: dict[str, str] | None = None,
    song_cache: dict[str, str] | None = None,
    song_cover_map: dict | None = None,
    song_track_album_map: dict | None = None,
) -> Path:
    out_dir = update_streams_dir(target_date)
    hdr_style, handle_color, body_style = _throwback_theme(header_album)

    if kind == "song":
        rows_html = generate_weekend_streams_image._row_html(
            "song",
            rows,
            song_cache or {},
            song_cover_map or {},
            song_track_album_map or {},
        )
    else:
        rows_html = generate_weekend_streams_image._row_html(
            "album",
            rows,
            album_cache or {},
            {},
            {},
        )

    section = generate_weekend_streams_image._section_html(title, "", rows_html, entity_label)
    date_text = _date_fmt(target_date)
    css = generate_weekend_streams_image.CSS
    html_text = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body style="{body_style}">
<div class="container">
  <div class="hdr" {hdr_style}>
    <div class="brand">
      {generate_weekend_streams_image.SPOTIFY_SVG}
      <div>
        <div class="hdr-title">Taylor Swift &middot; Throwback</div>
        <div class="hdr-sub">{html.escape(date_text)}</div>
      </div>
    </div>
  </div>
  <div class="sections">{section}</div>
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{html.escape(date_text)}</span>
  </div>
</div>
</body></html>"""
    return _render_html_image(html_text, out_dir / filename, f"_{filename}.html", force=force)


def _build_images(target_date: str, *, songs_top_n: int, force: bool) -> dict[str, object]:
    album_covers = generate_albums_image.load_covers()
    album_track_map = generate_albums_image.load_album_track_map()
    album_today, album_yest, album_week = generate_albums_image.load_history(target_date)
    if not album_today:
        raise ValueError(f"No album streams data for {target_date}")

    album_rows = [
        row for row in generate_albums_image.build_album_rows(
            album_today,
            album_yest,
            album_week,
            album_track_map,
            album_covers,
            merge_eras=False,
        )
        if _is_real_album_row(row)
    ]
    era_rows = [
        row for row in generate_albums_image.build_album_rows(
            album_today,
            album_yest,
            album_week,
            album_track_map,
            album_covers,
            merge_eras=True,
        )
        if _is_real_album_row(row)
    ]
    header_album = album_rows[0].get("album") if album_rows else ""

    song_db = generate_streams_image.load_song_db()
    song_cover_map = generate_streams_image.load_covers()
    song_track_album_map = generate_streams_image.load_track_album_map()
    song_today, song_yest, song_week = generate_streams_image.load_history(target_date)
    song_rows = generate_streams_image.build_top_n(song_today, song_yest, song_week, song_db, songs_top_n)
    album_updates = [
        {
            "album": row["album"],
            "daily_streams": int(row.get("daily_streams") or 0),
            "image": _album_update_image(row["album"], target_date, force=force),
        }
        for row in album_rows
    ]

    return {
        "albums": _section_image(
            target_date=target_date,
            rows=album_rows,
            title="Top Albums",
            entity_label="Album",
            kind="album",
            filename="throwback_albums.png",
            header_album=header_album,
            force=force,
            album_cache=generate_albums_image.prefetch_covers(album_rows),
        ),
        "songs": _section_image(
            target_date=target_date,
            rows=song_rows,
            title="Top Songs",
            entity_label="Song",
            kind="song",
            filename="throwback_songs.png",
            header_album=header_album,
            force=force,
            song_cache=generate_streams_image.prefetch_images(song_rows, song_cover_map, song_track_album_map),
            song_cover_map=song_cover_map,
            song_track_album_map=song_track_album_map,
        ),
        "eras": _section_image(
            target_date=target_date,
            rows=era_rows,
            title="Top Eras",
            entity_label="Era",
            kind="album",
            filename="throwback_eras.png",
            header_album=header_album,
            force=force,
            album_cache=generate_albums_image.prefetch_covers(era_rows),
        ),
        "album_updates": album_updates,
    }


def build_threads(
    *,
    target_date: str,
    action: str,
    event: str,
    label: str,
    images: dict[str, object],
    songs_top_n: int,
) -> tuple[list[tuple[str, Path]], list[tuple[str, Path]]]:
    date_text = _date_fmt(target_date)
    start_label = _sentence_start_label(label)
    event_context = f"following the {_event_noun(action)} of {event}"
    opener = (
        f"{BACK} Taylor Swift's top albums {label} ago, {event_context}.\n\n"
        f"Spotify streams on {date_text}."
    )
    tsm_posts: list[tuple[str, Path]] = [
        (opener, images["albums"]),
        (
            f"{BACK} Taylor Swift's top {songs_top_n} songs {label} ago, {event_context}.\n\n"
            f"Spotify streams on {date_text}.",
            images["songs"],
        ),
        (
            f"{BACK} Taylor Swift's top eras {label} ago, {event_context}.\n\n"
            f"Spotify streams on {date_text}.",
            images["eras"],
        ),
    ]
    swifties_posts: list[tuple[str, Path]] = []
    for item in images.get("album_updates", []):
        swifties_posts.append((
            f"{BACK} {start_label}, {item['album']} received {_fmt_streams(item.get('daily_streams'))} "
            f"streams on Spotify.\n\n"
            f"Full album update from {date_text}.",
            item["image"],
        ))
    return tsm_posts, swifties_posts


def main() -> int:
    parser = argparse.ArgumentParser(description="Post a Taylor Swift throwback streams thread.")
    parser.add_argument("date", nargs="?", help="Throwback stats date YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--action", choices=("announced", "released"), required=True)
    parser.add_argument("--event", required=True, help="What Taylor Swift announced/released.")
    parser.add_argument("--label", help="Override relative label, e.g. 'one year' or 'three weeks'.")
    parser.add_argument("--top-n", type=int, default=20, help="Number of songs in the top songs card.")
    parser.add_argument("--force", action="store_true", help="Regenerate throwback images even if they already exist.")
    parser.add_argument("--no-post", action="store_true")
    args = parser.parse_args()

    target_date = args.date or str(date.today() - timedelta(days=1))
    date.fromisoformat(target_date)
    songs_top_n = max(1, int(args.top_n))
    label = (args.label or _relative_label(target_date)).strip()

    day_dir = update_streams_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    legacy_lock = day_dir / "throwback_posted.lock"
    tsm_lock = day_dir / "throwback_tsmuseum13_posted.lock"
    swifties_lock = day_dir / "throwback_swiftiescharts_albums_posted.lock"
    if legacy_lock.exists() and not args.no_post:
        print(f"[throwback] Already posted for {target_date}, skipping.")
        return 0

    print(f"[throwback] Generating thread images for {target_date}...")
    images = _build_images(target_date, songs_top_n=songs_top_n, force=args.force)
    tsm_posts, swifties_posts = build_threads(
        target_date=target_date,
        action=args.action,
        event=args.event.strip(),
        label=label,
        images=images,
        songs_top_n=songs_top_n,
    )

    print(f"[throwback] @tsmuseum13 thread: {len(tsm_posts)} post(s)")
    for idx, (tweet, image_path) in enumerate(tsm_posts, 1):
        print(f"[throwback] @tsmuseum13 post {idx}/{len(tsm_posts)} ({len(tweet)} chars):\n{tweet}")
        print(f"[throwback] Image: {image_path}")
    print(f"[throwback] @swiftiescharts album updates: {len(swifties_posts)} post(s)")
    for idx, (tweet, image_path) in enumerate(swifties_posts, 1):
        print(f"[throwback] @swiftiescharts post {idx}/{len(swifties_posts)} ({len(tweet)} chars):\n{tweet}")
        print(f"[throwback] Image: {image_path}")

    if args.no_post:
        print("[throwback] Twitter post skipped (--no-post).")
        return 0

    if tsm_posts and not TSM_TWITTER_SESSION.exists():
        print(f"ERROR: @tsmuseum13 Twitter session not found at {TSM_TWITTER_SESSION}")
        return 1
    if swifties_posts and not SWIFTIES_TWITTER_SESSION.exists():
        print(f"ERROR: @swiftiescharts Twitter session not found at {SWIFTIES_TWITTER_SESSION}")
        return 1

    if tsm_lock.exists():
        print(f"[throwback] @tsmuseum13 already posted for {target_date}, skipping.")
    else:
        if tsm_posts and not post_image_thread(tsm_posts, TSM_TWITTER_SESSION):
            print("[throwback] Failed to post @tsmuseum13 thread.")
            return 1
        tsm_lock.touch()

    if swifties_lock.exists():
        print(f"[throwback] @swiftiescharts album updates already posted for {target_date}, skipping.")
    else:
        if swifties_posts and not post_image_thread(swifties_posts, SWIFTIES_TWITTER_SESSION):
            print("[throwback] Failed to post @swiftiescharts album updates.")
            return 1
        swifties_lock.touch()

    print(f"[throwback] Posted throwback parts for {target_date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
