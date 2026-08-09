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
ALBUM_BEST_DAY_MIN_DAYS = 30
RECAP_BEST_DAY_MIN_DAYS = 30
MAX_BEST_DAY_SONG_POSTS_PER_ALBUM = 3
POST_COLLECTION_MIN_SONG_DAILY_STREAMS = 300_000
POST_COLLECTION_MAX_SONG_POSTS = 5
MIN_SONG_DAILY_STREAMS_TO_POST = 80_000
POST_COLLECTION_MIN_SONG_PCT_CHANGE = 10.0
ALWAYS_POST_BEST_DAY_SINCE_AFTER_DAYS = 60

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(COLLECTORS_ROOT))              # collectors/
sys.path.insert(0, str(ROOT))                         # collectors/spotify/streams/
sys.path.insert(0, str(ROOT.parent))                  # collectors/spotify/

from comp.song_card import render_song_card, slugify, write_song_card_png  # noqa: E402
from comp.tables_image import build_table_html, render_html_to_png, url_to_data_uri  # noqa: E402
from comp.fmt import fmt_streams, fmt_pct, pct_cls, get_pct  # noqa: E402
from core.twitter import post_with_image  # noqa: E402
from core.data_paths import update_streams_dir  # noqa: E402
from twitter.text import best_day_since_recap_tweet, best_day_since_tweet  # noqa: E402
import best_day_since  # noqa: E402
import generate_album_update_image  # noqa: E402


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
        if not row or row.get("kind") != "since" or not best_day_since.passes_filters(row, min_days=min_days):
            continue
        rows.append(row)
    return rows


