"""Public "New on YouTube" feed — the YouTube page's New video button.

Built from the first-day registry (core/first_day.py) so the site shows the
exact same figure as the "first 24 hours" post: a video's cumulative
viewCount captured within CAPTURE_TOLERANCE of published_at + 24h. Before
that instant the video is listed as `waiting` with its `due_at`; if the
capture window was missed it is `missed` and carries no figure (never a
total taken at another moment).

Rewritten after every step that can change it — daily run, +24h capture
task, post tick — and uploaded to R2 with the YouTube CSVs, so the 24h
figure reaches the site minutes after it is captured.

Topic re-uploads of songs that already had a video are not new: left out,
exactly as the post leaves them out.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import first_day
from .config import DB_DIR

NEW_RELEASES_PATH = DB_DIR / "youtube_new_releases.json"
# How long a video stays behind the New video button after its release.
NEW_WINDOW = timedelta(days=7)


def _video(member: dict, capture: dict | None, state: str) -> dict:
    views = first_day._int((capture or {}).get("views"))
    return {
        "video_id": member.get("video_id"),
        "title": member.get("title"),
        "channel": member.get("channel") or "main",
        "kind": first_day.upload_kind(member.get("channel") or "main", member.get("title") or ""),
        "thumbnail_url": member.get("thumbnail_url") or "",
        "published_at": member.get("published_at"),
        "due_at": member.get("due_at"),
        "state": "captured" if views is not None else state,
        "views_24h": views,
        "captured_at": (capture or {}).get("captured_at"),
    }


def _pending_releases(now: datetime) -> list[dict]:
    pending = first_day.load_pending()
    if not pending:
        return []
    captures = first_day.load_captures()
    ctx = first_day._load_context(pending)
    out = []
    for release in first_day.group_releases(pending):
        states = release.states(captures, now)
        videos = []
        for member in release.members:
            if member.channel == "topic":
                matched = first_day._match(member.video_id, member.title, ctx)
                if first_day._has_prior_upload(
                    matched["title_key"], matched.get("title") or member.title, release, ctx
                ):
                    continue
            state = "missed" if states[member.video_id] == "expired" else states[member.video_id]
            videos.append(_video(member.to_json(), captures.get(member.video_id), state))
        out.append({"anchor": release.anchor, "published_at": first_day._iso(release.start), "videos": videos})
    return out


def _resolved_releases() -> list[dict]:
    out = []
    for path in sorted(first_day.RELEASES_DIR.glob("*.json")) if first_day.RELEASES_DIR.exists() else []:
        record = first_day._read_json(path) or {}
        members = [m for m in record.get("members") or [] if m.get("result") != "reupload"]
        if not members:
            continue
        videos = [_video(m, m.get("capture"), "missed") for m in members]
        start = min(str(m.get("published_at") or "") for m in members)
        out.append({"anchor": record.get("anchor") or path.stem, "published_at": start, "videos": videos})
    return out


def build(now: datetime) -> dict:
    cutoff = now - NEW_WINDOW
    releases = []
    for release in _pending_releases(now) + _resolved_releases():
        release["videos"] = [
            v for v in release["videos"]
            if (first_day.parse_utc(v.get("published_at")) or cutoff) > cutoff
        ]
        if release["videos"]:
            release["videos"].sort(key=lambda v: (v["published_at"] or "", v["video_id"] or ""))
            releases.append(release)
    releases.sort(key=lambda r: r["published_at"] or "", reverse=True)
    return {
        "generated_at": first_day._iso(now),
        "window_days": NEW_WINDOW.days,
        "releases": releases,
    }


def write(now: datetime | None = None, path: Path = NEW_RELEASES_PATH) -> dict:
    payload = build(now or datetime.now(timezone.utc))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def refresh_and_upload(log=print) -> None:
    """Rewrite the feed and push it to R2 (non-blocking: logs and returns)."""
    try:
        payload = write()
        count = sum(len(r["videos"]) for r in payload["releases"])
        log(f"[new_releases] {count} vidéo(s) récente(s) -> {NEW_RELEASES_PATH.name}")
    except Exception as e:  # never break a capture/post over the site feed
        log(f"[new_releases] Échec de l'export (non bloquant) : {e}")
        return
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        return
    try:
        from scripts import r2

        r2.upload_youtube_new_releases()
    except Exception as e:
        log(f"[new_releases] Échec de l'upload R2 (non bloquant) : {e}")
