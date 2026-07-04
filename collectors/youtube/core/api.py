"""Wrapper minimal YouTube Data API v3 — stdlib uniquement."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

from .config import API_BASE, BATCH_SIZE


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_uploads_page(api_key: str, playlist_id: str, page_token: str | None = None) -> dict:
    """Fetches one page (max 50 items) of the uploads playlist.

    Cost: 1 API unit per call.
    Returns the raw API response dict.
    """
    params: dict[str, str] = {
        "part": "snippet",
        "playlistId": playlist_id,
        "maxResults": "50",
        "key": api_key,
    }
    if page_token:
        params["pageToken"] = page_token
    url = f"{API_BASE}/playlistItems?{urllib.parse.urlencode(params)}"
    return _get(url)


def iter_uploads(api_key: str, playlist_id: str) -> Iterator[dict]:
    """Yield all items from the uploads playlist, paginating automatically."""
    page_token: str | None = None
    while True:
        data = fetch_uploads_page(api_key, playlist_id, page_token)
        yield from data.get("items", [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _best_thumbnail_url(thumbnails: dict) -> str:
    if not isinstance(thumbnails, dict):
        return ""
    for key in ("maxres", "standard", "high", "medium", "default"):
        url = thumbnails.get(key, {}).get("url")
        if url:
            return str(url)
    return ""


def fetch_video_stats(api_key: str, video_ids: list[str]) -> dict[str, dict]:
    """Fetch public metadata + statistics for up to BATCH_SIZE video IDs.

    Cost: 1 API unit per call (not per video).
    """
    if not video_ids:
        return {}

    params = {
        "part": "statistics,snippet,contentDetails,status",
        "id": ",".join(video_ids[:BATCH_SIZE]),
        "key": api_key,
    }
    url = f"{API_BASE}/videos?{urllib.parse.urlencode(params)}"
    data = _get(url)

    result: dict[str, dict] = {}
    for item in data.get("items", []):
        vid_id = item.get("id", "")
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})
        status = item.get("status", {})
        result[vid_id] = {
            "title": snippet.get("title", ""),
            "publishedAt": snippet.get("publishedAt", ""),
            "thumbnailUrl": _best_thumbnail_url(snippet.get("thumbnails", {})),
            "duration": content.get("duration", ""),
            "viewCount": int(stats.get("viewCount", 0)),
            "likeCount": _int_or_none(stats.get("likeCount")),
            "commentCount": _int_or_none(stats.get("commentCount")),
            "tags": snippet.get("tags", []),
            "categoryId": snippet.get("categoryId", ""),
            "liveBroadcastContent": snippet.get("liveBroadcastContent", ""),
            "privacyStatus": status.get("privacyStatus", ""),
            "uploadStatus": status.get("uploadStatus", ""),
        }
    return result


def chunked(lst: list, size: int) -> Iterator[list]:
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
