#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
DISCOGRAPHY_DIR = ROOT / "db" / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
SONGS_PATH = DISCOGRAPHY_DIR / "songs.json"
FEATURES_PATH = DISCOGRAPHY_DIR / "features.json"
MISC_PATH = DISCOGRAPHY_DIR / "misc.json"

TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")
SPOTIFY_TRACKS_URL = "https://api.spotify.com/v1/tracks"
TOKEN_URL = "https://accounts.spotify.com/api/token"
USER_AGENT = "tsm-backend-spotify-metadata/1.0"
SPOTIFY_COLLECTORS_DIR = ROOT / "collectors" / "spotify"
SPOTIFY_SCRIPT_DIR = ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts"

METADATA_KEYS = (
    "duration_ms",
    "track_number",
    "spotify_album_id",
    "spotify_album_uri",
    "album_type",
    "total_tracks",
    "popularity",
)


@dataclass
class TrackLocation:
    path: Path
    payload: Any
    track: dict[str, Any]
    track_id: str


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def backup(path: Path) -> None:
    if path.exists():
        target = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, target)
        print(f"[backup] {target.relative_to(ROOT)}")


def extract_track_id(url: str | None) -> str:
    match = TRACK_ID_RE.search(url or "")
    return match.group(1) if match else ""


