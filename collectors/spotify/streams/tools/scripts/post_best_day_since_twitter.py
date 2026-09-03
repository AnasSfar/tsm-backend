#!/usr/bin/env python3
"""
Post the top "best day since" songs to @swiftiescharts with song card images.

Usage:
  python post_best_day_since_twitter.py 2026-05-07
  python post_best_day_since_twitter.py 2026-05-07 --no-post
  python post_best_day_since_twitter.py 2026-05-07 --limit 3
"""
from __future__ import annotations

import argparse
import csv
import json
import html
import re
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # streams/tools/scripts/
ROOT = SCRIPT_DIR.parents[1]                          # streams/
REPO_ROOT = SCRIPT_DIR.parents[4]                     # repo root
COLLECTORS_ROOT = REPO_ROOT / "collectors"
TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"
DB_ROOT = REPO_ROOT / "db"
DISCOGRAPHY_DIR = DB_ROOT / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
SONGS_JSON = DISCOGRAPHY_DIR / "songs.json"
MISC_JSON = DISCOGRAPHY_DIR / "misc.json"
FEATURES_JSON = DISCOGRAPHY_DIR / "features.json"
COVERS_PATH = DISCOGRAPHY_DIR / "covers.json"
POST_COLLECTION_BEST_DAY_MIN_DAYS = 30
RECAP_BEST_DAY_MIN_DAYS = 30
RECAP_ROWS_PER_IMAGE_TARGET = 20
RECAP_MAX_IMAGES = 4
# Fixed masthead header for the best-day-since recap: the "all eras" strip built
# from the official Eras Tour site portraits (assets/eras-tour-hero/all-eras.jpg,
# one panel per era in album order). This folder holds only that one image so
# the recap always uses it - no album theming, no random pool pick.
RECAP_HEADERS_DIR = SCRIPT_DIR.parent / "headers" / "best_day_recap"
# Dedicated per-era "best day recap" card: when at least ERA_RECAP_MIN_SONGS
# songs of one era (Red + Red TV etc. count together) hit a best-day-since
# record AND clear the individual post gate that day, post one extra recap card
# for just that era, before the era's album update card. Songs stay in the
# global recap too. Once an era recap posts, that era's individual best-day song
# cards are suppressed for the day (a biggest-day-of-the-year card still posts).
ERA_RECAP_MIN_SONGS = 5
# Early lane (during collection): at most this many era recaps, tracked with
# their own lock dir, never spending the individual song early-post quota.
EARLY_ERA_RECAP_MAX_POSTS = 2
MAX_BEST_DAY_SONG_POSTS_PER_ALBUM = 3
# Finalize (post-collection) batch: 3 standard slots, up to 5 total. Slots 4-5
# are reserved for exceptional records (a >90-day gap, or a score clearing the
# early lane's exceptional bar). A >90-day priority row and a biggest-day-of-
# the-year row still bypass the cap entirely.
POST_COLLECTION_STANDARD_SONG_POSTS = 3
POST_COLLECTION_MAX_SONG_POSTS = 5
MIN_SONG_DAILY_STREAMS_TO_POST = 80_000
EARLY_BEST_DAY_MIN_SCORE = 58.0
EARLY_BEST_DAY_STANDARD_MAX_POSTS = 3
EARLY_BEST_DAY_EXCEPTIONAL_MAX_POSTS = 5
EARLY_BEST_DAY_EXCEPTIONAL_MIN_SCORE = 90.0
EARLY_BEST_DAY_MAX_POSTS_PER_ERA = 1
MAX_BEST_DAY_GROWER_POSTS = 3
ALWAYS_POST_BEST_DAY_SINCE_AFTER_DAYS = 60
PRIORITY_BEST_DAY_SINCE_MIN_DAYS = 90

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


sys.path.insert(0, str(COLLECTORS_ROOT))              # collectors/
sys.path.insert(0, str(ROOT))                         # collectors/spotify/streams/
sys.path.insert(0, str(ROOT.parent))                  # collectors/spotify/

from comp.song_card import image_data_uri  # noqa: E402
from comp.song_card_chart_sheet import format_change_html, render_chart_sheet_card, slugify, write_chart_sheet_card_png  # noqa: E402
from comp.tables_image import build_table_html, masthead_theme_for_date, render_html_to_png, url_to_data_uri  # noqa: E402
from comp.fmt import fmt_streams, fmt_pct, pct_cls, get_pct  # noqa: E402
from core.twitter import post_image_thread, post_with_image  # noqa: E402
from core.data_paths import update_streams_dir  # noqa: E402
from twitter.text import best_day_since_era_recap_tweet, best_day_since_recap_tweet, best_day_since_tweet  # noqa: E402
from twitter.albums import album_emoji  # noqa: E402
from twitter.text import best_day_grower_tweet  # noqa: E402
import best_day_since  # noqa: E402
import score_best_day_since  # noqa: E402
import generate_album_update_image  # noqa: E402


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{int(value):,}"


def _fmt_signed_int(value: int | None) -> str:
    if value is None:
        return "?"
    return f"+{int(value):,}"


def _fmt_release_date(raw: str | None) -> str | None:
    """Track release_date (catalog "YYYY-MM-DD") -> "DD/MM/YYYY", or None if
    missing/unparseable so the caller can fall back to something else."""
    if not raw:
        return None
    try:
        d = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
    return d.strftime("%d/%m/%Y")


def _fmt_pct(current: int | None, previous: int | None) -> str:
    if current is None or previous is None or previous <= 0:
        return "n/a"
    pct = (current - previous) / previous * 100
    return f"{pct:+.1f}%"


def _short_month_day(day: date) -> str:
    return f"{day.month}/{day.day}"


def _hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    raw = (value or "").strip().lstrip("#")
    if len(raw) != 6:
        return None
    try:
        return int(raw[:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return None


def _luma_from_hex(value: str) -> float:
    rgb = _hex_to_rgb(value) or (29, 185, 84)
    r, g, b = rgb
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255




def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _load_discography_sections() -> list[dict]:
    sections: list[dict] = []
    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda path: path.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            album_name = payload.get("album", "")
            for section in payload.get("sections", []):
                if not isinstance(section, dict):
                    continue
                item = dict(section)
                if not item.get("album"):
                    item["album"] = album_name
                sections.append(item)

    for extra_path in (SONGS_JSON, FEATURES_JSON, MISC_JSON):
        if not extra_path.exists():
            continue
        try:
            payload = json.loads(extra_path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, list):
                sections.extend(payload)
        except Exception:
            pass

    return sections


def load_all_tracks() -> list[dict]:
    tracks: list[dict] = []
    seen: set[str] = set()
    for section in _load_discography_sections():
        if not isinstance(section, dict):
            continue
        for item in section.get("tracks", []):
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or item.get("spotify_url") or "").strip()
            track_id = best_day_since.extract_track_id(url)
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)
            artists = item.get("artists") or []
            tracks.append({
                "track_id": track_id,
                "title": (item.get("title") or "").strip(),
                "artist": item.get("primary_artist") or (artists[0] if artists else "Taylor Swift"),
                "spotify_url": f"https://open.spotify.com/track/{track_id}",
                "image_url": (item.get("image_url") or "").strip(),
                "type": item.get("type", "album"),
                "single_image": (item.get("single_image") or "").strip(),
                "song_family": item.get("song_family", ""),
                "album": section.get("album", ""),
            })
    return tracks


