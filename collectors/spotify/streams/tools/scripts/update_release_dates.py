#!/usr/bin/env python3
"""Refresh discography release_date fields from Spotify's web-player API."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[4]
DISCOGRAPHY_DIR = REPO_ROOT / "db" / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
SONGS_JSON = DISCOGRAPHY_DIR / "songs.json"

sys.path.insert(0, str(SCRIPT_DIR))
from catalog_gap_report import _artist_release_tracks  # noqa: E402
from history_store import extract_track_id  # noqa: E402
from spotify_api import TokenManager  # noqa: E402


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _release_dates_from_api() -> dict[str, str]:
    token_mgr = TokenManager()
    if not token_mgr.capture():
        print("[release_dates] Spotify tokens unavailable; skipping.")
        return {}

    tokens = token_mgr.get()
    if not tokens.get("bearer") or not tokens.get("client_token"):
        print("[release_dates] Spotify tokens missing; skipping.")
        return {}

    with requests.Session() as session:
        tracks = _artist_release_tracks(session, tokens=tokens)

    release_dates: dict[str, str] = {}
    for track in tracks:
        track_id = str(track.get("track_id") or "").strip()
        dates = [
            str(release.get("release_date") or "").strip()
            for release in track.get("releases") or []
            if str(release.get("release_date") or "").strip()
        ]
        if track_id and dates:
            release_dates[track_id] = min(dates)
    return release_dates


def _update_track(track: dict, release_dates: dict[str, str]) -> bool:
    track_id = extract_track_id(track.get("url") or track.get("spotify_url") or "")
    if not track_id:
        return False
    release_date = release_dates.get(track_id)
    if not release_date or track.get("release_date") == release_date:
        return False
    track["release_date"] = release_date
    return True


def _update_album_files(release_dates: dict[str, str]) -> int:
    changed_files = 0
    if not ALBUMS_DIR.exists():
        return changed_files

    for path in sorted(ALBUMS_DIR.glob("*.json")):
        try:
            payload = _load_json(path)
        except Exception as exc:
            print(f"[release_dates] Failed to read {path.name}: {exc}")
            continue
        changed = False
        for section in payload.get("sections") or []:
            for track in section.get("tracks") or []:
                if isinstance(track, dict) and _update_track(track, release_dates):
                    changed = True
        if changed:
            _write_json(path, payload)
            changed_files += 1
    return changed_files


def _update_songs_json(release_dates: dict[str, str]) -> bool:
    if not SONGS_JSON.exists():
        return False
    try:
        sections = _load_json(SONGS_JSON)
    except Exception as exc:
        print(f"[release_dates] Failed to read songs.json: {exc}")
        return False

    changed = False
    for section in sections if isinstance(sections, list) else []:
        for track in section.get("tracks") or []:
            if isinstance(track, dict) and _update_track(track, release_dates):
                changed = True

    if changed:
        _write_json(SONGS_JSON, sections)
    return changed


def main() -> int:
    release_dates = _release_dates_from_api()
    if not release_dates:
        return 0

    album_files = _update_album_files(release_dates)
    songs_changed = _update_songs_json(release_dates)
    print(
        "[release_dates] Updated "
        f"{album_files} album file(s), songs.json={'yes' if songs_changed else 'no'}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
