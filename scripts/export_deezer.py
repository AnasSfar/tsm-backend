#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import (  # noqa: E402
    LEGACY_WEBSITE_DATA_DIR,
    WEB_EXPORT_DATA_DIR,
    deezer_daily_csv_paths,
    first_existing,
)

DB_DIR = ROOT / "db"
DATA_ROOT = ROOT / "data"
ARCHIVE_DB_DIR = DATA_ROOT / "_archive" / "original" / "db"
OUT_DIR = WEB_EXPORT_DATA_DIR

GLOBAL_CSV = DB_DIR / "deezer_global_chart.csv"
ARTIST_TOP_CSV = DB_DIR / "deezer_artist_top_tracks.csv"
ARTIST_STATS_CSV = DB_DIR / "deezer_artist_stats.csv"

OUT_DATA = OUT_DIR / "deezer.json"
OUT_HISTORY = OUT_DIR / "deezer_history.json"

# Same windowing rationale as export_apple_music.py: deezer_history.json is
# loaded whole by the API, so keep it to a rolling window with past days
# collapsed to their last snapshot. Older dates stay served by the per-date
# R2 snapshots (deezer/snapshots/) and the full CSV history on disk.
HISTORY_DAYS = int(os.getenv("DEEZER_HISTORY_DAYS", "30") or "30")
HISTORY_CUTOFF = (_date.today() - timedelta(days=HISTORY_DAYS)).isoformat()


def log(msg: str) -> None:
    print(f"[deezer-export] {msg}", flush=True)


def ensure_out_dir() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def to_int(value: Any) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def clean_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_date(row: dict[str, Any]) -> str:
    for key in ("scraped_at", "date"):
        val = clean_str(row.get(key))
        if val:
            return val
    return ""


def normalize_track_entry(row: dict[str, Any]) -> dict[str, Any]:
    previous_rank = to_int(row.get("previous_rank"))
    return {
        "title": clean_str(row.get("title")),
        "deezer_track_id": clean_str(row.get("deezer_track_id")),
        "rank": to_int(row.get("rank")),
        "previous_rank": previous_rank if previous_rank else None,
        "artist_name": clean_str(row.get("artist_name") or "Taylor Swift"),
        "album_title": clean_str(row.get("album_title")),
        "link": clean_str(row.get("link")),
        "cover_url": clean_str(row.get("cover_url")),
        "duration": to_int(row.get("duration")),
        "explicit_lyrics": clean_str(row.get("explicit_lyrics")).lower() == "true",
        "deezer_popularity": to_int(row.get("deezer_popularity")),
    }


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    candidates = []
    if path.exists():
        candidates.append(path)
    archived = ARCHIVE_DB_DIR / path.name
    if archived.exists() and archived not in candidates:
        candidates.append(archived)
    candidates.extend(deezer_daily_csv_paths(path.name))
    if not candidates:
        log(f"absent: {path.name}")
        return []

    rows: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for candidate in candidates:
        if candidate in seen_paths:
            continue
        seen_paths.add(candidate)
        with candidate.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows.extend(dict(row) for row in reader)

    log(f"lu {len(rows)} lignes depuis {path.name} ({len(seen_paths)} fichier(s))")
    return rows


def _load_prev_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _song_key(name: Any) -> str:
    return str(name or "").strip().casefold()


def sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda x: (
            x.get("rank") is None,
            x.get("rank") if x.get("rank") is not None else 10**9,
            x.get("title", "").lower(),
        ),
    )