def _song_family_single_image_map() -> dict[str, str]:
    family_map: dict[str, str] = {}
    for section in _load_discography_sections():
        if not isinstance(section, dict):
            continue
        for item in section.get("tracks", []):
            if not isinstance(item, dict):
                continue
            song_family = str(item.get("song_family") or "").strip()
            single_image = str(item.get("single_image") or "").strip()
            if song_family and single_image.startswith("http"):
                family_map[song_family] = single_image
    return family_map


def load_covers() -> dict[str, str]:
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


def get_cover_url(track: dict, covers: dict[str, str]) -> str:
    track_type = track.get("type", "album")
    track_image = str(track.get("image_url") or "").strip()
    single_image = str(track.get("single_image") or "").strip()
    song_family = str(track.get("song_family") or "").strip()
    album = str(track.get("album") or "").strip()

    if track_type in {"standalone", "alternate_version"}:
        family_image = _song_family_single_image_map().get(song_family, "") if song_family else ""
        if family_image.startswith("http"):
            return family_image
        if single_image.startswith("http"):
            return single_image
        if track_image.startswith("http"):
            return track_image
        return ""

    if track_image.startswith("http"):
        return track_image
    if album:
        cover = covers.get(_norm(album), "")
        if cover.startswith("http"):
            return cover
    return ""


def load_history_for_tracks(track_ids: list[str], stats_date: str) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    target = date.fromisoformat(stats_date)
    dates = {
        target.isoformat(): "today",
        (target - timedelta(days=1)).isoformat(): "y1",
        (target - timedelta(days=7)).isoformat(): "w1",
    }
    totals: dict[str, int] = {}
    dailies: dict[str, int] = {}
    wanted = set(track_ids)

    if not best_day_since.HISTORY_PATH.exists():
        return None, None, None, None, None

    with best_day_since.HISTORY_PATH.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if row.get("track_id") not in wanted:
                continue
            day = (row.get("date") or "").strip()
            if day not in dates:
                continue
            key = dates[day]
            try:
                totals[key] = totals.get(key, 0) + int(row.get("streams") or 0)
            except ValueError:
                pass
            try:
                daily_raw = (row.get("daily_streams") or "").strip()
                if daily_raw:
                    dailies[key] = dailies.get(key, 0) + int(daily_raw)
            except ValueError:
                pass

    return totals.get("today"), totals.get("y1"), dailies.get("today"), dailies.get("y1"), dailies.get("w1")

def _holiday_collection_out_of_season(album: str | None, target_date: str) -> bool:
    """Owner rule: a Holiday Collection song never posts a best-day-since card
    outside the album's Christmas window (Nov 25 - Jan 7). The seasonal block
    beats every other rule here, including biggest-day-of-the-year."""
    if not album or not generate_album_update_image.is_holiday_collection_album(album):
        return False
    return not generate_album_update_image.is_holiday_collection_season(target_date)


def _find_all_rows(target_date: str, *, min_days: int) -> list[dict]:
    tracks = best_day_since.load_tracks(include_extras=False)
    history = best_day_since.load_history()
    target = date.fromisoformat(target_date)

    rows: list[dict] = []
    for track_id, track in tracks.items():
        if _holiday_collection_out_of_season(track.album, target_date):
            continue
        row = best_day_since.compute_best_day_since(track, history.get(track_id) or [], target)
        if (
            not row
            or row.get("kind") != "since"
            or not (row.get("is_biggest_day_of_year") or best_day_since.passes_filters(row, min_days=min_days))
        ):
            continue
        # A dedicated era recap card already covered this song's era today: no
        # individual song card for it, unless it is an unconditional biggest
        # day of the year (that card always posts - owner decision).
        if not _is_unconditional_best_day(row) and _era_recap_posted_for(track.album, target_date):
            print(
                f"[best_day_since_post] Skipping {row['title']}: {track.album} era recap "
                f"already posted for {target_date}."
            )
            continue
        if _is_repeat_of_previous_day(row, target_date, min_days=min_days):
            print(
                f"[best_day_since_post] {row['title']} repeated the same best-day-since "
                f"({row['best_day_since']}); using grower tweet format."
            )
            row["_grower_repeat"] = True
        rows.append(row)
    return rows


def _find_recap_rows(target_date: str) -> list[dict]:
    """Every exact best-day record for the day, independent of posting gates."""
    tracks = best_day_since.load_tracks(include_extras=False)
    history = best_day_since.load_history()
    target = date.fromisoformat(target_date)

    rows: list[dict] = []
    for track_id, track in tracks.items():
        if _holiday_collection_out_of_season(track.album, target_date):
            continue
        row = best_day_since.compute_best_day_since(track, history.get(track_id) or [], target)
        if not row:
            continue
        if row.get("kind") == "best_ever" or best_day_since.passes_filters(row, min_days=RECAP_BEST_DAY_MIN_DAYS):
            rows.append(row)
    return rows


def _recap_sort_key(row: dict) -> tuple[int, str]:
    if row.get("kind") == "best_ever":
        return (0, "")
    return (1, str(row.get("best_day_since") or ""))


def _era_recap_groups(target_date: str) -> list[dict]:
    """Eras where >= ERA_RECAP_MIN_SONGS songs hit a best-day-since record today
    and clear the individual post gate. Each qualifying era gets one dedicated
    recap card (posted before its album update card); the songs still appear in
    the global recap."""
    return best_day_since.era_recap_groups(
        date.fromisoformat(target_date),
        min_songs=ERA_RECAP_MIN_SONGS,
        min_days=POST_COLLECTION_BEST_DAY_MIN_DAYS,
        min_daily_streams=MIN_SONG_DAILY_STREAMS_TO_POST,
        min_pct_change=best_day_since.LIVE_COLLECTION_MIN_PCT_CHANGE,
        always_post_after_days=ALWAYS_POST_BEST_DAY_SINCE_AFTER_DAYS,
        exclude_predicate=lambda track: _holiday_collection_out_of_season(track.album, target_date),
    )


def _era_recap_lock_path(era_key: str, target_date: str) -> Path:
    return _day_dir(target_date) / "best_day_since_era_recap_locks" / f"{slugify(era_key) or 'era'}.lock"


def _posted_era_recap_keys_for_date(target_date: str) -> set[str]:
    locks_dir = _day_dir(target_date) / "best_day_since_era_recap_locks"
    if not locks_dir.exists():
        return set()
    return {p.stem for p in locks_dir.glob("*.lock")}


def _write_era_recap_lock(era_key: str, target_date: str, group: dict) -> None:
    lock = _era_recap_lock_path(era_key, target_date)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({
            "era_key": era_key,
            "album": group.get("album"),
            "count": group.get("count"),
            "track_ids": group.get("track_ids"),
        }),
        encoding="utf-8",
    )


# Era keys whose recap card was produced in THIS process (covers --no-post
# previews, where no lock file is written): also suppresses their song cards.
_ERA_RECAP_DONE_THIS_RUN: set[str] = set()


def _era_recap_posted_for(album: str | None, target_date: str) -> bool:
    """True if a dedicated era recap card already posted today for this song's
    era - its individual best-day song card is then suppressed (unless it is an
    unconditional biggest-day-of-the-year row)."""
    key = _album_key(album)
    if not key:
        return False
    return key in _ERA_RECAP_DONE_THIS_RUN or _era_recap_lock_path(key, target_date).exists()


