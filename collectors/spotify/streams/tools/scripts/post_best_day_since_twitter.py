#!/usr/bin/env python3
"""
Post the top "best day since" songs to @swiftiescharts with spotlight images.

Usage:
  python post_best_day_since_twitter.py 2026-05-07
  python post_best_day_since_twitter.py 2026-05-07 --no-post
  python post_best_day_since_twitter.py 2026-05-07 --limit 3
"""
from __future__ import annotations

import argparse
import html
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent          # streams/tools/scripts/
ROOT = SCRIPT_DIR.parents[1]                          # streams/
REPO_ROOT = SCRIPT_DIR.parents[4]                     # repo root
COLLECTORS_ROOT = REPO_ROOT / "collectors"
DB_ROOT = REPO_ROOT / "db"
TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"
INDEPENDENT_BEST_DAY_MIN_DAYS = 61
ALBUM_BEST_DAY_MIN_DAYS = 61

sys.path.insert(0, str(COLLECTORS_ROOT))              # collectors/
sys.path.insert(0, str(ROOT))                         # collectors/spotify/streams/
sys.path.insert(0, str(ROOT.parent))                  # collectors/spotify/

from comp.song_card import render_song_card, slugify, write_song_card_png  # noqa: E402
from comp.discography import build_cover_map, _norm  # noqa: E402
from comp.tables_image import build_table_html, render_html_to_png, url_to_data_uri  # noqa: E402
from comp.fmt import fmt_streams, fmt_pct, pct_cls, get_pct  # noqa: E402
from core.twitter import post_with_image  # noqa: E402
from core.album_emoji import album_emoji  # noqa: E402
from core.data_paths import update_streams_dir  # noqa: E402
import best_day_since  # noqa: E402
import spotlight  # noqa: E402


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{int(value):,}"


def _fmt_signed_int(value: int | None) -> str:
    if value is None:
        return "?"
    return f"+{int(value):,}"


def _fmt_pct(current: int | None, previous: int | None) -> str:
    if current is None or previous is None or previous <= 0:
        return "n/a"
    pct = (current - previous) / previous * 100
    return f"{pct:+.1f}%"


def _find_all_rows(target_date: str, *, min_days: int) -> list[dict]:
    tracks = best_day_since.load_tracks(include_extras=False)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    history = best_day_since.load_history()
    target = date.fromisoformat(target_date)

    rows: list[dict] = []
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
        if row and row.get("kind") == "since" and best_day_since.passes_filters(row, min_days=min_days):
            rows.append(row)
    return rows


def _pick_rows(target_date: str, *, limit: int, min_days: int, exclude_ids: set[str] | None = None) -> list[dict]:
    rows = _find_all_rows(target_date, min_days=min_days)
    if exclude_ids:
        rows = [row for row in rows if row["track_id"] not in exclude_ids]
    rows.sort(key=best_day_since.sort_key, reverse=True)
    return rows[:limit]


def _post_single_track_early(track_id: str, target_date: str, *, min_days: int, no_post: bool) -> str:
    """Compute and post one track's best-day-since record immediately, if it
    already qualifies, without waiting for the rest of the day's collection.

    Returns "posted", "skipped" (doesn't qualify yet / missing data), or "error".
    """
    row = best_day_since.best_day_since_for_track(track_id, target_date, min_days=min_days, combined=True)
    if not row:
        return "skipped"

    tracks_by_id = {track["track_id"]: track for track in spotlight.load_all_tracks()}
    track = tracks_by_id.get(track_id)
    if not track:
        return "skipped"

    track_ids = row.get("combined_track_ids") or [track_id]
    total_today, total_yesterday, daily_today, daily_yesterday, _daily_last_week = (
        spotlight.load_history_for_tracks(track_ids, target_date)
    )
    if total_today is None or total_yesterday is None or daily_today is None or daily_yesterday is None or daily_yesterday <= 0:
        return "skipped"

    covers = spotlight.load_covers()
    cover_url = spotlight.get_cover_url(track, covers)
    image_path = _generate_best_day_since_image(
        row=row,
        track=track,
        total_today=total_today,
        daily_yesterday=daily_yesterday,
        cover_url=cover_url,
        target_date=target_date,
    )
    tweet = _build_tweet(row, daily_yesterday)
    print(f"[best_day_since_early] Tweet ({len(tweet)} chars):\n{tweet}")
    print(f"[best_day_since_early] Image: {image_path}")

    if no_post:
        return "posted"

    if not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return "error"
    if not post_with_image(tweet, image_path, TWITTER_SESSION):
        print(f"[best_day_since_early] Failed to post {row['title']}.")
        return "error"
    return "posted"


