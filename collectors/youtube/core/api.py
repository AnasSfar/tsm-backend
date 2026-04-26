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


def fetch_video_stats(api_key: str, video_ids: list[str]) -> dict[str, dict]:
    """Fetch statistics + snippet for up to BATCH_SIZE video IDs in one call.

    Cost: 1 API unit per call (not per video).
    Returns {video_id: {"title": str, "viewCount": int, "likeCount": int}}.
    """
    if not video_ids:
        return {}

    params = {
        "part": "statistics,snippet",
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
        result[vid_id] = {
            "title": snippet.get("title", ""),
            "viewCount": int(stats.get("viewCount", 0)),
            "likeCount": int(stats.get("likeCount", 0)),
            "publishedAt": snippet.get("publishedAt", "")[:10],
        }
    return result


def chunked(lst: list, size: int) -> Iterator[list]:
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