def _post_one_era_recap(
    group: dict,
    target_date: str,
    *,
    no_post: bool,
    early: bool,
    tracks_by_id: dict[str, dict] | None = None,
    covers: dict[str, str] | None = None,
) -> str:
    """Post the dedicated best-day recap card for one era. Returns "posted",
    "skipped" (already posted / early cap reached / too few rows) or "error"."""
    era_key = group.get("era_key") or _album_key(group.get("album"))
    era_display = group.get("album") or era_key
    rows = list(group.get("items") or [])
    if len(rows) < ERA_RECAP_MIN_SONGS:
        return "skipped"

    lock = _era_recap_lock_path(era_key, target_date)
    if lock.exists() and not no_post:
        print(f"[best_day_since_era_recap] Already posted for {era_display} on {target_date}, skipping.")
        return "skipped"

    if (
        early
        and not no_post
        and len(_posted_era_recap_keys_for_date(target_date)) >= EARLY_ERA_RECAP_MAX_POSTS
    ):
        print(
            f"[best_day_since_era_recap] Skipping {era_display}: already "
            f"{EARLY_ERA_RECAP_MAX_POSTS} early era recap(s) for {target_date}."
        )
        return "skipped"

    if tracks_by_id is None:
        tracks_by_id = {track["track_id"]: track for track in load_all_tracks()}
    if covers is None:
        covers = load_covers()

    rows.sort(key=_recap_sort_key)
    image_paths = _generate_recap_images(
        rows=rows,
        target_date=target_date,
        tracks_by_id=tracks_by_id,
        covers=covers,
        era_display=era_display,
    )
    tweet = best_day_since_era_recap_tweet(era=era_display, count=len(rows), stats_date=target_date)
    print(f"[best_day_since_era_recap] {era_display}: {len(rows)} song(s), {len(image_paths)} image(s).")
    print(f"[best_day_since_era_recap] Tweet ({len(tweet)} chars):\n{tweet}")
    for image_path in image_paths:
        print(f"[best_day_since_era_recap] - {image_path}")

    if no_post:
        _ERA_RECAP_DONE_THIS_RUN.add(era_key)
        return "posted"

    if not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return "error"
    if len(image_paths) == 1:
        posted = post_with_image(tweet, image_paths[0], TWITTER_SESSION)
    else:
        posted = post_image_thread([(tweet, image_paths)], TWITTER_SESSION)
    if not posted:
        print(f"[best_day_since_era_recap] Failed to post {era_display}.")
        return "error"
    _write_era_recap_lock(era_key, target_date, group)
    _ERA_RECAP_DONE_THIS_RUN.add(era_key)
    return "posted"


def _post_era_recaps_batch(
    target_date: str,
    *,
    no_post: bool,
    tracks_by_id: dict[str, dict],
    covers: dict[str, str],
    exclude_era_keys: set[str] | None = None,
) -> list[str]:
    """Finalize lane: post a dedicated recap card for every qualifying era not
    already posted early. No cap here (mirrors the global recap). Returns the
    list of era keys posted this call."""
    exclude_era_keys = exclude_era_keys or set()
    posted: list[str] = []
    for group in _era_recap_groups(target_date):
        era_key = group.get("era_key") or _album_key(group.get("album"))
        if era_key in exclude_era_keys:
            continue
        result = _post_one_era_recap(
            group,
            target_date,
            no_post=no_post,
            early=False,
            tracks_by_id=tracks_by_id,
            covers=covers,
        )
        if result == "error":
            sys.exit(1)
        if result == "posted":
            posted.append(era_key)
    return posted


def _album_key(album: str | None) -> str:
    """Era key (Red + Red TV -> "red", etc). Canonical definition lives in
    best_day_since.era_key so the web export and this poster agree."""
    return best_day_since.era_key(album)


def _track_album_counts(track_ids: set[str], tracks_by_id: dict[str, dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for track_id in track_ids:
        album = _album_key((tracks_by_id.get(track_id) or {}).get("album"))
        if not album:
            continue
        counts[album] = counts.get(album, 0) + 1
    return counts


def _track_era_label(track: dict) -> str:
    return _album_key(track.get("album"))


def _pick_rows(
    target_date: str,
    *,
    limit: int,
    min_days: int,
    min_daily_streams: int | None = None,
    min_pct_change: float | None = None,
    exclude_ids: set[str] | None = None,
    album_post_counts: dict[str, int] | None = None,
    max_per_album: int = MAX_BEST_DAY_SONG_POSTS_PER_ALBUM,
) -> list[dict]:
    rows = _find_all_rows(target_date, min_days=min_days)
    if exclude_ids:
        rows = [row for row in rows if row["track_id"] not in exclude_ids]
    if min_daily_streams is not None or min_pct_change is not None:
        rows = [
            row for row in rows
            if _passes_song_post_gate(
                row,
                min_daily_streams=min_daily_streams,
                min_pct_change=min_pct_change,
            )
        ]
    rows.sort(key=_song_post_sort_key, reverse=True)
    counts = dict(album_post_counts or {})

    # Priority rows sort first. The daily song-post limit is absolute for
    # normal rows, but a biggest day of the year is never dropped for it.
    picked: list[dict] = []
    capped_picked = 0
    for row in rows:
        is_priority = _is_priority_best_day_since(row)
        is_unconditional = _is_unconditional_best_day(row)
        album = _album_key(row.get("album"))
        if not is_priority and album and counts.get(album, 0) >= max_per_album:
            print(
                f"[best_day_since_post] Skipping {row['title']}: "
                f"already {max_per_album} best-day song post(s) for {row.get('album')}."
            )
            continue
        if not is_unconditional and capped_picked >= limit:
            continue
        picked.append(row)
        if not is_unconditional:
            capped_picked += 1
        if album:
            counts[album] = counts.get(album, 0) + 1
    return picked


def _song_post_sort_key(row: dict) -> tuple[int, int, int, int]:
    return (1 if row.get("is_biggest_day_of_year") else 0, *best_day_since.sort_key(row))


def _is_unconditional_best_day(row: dict) -> bool:
    """Biggest day of the year: always gets its own card, posted early, with no
    per-album / per-era / daily-count cap and no score gate (owner decision
    2026-08-29). Stronger than the >3-month priority below."""
    return bool(row.get("is_biggest_day_of_year"))


def _is_priority_best_day_since(row: dict) -> bool:
    """A best-day-since gap over PRIORITY_BEST_DAY_SINCE_MIN_DAYS (3 months)
    is rare and newsworthy enough that it must always get a post — it bypasses
    the per-album cap and the daily song-post limit instead of competing with
    same-day candidates for a capped spot. A biggest day of the year is
    unconditional and always counts as priority too."""
    if _is_unconditional_best_day(row):
        return True
    return row.get("kind") == "since" and (row.get("days_since") or 0) >= PRIORITY_BEST_DAY_SINCE_MIN_DAYS


def _passes_song_post_gate(
    row: dict,
    *,
    min_daily_streams: int | None,
    min_pct_change: float | None,
) -> bool:
    if row.get("is_biggest_day_of_year"):
        return True

    if (row.get("days_since") or 0) > ALWAYS_POST_BEST_DAY_SINCE_AFTER_DAYS:
        return True

    daily = int(row.get("daily_streams") or 0)
    if min_daily_streams is not None and daily >= min_daily_streams:
        return True

    previous_day_daily = row.get("previous_day_daily")
    if min_pct_change is not None and previous_day_daily and previous_day_daily > 0:
        pct_change = (daily - previous_day_daily) / previous_day_daily * 100
        if pct_change > min_pct_change:
            return True

    return min_daily_streams is None and min_pct_change is None


def _track_posted_lock_path(track_id: str, target_date: str) -> Path:
    return _day_dir(target_date) / "best_day_since_track_locks" / f"{track_id}.lock"


def _posted_track_ids_for_date(target_date: str) -> set[str]:
    track_locks_dir = _day_dir(target_date) / "best_day_since_track_locks"
    if not track_locks_dir.exists():
        return set()
    return {p.stem for p in track_locks_dir.glob("*.lock")}


def _write_track_lock(track_id: str, target_date: str, row: dict, *, post_type: str = "best_day") -> None:
    lock = _track_posted_lock_path(track_id, target_date)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({
            "best_day_since": row.get("best_day_since"),
            "kind": row.get("kind"),
            "post_type": post_type,
        }),
        encoding="utf-8",
    )


def _grower_posted_lock_path(track_id: str, target_date: str) -> Path:
    return _day_dir(target_date) / "best_day_grower_locks" / f"{track_id}.lock"


def _posted_grower_track_ids_for_date(target_date: str) -> set[str]:
    grower_locks_dir = _day_dir(target_date) / "best_day_grower_locks"
    if not grower_locks_dir.exists():
        return set()
    return {p.stem for p in grower_locks_dir.glob("*.lock")}


def _write_grower_lock(track_id: str, target_date: str, row: dict) -> None:
    lock = _grower_posted_lock_path(track_id, target_date)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({
            "best_day_since": row.get("best_day_since"),
            "kind": row.get("kind"),
            "post_type": "grower",
        }),
        encoding="utf-8",
    )