def _pick_album_rows(target_date: str, *, limit: int, min_days: int) -> list[dict]:
    tracks = best_day_since.load_tracks(include_extras=False)
    history = best_day_since.load_history()
    target = date.fromisoformat(target_date)
    by_album = best_day_since.load_album_track_ids(tracks)

    rows: list[dict] = []
    for album, track_ids in by_album.items():
        if len(track_ids) < 2:
            continue
        row = best_day_since.compute_album_best_day_since(album, track_ids, history, target)
        if row and row.get("kind") == "since" and best_day_since.passes_filters(row, min_days=min_days):
            rows.append(row)

    rows.sort(key=best_day_since.sort_key, reverse=True)
    return rows[:limit]


def _build_tweet(row: dict, daily_yesterday: int | None) -> str:
    emoji = album_emoji(row.get("album"))
    title = row["title"]
    track_id = row["track_id"]
    label = best_day_since.row_label(row)
    daily = int(row["daily_streams"])
    pct = _fmt_pct(daily, daily_yesterday)
    song_url = f"https://thetsmuseum.app/songs/{track_id}"
    return (
        f'{emoji} "{title}" earned its {label} with {_fmt_int(daily)} streams [{pct}].\n\n'
        f"See full track's history here : {song_url}"
    )


def _build_album_tweet(row: dict, daily_yesterday: int | None) -> str:
    emoji = album_emoji(row.get("album"))
    album = row["album"]
    label = best_day_since.row_label(row)
    daily = int(row["daily_streams"])
    pct = _fmt_pct(daily, daily_yesterday)
    album_url = f"https://thetsmuseum.app/albums/{quote(album)}"
    return (
        f'{emoji} "{album}" earned its {label} with {_fmt_int(daily)} streams [{pct}].\n\n'
        f"See full album's history here : {album_url}"
    )


def _recap_row_html(index: int, row: dict, track: dict, cover_url: str, daily: int, daily_pct: float | None, weekly_pct: float | None) -> str:
    art_html = f'<img class="art" src="{cover_url}" />' if cover_url else '<div class="art-ph"></div>'
    row_class = "data-row row-gold" if index == 1 else ("data-row row-odd" if index % 2 else "data-row")
    since_txt = best_day_since.format_long_date(row["best_day_since"])
    title = html.escape(track.get("title") or row["title"])
    subtitle = html.escape(track.get("album") or row.get("album") or "")
    return f"""<div class="{row_class}">
  <div class="col-rank">#{index}</div>
  <div class="col-entity">
    {art_html}
    <div class="entity-info">
      <div class="entity-name">{title}</div>
      <div class="entity-sub">{subtitle}</div>
    </div>
  </div>
  <div class="col-num">{fmt_streams(daily)}</div>
  <div class="col-num {pct_cls(daily_pct)}">{fmt_pct(daily_pct)}</div>
  <div class="col-num {pct_cls(weekly_pct)}">{fmt_pct(weekly_pct)}</div>
  <div class="col-num">{html.escape(since_txt)}</div>
</div>"""


