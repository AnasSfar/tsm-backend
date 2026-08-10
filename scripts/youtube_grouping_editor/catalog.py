#!/usr/bin/env python3
"""Load, edit and safely save collectors/youtube/tools/json/video_groups.json.

Plain data logic, no web framework dependency — mirrors
scripts/discography_editor/catalog.py's shape so the two tools stay
consistent, but this one is a full-state save (small dataset, single user)
instead of a diff-based one.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.youtube.core.config import (  # noqa: E402
    DISCOGRAPHY_SONGS_PATH,
    VIDEO_DB_PATH,
    VIDEO_GROUPS_PATH,
)
from collectors.youtube.core.title_groups import (  # noqa: E402
    load_song_catalog,
    match_video_title,
    title_key,
)


class CatalogError(Exception):
    pass


@dataclass
class Catalog:
    videos: dict[str, dict[str, Any]] = field(default_factory=dict)
    groups: list[dict[str, Any]] = field(default_factory=list)


def _thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return path.with_suffix(path.suffix + f".ytgroup-{stamp}.bak")


def load_catalog() -> Catalog:
    """Build the board: known videos, pre-grouped exactly like production.

    Any video with a saved manual override (video_groups.json) keeps that
    placement; every other video is placed using the same
    title_groups.match_video_title() logic build_title_rows() uses, so the
    board opens showing the grouping that's already live today instead of an
    empty pool the user would have to rebuild from scratch.
    """
    video_db = read_json(VIDEO_DB_PATH) or {}
    groups_payload = read_json(VIDEO_GROUPS_PATH) or []
    if not isinstance(groups_payload, list):
        groups_payload = []

    videos: dict[str, dict[str, Any]] = {}
    for video_id, meta in video_db.items():
        if not isinstance(meta, dict):
            continue
        videos[video_id] = {
            "video_id": video_id,
            "title": meta.get("title", ""),
            "published_at": meta.get("published_at", ""),
            "thumbnail_url": _thumbnail_url(video_id),
        }

    manual_lookup: dict[str, dict[str, str]] = {}
    for entry in groups_payload:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        key = str(entry.get("title_key") or title_key(title))
        for video_id in entry.get("video_ids") or []:
            video_id = str(video_id).strip()
            if video_id in videos:
                manual_lookup[video_id] = {"title_key": key, "title": title}

    song_catalog = load_song_catalog(DISCOGRAPHY_SONGS_PATH)
    groups_by_key: dict[str, dict[str, Any]] = {}
    for video_id, video in videos.items():
        matched = manual_lookup.get(video_id) or match_video_title(video["title"], song_catalog)
        group = groups_by_key.setdefault(
            matched["title_key"],
            {"title_key": matched["title_key"], "title": matched["title"], "video_ids": []},
        )
        group["video_ids"].append(video_id)

    for group in groups_by_key.values():
        group["video_ids"].sort(key=lambda vid: videos[vid].get("published_at", ""), reverse=True)

    groups = sorted(
        groups_by_key.values(),
        key=lambda g: (-len(g["video_ids"]), g["title"].lower()),
    )

    return Catalog(videos=videos, groups=groups)


def serialize_state(catalog: Catalog) -> dict:
    grouped_ids = {vid for group in catalog.groups for vid in group["video_ids"]}
    ungrouped_ids = [vid for vid in catalog.videos if vid not in grouped_ids]
    ungrouped_ids.sort(key=lambda vid: catalog.videos[vid].get("published_at", ""), reverse=True)

    return {
        "videos": catalog.videos,
        "groups": catalog.groups,
        "ungrouped_video_ids": ungrouped_ids,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _dedupe_key(base_key: str, taken: set[str]) -> str:
    if base_key not in taken:
        return base_key
    n = 2
    while f"{base_key}_{n}" in taken:
        n += 1
    return f"{base_key}_{n}"


def save_groups(catalog: Catalog, groups: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Validate `groups` (list of {title, video_ids}) against the known video
    catalog and, if valid, back up + atomically overwrite video_groups.json.
    Empty groups (no videos left) are dropped silently. Returns (ok, errors).
    """
    errors: list[str] = []
    seen_video_ids: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    taken_keys: set[str] = set()

    for index, entry in enumerate(groups):
        if not isinstance(entry, dict):
            errors.append(f"groupe #{index + 1} : format invalide.")
            continue
        title = str(entry.get("title") or "").strip()
        raw_ids = entry.get("video_ids") or []
        video_ids: list[str] = []
        for vid in raw_ids:
            vid = str(vid).strip()
            if not vid:
                continue
            if vid not in catalog.videos:
                errors.append(f"groupe {title!r} : video_id inconnu {vid!r}.")
                continue
            if vid in seen_video_ids:
                errors.append(f"video_id {vid!r} assigné à plusieurs colonnes.")
                continue
            seen_video_ids.add(vid)
            video_ids.append(vid)

        if not video_ids:
            continue  # empty column, nothing to persist
        if not title:
            errors.append(f"groupe #{index + 1} : titre manquant (contient {len(video_ids)} vidéo(s)).")
            continue

        key = _dedupe_key(title_key(title), taken_keys)
        taken_keys.add(key)
        cleaned.append({"title_key": key, "title": title, "video_ids": video_ids})

    if errors:
        return False, errors

    text = write_json_text(cleaned)
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return False, [f"Échec de validation JSON : {exc}"]

    if VIDEO_GROUPS_PATH.exists():
        shutil.copy2(VIDEO_GROUPS_PATH, backup_path(VIDEO_GROUPS_PATH))
    VIDEO_GROUPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = VIDEO_GROUPS_PATH.with_suffix(VIDEO_GROUPS_PATH.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(VIDEO_GROUPS_PATH)

    return True, []
