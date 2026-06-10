#!/usr/bin/env python3
"""Generate and post a priority Global Spotify Charts card for NEW songs."""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
COLLECTORS_ROOT = ROOT / "collectors"
SPOTIFY_ROOT = ROOT / "collectors" / "spotify"
if str(COLLECTORS_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLECTORS_ROOT))
if str(SPOTIFY_ROOT) not in sys.path:
    sys.path.insert(0, str(SPOTIFY_ROOT))

from comp.song_card import render_song_card, write_song_card_png  # noqa: E402
from core.data_paths import LEGACY_WEBSITE_DATA_DIR, WEB_EXPORT_DATA_DIR, first_existing, spotify_chart_dir  # noqa: E402
from core.twitter import post_with_image  # noqa: E402
import generate_card_images  # noqa: E402

TWITTER_SESSION = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json"
DISCOGRAPHY_DIR = ROOT / "db" / "discography"
CHARTS_HISTORY_GLOBAL = ROOT / "db" / "charts_history_global.csv"
SONGS_JSON = first_existing(WEB_EXPORT_DATA_DIR / "songs.json", LEGACY_WEBSITE_DATA_DIR / "songs.json")
HANDLE = "@tsmuseum13"
PRIORITY_WINDOW_DAYS = 7


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm(value)).strip("_") or "song"


def _fmt(value) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "-"


def _to_int(value) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _fmt_change(value, *, invert: bool = False) -> tuple[str, str]:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return ("", "")
    if amount == 0:
        return ("0", "flat")
    positive = amount > 0
    color = "up" if positive else "down"
    signed = f"+{_fmt(abs(amount))}" if positive else f"-{_fmt(abs(amount))}"
    if invert:
        color = "down" if positive else "up"
    return (signed, color)


def _fmt_pct(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:.1f}%"


def _track_id_from_url(value: str | None) -> str:
    match = re.search(r"track/([A-Za-z0-9]+)", value or "")
    return match.group(1) if match else ""


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _parse_date(value: str | None):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _iter_discography_tracks():
    albums_dir = DISCOGRAPHY_DIR / "albums"
    if albums_dir.exists():
        for path in sorted(albums_dir.glob("*.json")):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            album = payload.get("album") or ""
            for section in payload.get("sections") or []:
                for track in section.get("tracks") or []:
                    if isinstance(track, dict):
                        yield {**track, "album": track.get("album") or album}

    for path in (DISCOGRAPHY_DIR / "songs.json", DISCOGRAPHY_DIR / "features.json", DISCOGRAPHY_DIR / "misc.json"):
        payload = _read_json(path)
        sections = payload if isinstance(payload, list) else []
        for section in sections:
            album = section.get("album") or section.get("section") or ""
            for track in section.get("tracks") or []:
                if isinstance(track, dict):
                    yield {**track, "album": track.get("album") or album}


def _load_song_meta() -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    by_title: dict[str, dict] = {}

    def remember(target: dict[str, dict], key: str, item: dict) -> None:
        if not key:
            return
        existing = target.setdefault(key, item)
        for field in ("track_id", "title", "album", "image_url", "release_date"):
            if not existing.get(field) and item.get(field):
                existing[field] = item[field]

    payload = _read_json(SONGS_JSON)
    for song in (payload.get("songs") if isinstance(payload, dict) else []) or []:
        tid = str(song.get("track_id") or "").strip()
        title = str(song.get("title") or song.get("name") or "").strip()
        item = {
            "track_id": tid,
            "title": title,
            "album": song.get("primary_album") or song.get("album") or "",
            "image_url": song.get("image_url") or song.get("apple_music_image_url") or "",
            "release_date": song.get("release_date") or song.get("released_at") or "",
        }
        remember(by_id, tid, item)
        remember(by_title, _norm(title), item)

    for track in _iter_discography_tracks():
        tid = _track_id_from_url(track.get("url") or track.get("spotify_url")) or str(track.get("track_id") or "")
        title = str(track.get("title") or "").strip()
        item = {
            "track_id": tid,
            "title": title,
            "album": track.get("album") or "",
            "image_url": track.get("image_url") or "",
            "release_date": track.get("release_date") or track.get("released_at") or "",
        }
        remember(by_id, tid, item)
        remember(by_title, _norm(title), item)

    return {"by_id": by_id, "by_title": by_title}


def _chart_path(chart_date: str) -> Path:
    return spotify_chart_dir("global", chart_date) / f"ts_chart_{chart_date}.json"


def _previous_chart_date(chart_date: str) -> str | None:
    day = _parse_date(chart_date)
    if day is None:
        return None
    return (day - timedelta(days=1)).isoformat()


