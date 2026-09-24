from __future__ import annotations

"""Shared catalog helpers (db/discography/) for Apple Music scripts that need
to tell a genuinely new release apart from an old song re-entering a chart —
factored out of generate_snapshot_images.py (2026-09-24) so
post_new_release_progression.py can reuse the same release-date lookup
instead of duplicating it a second time."""

import json
import re
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO_ROOT = HERE.parents[1]
DISCOGRAPHY_DIR = REPO_ROOT / "db" / "discography"


def song_name_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text)
    return text


def song_key_candidates(value: object) -> list[str]:
    """Apple sometimes retitles a track with a soundtrack/feature suffix
    (e.g. 'Song (From "Movie")') that our catalog doesn't have -> also try
    the key with that trailing parenthetical stripped."""
    key = song_name_key(value)
    candidates = [key]
    stripped = re.sub(r"\s*\(from\b[^)]*\)\s*$", "", key).strip()
    if stripped and stripped != key:
        candidates.append(stripped)
    return candidates


def iter_catalog_tracks() -> list[dict]:
    """Every track dict from the discography source-of-truth files
    (db/discography/, committed to git — unlike the runtime web export,
    which only exists on hosts that also ran the Spotify streams export,
    not the case on the Apple Music-only VPS)."""
    tracks: list[dict] = []

    def _collect(sections: object) -> None:
        if not isinstance(sections, list):
            return
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_tracks = section.get("tracks")
            if isinstance(section_tracks, list):
                tracks.extend(t for t in section_tracks if isinstance(t, dict))

    albums_dir = DISCOGRAPHY_DIR / "albums"
    if albums_dir.is_dir():
        for path in sorted(albums_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if isinstance(payload, dict):
                _collect(payload.get("sections"))

    for name in ("songs.json", "features.json", "misc.json"):
        path = DISCOGRAPHY_DIR / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(payload, list):
            _collect(payload)

    return tracks


def load_release_dates() -> dict[str, str]:
    """Map every known song-title key -> its catalog release_date, so a
    missing previous_rank can be told apart between a genuine new release
    and a re-entry (data-rules: never infer NEW for an already-released
    song)."""
    dates: dict[str, str] = {}
    for track in iter_catalog_tracks():
        release_date = str(track.get("release_date") or "").strip()
        if not release_date:
            continue
        for field in ("title", "base_title"):
            key = song_name_key(track.get(field))
            if key and key not in dates:
                dates[key] = release_date
    return dates


def _album_query_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", song_name_key(value))


def resolve_album_filter(query: str) -> tuple[str, set[str]]:
    """Resolve a user-typed album (file slug, full name or unique substring,
    e.g. "showgirl") to (catalog album name, title keys of every track in any
    of its sections). Matched on the catalog's own `title` only — never
    `base_title`, which would pull "Love Story" into a Fearless (Taylor's
    Version) filter. Raises ValueError when no/several albums match."""
    wanted = _album_query_key(query)
    albums: list[tuple[str, str, dict]] = []
    albums_dir = DISCOGRAPHY_DIR / "albums"
    for path in sorted(albums_dir.glob("*.json")) if albums_dir.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("album"):
            albums.append((path.stem, str(payload["album"]), payload))

    exact = [a for a in albums if wanted in (_album_query_key(a[0]), _album_query_key(a[1]))]
    partial = [a for a in albums if wanted and (wanted in _album_query_key(a[0]) or wanted in _album_query_key(a[1]))]
    matches = exact or partial
    if len(matches) != 1:
        names = ", ".join(a[1] for a in (matches or albums))
        raise ValueError(
            f"album {query!r} matched {len(matches)} albums"
            + (f" ({names})" if matches else f"; known albums: {names}")
        )

    _stem, album_name, payload = matches[0]
    keys: set[str] = set()
    for section in payload.get("sections") or []:
        for track in (section.get("tracks") or []) if isinstance(section, dict) else []:
            if isinstance(track, dict):
                key = song_name_key(track.get("title"))
                if key:
                    keys.add(key)
    return album_name, keys