def _is_repeat_of_previous_day(row: dict, target_date: str, *, min_days: int = RECAP_BEST_DAY_MIN_DAYS) -> bool:
    """True if yesterday's post for this track already claimed the same
    best-day-since reference, or yesterday's exact data produced the same
    best-day-since reference.

    Posting "best day since <X>" (or "best day ever") again the very next
    day for the same track reads as a duplicate even though it's technically
    still true, so the second consecutive day switches to the grower format."""
    previous_target = date.fromisoformat(target_date) - timedelta(days=1)
    try:
        tracks = best_day_since.load_tracks(include_extras=False)
        history = best_day_since.load_history()
        track = tracks.get(row["track_id"])
        previous_row = (
            best_day_since.compute_best_day_since(track, history.get(row["track_id"]) or [], previous_target)
            if track
            else None
        )
        if (
            previous_row
            and previous_row.get("kind") == row.get("kind")
            and bool(previous_row.get("best_day_since"))
            and previous_row.get("best_day_since") == row.get("best_day_since")
            and (
                previous_row.get("is_biggest_day_of_year")
                or best_day_since.passes_filters(previous_row, min_days=min_days)
            )
        ):
            return True
    except Exception:
        pass

    previous_date = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
    lock = _track_posted_lock_path(row["track_id"], previous_date)
    if not lock.exists():
        return False
    try:
        previous = json.loads(lock.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(previous.get("best_day_since")) and previous.get("best_day_since") == row.get("best_day_since")


def _daily_grower_points(track_ids: list[str], target_date: str, *, days: int = 4) -> list[dict] | None:
    target = date.fromisoformat(target_date)
    wanted_dates = [(target - timedelta(days=offset)).isoformat() for offset in range(days, -1, -1)]
    wanted_ids = set(track_ids)
    dailies: dict[str, int] = {}
    if not best_day_since.HISTORY_PATH.exists():
        return None

    with best_day_since.HISTORY_PATH.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if row.get("track_id") not in wanted_ids:
                continue
            day = (row.get("date") or "").strip()
            if day not in wanted_dates:
                continue
            daily_raw = (row.get("daily_streams") or "").strip()
            if not daily_raw:
                continue
            try:
                dailies[day] = dailies.get(day, 0) + int(daily_raw)
            except ValueError:
                continue

    points: list[dict] = []
    for index, day in enumerate(wanted_dates[1:], 1):
        previous_day = wanted_dates[index - 1]
        if day not in dailies or previous_day not in dailies:
            return None
        previous_daily = dailies[previous_day]
        if previous_daily <= 0:
            return None
        current_daily = dailies[day]
        pct = (current_daily - previous_daily) / previous_daily * 100
        points.append({
            "date": date.fromisoformat(day),
            "daily": current_daily,
            "pct": pct,
        })
    return points


def _grower_label(row: dict) -> str:
    if row.get("is_biggest_day_of_year") and row.get("kind") == "since":
        return f"biggest day of the year and its best day since {best_day_since.format_long_date(row['best_day_since'])}"
    return best_day_since.row_label(row)


def _grower_tweet(row: dict, track: dict, points: list[dict]) -> str:
    lines = [
        f"{_short_month_day(point['date'])} — {_fmt_int(point['daily'])} [{point['pct']:+.1f}%]"
        for point in points
    ]
    return best_day_grower_tweet(
        title=track.get("title") or row["title"],
        artist=track.get("artist") or track.get("primary_artist") or "Taylor Swift",
        lines=lines,
        label=_grower_label(row),
        prefix=album_emoji(track.get("album")),
    )


def _spotify_logo_svg(fill: str) -> str:
    return f"""<svg class="spotify-logo" viewBox="0 0 24 24" fill="{fill}" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
</svg>"""


def _generate_grower_image(*, track: dict, cover_url: str, target_date: str) -> Path:
    cover_uri, cover_bytes = image_data_uri(cover_url)
    if cover_url:
        try:
            bg_color = generate_album_update_image._dominant_color_from_url(cover_url)
        except Exception:
            bg_color = "#1db954"
    else:
        bg_color = "#1db954"
    logo_fill = "#000000" if _luma_from_hex(bg_color) > 0.58 else "#ffffff"
    art_html = f'<img class="cover" src="{html.escape(cover_uri, quote=True)}" />' if cover_uri else '<div class="cover-ph"></div>'
    html_text = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{
  width:900px;height:900px;
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  background:
    radial-gradient(circle at 78% 18%,rgba(255,255,255,.22),rgba(255,255,255,0) 28%),
    radial-gradient(circle at 10% 90%,rgba(0,0,0,.20),rgba(0,0,0,0) 34%),
    {bg_color};
  overflow:hidden;
  position:relative;
}}
.wrap{{
  position:absolute;inset:0;
  display:flex;align-items:center;justify-content:center;
}}
.cover-frame{{
  width:620px;height:620px;border-radius:46px;overflow:hidden;
  box-shadow:0 42px 90px rgba(0,0,0,.34),0 0 0 1px rgba(255,255,255,.18);
}}
.cover,.cover-ph{{width:100%;height:100%;object-fit:cover;display:block}}
.cover-ph{{background:rgba(255,255,255,.18)}}
.logo-badge{{
  position:absolute;right:96px;bottom:92px;
  width:170px;height:170px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  background:rgba(255,255,255,.16);
  backdrop-filter:blur(10px);
  box-shadow:0 24px 60px rgba(0,0,0,.32), inset 0 0 0 1px rgba(255,255,255,.18);
}}
.spotify-logo{{width:102px;height:102px;display:block}}
.grain{{
  position:absolute;inset:0;opacity:.10;mix-blend-mode:overlay;
  background-image:linear-gradient(45deg,rgba(255,255,255,.18) 25%,transparent 25%,transparent 50%,rgba(255,255,255,.18) 50%,rgba(255,255,255,.18) 75%,transparent 75%);
  background-size:9px 9px;
}}
</style>
</head>
<body>
  <div class="grain"></div>
  <div class="wrap">
    <div class="cover-frame">{art_html}</div>
    <div class="logo-badge">{_spotify_logo_svg(logo_fill)}</div>
  </div>