def _selector_matches(row: dict, selector: str) -> bool:
    needle = (selector or "").strip()
    if not needle:
        return False
    folded = needle.casefold()
    return folded in {
        str(row.get("track_id") or "").casefold(),
        str(row.get("track_name") or row.get("song_name") or "").casefold(),
    }


def _same_track(left: dict, right: dict) -> bool:
    left_id = str(left.get("track_id") or "").casefold()
    right_id = str(right.get("track_id") or "").casefold()
    if left_id and right_id and left_id == right_id:
        return True
    return _norm(str(left.get("title") or left.get("track_name") or left.get("song_name") or "")) == _norm(
        str(right.get("title") or right.get("track_name") or right.get("song_name") or "")
    )


def _is_api_new_row(row: dict) -> bool:
    if row.get("is_new") is True:
        return True
    if "is_new" in row:
        return False

    # Backward-compatible fallback for old snapshots. New runs set is_new
    # directly from Spotify Charts API fields in worldwide/daily.py.
    previous_rank = row.get("previous_rank")
    total_days = row.get("total_days") or row.get("streak")
    try:
        total_days_int = int(total_days) if total_days not in (None, "") else 1
    except (TypeError, ValueError):
        total_days_int = 1
    return previous_rank in (None, "", 0) and total_days_int <= 1


def _is_first_global_day(row: dict) -> bool:
    previous_rank = row.get("previous_rank")
    total_days = row.get("total_days") or row.get("streak")
    try:
        total_days_int = int(total_days) if total_days not in (None, "") else None
    except (TypeError, ValueError):
        total_days_int = None
    return previous_rank in (None, "", 0) and (total_days_int is None or total_days_int <= 1)


def _global_debut_rank(row: dict) -> int | None:
    streams = _to_int(row.get("streams"))
    title = str(row.get("title") or row.get("track_name") or row.get("song_name") or "").strip()
    if streams <= 0 or not title:
        return None

    debuts: dict[str, dict] = {
        _norm(title): {
            "title": title,
            "streams": streams,
        }
    }
    if CHARTS_HISTORY_GLOBAL.exists():
        with CHARTS_HISTORY_GLOBAL.open(newline="", encoding="utf-8-sig") as f:
            for csv_row in csv.DictReader(f):
                song_name = str(csv_row.get("song_name") or "").strip()
                key = _norm(song_name)
                if not key:
                    continue
                if key in debuts:
                    continue
                previous_rank = csv_row.get("previous_rank")
                total_days = str(csv_row.get("total_days") or "")
                if total_days not in {"1", "1.0"} and previous_rank not in {"", "-1"}:
                    continue
                debuts[key] = {
                    "title": song_name,
                    "streams": _to_int(csv_row.get("streams")),
                }

    ranked = sorted(debuts.values(), key=lambda item: int(item.get("streams") or 0), reverse=True)
    target_key = _norm(title)
    for index, debut in enumerate(ranked, 1):
        if _norm(str(debut.get("title") or "")) == target_key:
            return index
    return None


def _new_card_tweet(rows: list[dict], chart_date: str) -> str:
    date_text = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    if len(rows) == 1:
        row = rows[0]
        title = str(row.get("title") or "New song")
        rank = row.get("rank")
        streams = _fmt(row.get("streams"))
        if _is_first_global_day(row):
            debut_rank_text = ""
            debut_rank = _global_debut_rank(row)
            if debut_rank:
                debut_rank_text = f"\n\nIt marks her {_ordinal(debut_rank)} biggest debut on the chart."
            body = (
                f'"{title}" charted at #{rank} on Global Spotify Charts '
                f"with {streams} streams ({date_text})."
                f"{debut_rank_text}"
            )
        else:
            body = (
                f'"{title}" ranks at #{rank} on Global Spotify Charts '
                f"with {streams} streams ({date_text})."
            )
            streams_change = row.get("streams_change")
            if streams_change not in (None, ""):
                try:
                    delta = int(streams_change)
                    sign = "+" if delta > 0 else ""
                    body += f"\n\nDaily change: {sign}{_fmt(delta)} streams."
                except (TypeError, ValueError):
                    pass
        return f"{body}\n\nSee full update here : https://thetsmuseum.app/charts?region=global&view=today"

    first_day_count = sum(1 for row in rows if _is_first_global_day(row))
    if first_day_count == len(rows):
        headline = f"{len(rows)} new Taylor Swift songs charted on Global Spotify Charts ({date_text})."
    elif first_day_count:
        headline = (
            f"{len(rows)} recent Taylor Swift songs are charting on Global Spotify Charts ({date_text}), "
            f"including {first_day_count} new debut{'s' if first_day_count > 1 else ''}."
        )
    else:
        headline = f"{len(rows)} recent Taylor Swift songs are charting on Global Spotify Charts ({date_text})."
    return f"{headline}\n\nSee full update here : https://thetsmuseum.app/charts?region=global&view=today"


