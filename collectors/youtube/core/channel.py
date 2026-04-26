"""Découverte et gestion du catalogue de vidéos Taylor Swift."""
from __future__ import annotations

import json
from pathlib import Path

from .api import iter_uploads
from .config import UPLOADS_PLAYLIST_ID


def load_video_db(path: Path) -> dict[str, dict]:
    """Load the video catalog from JSON. Returns {} on missing/corrupt file."""
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_video_db(db: dict[str, dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def discover_new_videos(
    api_key: str,
    existing_ids: set[str],
    playlist_id: str = UPLOADS_PLAYLIST_ID,
) -> list[dict]:
    """Return new video entries not yet in existing_ids.

    Short-circuits as soon as a full page contains only known IDs —
    the uploads playlist is ordered newest-first, so after the first
    fully-known page we're done.
    """
    new_videos: list[dict] = []

    for item in iter_uploads(api_key, playlist_id):
        snippet = item.get("snippet", {})
        resource = snippet.get("resourceId", {})
        if resource.get("kind") != "youtube#video":
            continue

        video_id = resource.get("videoId", "")
        if not video_id:
            continue

        if video_id not in existing_ids:
            new_videos.append(
                {
                    "video_id": video_id,
                    "title": snippet.get("title", ""),
                    "published_at": snippet.get("publishedAt", "")[:10],
                    "channel_id": snippet.get("channelId", ""),
                }
            )

    return new_videos


def discover_new_videos_short_circuit(
    api_key: str,
    existing_ids: set[str],
    playlist_id: str = UPLOADS_PLAYLIST_ID,
) -> list[dict]:
    """Same as discover_new_videos but stops after one fully-known page.

    Use this in steady-state (daily) runs to minimise quota usage (~1 unit/day).
    Use discover_new_videos for the initial bootstrap run.
    """
    from .api import fetch_uploads_page

    new_videos: list[dict] = []
    page_token: str | None = None

    while True:
        data = fetch_uploads_page(api_key, playlist_id, page_token)
        items = data.get("items", [])
        page_new: list[dict] = []

        for item in items:
            snippet = item.get("snippet", {})
            resource = snippet.get("resourceId", {})
            if resource.get("kind") != "youtube#video":
                continue
            video_id = resource.get("videoId", "")
            if not video_id:
                continue
            if video_id not in existing_ids:
                page_new.append(
                    {
                        "video_id": video_id,
                        "title": snippet.get("title", ""),
                        "published_at": snippet.get("publishedAt", "")[:10],
                        "channel_id": snippet.get("channelId", ""),
                    }
                )

        new_videos.extend(page_new)

        page_token = data.get("nextPageToken")
        # Stop if this page had no new videos or no more pages
        if not page_new or not page_token:
            break

    return new_videos


def update_video_db(
    existing_db: dict[str, dict],
    new_videos: list[dict],
) -> dict[str, dict]:
    """Merge newly discovered videos into the catalog."""
    updated = dict(existing_db)
    for v in new_videos:
        vid_id = v.pop("video_id")
        updated[vid_id] = v
    return updated