def get_access_token(session: requests.Session) -> str:
    env_token = os.getenv("SPOTIFY_ACCESS_TOKEN", "").strip()
    if env_token:
        return env_token

    session_token = get_access_token_from_session()
    if session_token:
        return session_token

    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "Spotify session token not found. Refresh spotify_session.json, or set SPOTIFY_ACCESS_TOKEN."
        )

    response = session.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    token = str((response.json() or {}).get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Spotify client credentials token response did not include access_token.")
    return token


def get_access_token_from_session() -> str:
    # spotify_api imports core.*, so both roots must be importable.
    for path in (SPOTIFY_COLLECTORS_DIR, SPOTIFY_SCRIPT_DIR):
        path_text = str(path)
        if path_text not in sys.path:
            sys.path.append(path_text)
    try:
        from spotify_api import TokenManager  # type: ignore
    except Exception as exc:
        print(f"[token] Spotify session helper unavailable: {exc}")
        return ""

    manager = TokenManager()
    if not manager.capture():
        print("[token] Spotify session token capture failed")
        return ""
    token = str((manager.get() or {}).get("bearer") or "").strip()
    if token:
        print("[token] using Spotify bearer from existing session")
    return token


def iter_tracks_from_payload(path: Path, payload: Any) -> list[TrackLocation]:
    locations: list[TrackLocation] = []
    if isinstance(payload, list):
        sections = payload
    elif isinstance(payload, dict):
        sections = payload.get("sections") or []
    else:
        sections = []

    for section in sections:
        if not isinstance(section, dict):
            continue
        for track in section.get("tracks") or []:
            if not isinstance(track, dict):
                continue
            track_id = extract_track_id(track.get("url") or track.get("spotify_url"))
            if track_id:
                locations.append(TrackLocation(path, payload, track, track_id))
    return locations


def load_discography_locations() -> tuple[dict[Path, Any], list[TrackLocation]]:
    payloads: dict[Path, Any] = {}
    locations: list[TrackLocation] = []

    for path in (SONGS_PATH, FEATURES_PATH, MISC_PATH):
        if not path.exists():
            continue
        payload = read_json(path)
        payloads[path] = payload
        locations.extend(iter_tracks_from_payload(path, payload))

    for path in sorted(ALBUMS_DIR.glob("*.json"), key=lambda item: item.name.casefold()):
        payload = read_json(path)
        payloads[path] = payload
        locations.extend(iter_tracks_from_payload(path, payload))

    return payloads, locations


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def fetch_tracks(session: requests.Session, token: str, track_ids: list[str], sleep: float) -> dict[str, dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    by_id: dict[str, dict[str, Any]] = {}
    for batch in chunks(track_ids, 50):
        if sleep:
            time.sleep(sleep)
        response = None
        for attempt in range(1, 8):
            response = session.get(
                SPOTIFY_TRACKS_URL,
                params={"ids": ",".join(batch)},
                headers=headers,
                timeout=30,
            )
            if response.status_code != 429:
                response.raise_for_status()
                break
            retry_after = response.headers.get("Retry-After", "").strip()
            try:
                wait = float(retry_after)
            except ValueError:
                wait = min(60.0, 2.0 * attempt)
            wait = max(wait, 1.0)
            print(f"[rate-limit] Spotify 429, retry in {wait:.1f}s (attempt {attempt}/7)")
            time.sleep(wait)
        if response is None:
            continue
        if response.status_code == 429:
            response.raise_for_status()
        for item in (response.json() or {}).get("tracks") or []:
            if isinstance(item, dict) and item.get("id"):
                by_id[str(item["id"])] = item
        print(f"[fetch] {len(by_id)}/{len(track_ids)} track(s)")
    return by_id


def metadata_from_spotify(track: dict[str, Any]) -> dict[str, Any]:
    album = track.get("album") or {}
    return {
        "duration_ms": track.get("duration_ms"),
        "track_number": track.get("track_number"),
        "spotify_album_id": album.get("id"),
        "spotify_album_uri": album.get("uri"),
        "album_type": album.get("album_type"),
        "total_tracks": album.get("total_tracks"),
        "popularity": track.get("popularity"),
    }


def has_changed(track: dict[str, Any], metadata: dict[str, Any]) -> bool:
    return any(track.get(key) != value for key, value in metadata.items() if value is not None)


def should_include(location: TrackLocation, only: str | None) -> bool:
    if not only:
        return True
    needle = only.strip().casefold()
    haystack = " ".join(
        str(location.track.get(key) or "")
        for key in ("title", "base_title", "title_clean", "album")
    ).casefold()
    return needle in haystack or needle in location.track_id.casefold()


def run(args: argparse.Namespace) -> int:
    load_env()
    payloads, locations = load_discography_locations()
    locations = [location for location in locations if should_include(location, args.track)]
    if args.skip_existing:
        locations = [
            location for location in locations
            if any(location.track.get(key) is None for key in METADATA_KEYS)
        ]
    if args.limit:
        locations = locations[:args.limit]

    unique_ids = sorted({location.track_id for location in locations})
    print(f"[load] {len(locations)} track occurrence(s), {len(unique_ids)} unique Spotify track id(s)")
    if not unique_ids:
        return 0

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    token = get_access_token(session)
    spotify_tracks = fetch_tracks(session, token, unique_ids, args.sleep)

    touched: set[Path] = set()
    changed = 0
    missing = 0
    for location in locations:
        spotify_track = spotify_tracks.get(location.track_id)
        if not spotify_track:
            missing += 1
            continue
        metadata = metadata_from_spotify(spotify_track)
        if not has_changed(location.track, metadata):
            continue
        changed += 1
        title = location.track.get("title") or location.track.get("base_title") or location.track_id
        print(
            f"[change] {title}: "
            + ", ".join(f"{key}={metadata[key]!r}" for key in METADATA_KEYS if metadata.get(key) is not None)
        )
        if args.apply:
            for key, value in metadata.items():
                if value is not None:
                    location.track[key] = value
            touched.add(location.path)

    if args.apply:
        for path in sorted(touched, key=lambda item: str(item).casefold()):
            backup(path)
            write_json(path, payloads[path])
            print(f"[write] {path.relative_to(ROOT)}")
    else:
        print("[dry-run] No files written. Re-run with --apply to save metadata.")

    print(f"[summary] changed={changed}, missing_from_spotify={missing}, files_touched={len(touched)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill selected Spotify track/album metadata into db/discography JSON files."
    )
    parser.add_argument("--apply", action="store_true", help="Write changes to db/discography.")
    parser.add_argument("--track", help="Only process matching title, album, or Spotify track ID.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum track occurrences to process.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip tracks that already have all target fields.")
    parser.add_argument("--sleep", type=float, default=0.1, help="Delay between Spotify batch requests.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