def _generate_recap_image(
    *,
    rows: list[dict],
    target_date: str,
    tracks_by_id: dict,
    covers: dict,
) -> Path:
    from datetime import datetime

    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows_html = []
    for index, row in enumerate(rows, 1):
        track = tracks_by_id.get(row["track_id"])
        if not track:
            continue
        track_ids = row.get("combined_track_ids") or [row["track_id"]]
        _total_today, _total_yesterday, daily_today, daily_yesterday, daily_last_week = (
            spotlight.load_history_for_tracks(track_ids, target_date)
        )
        if daily_today is None:
            continue
        cover_url = url_to_data_uri(spotlight.get_cover_url(track, covers))
        daily_pct = get_pct(daily_today, daily_yesterday)
        weekly_pct = get_pct(daily_today, daily_last_week)
        rows_html.append(_recap_row_html(index, row, track, cover_url, daily_today, daily_pct, weekly_pct))

    html_text = build_table_html(
        title="Best Day Since — Full Recap",
        subtitle=f"Every song that hit a best-day-since record - {date_text}",
        col_heads=[
            ("Pos", False),
            ("Track", False),
            ("Daily", True),
            ("Vs Day", True),
            ("Vs Week", True),
            ("Best Since", True),
        ],
        grid_cols="48px minmax(220px,1fr) 100px 76px 76px 130px",
        rows_html="\n".join(rows_html),
        handle="@swiftiescharts",
        date_str=date_text,
        headers_dir=SCRIPT_DIR.parent / "headers",
        body_width=960,
        art_size=48,
    )
    out_dir = _day_dir(target_date) / "best_day_since"
    out_path = out_dir / f"best_day_since_recap_{target_date}.png"
    tmp_path = out_dir / f"_best_day_since_recap_{target_date}.html"
    return render_html_to_png(html_text, out_path, tmp_path, width=960)


def _build_recap_tweet(rows: list[dict], target_date: str) -> str:
    from datetime import datetime

    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    count = len(rows)
    plural = "s" if count != 1 else ""
    return f"📊 {count} song{plural} hit a best day since record on {date_text}. Full recap below."


def _day_dir(target_date: str) -> Path:
    return update_streams_dir(target_date)


def _badge_class(text: str) -> str:
    if text in {"+0.0%", "0.0%"}:
        return "flat"
    if text.startswith("+"):
        return "up"
    if text.startswith("-"):
        return "down"
    return "flat"


def _generate_best_day_since_image(
    *,
    row: dict,
    track: dict,
    total_today: int,
    daily_yesterday: int | None,
    cover_url: str,
    target_date: str,
) -> Path:
    from datetime import datetime

    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    daily = int(row["daily_streams"])
    pct = _fmt_pct(daily, daily_yesterday)
    label = best_day_since.row_label(row)
    html = render_song_card(
        title=track.get("title") or row["title"],
        eyebrow="Spotify Streams",
        subtitle=label,
        stats=[
            {"label": "Daily Streams", "value": _fmt_signed_int(daily), "badge": pct, "badge_class": _badge_class(pct)},
            {"label": "Total Streams", "value": _fmt_int(total_today), "badge": "Since release", "badge_class": "flat"},
        ],
        cover_url=cover_url,
        footer_left="@swiftiescharts",
        footer_right=date_text,
        extra=track.get("album") or row.get("album") or "",
        best_since=True,
        combined_versions=bool(row.get("combined")),
    )
    out_dir = _day_dir(target_date) / "best_day_since"
    out_path = out_dir / f"best_day_since_{slugify(track.get('title') or row['title'])}_{target_date}.png"
    tmp_path = out_dir / f"_best_day_since_{row['track_id']}.html"
    return write_song_card_png(html, out_path, tmp_path)