def _album_key(album: str | None) -> str:
    text = re.sub(r"\s+", " ", (album or "").strip())
    if not text:
        return ""

    normalized = text.casefold()
    aliases = {
        "fearless (taylor's version)": "fearless",
        "speak now (taylor's version)": "speak now",
        "red (taylor's version)": "red",
        "1989 (taylor's version)": "1989",
        "the tortured poets department: the anthology": "the tortured poets department",
        "midnights (the til dawn edition)": "midnights",
        "midnights (3am edition)": "midnights",
        "folklore: the long pond studio sessions": "folklore",
    }
    if normalized in aliases:
        return aliases[normalized]

    text = re.sub(r"\s*\(taylor's version\)", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\s*\((?:deluxe|standard|expanded|bonus|anniversary|karaoke|acoustic|live|tour|edition|"
        r"the anthology|the til dawn edition|3am edition)[^)]*\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*[-â€“â€”]\s*(?:deluxe|standard|expanded|bonus|anniversary|karaoke|acoustic|live|tour|edition).*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*:\s*(?:the anthology|the long pond studio sessions|.*edition).*$", "", text, flags=re.IGNORECASE)

    karaoke_match = re.match(r"Taylor Swift Karaoke:\s*(.+)", text, flags=re.IGNORECASE)
    if karaoke_match:
        text = karaoke_match.group(1)

    return re.sub(r"\s+", " ", text.strip()).casefold()


def _track_album_counts(track_ids: set[str], tracks_by_id: dict[str, dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for track_id in track_ids:
        album = _album_key((tracks_by_id.get(track_id) or {}).get("album"))
        if not album:
            continue
        counts[album] = counts.get(album, 0) + 1
    return counts


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
    rows.sort(key=best_day_since.sort_key, reverse=True)

    picked: list[dict] = []
    counts = dict(album_post_counts or {})
    for row in rows:
        album = _album_key(row.get("album"))
        if album and counts.get(album, 0) >= max_per_album:
            print(
                f"[best_day_since_post] Skipping {row['title']}: "
                f"already {max_per_album} best-day song post(s) for {row.get('album')}."
            )
            continue
        picked.append(row)
        if album:
            counts[album] = counts.get(album, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def _passes_song_post_gate(
    row: dict,
    *,
    min_daily_streams: int | None,
    min_pct_change: float | None,
) -> bool:
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


def _post_single_track_early(
    track_id: str,
    target_date: str,
    *,
    min_days: int,
    min_daily_streams: int,
    min_pct_change: float | None,
    no_post: bool,
) -> str:
    """Compute and post one track's best-day-since record immediately, if it
    already qualifies, without waiting for the rest of the day's collection.

    Returns "posted", "skipped" (doesn't qualify yet / missing data, or already
    posted for this track+date), or "error".
    """
    lock = _track_posted_lock_path(track_id, target_date)
    if lock.exists() and not no_post:
        print(f"[best_day_since_early] Already posted for {track_id} on {target_date}, skipping.")
        return "skipped"

    tracks_by_id = {track["track_id"]: track for track in load_all_tracks()}
    track = tracks_by_id.get(track_id)
    if not track:
        return "skipped"

    locked_track_ids = _posted_track_ids_for_date(target_date)
    if len(locked_track_ids) >= POST_COLLECTION_MAX_SONG_POSTS and not no_post:
        print(
            f"[best_day_since_early] Skipping {track_id}: already "
            f"{POST_COLLECTION_MAX_SONG_POSTS} best-day song post(s) for {target_date}."
        )
        return "skipped"

    album_counts = _track_album_counts(locked_track_ids, tracks_by_id)
    album = _album_key(track.get("album"))
    if album and album_counts.get(album, 0) >= MAX_BEST_DAY_SONG_POSTS_PER_ALBUM:
        print(
            f"[best_day_since_early] Skipping {track_id}: already "
            f"{MAX_BEST_DAY_SONG_POSTS_PER_ALBUM} best-day song post(s) for {track.get('album')}."
        )
        return "skipped"

    row = best_day_since.best_day_since_for_track(
        track_id,
        target_date,
        min_days=min_days,
        combined=True,
    )
    if not row:
        return "skipped"

    track_ids = row.get("combined_track_ids") or [track_id]
    total_today, total_yesterday, daily_today, daily_yesterday, _daily_last_week = (
        load_history_for_tracks(track_ids, target_date)
    )
    if total_today is None or total_yesterday is None or daily_today is None or daily_yesterday is None or daily_yesterday <= 0:
        return "skipped"
    if not _passes_song_post_gate(row, min_daily_streams=min_daily_streams, min_pct_change=min_pct_change):
        pct = (daily_today - daily_yesterday) / daily_yesterday * 100
        print(
            f"[best_day_since_early] Skipping {track_id}: "
            f"{daily_today:,} daily streams and {pct:+.1f}% vs previous day "
            f"do not pass the early gate."
        )
        return "skipped"

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
        return "posted"

    if not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return "error"
    if not post_with_image(tweet, image_path, TWITTER_SESSION):
        print(f"[best_day_since_early] Failed to post {row['title']}.")
        return "error"

    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
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
        if not row or row.get("kind") != "since" or not best_day_since.passes_filters(row, min_days=min_days):
            continue
        rows.append(row)

    rows.sort(key=best_day_since.sort_key, reverse=True)
    return rows[:limit]


def _album_row(
    target_date: str,
    album_name: str,
    *,
    min_days: int,
    min_pct_change: float | None = None,
) -> dict | None:
    tracks = best_day_since.load_tracks(include_extras=False)
    history = best_day_since.load_history()
    target = date.fromisoformat(target_date)
    by_album = best_day_since.load_album_track_ids(tracks)

    track_ids = by_album.get(album_name)
    if not track_ids or len(track_ids) < 2:
        return None

    row = best_day_since.compute_album_best_day_since(album_name, track_ids, history, target)
    if (
        not row
        or row.get("kind") != "since"
        or not best_day_since.passes_filters(row, min_days=min_days, min_pct_change=min_pct_change)
    ):
        return None
    return row


def _build_tweet(row: dict, daily_yesterday: int | None) -> str:
    title = row["title"]
    track_id = row["track_id"]
    label = best_day_since.row_label(row)
    label = label.replace("best day", "BEST DAY", 1).replace("biggest day", "BIGGEST DAY", 1)
    daily = int(row["daily_streams"])
    pct = _fmt_pct(daily, daily_yesterday)
    return best_day_since_tweet(
        title=title,
        label=label,
        daily_streams=daily,
        pct=pct,
        track_id=track_id,
    )

def _validated_song_rows_for_post(
    candidate_rows: list[dict],
    *,
    target_date: str,
    tracks_by_id: dict[str, dict],
    limit: int,
) -> list[dict]:
    rows: list[dict] = []
    for row in candidate_rows:
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
        if len(rows) >= limit:
            break
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
            load_history_for_tracks(track_ids, target_date)
        )
        if daily_today is None:
            continue
        cover_url = url_to_data_uri(get_cover_url(track, covers))
        daily_pct = get_pct(daily_today, daily_yesterday)
        weekly_pct = get_pct(daily_today, daily_last_week)
        rows_html.append(_recap_row_html(index, row, track, cover_url, daily_today, daily_pct, weekly_pct))

    html_text = build_table_html(
        title="Best Day Since â€” Full Recap",
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
    return best_day_since_recap_tweet(count=len(rows), stats_date=target_date)

def _best_since_badge_text(row: dict) -> str:
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

    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    daily = int(row["daily_streams"])
    pct = _fmt_pct(daily, daily_yesterday)
    label = _best_since_badge_text(row)
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


def _post_album_best_day_rows(args, target_date: str, album_lock: Path, *, only_album: str | None = None) -> int:
    album_locked = only_album and album_lock.exists() and not args.no_post and not args.force
    if album_locked:
        print(f"[best_day_since_post] Album already posted for {target_date}, skipping.")
        return 0
    if args.no_albums or args.album_limit <= 0:
        return 0

    if only_album:
        row = _album_row(
            target_date,
            only_album,
            min_days=max(args.album_min_days, best_day_since.LIVE_COLLECTION_MIN_DAYS),
            min_pct_change=best_day_since.LIVE_COLLECTION_MIN_PCT_CHANGE,
        )
        album_rows = [row] if row else []
    else:
        album_rows = _pick_album_rows(
            target_date,
            limit=args.album_limit,
            min_days=args.album_min_days,
        )
    if not album_rows:
        label = f" for {only_album}" if only_album else ""
        print(f"[best_day_since_post] No album best-day-since found{label} on {target_date}.")
        return 0

    album_posted_count = 0
    for index, row in enumerate(album_rows, 1):
        if only_album:
            track_ids = row.get("track_ids") or []
            total_today, total_yesterday, daily_today, daily_yesterday, _daily_last_week = (
                load_history_for_tracks(track_ids, target_date)
            )
            if total_today is None or total_yesterday is None or daily_today is None or daily_yesterday is None or daily_yesterday <= 0:
                print(f"[best_day_since_post] Incomplete comparison history for album {row['album']} on {target_date}; skipping.")
                continue
        existing_album_update_lock = generate_album_update_image.existing_album_update_lock_path(
            row["album"],
            target_date,
        )
        if existing_album_update_lock is not None and not args.no_post and not args.force:
            print(
                f"[best_day_since_post] Album update already posted "
                f"({existing_album_update_lock.name}); skipping best-day-since album post."
            )
            continue

        image_path = generate_album_update_image.generate(row["album"], target_date)
        print(f"[best_day_since_post] Album update {index}/{len(album_rows)}: {row['album']}")
        print(f"[best_day_since_post] Image: {image_path}")

        album_posted_count += 1
        if args.no_post:
            continue

        if not generate_album_update_image.post(row["album"], image_path, target_date):
            print(f"[best_day_since_post] Failed to post album {row['album']}.")
            sys.exit(1)
        generate_album_update_image.album_update_lock_path(row["album"], target_date).write_text(
            f"posted {target_date}\n",
            encoding="utf-8",
        )
        if index < len(album_rows) and args.post_spacing_seconds > 0:
            print(f"[best_day_since_post] Waiting {args.post_spacing_seconds}s before next post...")
            time.sleep(args.post_spacing_seconds)

    if album_posted_count and only_album and not args.no_post:
        album_lock.touch()
    if album_rows:
        print(f"[best_day_since_post] Posted {album_posted_count} album(s) for {target_date}.")
    return album_posted_count


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
        "--post-spacing-seconds",
        type=int,
        default=0,
        help="Extra seconds to wait between Twitter posts; core.twitter enforces account spacing.",
    )
    parser.add_argument("--album-limit", type=int, default=10, help="Number of album posts to add (default: 10).")
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
        "--only-album",
        help=(
            "Post best-day-since for a single album immediately, bypassing the normal "
            "batch flow. Used during collection once all album tracks are done. "
            "Exits 0 if posted, 3 if it doesn't qualify yet."
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
            no_post=args.no_post,
        )
        if result == "posted":
            sys.exit(0)
        if result == "skipped":
            sys.exit(3)
        sys.exit(1)

    day_dir = _day_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    album_lock = day_dir / "best_day_since_album_posted.lock"

    if args.only_album:
        if not args.no_post and not TWITTER_SESSION.exists():
            print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
            sys.exit(1)
        count = _post_album_best_day_rows(args, target_date, album_lock, only_album=args.only_album)
        sys.exit(0 if count else 3)

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

    # Album best-day-since posts are the highest-priority best-day posts:
    # they use the full album update image and should go out before song cards.
    _post_album_best_day_rows(args, target_date, album_lock)

    tracks_by_id = {track["track_id"]: track for track in load_all_tracks()}
    exclude_ids = {t.strip() for t in args.exclude_tracks.split(",") if t.strip()}
    exclude_ids.update(_posted_track_ids_for_date(target_date))
    album_post_counts = _track_album_counts(exclude_ids, tracks_by_id)
    remaining_song_limit = max(0, limit - len(exclude_ids))
    candidate_rows = (
        []
        if track_locked or remaining_song_limit <= 0
        else _pick_rows(
            target_date,
            limit=max(remaining_song_limit * 5, remaining_song_limit + 20),
            min_days=args.min_days,
            min_daily_streams=max(POST_COLLECTION_MIN_SONG_DAILY_STREAMS, MIN_SONG_DAILY_STREAMS_TO_POST),
            min_pct_change=POST_COLLECTION_MIN_SONG_PCT_CHANGE,
            exclude_ids=exclude_ids,
            album_post_counts=album_post_counts,
        )
    )
    rows = _validated_song_rows_for_post(
        candidate_rows,
        target_date=target_date,
        tracks_by_id=tracks_by_id,
        limit=remaining_song_limit,
    )
    if not rows:
        print(f"[best_day_since_post] No best-day-since songs found for {target_date}.")

    covers = load_covers()

    posted_count = 0
    for index, row in enumerate(rows, 1):
        track = row["_post_track"]
        total_today = row["_post_total_today"]
        daily_yesterday = row["_post_daily_yesterday"]

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
        track_lock = _track_posted_lock_path(row["track_id"], target_date)
        track_lock.parent.mkdir(parents=True, exist_ok=True)
        track_lock.touch()
        if index < len(rows) and args.post_spacing_seconds > 0:
            print(f"[best_day_since_post] Waiting {args.post_spacing_seconds}s before next post...")
            time.sleep(args.post_spacing_seconds)

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
        recap_rows = _find_all_rows(target_date, min_days=RECAP_BEST_DAY_MIN_DAYS)
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

