from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    from .track_cover_cache import get_cached_cover
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from comp.track_cover_cache import get_cached_cover


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _title_lookup_keys(title: str) -> list[str]:
    """Normalized keys for chart titles that may include source subtitles."""
    keys: list[str] = []
    candidates = [title]
    soundtrack_base = re.sub(r"\s+-\s+from\b.+$", "", title or "", flags=re.IGNORECASE).strip()
    if soundtrack_base and soundtrack_base != title:
        candidates.append(soundtrack_base)
    for candidate in candidates:
        key = _norm(candidate)
        if key and key not in keys:
            keys.append(key)
    return keys


def _track_title_fields(track: dict) -> list[str]:
    titles: list[str] = []
    for field in ("title", "title_clean", "base_title"):
        title = (track.get(field) or "").strip()
        if title and title not in titles:
            titles.append(title)
    return titles


def _iter_song_entries(payload) -> list[dict]:
    if isinstance(payload, dict):
        payload = payload.get("songs") or payload.get("tracks") or payload.get("sections") or []
    if not isinstance(payload, list):
        return []
    songs: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("tracks"), list):
            songs.extend(track for track in item["tracks"] if isinstance(track, dict))
        else:
            songs.append(item)
    return songs


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
                    for title in _track_title_fields(track):
                        result[_norm(title)] = album_name
    songs_file = discography_root / "songs.json"
    if songs_file.exists():
        try:
            sections = _iter_song_entries(json.loads(songs_file.read_text(encoding="utf-8-sig")))
        except Exception:
            sections = []
        for track in sections:
            title = track.get("title", "")
            album_name = track.get("album", "")
            if title and album_name:
                for title_key in _track_title_fields(track):
                    result[_norm(title_key)] = album_name
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
                        titles = _track_title_fields(track)
                        img = (track.get("image_url") or "").strip()
                        if titles and img:
                            for title in titles:
                                result[_norm(title)] = img
            except Exception:
                pass
    songs_path = discography_root / "songs.json"
    if songs_path.exists():
        try:
            for track in _iter_song_entries(json.loads(songs_path.read_text(encoding="utf-8-sig"))):
                titles = _track_title_fields(track)
                img = (track.get("image_url") or "").strip()
                if titles and img:
                    for title in titles:
                        result.setdefault(_norm(title), img)
        except Exception:
            pass
    for extra_path in extra_song_sources:
        if not extra_path or not extra_path.exists():
            continue
        try:
            payload = json.loads(extra_path.read_text(encoding="utf-8-sig"))
            for song in _iter_song_entries(payload):
                title = (song.get("title") or song.get("name") or "").strip()
                titles = _track_title_fields(song) or ([title] if title else [])
                img = (song.get("image_url") or song.get("apple_music_image_url") or "").strip()
                if titles and img:
                    for title in titles:
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
    *,
    track_id: str = "",
    track_cover_cache: dict | None = None,
) -> str:
    """Cover URL for a track.

    Priority: live-API cache keyed by track_id (exact per version) > covers.json
    (album) > per-track image_url from albums/*.json (includes Apple Music
    fallback) > fallback CDN URL (scraped chart image_url, often null).
    """
    if track_id and track_cover_cache:
        cached = get_cached_cover(track_cover_cache, track_id)
        if cached:
            return cached
    lookup_keys = _title_lookup_keys(track_name)
    album_name = next((track_album_map.get(key, "") for key in lookup_keys if track_album_map.get(key, "")), "")
    if album_name:
        cover = cover_map.get(_norm(album_name), "")
        if cover and str(cover).startswith("http"):
            return cover
    track_img = next((track_image_map.get(key, "") for key in lookup_keys if track_image_map.get(key, "")), "")
    if track_img and str(track_img).startswith("http"):
        return track_img
    if fallback_url and str(fallback_url).startswith("http"):
        return fallback_url
    return ""