def _load_chart_rows(chart_date: str) -> list[dict]:
    path = _chart_path(chart_date)
    rows = _read_json(path)
    if not isinstance(rows, list):
        return []
    return rows


def _with_daily_changes(rows: list[dict], chart_date: str) -> list[dict]:
    previous_date = _previous_chart_date(chart_date)
    previous_rows = _enrich_rows(_load_chart_rows(previous_date)) if previous_date else []
    out = []
    for row in rows:
        item = dict(row)
        previous = next((prev for prev in previous_rows if _same_track(item, prev)), None)
        previous_rank = row.get("previous_rank")
        if previous_rank in (None, "") and previous:
            previous_rank = previous.get("rank")
        try:
            if previous_rank not in (None, "") and row.get("rank") not in (None, ""):
                item["rank_change"] = int(previous_rank) - int(row.get("rank"))
        except (TypeError, ValueError):
            pass
        if previous and previous.get("streams") not in (None, "") and row.get("streams") not in (None, ""):
            try:
                item["streams_change"] = int(row.get("streams")) - int(previous.get("streams"))
                item["streams_change_pct"] = item["streams_change"] / int(previous.get("streams")) * 100
            except (TypeError, ValueError):
                pass
        out.append(item)
    return out


def _global_debut_date_for_song(row: dict, chart_day) -> "date | None":
    """Return the most recent Global chart debut date for this song within PRIORITY_WINDOW_DAYS."""
    title = str(row.get("title") or row.get("track_name") or row.get("song_name") or "").strip()
    track_id = str(row.get("track_id") or "").strip()
    if not title and not track_id:
        return None
    if not CHARTS_HISTORY_GLOBAL.exists():
        return None
    cutoff = chart_day - timedelta(days=PRIORITY_WINDOW_DAYS)
    best = None
    with CHARTS_HISTORY_GLOBAL.open(newline="", encoding="utf-8-sig") as f:
        for csv_row in csv.DictReader(f):
            if str(csv_row.get("total_days") or "").strip() not in {"1", "1.0"}:
                continue
            row_date = _parse_date(csv_row.get("date"))
            if row_date is None or row_date < cutoff or row_date > chart_day:
                continue
            song_name = str(csv_row.get("song_name") or "").strip()
            if (track_id and song_name == track_id) or _norm(song_name) == _norm(title):
                if best is None or row_date > best:
                    best = row_date
    return best


def _load_priority_rows(chart_date: str, *, force_songs: set[str] | None = None) -> list[dict]:
    chart_day = _parse_date(chart_date)
    rows = _enrich_rows(_load_chart_rows(chart_date))
    force_songs = force_songs or set()
    priority_rows = []
    for row in rows:
        if any(_selector_matches(row, selector) for selector in force_songs):
            forced = dict(row)
            forced["is_new"] = True
            forced["priority_reason"] = "forced"
            priority_rows.append(forced)
            continue
        if _is_api_new_row(row):
            item = dict(row)
            item["priority_reason"] = "api_new"
            priority_rows.append(item)
            continue

        release_day = _parse_date(row.get("release_date"))
        if chart_day and release_day:
            age_days = (chart_day - release_day).days
            if 0 <= age_days <= PRIORITY_WINDOW_DAYS:
                item = dict(row)
                item["release_age_days"] = age_days
                item["priority_reason"] = "release_window"
                priority_rows.append(item)
                continue

        if chart_day:
            debut_date = _global_debut_date_for_song(row, chart_day)
            if debut_date:
                item = dict(row)
                item["debut_date"] = debut_date.isoformat()
                item["priority_reason"] = "global_debut_window"
                priority_rows.append(item)

    return _with_daily_changes(sorted(priority_rows, key=lambda item: int(item.get("rank") or 9999)), chart_date)


def _enrich_rows(rows: list[dict]) -> list[dict]:
    meta = _load_song_meta()
    out = []
    for row in rows:
        tid = str(row.get("track_id") or "").strip()
        title = str(row.get("track_name") or row.get("song_name") or "").strip()
        item = meta["by_id"].get(tid) if tid else None
        if item is None:
            item = meta["by_title"].get(_norm(title), {})
        out.append({
            **row,
            "track_id": tid or item.get("track_id") or "",
            "title": title or item.get("title") or "New song",
            "album": item.get("album") or "",
            "image_url": row.get("image_url") or item.get("image_url") or "",
            "release_date": row.get("release_date") or item.get("release_date") or "",
        })
    return out