def _generate_album_best_day_since_image(
    *,
    row: dict,
    total_today: int,
    daily_yesterday: int | None,
    cover_url: str,
    target_date: str,
) -> Path:
    from datetime import datetime

    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    album = row["album"]
    daily = int(row["daily_streams"])
    pct = _fmt_pct(daily, daily_yesterday)
    label = best_day_since.row_label(row)
    html = render_song_card(
        title=album,
        eyebrow="Spotify Streams",
        subtitle=label,
        stats=[
            {"label": "Daily Streams", "value": _fmt_signed_int(daily), "badge": pct, "badge_class": _badge_class(pct)},
            {"label": "Total Streams", "value": _fmt_int(total_today), "badge": "Since release", "badge_class": "flat"},
        ],
        cover_url=cover_url,
        footer_left="@swiftiescharts",
        footer_right=date_text,
        extra="",
        best_since=True,
        badge_text=f"{album} - {date_text}",
    )
    out_dir = _day_dir(target_date) / "best_day_since"
    out_path = out_dir / f"best_day_since_album_{slugify(album)}_{target_date}.png"
    tmp_path = out_dir / f"_best_day_since_album_{slugify(album)}.html"
    return write_song_card_png(html, out_path, tmp_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Post top best-day-since songs to @swiftiescharts.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--no-post", action="store_true", help="Generate images but skip Twitter posts.")
    parser.add_argument("--force", action="store_true", help="Post again even if best_day_since_posted.lock exists.")
    parser.add_argument("--limit", type=int, default=5, help="Number of individual song posts (default: 5).")
    parser.add_argument(
        "--min-days",
        type=int,
        default=INDEPENDENT_BEST_DAY_MIN_DAYS,
        help=f"Minimum days for best-day-since (default: {INDEPENDENT_BEST_DAY_MIN_DAYS}).",
    )
    parser.add_argument(
        "--post-spacing-seconds",
        type=int,
        default=0,
        help="Extra seconds to wait between Twitter posts; core.twitter enforces account spacing.",
    )
    parser.add_argument("--album-limit", type=int, default=1, help="Number of album posts to add (default: 1).")
    parser.add_argument(
        "--album-min-days",
        type=int,
        default=ALBUM_BEST_DAY_MIN_DAYS,
        help=f"Minimum days for album best-day-since (default: {ALBUM_BEST_DAY_MIN_DAYS}).",
    )
    parser.add_argument("--no-albums", action="store_true", help="Skip album best-day-since posts.")
    parser.add_argument("--no-recap", action="store_true", help="Skip the full best-day-since recap table post.")
    parser.add_argument(
        "--only-track",
        help=(
            "Post best-day-since for a single track id immediately, bypassing the normal "
            "batch/lock flow. Used to post records early during collection, before the rest "
            "of the day's tracks are done. Exits 0 if posted, 3 if it doesn't qualify yet."
        ),
    )
    parser.add_argument(
        "--exclude-tracks",
        default="",
        help="Comma-separated track ids already posted early (e.g. via --only-track); skip them here.",
    )
    args = parser.parse_args()

    target_date = args.date or str(date.today() - timedelta(days=1))

    if args.only_track:
        result = _post_single_track_early(
            args.only_track, target_date, min_days=args.min_days, no_post=args.no_post
        )
        if result == "posted":
            sys.exit(0)
        if result == "skipped":
            sys.exit(3)
        sys.exit(1)

    limit = max(0, int(args.limit))
    if limit == 0:
        print("[best_day_since_post] Limit is 0, nothing to do.")
        return

    day_dir = _day_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    lock = day_dir / "best_day_since_posted.lock"
    album_lock = day_dir / "best_day_since_album_posted.lock"
    recap_lock = day_dir / "best_day_since_recap_posted.lock"

    track_locked = lock.exists() and not args.no_post and not args.force
    if track_locked:
        print(f"[best_day_since_post] Already posted for {target_date}, skipping track posts.")
    if lock.exists() and args.no_post:
        print(f"[best_day_since_post] Already posted for {target_date}, regenerating only (--no-post).")

    if not args.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    exclude_ids = {t.strip() for t in args.exclude_tracks.split(",") if t.strip()}
    remaining_limit = max(0, limit - len(exclude_ids))
    rows = (
        []
        if track_locked
        else _pick_rows(target_date, limit=remaining_limit, min_days=args.min_days, exclude_ids=exclude_ids)
    )
    if not rows:
        print(f"[best_day_since_post] No best-day-since songs found for {target_date}.")

    tracks_by_id = {track["track_id"]: track for track in spotlight.load_all_tracks()}
    covers = spotlight.load_covers()

    posted_count = 0
    for index, row in enumerate(rows, 1):
        track = tracks_by_id.get(row["track_id"])
        if not track:
            print(f"[best_day_since_post] Track missing in spotlight DB: {row['title']} [{row['track_id']}]")
            continue

        track_ids = row.get("combined_track_ids") or [row["track_id"]]
        total_today, total_yesterday, daily_today, daily_yesterday, _daily_last_week = (
            spotlight.load_history_for_tracks(track_ids, target_date)
        )
        if total_today is None:
            print(f"[best_day_since_post] Missing total streams for {row['title']} on {target_date}; skipping.")
            continue
        if total_yesterday is None or daily_today is None or daily_yesterday is None or daily_yesterday <= 0:
            print(
                f"[best_day_since_post] Incomplete comparison history for {row['title']} "
                f"on {target_date}; skipping."
            )
            continue

        cover_url = spotlight.get_cover_url(track, covers)
        image_path = _generate_best_day_since_image(
            row=row,
            track=track,
            total_today=total_today,
            daily_yesterday=daily_yesterday,
            cover_url=cover_url,
            target_date=target_date,
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

    if posted_count and not args.no_post:
        lock.touch()
    if rows:
        print(f"[best_day_since_post] Posted {posted_count} song(s) for {target_date}.")

    # Album best-day-since is its own slot: it never competes with track
    # posts for the --limit spots above.
    album_locked = album_lock.exists() and not args.no_post and not args.force
    if album_locked:
        print(f"[best_day_since_post] Album already posted for {target_date}, skipping.")
    elif args.no_albums or args.album_limit <= 0:
        pass
    else:
        album_rows = _pick_album_rows(target_date, limit=args.album_limit, min_days=args.album_min_days)
        if not album_rows:
            print(f"[best_day_since_post] No album best-day-since found for {target_date}.")
        else:
            album_covers = build_cover_map(DB_ROOT / "discography" / "covers.json")
            album_posted_count = 0
            for index, row in enumerate(album_rows, 1):
                track_ids = row.get("track_ids") or []
                total_today, total_yesterday, daily_today, daily_yesterday, _daily_last_week = (
                    spotlight.load_history_for_tracks(track_ids, target_date)
                )
                if total_today is None or total_yesterday is None or daily_today is None or daily_yesterday is None or daily_yesterday <= 0:
                    print(f"[best_day_since_post] Incomplete comparison history for album {row['album']} on {target_date}; skipping.")
                    continue

                cover_url = album_covers.get(_norm(row["album"]), "")
                image_path = _generate_album_best_day_since_image(
                    row=row,
                    total_today=total_today,
                    daily_yesterday=daily_yesterday,
                    cover_url=cover_url,
                    target_date=target_date,
                )

                tweet = _build_album_tweet(row, daily_yesterday)
                print(f"[best_day_since_post] Album tweet {index}/{len(album_rows)} ({len(tweet)} chars):\n{tweet}")
                print(f"[best_day_since_post] Image: {image_path}")

                if args.no_post:
                    continue

                if not post_with_image(tweet, image_path, TWITTER_SESSION):
                    print(f"[best_day_since_post] Failed to post album {row['album']}.")
                    sys.exit(1)
                album_posted_count += 1
                if index < len(album_rows) and args.post_spacing_seconds > 0:
                    print(f"[best_day_since_post] Waiting {args.post_spacing_seconds}s before next post...")
                    time.sleep(args.post_spacing_seconds)

            if album_posted_count and not args.no_post:
                album_lock.touch()
            if album_rows:
                print(f"[best_day_since_post] Posted {album_posted_count} album(s) for {target_date}.")

    # Full recap table: every song that hit a best-day-since record today
    # (not just the ones with individual posts), oldest record first.
    recap_locked = recap_lock.exists() and not args.no_post and not args.force
    if recap_locked:
        print(f"[best_day_since_post] Recap already posted for {target_date}, skipping.")
    elif args.no_recap:
        pass
    else:
        recap_rows = _find_all_rows(target_date, min_days=args.min_days)
        recap_rows.sort(key=lambda row: row["best_day_since"])
        if not recap_rows:
            print(f"[best_day_since_post] No best-day-since songs found for recap on {target_date}.")
        else:
            image_path = _generate_recap_image(
                rows=recap_rows,
                target_date=target_date,
                tracks_by_id=tracks_by_id,
                covers=covers,
            )
            tweet = _build_recap_tweet(recap_rows, target_date)
            print(f"[best_day_since_post] Recap tweet ({len(tweet)} chars):\n{tweet}")
            print(f"[best_day_since_post] Image: {image_path}")

            if not args.no_post:
                if not post_with_image(tweet, image_path, TWITTER_SESSION):
                    print("[best_day_since_post] Failed to post recap.")
                    sys.exit(1)
                recap_lock.touch()
                print(f"[best_day_since_post] Posted recap for {target_date}.")

    if args.no_post:
        print("[best_day_since_post] Twitter posts skipped (--no-post).")


if __name__ == "__main__":
    main()
