#!/usr/bin/env python3
"""Post independent X updates for Spotify debut releases.

Detects tracks whose Spotify API release_date is the target stats date. If
multiple tracks from the same album are released on that date, the album gets
one standalone post. Released tracks not covered by an album post get one
standalone song post.

Usage:
  python post_debut_releases.py 2026-06-01
  python post_debut_releases.py 2026-06-01 --no-post
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
REPO_ROOT = SCRIPT_DIR.parents[4]
DB_DIR = REPO_ROOT / "db"
HISTORY_PATH = DB_DIR / "streams_history.csv"
ALBUMS_DIR = DB_DIR / "discography" / "albums"
SONGS_PATH = DB_DIR / "discography" / "songs.json"
TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(ROOT.parent))
from core.data_paths import update_streams_dir  # noqa: E402
from core.twitter import post_thread  # noqa: E402


def _track_id(url: str | None) -> str | None:
    if not url or "/track/" not in url:
        return None
    return url.split("/track/", 1)[1].split("?", 1)[0].split("/", 1)[0].strip() or None


def _fmt(n: int | None) -> str:
    return f"{int(n or 0):,}"


def _date_label(iso_day: str) -> str:
    parsed = date.fromisoformat(iso_day)
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def _remember_track(meta: dict[str, dict], track_id: str, *, title: str, album: str, release_date: str | None) -> None:
    item = meta.setdefault(track_id, {"title": title, "album": album, "release_date": None})
    if item.get("title") in (None, ""):
        item["title"] = title
    if item.get("album") in (None, ""):
        item["album"] = album
    if item.get("release_date") in (None, "") and release_date:
        item["release_date"] = release_date


def _load_album_tracks() -> tuple[dict[str, list[str]], dict[str, dict]]:
    album_tracks: dict[str, list[str]] = defaultdict(list)
    meta: dict[str, dict] = {}

    if not ALBUMS_DIR.exists():
        return {}, {}

    for path in sorted(ALBUMS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        album = payload.get("album") if isinstance(payload, dict) else None
        if not album:
            continue
        for section in payload.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for track in section.get("tracks") or []:
                if not isinstance(track, dict):
                    continue
                tid = _track_id(track.get("url") or track.get("spotify_url"))
                title = (track.get("title") or "").strip()
                if not tid or not title:
                    continue
                if tid not in album_tracks[album]:
                    album_tracks[album].append(tid)
                _remember_track(
                    meta,
                    tid,
                    title=title,
                    album=album,
                    release_date=track.get("release_date") or None,
                )

    return dict(album_tracks), meta


def _load_misc_tracks(meta: dict[str, dict]) -> None:
    if not SONGS_PATH.exists():
        return
    try:
        sections = json.loads(SONGS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return
    for section in sections if isinstance(sections, list) else []:
        album = (section.get("album") or section.get("section") or "").strip()
        for track in section.get("tracks") or []:
            tid = _track_id(track.get("url") or track.get("spotify_url"))
            title = (track.get("title") or "").strip()
            if tid and title:
                _remember_track(
                    meta,
                    tid,
                    title=title,
                    album=album,
                    release_date=track.get("release_date") or None,
                )


def _load_rows_for_date(target_date: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not HISTORY_PATH.exists():
        return rows

    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = (row.get("track_id") or "").strip()
            day = (row.get("date") or "").strip()
            if not tid or day != target_date:
                continue
            try:
                streams = int((row.get("streams") or "0").strip() or "0")
            except ValueError:
                streams = 0
            rows[tid] = {"date": day, "streams": streams}
    return rows


def _build_posts(target_date: str) -> list[tuple[str, str]]:
    album_tracks, meta = _load_album_tracks()
    _load_misc_tracks(meta)
    day_rows = _load_rows_for_date(target_date)
    debut_ids = {
        tid for tid, item in meta.items()
        if item.get("release_date") == target_date and tid in day_rows
    }
    if not debut_ids:
        return []

    covered_song_ids: set[str] = set()
    posts: list[tuple[str, str]] = []
    date_text = _date_label(target_date)

    for album, track_ids in sorted(album_tracks.items(), key=lambda item: item[0].casefold()):
        album_ids = {
            tid for tid in track_ids
            if tid in debut_ids and meta.get(tid, {}).get("release_date") == target_date
        }
        if len(album_ids) < 2:
            continue
        total = sum(day_rows[tid]["streams"] for tid in album_ids)
        covered_song_ids.update(album_ids)
        posts.append((
            f"album:{album}",
            (
                f'"{album}" debuted with {_fmt(total)} streams on Spotify ({date_text}).\n\n'
                "See full update here : https://thetsmuseum.app/streams/latest"
            ),
        ))

    for tid in sorted(debut_ids - covered_song_ids, key=lambda x: meta.get(x, {}).get("title", x).casefold()):
        item = meta.get(tid, {})
        title = item.get("title") or tid
        streams = day_rows[tid]["streams"]
        posts.append((
            f"song:{tid}",
            (
                f'"{title}" debuted with {_fmt(streams)} streams on Spotify ({date_text}).\n\n'
                "See full update here : https://thetsmuseum.app/streams/latest"
            ),
        ))

    return posts


def post_debut_releases(target_date: str, *, no_post: bool = False) -> int:
    day_dir = update_streams_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    lock_path = day_dir / "debut_releases_posted.json"
    already_posted: set[str] = set()
    if lock_path.exists():
        try:
            already_posted = set(json.loads(lock_path.read_text(encoding="utf-8")).get("posted", []))
        except Exception:
            already_posted = set()

    posts = [(slug, text) for slug, text in _build_posts(target_date) if slug not in already_posted]
    if not posts:
        print(f"[debut_releases] No new debut release posts for {target_date}.")
        return 0

    for slug, text in posts:
        print(f"[debut_releases] {slug}: {text}")

    if no_post:
        print("[debut_releases] Twitter posts skipped (--no-post).")
        return 0

    if not TWITTER_SESSION.exists():
        print(f"[debut_releases] Twitter session not found: {TWITTER_SESSION}")
        return 1

    posted = set(already_posted)
    for slug, text in posts:
        if not post_thread([text], TWITTER_SESSION):
            print(f"[debut_releases] Failed to post {slug}.")
            return 1
        posted.add(slug)
        lock_path.write_text(
            json.dumps({"date": target_date, "posted": sorted(posted)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"[debut_releases] Posted {len(posts)} debut release(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--no-post", action="store_true")
    args = parser.parse_args()
    return post_debut_releases(args.date, no_post=args.no_post)


if __name__ == "__main__":
    raise SystemExit(main())