def _build_html(rows: list[dict], chart_date: str) -> tuple[str, str]:
    primary = rows[0]
    date_text = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    count = len(rows)

    display_title = str(primary.get("title") or "New Song") if count == 1 else f"{count} New Songs"
    rank_val = str(primary.get("rank") or "-")
    streams_val = _fmt(primary.get("streams"))
    extra_text = f"+ {count - 1} more new song{'s' if count - 1 > 1 else ''}" if count > 1 else ""
    slug_val = _slug(primary.get("title") or "new_song") if count == 1 else f"{count}_new_songs"

    if primary.get("is_new"):
        rank_badge = "NEW"
        rank_badge_class = "new"
    elif primary.get("previous_rank") in (None, "", 0):
        rank_badge = "RE"
        rank_badge_class = "re"
    else:
        rank_badge, rank_badge_class = _fmt_change(primary.get("rank_change"))

    streams_pct = primary.get("streams_change_pct")
    if streams_pct not in (None, ""):
        streams_badge = _fmt_pct(streams_pct)
        streams_badge_class = "up" if float(streams_pct) > 0 else ("down" if float(streams_pct) < 0 else "flat")
    else:
        streams_badge = ""
        streams_badge_class = "flat"

    html_text = render_song_card(
        title=display_title,
        eyebrow="Global Spotify Charts",
        subtitle=f"Global Spotify Charts update - {date_text}",
        stats=[
            {"label": "Rank", "value": f"#{rank_val}", "badge": rank_badge, "badge_class": rank_badge_class},
            {"label": "Streams", "value": streams_val, "badge": streams_badge, "badge_class": streams_badge_class},
        ],
        cover_url=primary.get("image_url"),
        footer_left=HANDLE,
        footer_right=date_text,
        extra=extra_text,
    )
    return html_text, slug_val