</body>
</html>"""
    out_dir = _day_dir(target_date) / "best_day_since"
    out_path = out_dir / f"best_day_grower_{slugify(track.get('title') or track.get('track_id') or 'track')}_{target_date}.png"
    tmp_path = out_dir / f"_best_day_grower_{track.get('track_id') or slugify(track.get('title') or 'track')}.html"
    return render_html_to_png(html_text, out_path, tmp_path, width=900)


def _post_grower_for_repeat(row: dict, track: dict, target_date: str, *, no_post: bool) -> str:
    posted_grower_ids = _posted_grower_track_ids_for_date(target_date)
    if row["track_id"] in posted_grower_ids and not no_post:
        print(f"[best_day_grower] Already posted grower for {row['title']} on {target_date}, skipping.")
        return "skipped"
    if len(posted_grower_ids) >= MAX_BEST_DAY_GROWER_POSTS and not no_post:
        print(
            f"[best_day_grower] Skipping {row['title']}: already "
            f"{MAX_BEST_DAY_GROWER_POSTS} grower post(s) for {target_date}."
        )
        return "skipped"

    points = _daily_grower_points(row.get("combined_track_ids") or [row["track_id"]], target_date)
    if not points:
        print(f"[best_day_grower] Skipping {row['title']}: incomplete exact 4-day daily history.")
        return "skipped"

    tweet = _grower_tweet(row, track, points)
    print(f"[best_day_grower] Tweet ({len(tweet)} chars):\n{tweet}")
    if len(tweet) > 280:
        print(f"[best_day_grower] Skipping {row['title']}: tweet is {len(tweet)} chars (limit 280).")
        return "skipped"

    cover_url = get_cover_url(track, load_covers())
    image_path = _generate_grower_image(track=track, cover_url=cover_url, target_date=target_date)
    print(f"[best_day_grower] Image: {image_path}")

    if no_post:
        return "posted"

    if not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return "error"
    if not post_with_image(tweet, image_path, TWITTER_SESSION):
        print(f"[best_day_grower] Failed to post {row['title']}.")
        return "error"

    _write_track_lock(row["track_id"], target_date, row, post_type="grower")
    _write_grower_lock(row["track_id"], target_date, row)
    return "posted"


def _post_single_track_early(
    track_id: str,
    target_date: str,
    *,
    min_days: int,
    min_daily_streams: int,
    min_pct_change: float | None,
    min_score: float | None,
    no_post: bool,
) -> str:
    """Compute and post one track's best-day-since record immediately, if it
    already qualifies, without waiting for the rest of the day's collection.

    Returns "posted", "posted_unconditional" (posted a biggest-day-of-the-year
    card that bypassed every cap), "skipped" (doesn't qualify yet / missing
    data, or already posted for this track+date), or "error".
    """
    lock = _track_posted_lock_path(track_id, target_date)
    if lock.exists() and not no_post:
        print(f"[best_day_since_early] Already posted for {track_id} on {target_date}, skipping.")
        return "skipped"

    tracks_by_id = {track["track_id"]: track for track in load_all_tracks()}
    track = tracks_by_id.get(track_id)
    if not track:
        return "skipped"

    if _holiday_collection_out_of_season(track.get("album"), target_date):
        print(
            f"[best_day_since_early] Skipping {track_id}: "
            f"{track.get('album')} is outside its Christmas posting season."
        )
        return "skipped"

    row = best_day_since.best_day_since_for_track(
        track_id,
        target_date,
        min_days=min_days,
        combined=False,
        keep_year_record=True,
    )
    if not row:
        return "skipped"
    is_priority = _is_priority_best_day_since(row)

    if not _is_unconditional_best_day(row) and _era_recap_posted_for(track.get("album"), target_date):
        print(
            f"[best_day_since_early] Skipping {track_id}: {track.get('album')} era recap "
            f"already posted for {target_date}."
        )
        return "skipped"
    is_unconditional = _is_unconditional_best_day(row)

    locked_track_ids = _posted_track_ids_for_date(target_date)
    if (
        not is_unconditional
        and len(locked_track_ids) >= EARLY_BEST_DAY_EXCEPTIONAL_MAX_POSTS
        and not no_post
    ):
        print(
            f"[best_day_since_early] Skipping {track_id}: already "
            f"{EARLY_BEST_DAY_EXCEPTIONAL_MAX_POSTS} early best-day song post(s) for {target_date}."
        )
        return "skipped"

    album_counts = _track_album_counts(locked_track_ids, tracks_by_id)
    album = _track_era_label(track)
    if not is_unconditional and album and album_counts.get(album, 0) >= EARLY_BEST_DAY_MAX_POSTS_PER_ERA:
        print(
            f"[best_day_since_early] Skipping {track_id}: already "
            f"{EARLY_BEST_DAY_MAX_POSTS_PER_ERA} early best-day post(s) for this album/era "
            f"({track.get('album')})."
        )
        return "skipped"
    if not is_priority and album and album_counts.get(album, 0) >= MAX_BEST_DAY_SONG_POSTS_PER_ALBUM:
        print(
            f"[best_day_since_early] Skipping {track_id}: already "
            f"{MAX_BEST_DAY_SONG_POSTS_PER_ALBUM} best-day song post(s) for {track.get('album')}."
        )
        return "skipped"

    if _is_repeat_of_previous_day(row, target_date, min_days=min_days):
        print(
            f"[best_day_since_early] {track_id} repeated the same best-day-since "
            f"({row['best_day_since']}); using grower tweet format."
        )
        grower_result = _post_grower_for_repeat(row, track, target_date, no_post=no_post)
        if is_unconditional and grower_result == "posted":
            return "posted_unconditional"
        return grower_result

    track_ids = row.get("combined_track_ids") or [track_id]
    total_today, total_yesterday, daily_today, daily_yesterday, daily_last_week = (
        load_history_for_tracks(track_ids, target_date)
    )
    if total_today is None or total_yesterday is None or daily_today is None or daily_yesterday is None or daily_yesterday <= 0:
        return "skipped"
    row["_post_daily_last_week"] = daily_last_week
    if not _passes_song_post_gate(row, min_daily_streams=min_daily_streams, min_pct_change=min_pct_change):
        pct = (daily_today - daily_yesterday) / daily_yesterday * 100
        print(
            f"[best_day_since_early] Skipping {track_id}: "
            f"{daily_today:,} daily streams and {pct:+.1f}% vs previous day "
            f"do not pass the early gate."
        )
        return "skipped"

    needs_score = not is_unconditional and (
        min_score is not None or len(locked_track_ids) >= EARLY_BEST_DAY_STANDARD_MAX_POSTS
    )
    if needs_score:
        score = score_best_day_since.score_single_best_day_candidate(
            track_id,
            date.fromisoformat(target_date),
            min_days=min_days,
            min_daily_streams=min_daily_streams,
            combined=False,
        )
        if score.get("status") != "scored":
            print(
                f"[best_day_since_early] Skipping {track_id}: "
                f"score status={score.get('status')} reasons={','.join(score.get('reasons') or [])}."
            )
            return "skipped"
        dynamic_min_score, threshold_adjustments = score_best_day_since.dynamic_early_min_score(
            score,
            min_score if min_score is not None else EARLY_BEST_DAY_MIN_SCORE,
        )
        score["dynamic_early_min_score"] = dynamic_min_score
        score["threshold_adjustments"] = {
            key: round(value, 3)
            for key, value in threshold_adjustments.items()
        }
        numeric_score = float(score.get("score") or 0.0)
        if numeric_score < dynamic_min_score:
            print(
                f"[best_day_since_early] Skipping {track_id}: "
                f"score {numeric_score:.2f} < dynamic threshold {dynamic_min_score:.2f} "
                f"({score.get('title')})."
            )
            return "skipped"
        if (
            len(locked_track_ids) >= EARLY_BEST_DAY_STANDARD_MAX_POSTS
            and numeric_score < EARLY_BEST_DAY_EXCEPTIONAL_MIN_SCORE
        ):
            print(
                f"[best_day_since_early] Skipping {track_id}: already "
                f"{EARLY_BEST_DAY_STANDARD_MAX_POSTS} early best-day post(s), and score "
                f"{numeric_score:.2f} < exceptional threshold {EARLY_BEST_DAY_EXCEPTIONAL_MIN_SCORE:.2f}."
            )
            return "skipped"
        row["_early_score"] = score
        print(
            f"[best_day_since_early] Score {numeric_score:.2f} "
            f"passes dynamic threshold {dynamic_min_score:.2f} for {score.get('title')}."
        )

    covers = load_covers()
    cover_url = get_cover_url(track, covers)
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
        return "posted_unconditional" if is_unconditional else "posted"

    if not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return "error"
    if not post_with_image(tweet, image_path, TWITTER_SESSION):
        print(f"[best_day_since_early] Failed to post {row['title']}.")
        return "error"

    _write_track_lock(track_id, target_date, row)
    return "posted_unconditional" if is_unconditional else "posted"


def _best_day_post_label(row: dict) -> str:
    if row.get("is_biggest_day_of_year") and row.get("kind") == "since":
        return f"BIGGEST DAY of the year and BEST DAY since {best_day_since.format_long_date(row['best_day_since'])}"
    label = best_day_since.row_label(row)
    return label.replace("best day", "BEST DAY", 1).replace("biggest day", "BIGGEST DAY", 1)


def _build_tweet(row: dict, daily_yesterday: int | None) -> str:
    title = row["title"]
    track_id = row["track_id"]
    label = _best_day_post_label(row)
    daily = int(row["daily_streams"])
    pct = _fmt_pct(daily, daily_yesterday)
    return best_day_since_tweet(
        title=title,
        label=label,
        daily_streams=daily,
        pct=pct,
        track_id=track_id,
        repeat=best_day_since.is_recent_repeat_record(row),
    )


def _validated_song_rows_for_post(
    candidate_rows: list[dict],
    *,
    target_date: str,
    tracks_by_id: dict[str, dict],
    limit: int,
    standard_limit: int = POST_COLLECTION_STANDARD_SONG_POSTS,
    min_days: int = POST_COLLECTION_BEST_DAY_MIN_DAYS,
) -> list[dict]:
    exceptional_score_by_id: dict[str, float] | None = None

    def _exceptional_score(track_id: str) -> float:
        nonlocal exceptional_score_by_id
        if exceptional_score_by_id is None:
            try:
                result = score_best_day_since.score_best_day_since(
                    date.fromisoformat(target_date),
                    min_days=min_days,
                )
                exceptional_score_by_id = {
                    item["track_id"]: float(item.get("score") or 0.0)
                    for item in result.get("items", [])
                }
            except Exception as exc:  # scoring must never block a post
                print(f"[best_day_since_post] Batch scoring unavailable ({exc}); "
                      f"treating extra-slot candidates as non-exceptional.")
                exceptional_score_by_id = {}
        return exceptional_score_by_id.get(track_id, 0.0)

    rows: list[dict] = []
    capped_count = 0
    for row in candidate_rows:
        is_unconditional = _is_unconditional_best_day(row)
        if not is_unconditional and capped_count >= limit:
            continue
        # Slots beyond the standard batch size are reserved for exceptional
        # records: a >90-day gap (priority), or a score that clears the early
        # lane's exceptional bar. Everything else stops at the standard count.
        if (
            not is_unconditional
            and capped_count >= standard_limit
            and not _is_priority_best_day_since(row)
        ):
            numeric_score = _exceptional_score(row["track_id"])
            if numeric_score < EARLY_BEST_DAY_EXCEPTIONAL_MIN_SCORE:
                print(
                    f"[best_day_since_post] Skipping {row['title']}: batch slot "
                    f"{capped_count + 1} needs a >90-day gap or score >= "
                    f"{EARLY_BEST_DAY_EXCEPTIONAL_MIN_SCORE:.0f} "
                    f"(score {numeric_score:.1f})."
                )
                continue
        track = tracks_by_id.get(row["track_id"])
        if not track:
            print(f"[best_day_since_post] Candidate skipped: track missing in discography: {row['title']} [{row['track_id']}].")
            continue

        track_ids = row.get("combined_track_ids") or [row["track_id"]]
        total_today, total_yesterday, daily_today, daily_yesterday, daily_last_week = (
            load_history_for_tracks(track_ids, target_date)
        )
        if total_today is None:
            print(f"[best_day_since_post] Candidate skipped: missing total streams for {row['title']} on {target_date}.")
            continue
        if total_yesterday is None or daily_today is None or daily_yesterday is None or daily_yesterday <= 0:
            print(f"[best_day_since_post] Candidate skipped: incomplete comparison history for {row['title']} on {target_date}.")
            continue

        row["_post_track"] = track
        row["_post_total_today"] = total_today
        row["_post_daily_yesterday"] = daily_yesterday
        row["_post_daily_last_week"] = daily_last_week
        rows.append(row)
        if not is_unconditional:
            capped_count += 1
    return rows


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


def _recap_image_count(row_count: int) -> int:
    if row_count <= RECAP_ROWS_PER_IMAGE_TARGET:
        return 1
    return min(RECAP_MAX_IMAGES, (row_count + RECAP_ROWS_PER_IMAGE_TARGET - 1) // RECAP_ROWS_PER_IMAGE_TARGET)


def _split_recap_rows(rows: list[dict]) -> list[list[dict]]:
    image_count = _recap_image_count(len(rows))
    if image_count <= 1:
        return [rows]

    base_size, extra = divmod(len(rows), image_count)
    chunks: list[list[dict]] = []
    start = 0
    for index in range(image_count):
        size = base_size + (1 if index < extra else 0)
        chunks.append(rows[start:start + size])
        start += size
    return chunks


def _generate_recap_image(
    *,
    rows: list[dict],
    target_date: str,
    tracks_by_id: dict,
    covers: dict,
    all_rows: list[dict] | None = None,
    start_index: int = 1,
    page_index: int = 1,
    page_count: int = 1,
    era_display: str | None = None,
) -> Path:
    from datetime import datetime

    all_rows = all_rows or rows
    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows_html = []
    for index, row in enumerate(rows, start_index):
        track = tracks_by_id.get(row["track_id"]) or {
            "track_id": row["track_id"],
            "title": row.get("title") or row["track_id"],
            "album": row.get("album") or "",
        }
        track_ids = row.get("combined_track_ids") or [row["track_id"]]
        _total_today, _total_yesterday, daily_today, daily_yesterday, daily_last_week = (
            load_history_for_tracks(track_ids, target_date)
        )
        if daily_today is None:
            daily_today = row.get("daily_streams")
        if daily_today is None:
            print(
                f"[best_day_since_post] Recap row skipped: missing daily streams for "
                f"{row.get('title') or row['track_id']} on {target_date}."
            )
            continue
        cover_url = url_to_data_uri(get_cover_url(track, covers))
        daily_pct = get_pct(daily_today, daily_yesterday)
        weekly_pct = get_pct(daily_today, daily_last_week)
        rows_html.append(_recap_row_html(index, row, track, cover_url, daily_today, daily_pct, weekly_pct))
    if len(rows_html) != len(rows):
        print(
            f"[best_day_since_post] Recap image contains {len(rows_html)}/{len(rows)} "
            f"validated best-day row(s)."
        )

    # Global recap: fixed "all eras" strip header + "BEST DAY" masthead, no album
    # theming (owner decision 2026-09-03). The dedicated per-era recap card
    # (era_display set) uses that era's own header pool and title instead.
    # Weekday posts (Mon-Fri) -> light; weekend posts (Sat/Sun) -> dark.
    masthead_word = "BEST DAY"
    masthead_theme = masthead_theme_for_date(target_date)
    if era_display:
        album_headers = generate_album_update_image.header_images_for_album(era_display)
        headers_dir = album_headers[0].parent if album_headers else RECAP_HEADERS_DIR
        themed_count = sum(
            1
            for row in all_rows
            if _album_key((tracks_by_id.get(row["track_id"]) or {}).get("album") or row.get("album"))
            == _album_key(era_display)
        )
        title = f"{era_display} - Best Day Recap"
        subtitle = f"{themed_count} songs from the era hit a best-day-since record - {date_text}"
        if "holiday collection" in era_display.casefold():
            masthead_theme = "light"
        # Pick via the album-scoped picker rather than passing headers_dir down to
        # build_table_html's generic pick_header_image(headers_dir): legacy flat-file
        # albums (most of them - only a few use the new per-album subfolder layout)
        # all live directly under HEADERS_DIR, so headers_dir there is the shared
        # root, not this era's own folder. Re-scanning it would surface any era's
        # header at random (e.g. a "Red" recap picking the Debut-era header).
        header_image = generate_album_update_image.pick_header_image(era_display, masthead_theme) if album_headers else None
    else:
        headers_dir = RECAP_HEADERS_DIR
        header_image = None
        title = "Best Day Since - Full Recap"
        subtitle = f"Every song that hit a best-day-since record - {date_text}"
    if page_count > 1:
        title = f"{title} ({page_index}/{page_count})"
        end_index = start_index + len(rows) - 1
        subtitle = f"Songs {start_index}-{end_index} of {len(all_rows)} - {date_text}"

    html_text = build_table_html(
        title=title,
        subtitle=subtitle,
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
        headers_dir=headers_dir,
        header_image=header_image,
        body_width=960,
        art_size=48,
        masthead_word=masthead_word,
        masthead_theme=masthead_theme,
    )
    out_dir = _day_dir(target_date) / "best_day_since"
    era_suffix = f"_era_{slugify(era_display)}" if era_display else ""
    part_suffix = f"_part{page_index}of{page_count}" if page_count > 1 else ""
    out_path = out_dir / f"best_day_since{'_era_recap' if era_display else '_recap'}{era_suffix}{part_suffix}_{target_date}.png"
    tmp_path = out_dir / f"_best_day_since{'_era_recap' if era_display else '_recap'}{era_suffix}{part_suffix}_{target_date}.html"
    return render_html_to_png(html_text, out_path, tmp_path, width=960)


def _generate_recap_images(
    *,
    rows: list[dict],
    target_date: str,
    tracks_by_id: dict,
    covers: dict,
    era_display: str | None = None,
) -> list[Path]:
    chunks = _split_recap_rows(rows)
    page_count = len(chunks)
    paths: list[Path] = []
    start_index = 1
    for page_index, chunk in enumerate(chunks, 1):
        paths.append(_generate_recap_image(
            rows=chunk,
            target_date=target_date,
            tracks_by_id=tracks_by_id,
            covers=covers,
            all_rows=rows,
            start_index=start_index,
            page_index=page_index,
            page_count=page_count,
            era_display=era_display,
        ))
        start_index += len(chunk)
    return paths


def _build_recap_tweet(rows: list[dict], target_date: str) -> str:
    return best_day_since_recap_tweet(count=len(rows), stats_date=target_date)

def _best_since_badge_text(row: dict) -> str:
    if row.get("is_biggest_day_of_year") and row.get("kind") == "since":
        return f"biggest day of the year and best day since {best_day_since.format_long_date(row['best_day_since'])}"
    return best_day_since.row_label(row)


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

    target = date.fromisoformat(target_date)
    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%b %d, %Y").upper()
    daily = int(row["daily_streams"])
    pct = _fmt_pct(daily, daily_yesterday)
    daily_class = _badge_class(pct)

    daily_last_week = row.get("_post_daily_last_week")
    weekly_pct = _fmt_pct(daily, daily_last_week) if daily_last_week else None
    weekly_class = _badge_class(weekly_pct) if weekly_pct else "flat"

    historical_date = None
    historical_daily = None
    if row.get("kind") == "since" and row.get("previous_higher_or_equal_date"):
        historical_date = date.fromisoformat(row["previous_higher_or_equal_date"])
        historical_daily = row.get("previous_higher_or_equal_daily")

    track_ids = row.get("combined_track_ids") or [row["track_id"]]
    history = best_day_since.load_history()
    points_by_track = [history.get(tid) or [] for tid in track_ids]
    points = best_day_since.combine_points(points_by_track) if len(track_ids) > 1 else (points_by_track[0] or [])
    bars = best_day_since.build_chart_sheet_bars(
        points,
        target,
        historical_date=historical_date,
        historical_daily=historical_daily,
    )

    release_text = _fmt_release_date(track.get("release_date"))
    footer_right = f"Released {release_text}" if release_text else date_text

    html = render_chart_sheet_card(
        title=track.get("title") or row["title"],
        album=track.get("album") or row.get("album") or "",
        date_text=date_text,
        kicker_text=_best_since_badge_text(row),
        bars=bars,
        daily_value_text=_fmt_signed_int(daily),
        daily_class=daily_class,
        change_text=format_change_html(pct, daily_class, weekly_pct, weekly_class),
        total_value_text=_fmt_int(total_today),
        cover_url=cover_url,
        footer_left="@swiftiescharts",
        footer_right=footer_right,
    )
    out_dir = _day_dir(target_date) / "best_day_since"
    out_path = out_dir / f"best_day_since_{slugify(track.get('title') or row['title'])}_{target_date}.png"
    tmp_path = out_dir / f"_best_day_since_{slugify(str(row.get('track_id') or track.get('title') or row['title']))}.html"
    return write_chart_sheet_card_png(html, out_path, tmp_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Post top best-day-since songs to @swiftiescharts.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--no-post", action="store_true", help="Generate images but skip Twitter posts.")
    parser.add_argument("--force", action="store_true", help="Post again even if best_day_since_posted.lock exists.")
    parser.add_argument(
        "--limit",
        type=int,
        default=POST_COLLECTION_MAX_SONG_POSTS,
        help=f"Number of individual song posts (default: {POST_COLLECTION_MAX_SONG_POSTS}).",
    )
    parser.add_argument(
        "--min-days",
        type=int,
        default=POST_COLLECTION_BEST_DAY_MIN_DAYS,
        help=f"Minimum days for best-day-since (default: {POST_COLLECTION_BEST_DAY_MIN_DAYS}).",
    )
    parser.add_argument(
        "--min-daily-streams",
        type=int,
        default=MIN_SONG_DAILY_STREAMS_TO_POST,
        help=f"Minimum daily streams for --only-track early posts (default: {MIN_SONG_DAILY_STREAMS_TO_POST}).",
    )
    parser.add_argument(
        "--min-pct-change",
        type=float,
        default=best_day_since.LIVE_COLLECTION_MIN_PCT_CHANGE,
        help=(
            "Minimum day-over-day percent change for --only-track early posts "
            f"(default: {best_day_since.LIVE_COLLECTION_MIN_PCT_CHANGE})."
        ),
    )
    parser.add_argument(
        "--early-min-score",
        type=float,
        default=EARLY_BEST_DAY_MIN_SCORE,
        help=f"Minimum single-candidate score for --only-track early posts (default: {EARLY_BEST_DAY_MIN_SCORE}).",
    )
    parser.add_argument(
        "--post-spacing-seconds",
        type=int,
        default=0,
        help="Extra seconds to wait between Twitter posts; core.twitter enforces account spacing.",
    )
    parser.add_argument("--no-recap", action="store_true", help="Skip the full best-day-since recap table post.")
    parser.add_argument(
        "--no-era-recap",
        action="store_true",
        help="Skip the dedicated per-era best-day recap cards.",
    )
    parser.add_argument(
        "--only-era-recap",
        help=(
            "Post the dedicated best-day recap card for a single era immediately (early "
            "lane, own cap, does not spend the song quota). Pass any album name of the era. "
            "Exits 0 if posted, 3 if the era does not have enough best-day songs yet."
        ),
    )
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
            args.only_track,
            target_date,
            min_days=args.min_days,
            min_daily_streams=args.min_daily_streams,
            min_pct_change=args.min_pct_change,
            min_score=args.early_min_score,
            no_post=args.no_post,
        )
        if result == "posted":
            sys.exit(0)
        if result == "posted_unconditional":
            sys.exit(2)
        if result == "skipped":
            sys.exit(3)
        sys.exit(1)

    day_dir = _day_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)

    if args.only_era_recap:
        if not args.no_post and not TWITTER_SESSION.exists():
            print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
            sys.exit(1)
        wanted_key = _album_key(args.only_era_recap)
        group = next(
            (
                g
                for g in _era_recap_groups(target_date)
                if (g.get("era_key") or _album_key(g.get("album"))) == wanted_key
            ),
            None,
        )
        if not group:
            print(
                f"[best_day_since_era_recap] {args.only_era_recap}: fewer than "
                f"{ERA_RECAP_MIN_SONGS} post-eligible best-day songs for its era on {target_date}."
            )
            sys.exit(3)
        result = _post_one_era_recap(group, target_date, no_post=args.no_post, early=True)
        sys.exit(0 if result == "posted" else (3 if result == "skipped" else 1))

    limit = min(POST_COLLECTION_MAX_SONG_POSTS, max(0, int(args.limit)))
    if limit == 0:
        print("[best_day_since_post] Limit is 0, skipping individual song posts.")

    lock = day_dir / "best_day_since_posted.lock"
    recap_lock = day_dir / "best_day_since_recap_posted.lock"

    recap_locked = recap_lock.exists() and not args.no_post and not args.force
    if recap_locked and not args.no_recap:
        print(f"[best_day_since_post] Recap already posted for {target_date}, skipping.")
        return

    track_locked = lock.exists() and not args.no_post and not args.force
    if track_locked:
        print(f"[best_day_since_post] Already posted for {target_date}, skipping track posts.")
    if lock.exists() and args.no_post:
        print(f"[best_day_since_post] Already posted for {target_date}, regenerating only (--no-post).")

    if not args.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        sys.exit(1)

    # Album best-day records are no longer posted as their own card - they are
    # folded into the first line of each album's daily update card
    # (generate_album_update_image._build_album_post_text).

    tracks_by_id = {track["track_id"]: track for track in load_all_tracks()}
    covers = load_covers()

    # Dedicated per-era recap cards go out before the individual song cards (and
    # before each era's album update card, which is a later finalize step). Once
    # an era recap posts, that era's individual best-day song cards are
    # suppressed below via _era_recap_posted_for (biggest-day-of-year excepted).
    if not args.no_era_recap:
        _post_era_recaps_batch(
            target_date,
            no_post=args.no_post,
            tracks_by_id=tracks_by_id,
            covers=covers,
            exclude_era_keys=set(_posted_era_recap_keys_for_date(target_date)),
        )

    exclude_ids = {t.strip() for t in args.exclude_tracks.split(",") if t.strip()}
    exclude_ids.update(_posted_track_ids_for_date(target_date))
    album_post_counts = _track_album_counts(exclude_ids, tracks_by_id)
    # Early best-day posts are a separate lane (up to 3 during collection).
    # Exclude those track IDs from the final batch to avoid duplicates, but do
    # not spend the final batch's own song slots.
    remaining_song_limit = limit
    candidate_rows = (
        []
        if track_locked or remaining_song_limit <= 0
        else _pick_rows(
            target_date,
            limit=max(remaining_song_limit * 5, remaining_song_limit + 20),
            min_days=args.min_days,
            min_daily_streams=None,
            min_pct_change=None,
            exclude_ids=exclude_ids,
            album_post_counts=album_post_counts,
        )
    )
    rows = _validated_song_rows_for_post(
        candidate_rows,
        target_date=target_date,
        tracks_by_id=tracks_by_id,
        limit=remaining_song_limit,
        min_days=args.min_days,
    )
    if not rows:
        print(f"[best_day_since_post] No best-day-since songs found for {target_date}.")

    posted_count = 0
    grower_post_count = len(_posted_grower_track_ids_for_date(target_date))
    for index, row in enumerate(rows, 1):
        track = row["_post_track"]
        total_today = row["_post_total_today"]
        daily_yesterday = row["_post_daily_yesterday"]

        if row.get("_grower_repeat"):
            if grower_post_count >= MAX_BEST_DAY_GROWER_POSTS:
                print(
                    f"[best_day_grower] Skipping {row['title']}: already "
                    f"{MAX_BEST_DAY_GROWER_POSTS} grower post(s) selected for {target_date}."
                )
                continue
            result = _post_grower_for_repeat(row, track, target_date, no_post=args.no_post)
            if result == "error":
                sys.exit(1)
            if result == "posted":
                grower_post_count += 1
                if not args.no_post:
                    posted_count += 1
            continue

        cover_url = get_cover_url(track, covers)
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
        _write_track_lock(row["track_id"], target_date, row)

    if posted_count and not args.no_post:
        lock.touch()
    if rows:
        print(f"[best_day_since_post] Posted {posted_count} song(s) for {target_date}.")

    # Full recap table: every song that hit a best-day-since record today
    # (not just the ones with individual posts), oldest record first.
    if recap_locked:
        print(f"[best_day_since_post] Recap already posted for {target_date}, skipping.")
    elif args.no_recap:
        pass
    else:
        recap_rows = _find_recap_rows(target_date)
        recap_rows.sort(key=_recap_sort_key)
        if len(recap_rows) <= 1:
            print(
                f"[best_day_since_post] Recap skipped for {target_date}: "
                f"{len(recap_rows)} best-day-since song(s)."
            )
        else:
            image_paths = _generate_recap_images(
                rows=recap_rows,
                target_date=target_date,
                tracks_by_id=tracks_by_id,
                covers=covers,
            )
            tweet = _build_recap_tweet(recap_rows, target_date)
            print(f"[best_day_since_post] Recap tweet ({len(tweet)} chars):\n{tweet}")
            print(f"[best_day_since_post] Recap images ({len(image_paths)}):")
            for image_path in image_paths:
                print(f"[best_day_since_post] - {image_path}")

            if not args.no_post:
                if len(image_paths) == 1:
                    posted = post_with_image(tweet, image_paths[0], TWITTER_SESSION)
                else:
                    posted = post_image_thread([(tweet, image_paths)], TWITTER_SESSION)
                if not posted:
                    print("[best_day_since_post] Failed to post recap.")
                    sys.exit(1)
                recap_lock.touch()
                print(f"[best_day_since_post] Posted recap for {target_date}.")

    if args.no_post:
        print("[best_day_since_post] Twitter posts skipped (--no-post).")


if __name__ == "__main__":
    main()