def build_ranked_series(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    track_dates: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        d = normalize_date(row)
        if not d:
            continue
        entry = normalize_track_entry(row)
        by_date[d].append(entry)
        track_dates[_song_key(entry["title"])].add(d)

    dates = sorted(by_date.keys())
    latest = dates[-1] if dates else None

    if latest:
        for entry in by_date[latest]:
            if entry.get("previous_rank") is None:
                past = track_dates.get(_song_key(entry["title"]), set()) - {latest}
                if past:
                    entry["is_reentry"] = True

    for d in list(by_date.keys()):
        by_date[d] = sort_entries(by_date[d])

    current = {"date": latest, "entries": by_date.get(latest, [])}
    return current, by_date, dates


def build_fan_stats_series(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One point per day (last snapshot of the day wins on a rerun)."""
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = normalize_date(row)
        if not d:
            continue
        day = d[:10]
        existing = by_day.get(day)
        if existing is None or d >= existing["_scraped_at"]:
            by_day[day] = {
                "_scraped_at": d,
                "date": day,
                "nb_fan": to_int(row.get("nb_fan")),
                "nb_album": to_int(row.get("nb_album")),
            }
    series = [
        {"date": v["date"], "nb_fan": v["nb_fan"], "nb_album": v["nb_album"]}
        for v in sorted(by_day.values(), key=lambda v: v["date"])
    ]
    latest = series[-1] if series else {}
    return latest, series


def _build_rank_lookup(entries: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    by_id: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for entry in entries or []:
        rank = entry.get("rank")
        if rank is None:
            continue
        track_id = clean_str(entry.get("deezer_track_id"))
        name = _song_key(entry.get("title") or "")
        if track_id and track_id not in by_id:
            by_id[track_id] = rank
        if name and name not in by_name:
            by_name[name] = rank
    return by_id, by_name


def _backfill_flat(current: dict[str, Any] | None, prev_section: Any) -> None:
    if not current or not prev_section:
        return
    prev_entries = prev_section.get("entries") if isinstance(prev_section, dict) else prev_section
    by_id, by_name = _build_rank_lookup(prev_entries or [])
    for entry in current.get("entries") or []:
        if entry.get("previous_rank") not in (None, ""):
            continue
        track_id = clean_str(entry.get("deezer_track_id"))
        name = _song_key(entry.get("title") or "")
        rank = (by_id.get(track_id) if track_id else None) or by_name.get(name)
        if rank is not None:
            entry["previous_rank"] = rank


def main() -> None:
    ensure_out_dir()

    prev_data = _load_prev_snapshot(first_existing(OUT_DATA, LEGACY_WEBSITE_DATA_DIR / "deezer.json"))
    if prev_data:
        log("snapshot précédent chargé pour backfill previous_rank")

    all_dates_set: set[str] = set()
    today_day = _date.today().isoformat()

    def window_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keyed: list[tuple[str, dict[str, Any]]] = []
        for row in rows:
            d = normalize_date(row)
            if not d:
                continue
            all_dates_set.add(d)
            if d[:10] >= HISTORY_CUTOFF:
                keyed.append((d, row))
        last_by_day: dict[str, str] = {}
        for d, _row in keyed:
            day = d[:10]
            if d > last_by_day.get(day, ""):
                last_by_day[day] = d
        return [row for d, row in keyed if d[:10] == today_day or d == last_by_day[d[:10]]]

    global_rows = window_rows(read_csv_rows(GLOBAL_CSV))
    artist_top_rows = window_rows(read_csv_rows(ARTIST_TOP_CSV))
    fan_stats_rows = read_csv_rows(ARTIST_STATS_CSV)

    global_current, global_history, _ = build_ranked_series(global_rows)
    artist_top_current, artist_top_history, _ = build_ranked_series(artist_top_rows)
    fan_stats_current, fan_stats_series = build_fan_stats_series(fan_stats_rows)

    all_dates = sorted(all_dates_set)
    latest_any = all_dates[-1] if all_dates else None

    if prev_data:
        _backfill_flat(global_current, prev_data.get("global_chart"))
        _backfill_flat(artist_top_current, prev_data.get("ts_top_tracks"))

    deezer_data = {
        "scraped_at": latest_any,
        "dates": all_dates,
        "global_chart": global_current,
        "ts_top_tracks": artist_top_current,
        "fan_stats": fan_stats_current,
    }

    history_dates = sorted(set(global_history) | set(artist_top_history))
    deezer_history = {
        "dates": history_dates,
        "global": global_history,
        "ts_top_tracks": artist_top_history,
        "fan_stats_series": fan_stats_series,
    }

    OUT_DATA.write_text(json.dumps(deezer_data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HISTORY.write_text(
        json.dumps(deezer_history, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    log(f"écrit: {OUT_DATA}")
    log(f"écrit: {OUT_HISTORY}")
    log(f"dates detectees: {len(all_dates)} (history fenetre: {len(history_dates)} dates >= {HISTORY_CUTOFF})")


if __name__ == "__main__":
    main()