def generate_card(chart_date: str, *, force_songs: set[str] | None = None) -> Path | None:
    rows = _load_priority_rows(chart_date, force_songs=force_songs)
    if not rows:
        print(f"[global-new] No priority Global Spotify Chart entries for {chart_date}.")
        return None
    html_text, slug = _build_html(rows, chart_date)
    out_dir = spotify_chart_dir("global", chart_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"new_card_{slug}.png"
    tmp_path = out_dir / "_new_card_tmp.html"
    write_song_card_png(html_text, out_path, tmp_path)
    print(f"[global-new] Card generated: {out_path}")
    return out_path


def post_card(
    chart_date: str,
    *,
    force: bool = False,
    no_post: bool = False,
    force_songs: set[str] | None = None,
) -> int:
    rows = _load_priority_rows(chart_date, force_songs=force_songs)
    if not rows:
        print(f"[global-new] No priority post needed for {chart_date}.")
        return 0

    out_dir = spotify_chart_dir("global", chart_date)
    lock_path = out_dir / "global_new_releases_posted.json"
    slugs = sorted(_slug(row.get("track_id") or row["title"]) for row in rows)
    if lock_path.exists() and not force:
        try:
            posted = set(json.loads(lock_path.read_text(encoding="utf-8")).get("posted", []))
        except Exception:
            posted = set()
        if set(slugs).issubset(posted):
            print(f"[global-new] Priority Global card already posted for {chart_date}.")
            return 0

    image_path = generate_card(chart_date, force_songs=force_songs)
    if image_path is None:
        return 0

    tweet = _new_card_tweet(rows, chart_date)
    print(f"[global-new] Tweet: {tweet}")
    if no_post:
        print("[global-new] Twitter post skipped (--no-post).")
        return 0
    if not TWITTER_SESSION.exists():
        print(f"[global-new] Twitter session missing: {TWITTER_SESSION}")
        return 1
    if not post_with_image(tweet, image_path, TWITTER_SESSION):
        print("[global-new] Twitter post failed.")
        return 1
    lock_path.write_text(
        json.dumps({"date": chart_date, "posted": slugs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("[global-new] Priority Global card posted.")
    return 0


def _worldwide_lock_path(chart_date: str) -> Path:
    return spotify_chart_dir("worldwide", chart_date) / "cards" / "priority_global_new_posted.json"


def _load_worldwide_song_meta() -> dict[str, dict]:
    songs_raw = generate_card_images._load_json(generate_card_images.SONGS_JSON)
    songs_list = songs_raw.get("songs", songs_raw) if isinstance(songs_raw, dict) else songs_raw
    return {str(s["track_id"]): s for s in songs_list if isinstance(s, dict) and s.get("track_id")}


def post_worldwide_cards(
    chart_date: str,
    *,
    force: bool = False,
    no_post: bool = False,
    force_songs: set[str] | None = None,
) -> int:
    rows = _load_priority_rows(chart_date, force_songs=force_songs)
    if not rows:
        print(f"[global-new-worldwide] No priority worldwide card needed for {chart_date}.")
        return 0

    data = generate_card_images._load_json(generate_card_images.WORLDWIDE_JSON)
    if not isinstance(data, dict) or data.get("date") != chart_date:
        print(f"[global-new-worldwide] Worldwide snapshot not ready for {chart_date}.")
        return 0

    by_track = data.get("by_track", {})
    if not isinstance(by_track, dict) or not by_track:
        print(f"[global-new-worldwide] Worldwide snapshot empty for {chart_date}.")
        return 0

    song_meta = _load_worldwide_song_meta()
    prev_counts = generate_card_images._load_prev_country_counts(chart_date)
    out_dir = spotify_chart_dir("worldwide", chart_date) / "cards"
    lock_path = _worldwide_lock_path(chart_date)

    already_posted: set[str] = set()
    if lock_path.exists() and not force:
        try:
            already_posted = set(json.loads(lock_path.read_text(encoding="utf-8")).get("posted", []))
        except Exception:
            already_posted = set()

    posts: list[tuple[str, str, Path]] = []
    for row in rows:
        track_id = str(row.get("track_id") or "").strip()
        if track_id not in by_track:
            print(f"[global-new-worldwide] Skip {row.get('title')}: not in worldwide snapshot.")
            continue
        meta = song_meta.get(track_id, {"track_id": track_id, "title": row.get("title") or track_id})
        slug = generate_card_images._slugify(str(meta.get("title") or row.get("title") or track_id))
        if slug in already_posted:
            print(f"[global-new-worldwide] Skip {meta.get('title')}: already posted.")
            continue
        base_image_path = out_dir / f"{slug}.png"
        image_path = out_dir / f"worldwide_new_card_{slug}.png"
        if not base_image_path.exists() or force:
            rc = generate_card_images.generate(chart_date, min_countries=1, force=force, post=False)
            if rc != 0:
                return rc
        if not base_image_path.exists():
            print(f"[global-new-worldwide] Missing card image: {base_image_path}")
            return 1
        if not image_path.exists() or force:
            shutil.copyfile(base_image_path, image_path)
        tweet = generate_card_images._build_tweet(
            meta,
            by_track.get(track_id) or [],
            chart_date,
            prev_counts.get(track_id),
        )
        posts.append((slug, tweet, image_path))

    if not posts:
        print(f"[global-new-worldwide] No pending worldwide priority posts for {chart_date}.")
        return 0

    for slug, tweet, image_path in posts:
        print(f"[global-new-worldwide] {slug}: {tweet}")
        print(f"[global-new-worldwide] Image: {image_path}")

    if no_post:
        print("[global-new-worldwide] Twitter post skipped (--no-post).")
        return 0
    if not TWITTER_SESSION.exists():
        print(f"[global-new-worldwide] Twitter session missing: {TWITTER_SESSION}")
        return 1

    posted = set(already_posted)
    for slug, tweet, image_path in posts:
        if not post_with_image(tweet, image_path, TWITTER_SESSION):
            print(f"[global-new-worldwide] Twitter post failed for {slug}.")
            return 1
        posted.add(slug)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({"date": chart_date, "posted": sorted(posted)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"[global-new-worldwide] Posted {len(posts)} worldwide priority card(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", help="Chart date YYYY-MM-DD")
    parser.add_argument("--post", action="store_true", help="Post to @tsmuseum13")
    parser.add_argument("--post-worldwide", action="store_true", help="Post matching worldwide cards individually.")
    parser.add_argument("--no-post", action="store_true", help="Generate only")
    parser.add_argument("--force", action="store_true", help="Ignore posted lock")
    parser.add_argument(
        "--force-song",
        action="append",
        default=[],
        help="Test mode: treat this title or track_id as NEW for this run.",
    )
    args = parser.parse_args()
    force_songs = set(args.force_song or [])
    if args.post_worldwide:
        return post_worldwide_cards(args.date, force=args.force, no_post=args.no_post, force_songs=force_songs)
    if args.post and not args.no_post:
        return post_card(args.date, force=args.force, no_post=False, force_songs=force_songs)
    image_path = generate_card(args.date, force_songs=force_songs)
    return 0 if image_path is not None or not _load_priority_rows(args.date, force_songs=force_songs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
