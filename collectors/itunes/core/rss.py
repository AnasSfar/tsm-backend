"""Fetch + parse the iTunes Store legacy RSS purchase charts.

Feed shape (JSON): {"feed": {"entry": [ {...}, ... ]}}. `entry` is absent when
the chart is empty, a single dict when it has exactly one item, or a list.
Rank = 1-based position in the feed (Taylor filtering happens afterwards, so a
kept entry preserves its true chart position — same rule as
collectors/apple_music/country_all.py).
"""

from __future__ import annotations

import random
import re
import time
from typing import Any

from requests import RequestException

from collectors.apple_music.core.filters import clean_text

from .config import (
    ARTIST_FILTER,
    ARTIST_ID,
    CHART_LIMIT,
    REQUEST_JITTER_MAX,
    RSS_ALBUMS_PATH,
    RSS_BASE,
    RSS_SONGS_PATH,
    THROTTLE_BASE_SLEEP,
    THROTTLE_RETRIES,
)

_THROTTLE_STATUSES = {403, 429, 503}

_ARTIST_ID_RE = re.compile(r"/artist/[^/?]+/(\d+)")
_ARTWORK_SIZE_RE = re.compile(r"/(\d+)x(\d+)((?:bb)?\.(?:png|jpg|jpeg))$", re.IGNORECASE)


class ITunesFeedError(RuntimeError):
    """Network / server error worth counting against the failure budget.

    A 404 is NOT this: it means the storefront has no such chart (empty), which
    the caller treats as a legitimately empty result.
    """


def _label(node: Any) -> str:
    if isinstance(node, dict):
        return clean_text(node.get("label", ""))
    return clean_text(node if isinstance(node, str) else "")


def _entries(payload: dict) -> list[dict]:
    feed = payload.get("feed") or {}
    raw = feed.get("entry")
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    return [e for e in raw if isinstance(e, dict)]


def _artist_href(entry: dict) -> str:
    artist = entry.get("im:artist") or {}
    attrs = artist.get("attributes") or {}
    return str(attrs.get("href", ""))


def _artist_id(entry: dict) -> str:
    match = _ARTIST_ID_RE.search(_artist_href(entry))
    return match.group(1) if match else ""


def is_taylor_entry(entry: dict) -> bool:
    if _artist_id(entry) == ARTIST_ID:
        return True
    artist_name = _label(entry.get("im:artist")).casefold()
    return ARTIST_FILTER.casefold() in artist_name


def _biggest_artwork(entry: dict, size: int = 300) -> str:
    images = entry.get("im:image") or []
    if isinstance(images, dict):
        images = [images]
    best_url = ""
    best_h = -1
    for image in images:
        if not isinstance(image, dict):
            continue
        url = str(image.get("label", ""))
        try:
            height = int((image.get("attributes") or {}).get("height", 0))
        except (TypeError, ValueError):
            height = 0
        if url and height > best_h:
            best_h, best_url = height, url
    if best_url:
        best_url = _ARTWORK_SIZE_RE.sub(f"/{size}x{size}\\3", best_url)
    return best_url


def _entry_url(entry: dict) -> str:
    node = entry.get("id") or {}
    url = _label(node)
    if url:
        return url.split("?", 1)[0]
    links = entry.get("link") or []
    if isinstance(links, dict):
        links = [links]
    for link in links:
        attrs = (link or {}).get("attributes") or {}
        if attrs.get("rel") == "alternate" and attrs.get("href"):
            return str(attrs["href"]).split("?", 1)[0]
    return ""


def _apple_id(entry: dict) -> str:
    node = entry.get("id") or {}
    attrs = node.get("attributes") or {}
    return str(attrs.get("im:id", "")).strip()


def _genre(entry: dict) -> str:
    category = entry.get("category") or {}
    attrs = category.get("attributes") or {}
    return clean_text(attrs.get("term", "") or attrs.get("label", ""))


def _collection_name(entry: dict) -> str:
    collection = entry.get("im:collection") or {}
    return _label(collection.get("im:name"))


_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _release_date(entry: dict) -> str:
    """iTunes puts the ISO 8601 timestamp in `label` and the localized string in
    `attributes.label`; keep the machine-readable date only (empty when absent —
    the Top Songs feed has no release date)."""
    node = entry.get("im:releaseDate") or {}
    match = _ISO_DATE_RE.match(str(node.get("label", "")))
    return match.group(1) if match else ""


def _fetch(session, storefront: str, path: str) -> list[dict]:
    url = f"{RSS_BASE}/{storefront}/{path.format(limit=CHART_LIMIT)}"
    last_status = None
    for attempt in range(THROTTLE_RETRIES + 1):
        if REQUEST_JITTER_MAX > 0:
            time.sleep(random.uniform(0, REQUEST_JITTER_MAX))
        try:
            resp = session.get(url)
        except RequestException as exc:
            raise ITunesFeedError(f"{storefront}: {exc}") from exc
        if resp.status_code == 404:
            return []
        if resp.status_code < 400:
            try:
                return _entries(resp.json())
            except ValueError as exc:
                raise ITunesFeedError(f"{storefront}: invalid JSON") from exc
        last_status = resp.status_code
        if resp.status_code in _THROTTLE_STATUSES and attempt < THROTTLE_RETRIES:
            time.sleep(min(THROTTLE_BASE_SLEEP * (2 ** attempt), 8.0) + random.uniform(0, 1))
            continue
        raise ITunesFeedError(f"{storefront}: HTTP {resp.status_code}")
    raise ITunesFeedError(f"{storefront}: HTTP {last_status} after {THROTTLE_RETRIES} retries")


def parse_songs(entries: list[dict]) -> list[dict]:
    out: list[dict] = []
    for idx, entry in enumerate(entries, start=1):
        if not is_taylor_entry(entry):
            continue
        out.append(
            {
                "song_name": _label(entry.get("im:name")),
                "apple_music_id": _apple_id(entry),
                "rank": idx,
                "image_url": _biggest_artwork(entry, size=300),
                "url": _entry_url(entry),
                "artist_name": _label(entry.get("im:artist")),
                "album_name": _collection_name(entry),
                "genre_names": _genre(entry),
                "release_date": _release_date(entry),
            }
        )
    return out


def parse_albums(entries: list[dict]) -> list[dict]:
    out: list[dict] = []
    for idx, entry in enumerate(entries, start=1):
        if not is_taylor_entry(entry):
            continue
        out.append(
            {
                "album_name": _label(entry.get("im:name")),
                "apple_music_id": _apple_id(entry),
                "rank": idx,
                "image_url": _biggest_artwork(entry, size=500),
                "url": _entry_url(entry),
                "artist_name": _label(entry.get("im:artist")),
                "genre_names": _genre(entry),
                "release_date": _release_date(entry),
            }
        )
    return out


def fetch_storefront(session, storefront: str) -> tuple[list[dict], list[dict]]:
    songs = parse_songs(_fetch(session, storefront, RSS_SONGS_PATH))
    albums = parse_albums(_fetch(session, storefront, RSS_ALBUMS_PATH))
    return songs, albums
