from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = _REPO_ROOT / "db" / "discography" / "track_cover_cache.json"


def load_track_cover_cache() -> dict[str, dict]:
    """Returns {track_id: {"cover_url": str, "fetched_at": str}}."""
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def save_track_cover_cache(data: dict[str, dict]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def merge_track_cover_cache(updates: dict[str, str]) -> dict[str, dict]:
    """Read-modify-write: merge {track_id: cover_url} into the on-disk cache
    (always overwrites on a fresh successful fetch, never deletes)."""
    cache = load_track_cover_cache()
    now = datetime.now(timezone.utc).isoformat()
    for track_id, cover_url in updates.items():
        if not track_id or not cover_url:
            continue
        cache[track_id] = {"cover_url": cover_url, "fetched_at": now}
    save_track_cover_cache(cache)
    return cache


def get_cached_cover(cache: dict, track_id: str) -> str:
    if not track_id:
        return ""
    entry = cache.get(track_id) or {}
    url = entry.get("cover_url") or ""
    return url if str(url).startswith("http") else ""
