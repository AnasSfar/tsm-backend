from __future__ import annotations

import csv
import json
import re
from datetime import date
from pathlib import Path

from core.data_paths import update_streams_dir

STREAMS_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = STREAMS_DIR.parents[2]
ROOT = REPO_ROOT / "website"
DATA_DIR = ROOT / "data"
DB_ROOT = REPO_ROOT / "db"
DISCOGRAPHY_DIR = DB_ROOT / "discography"
DB_ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
DB_SONGS_JSON = DISCOGRAPHY_DIR / "songs.json"
FAILED_PATH = DATA_DIR / "not_found_today.csv"
PENDING_LOG_PATH = DATA_DIR / "pending_debug_today.csv"
LAST_SUCCESSFUL_UPDATE_JSON = DATA_DIR / "last_successful_updates.json"
LAST_UNFINISHED_UPDATE_JSON = DATA_DIR / "last_unfinished_updates.json"
NOT_FOUND_STREAK_PATH = DATA_DIR / "not_found_streak.json"
MAX_NOT_FOUND_DAYS = 7


def get_scrape_date_str() -> str:
    return date.today().isoformat()


def configure_daily_data_paths(stats_date: str) -> None:
    global DATA_DIR, FAILED_PATH, PENDING_LOG_PATH
    global LAST_SUCCESSFUL_UPDATE_JSON, LAST_UNFINISHED_UPDATE_JSON, NOT_FOUND_STREAK_PATH

    DATA_DIR = update_streams_dir(stats_date)
    FAILED_PATH = DATA_DIR / "not_found_today.csv"
    PENDING_LOG_PATH = DATA_DIR / "pending_debug_today.csv"
    LAST_SUCCESSFUL_UPDATE_JSON = DATA_DIR / "last_successful_updates.json"
    LAST_UNFINISHED_UPDATE_JSON = DATA_DIR / "last_unfinished_updates.json"
    NOT_FOUND_STREAK_PATH = DATA_DIR / "not_found_streak.json"

def save_failed_rows(rows: list[dict]) -> None:
    if not rows:
        if FAILED_PATH.exists():
            FAILED_PATH.unlink()
        return

    with FAILED_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "track_id", "spotify_url", "status"])
        for row in rows:
            writer.writerow(
                [
                    row["title"],
                    row["track_id"],
                    row.get("spotify_url", ""),
                    row.get("status", ""),
                ]
            )

def save_pending_debug_rows(rows: list[dict]) -> None:
    if not rows:
        if PENDING_LOG_PATH.exists():
            PENDING_LOG_PATH.unlink()
        return

    with PENDING_LOG_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "title",
            "track_id",
            "spotify_url",
            "previous_streams",
            "new_streams",
            "delta",
            "reason",
            "raw",
        ])
        for row in rows:
            writer.writerow([
                row.get("title", ""),
                row.get("track_id", ""),
                row.get("spotify_url", ""),
                row.get("previous_streams", ""),
                row.get("new_streams", ""),
                row.get("delta", ""),
                row.get("reason", ""),
                row.get("raw", ""),
            ])

