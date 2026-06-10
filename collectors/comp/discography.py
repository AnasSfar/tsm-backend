from __future__ import annotations

import json
import re
from pathlib import Path


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def build_cover_map(covers_path: Path) -> dict:
    """Returns {normalized_album_title → cover_url} from covers.json."""
    if not covers_path.exists():
        return {}
    covers = json.loads(covers_path.read_text(encoding="utf-8-sig"))
    result = {}
    for v in covers.values():
        key = _norm(v.get("title", ""))
        if key and "cover_url" in v:
            result[key] = v["cover_url"]
    return result


def build_track_album_map(discography_root: Path) -> dict:
    """Returns {normalized_track_title → album_title} from albums/*.json + songs.json."""
    result = {}
    albums_dir = discography_root / "albums"
    if albums_dir.exists():
        for album_file in sorted(albums_dir.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            album_name = payload.get("album", "") if isinstance(payload, dict) else ""
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                for track in section.get("tracks", []):
                    title = track.get("title", "")
                    if title:
                        result[_norm(title)] = album_name
    songs_file = discography_root / "songs.json"
    if songs_file.exists():
        try:
            sections = json.loads(songs_file.read_text(encoding="utf-8-sig"))
        except Exception:
            sections = []
        for section in sections:
            album_name = section.get("album", "")
            if not album_name:
                continue
            for track in section.get("tracks", []):
                title = track.get("title", "")
                if title:
                    result[_norm(title)] = album_name
    return result


def build_track_image_map(
    discography_root: Path,
    extra_song_sources: list[Path] = (),
) -> dict:
    """Returns {normalized_track_title → image_url}.

    extra_song_sources: optional additional songs.json-style files to merge in
    (used by regions that also read from web export data).
    """
    result: dict[str, str] = {}
    albums_dir = discography_root / "albums"
    if albums_dir.exists():
        for album_file in sorted(albums_dir.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
                for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                    for track in section.get("tracks", []):
                        title = track.get("title", "")
                        img = (track.get("image_url") or "").strip()
                        if title and img:
                            result[_norm(title)] = img
            except Exception:
                pass
    songs_path = discography_root / "songs.json"
    if songs_path.exists():
        try:
            for track in json.loads(songs_path.read_text(encoding="utf-8-sig")):
                title = track.get("title", "")
                img = (track.get("image_url") or "").strip()
                if title and img:
                    result.setdefault(_norm(title), img)
        except Exception:
            pass
    for extra_path in extra_song_sources:
        if not extra_path or not extra_path.exists():
            continue
        try:
            payload = json.loads(extra_path.read_text(encoding="utf-8-sig"))
            songs_list = payload.get("songs", payload) if isinstance(payload, dict) else payload
            for song in (songs_list or []):
                title = (song.get("title") or song.get("name") or "").strip()
                img = (song.get("image_url") or song.get("apple_music_image_url") or "").strip()
                if title and img:
                    result.setdefault(_norm(title), img)
        except Exception:
            pass
    return result


def get_album_cover(
    track_name: str,
    track_album_map: dict,
    cover_map: dict,
    track_image_map: dict,
    fallback_url: str = "",
) -> str:
    """Cover URL for a track.

    Priority: covers.json (album) > per-track image_url from albums/*.json > fallback CDN URL.
    """
    album_name = track_album_map.get(_norm(track_name), "")
    if album_name:
        cover = cover_map.get(_norm(album_name), "")
        if cover and str(cover).startswith("http"):
            return cover
    track_img = track_image_map.get(_norm(track_name), "")
    if track_img and str(track_img).startswith("http"):
        return track_img
    if fallback_url and str(fallback_url).startswith("http"):
        return fallback_url
    return ""
