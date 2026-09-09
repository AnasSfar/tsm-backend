#!/usr/bin/env python3
"""Export the iTunes Store purchase charts (Top Songs / Top Albums) CSV history
to the JSON payloads the frontend API reads.

Writes (into runtime/exports/web/site/data/):
  itunes.json                 latest snapshot per country + last_charted lookup
  itunes_history.json         windowed history (whole file loaded by the API)
  itunes_history_dates/*.json  per-date splits for deep history

Modeled on scripts/export_apple_music.py (same windowing + previous_rank
backfill), trimmed to the two charts this collector produces.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import (  # noqa: E402
    LEGACY_WEBSITE_DATA_DIR,
    WEB_EXPORT_DATA_DIR,
    first_existing,
    itunes_daily_csv_paths,
)

DB_DIR = ROOT / "db"
ARCHIVE_DB_DIR = ROOT / "data" / "_archive" / "original" / "db"
OUT_DIR = WEB_EXPORT_DATA_DIR

SONGS_CSV = DB_DIR / "itunes_top_songs.csv"
ALBUMS_CSV = DB_DIR / "itunes_top_albums.csv"

OUT_DATA = OUT_DIR / "itunes.json"
OUT_HISTORY = OUT_DIR / "itunes_history.json"
OUT_HISTORY_DATES_DIR = OUT_DIR / "itunes_history_dates"

HISTORY_DAYS = int(os.getenv("ITUNES_HISTORY_DAYS", "30") or "30")
HISTORY_CUTOFF = (_date.today() - timedelta(days=HISTORY_DAYS)).isoformat()


def log(msg: str) -> None:
    print(f"[itunes-export] {msg}", flush=True)


def to_int(value: Any) -> int | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def clean_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _song_key(name: Any) -> str:
    return str(name or "").strip().casefold()


def normalize_date(row: dict[str, Any]) -> str:
    for key in ("scraped_at", "date"):
        val = clean_str(row.get(key))
        if val:
            return val
    return ""


def normalize_song_entry(row: dict[str, Any]) -> dict[str, Any]:
    previous_rank = to_int(row.get("previous_rank"))
    genre_raw = clean_str(row.get("genre_names"))
    return {
        "song_name": clean_str(row.get("song_name")),
        "apple_music_id": clean_str(row.get("apple_music_id")),
        "rank": to_int(row.get("rank")),
        "previous_rank": previous_rank if previous_rank else None,
        "image_url": clean_str(row.get("image_url")),
        "url": clean_str(row.get("url")),
        "artist_name": clean_str(row.get("artist_name")) or "Taylor Swift",
        "album_name": clean_str(row.get("album_name")),
        "release_date": clean_str(row.get("release_date")),
        "genre_names": [p.strip() for p in genre_raw.split("|") if p.strip()] if genre_raw else [],
    }


def normalize_album_entry(row: dict[str, Any]) -> dict[str, Any]:
    previous_rank = to_int(row.get("previous_rank"))
    genre_raw = clean_str(row.get("genre_names"))
    return {
        "album_name": clean_str(row.get("album_name")),
        "apple_music_id": clean_str(row.get("apple_music_id")),
        "rank": to_int(row.get("rank")),
        "previous_rank": previous_rank if previous_rank else None,
        "image_url": clean_str(row.get("image_url")),
        "url": clean_str(row.get("url")),
        "artist_name": clean_str(row.get("artist_name")) or "Taylor Swift",
        "release_date": clean_str(row.get("release_date")),
        "genre_names": [p.strip() for p in genre_raw.split("|") if p.strip()] if genre_raw else [],
    }


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    candidates: list[Path] = []
    if path.exists():
        candidates.append(path)
    archived = ARCHIVE_DB_DIR / path.name
    if archived.exists():
        candidates.append(archived)
    candidates.extend(itunes_daily_csv_paths(path.name))
    if not candidates:
        log(f"absent: {path.name}")
        return []

    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        with candidate.open("r", encoding="utf-8-sig", newline="") as fh:
            rows.extend(dict(r) for r in csv.DictReader(fh))
    log(f"lu {len(rows)} lignes depuis {path.name} ({len(seen)} fichier(s))")
    return rows


def sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda x: (
            x.get("rank") is None,
            x.get("rank") if x.get("rank") is not None else 10**9,
            (x.get("song_name") or x.get("album_name") or "").lower(),
        ),
    )


def _load_prev_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_rank_lookup(entries: list[dict[str, Any]]):
    by_id: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for entry in entries or []:
        rank = entry.get("rank")
        if rank is None:
            continue
        am_id = clean_str(entry.get("apple_music_id"))
        name = _song_key(entry.get("song_name") or entry.get("album_name") or "")
        if am_id:
            by_id.setdefault(am_id, rank)
        if name:
            by_name.setdefault(name, rank)
    return by_id, by_name


def _backfill_entries(entries, by_id, by_name) -> None:
    for entry in entries:
        if entry.get("previous_rank") not in (None, ""):
            continue
        am_id = clean_str(entry.get("apple_music_id"))
        name = _song_key(entry.get("song_name") or entry.get("album_name") or "")
        rank = (by_id.get(am_id) if am_id else None) or by_name.get(name)
        if rank is not None:
            entry["previous_rank"] = rank


def _backfill_by_country(current: dict[str, Any] | None, prev_section: Any) -> None:
    if not current or not prev_section:
        return
    prev_cc = prev_section if isinstance(prev_section, dict) else {}
    if "countries" in prev_cc:
        prev_cc = prev_cc["countries"]
    for country, entries in (current.get("countries") or {}).items():
        by_id, by_name = _build_rank_lookup(prev_cc.get(country) or [])
        _backfill_entries(entries if isinstance(entries, list) else [], by_id, by_name)


def build_by_country(rows: list[dict[str, Any]], *, album: bool):
    normalize = normalize_album_entry if album else normalize_song_entry
    name_field = "album_name" if album else "song_name"
    by_date: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    seen_dates: dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in rows:
        d = normalize_date(row)
        country = clean_str(row.get("country")).lower()
        if not d or not country:
            continue
        entry = normalize(row)
        by_date[d][country].append(entry)
        seen_dates[(country, _song_key(entry[name_field]))].add(d)

    dates = sorted(by_date.keys())
    latest = dates[-1] if dates else None

    if latest:
        for country, entries in by_date[latest].items():
            for entry in entries:
                if entry.get("previous_rank") is None:
                    past = seen_dates.get((country, _song_key(entry[name_field])), set()) - {latest}
                    if past:
                        entry["is_reentry"] = True

    for _d, countries in by_date.items():
        for country in list(countries.keys()):
            countries[country] = sort_entries(countries[country])

    current = {"date": latest, "countries": by_date[latest]} if latest else None
    return current, by_date, dates


def build_last_charted(rows: list[dict[str, Any]], *, album: bool) -> dict[str, dict[str, Any]]:
    normalize = normalize_album_entry if album else normalize_song_entry
    best: dict[str, tuple[str, dict[str, Any]]] = {}
    for row in rows:
        d = normalize_date(row)
        country = clean_str(row.get("country")).lower()
        if not d or not country:
            continue
        current = best.get(country)
        if current is None or d > current[0]:
            best[country] = (d, row)
        elif d == current[0]:
            new_rank = to_int(row.get("rank"))
            old_rank = to_int(current[1].get("rank"))
            if new_rank is not None and (old_rank is None or new_rank < old_rank):
                best[country] = (d, row)
    return {c: {"date": d, "entry": normalize(row)} for c, (d, row) in best.items()}


def history_date_filename(date_key: str) -> str:
    return f"{quote(date_key, safe='')}.json"


def write_history_by_date(history: dict[str, Any]) -> int:
    dates = [d for d in history.get("dates", []) if isinstance(d, str) and d]
    OUT_HISTORY_DATES_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUT_HISTORY_DATES_DIR.glob("*.json"):
        stale.unlink()

    (OUT_HISTORY_DATES_DIR / "index.json").write_text(
        json.dumps(
            {"dates": dates, "count": len(dates), "latest": dates[-1] if dates else None},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    def _value_for(bucket: dict[str, Any], key: str):
        if key in bucket:
            return bucket[key]
        day = key[:10]
        same_day = sorted(k for k in bucket if isinstance(k, str) and k.startswith(day))
        if not same_day:
            return {}
        earlier = [k for k in same_day if k <= key]
        return bucket[(earlier or same_day)[-1]]

    for date_key in dates:
        payload = {
            "date": date_key,
            "country": _value_for(history.get("country", {}), date_key),
            "country_albums": _value_for(history.get("country_albums", {}), date_key),
        }
        (OUT_HISTORY_DATES_DIR / history_date_filename(date_key)).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
    return len(dates)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_HISTORY_DATES_DIR.mkdir(parents=True, exist_ok=True)

    prev_data = _load_prev_snapshot(first_existing(OUT_DATA, LEGACY_WEBSITE_DATA_DIR / "itunes.json"))
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

    songs_full = read_csv_rows(SONGS_CSV)
    albums_full = read_csv_rows(ALBUMS_CSV)
    last_charted_songs = build_last_charted(songs_full, album=False)
    last_charted_albums = build_last_charted(albums_full, album=True)
    song_rows = window_rows(songs_full)
    album_rows = window_rows(albums_full)
    del songs_full, albums_full

    song_current, song_history, _ = build_by_country(song_rows, album=False)
    album_current, album_history, _ = build_by_country(album_rows, album=True)

    all_dates = sorted(all_dates_set)
    latest_any = all_dates[-1] if all_dates else None

    if prev_data:
        _backfill_by_country(song_current, prev_data.get("top_songs"))
        _backfill_by_country(album_current, prev_data.get("top_albums"))

    itunes_data = {
        "scraped_at": latest_any,
        "dates": all_dates,
        "last_charted": {"songs": last_charted_songs, "albums": last_charted_albums},
        "top_songs": song_current,
        "top_albums": album_current,
    }
    history_dates = sorted(set(song_history) | set(album_history))
    itunes_history = {
        "dates": history_dates,
        "country": song_history,
        "country_albums": album_history,
    }

    OUT_DATA.write_text(json.dumps(itunes_data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HISTORY.write_text(
        json.dumps(itunes_history, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    split_count = write_history_by_date(itunes_history)

    log(f"écrit: {OUT_DATA}")
    log(f"écrit: {OUT_HISTORY} ({len(history_dates)} dates >= {HISTORY_CUTOFF})")
    log(f"écrit: {split_count} fichiers dans {OUT_HISTORY_DATES_DIR}")


if __name__ == "__main__":
    main()