def save_last_successful_updates_json(stats_date: str, updated_results: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": get_scrape_date_str(),
        "stats_date": stats_date,
        "track_ids": [r["track_id"] for r in updated_results if r.get("track_id")],
        "tracks": [
            {
                "track_id": r.get("track_id"),
                "title": r.get("title"),
                "spotify_url": r.get("spotify_url"),
                "streams": r.get("streams"),
                "daily_streams": r.get("daily_streams"),
            }
            for r in updated_results
            if r.get("track_id")
        ],
    }
    LAST_SUCCESSFUL_UPDATE_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def save_last_unfinished_updates_json(stats_date: str, results: list[dict], failed_results: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    unfinished_map: dict[str, dict] = {}

    for r in results:
        if not r:
            continue
        if r.get("status") in {"pending", "timeout", "error", "not_found"}:
            tid = r.get("track_id")
            if tid:
                unfinished_map[tid] = {
                    "track_id": r.get("track_id"),
                    "title": r.get("title"),
                    "spotify_url": r.get("spotify_url"),
                    "status": r.get("status"),
                    "streams": r.get("streams"),
                    "daily_streams": r.get("daily_streams"),
                    "previous_streams": r.get("previous_streams"),
                    "delta": r.get("delta"),
                    "reason": r.get("reason"),
                    "raw": r.get("raw"),
                }

    for r in failed_results:
        tid = r.get("track_id")
        if tid:
            unfinished_map[tid] = {
                "track_id": r.get("track_id"),
                "title": r.get("title"),
                "spotify_url": r.get("spotify_url"),
                "status": r.get("status"),
                "streams": r.get("streams"),
                "daily_streams": r.get("daily_streams"),
                "previous_streams": r.get("previous_streams"),
                "delta": r.get("delta"),
                "reason": r.get("reason"),
                "raw": r.get("raw"),
            }

    payload = {
        "generated_at": get_scrape_date_str(),
        "stats_date": stats_date,
        "track_ids": list(unfinished_map.keys()),
        "tracks": list(unfinished_map.values()),
    }

    LAST_UNFINISHED_UPDATE_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def load_last_unfinished_update_track_ids(stats_date: str | None = None) -> set[str]:
    if not LAST_UNFINISHED_UPDATE_JSON.exists():
        return set()

    try:
        payload = json.loads(LAST_UNFINISHED_UPDATE_JSON.read_text(encoding="utf-8-sig"))
    except Exception:
        return set()

    payload_date = payload.get("stats_date")
    if stats_date is not None and payload_date and payload_date != stats_date:
        return set()

    track_ids = payload.get("track_ids") or []
    return {tid for tid in track_ids if isinstance(tid, str) and tid}

def load_not_found_streak() -> dict:
    if not NOT_FOUND_STREAK_PATH.exists():
        return {}
    try:
        return json.loads(NOT_FOUND_STREAK_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}

def save_not_found_streak(streak: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NOT_FOUND_STREAK_PATH.write_text(
        json.dumps(streak, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def update_not_found_streak(streak: dict, not_found_ids: set[str], updated_ids: set[str]) -> None:
    for track_id in not_found_ids:
        streak[track_id] = streak.get(track_id, 0) + 1
    for track_id in updated_ids:
        streak.pop(track_id, None)

def remove_track_from_discography(track_id: str) -> int:
    removed = 0

    if DB_ALBUMS_DIR.exists():
        for album_file in sorted(DB_ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue

            raw_sections = payload.get("sections", [])
            if not isinstance(raw_sections, list):
                continue

            changed = False
            for data in raw_sections:
                if not isinstance(data, dict):
                    continue
                tracks = data.get("tracks", [])
                if not isinstance(tracks, list):
                    tracks = []
                new_tracks = [
                    t for t in tracks
                    if extract_track_id(t.get("url") or t.get("spotify_url") or "") != track_id
                ]
                if len(new_tracks) < len(tracks):
                    data["tracks"] = new_tracks
                    data["track_count"] = len(new_tracks)
                    changed = True
                    removed += 1

            if changed:
                payload["sections"] = raw_sections
                payload["section_count"] = len(raw_sections)
                payload["track_count"] = sum(len(s.get("tracks", [])) for s in raw_sections if isinstance(s, dict))
                album_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    if DB_SONGS_JSON.exists():
        try:
            sections = json.loads(DB_SONGS_JSON.read_text(encoding="utf-8-sig"))
        except Exception:
            sections = []

        changed = False
        for data in sections:
            tracks = data.get("tracks", [])
            new_tracks = [
                t for t in tracks
                if extract_track_id(t.get("url") or t.get("spotify_url") or "") != track_id
            ]
            if len(new_tracks) < len(tracks):
                data["tracks"] = new_tracks
                data["track_count"] = len(new_tracks)
                changed = True
                removed += 1

        if changed:
            DB_SONGS_JSON.write_text(
                json.dumps(sections, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    return removed

def purge_stale_tracks(streak: dict, tracks: list[dict]) -> list[str]:
    deleted = []
    for track_id, count in list(streak.items()):
        if count >= MAX_NOT_FOUND_DAYS:
            title = next((t["title"] for t in tracks if t["track_id"] == track_id), track_id)
            print(
                f"AUTO-DELETE | {title} | track_id={track_id} | "
                f"not found for {count} consecutive days — removing from discography"
            )
            remove_track_from_discography(track_id)
            del streak[track_id]
            deleted.append(track_id)
    return deleted
