#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

DB_DIR = ROOT / "db"
DATA_ROOT = ROOT / "data"
ARCHIVE_DB_DIR = DATA_ROOT / "_archive" / "original" / "db"
OUT_DIR = ROOT / "website" / "site" / "data"

GLOBAL_CSV = DB_DIR / "apple_music_global.csv"
TOP_SONGS_CSV = DB_DIR / "apple_music_ts_top_songs.csv"
TOP_VIDEOS_CSV = DB_DIR / "apple_music_ts_top_videos.csv"
COUNTRY_CSV = DB_DIR / "apple_music_country_charts.csv"
COUNTRY_ALBUMS_CSV = DB_DIR / "apple_music_country_albums.csv"
GENRE_ALBUMS_CSV = DB_DIR / "apple_music_genre_album_charts.csv"
MUSIC_VIDEO_CHARTS_CSV = DB_DIR / "apple_music_music_video_charts.csv"
GENRE_CSV = DB_DIR / "apple_music_genre_charts.csv"

OUT_DATA = OUT_DIR / "applemusic.json"
OUT_HISTORY = OUT_DIR / "applemusic_history.json"


def log(msg: str) -> None:
    print(f"[applemusic-export] {msg}", flush=True)


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
    # Prefer scraped_at (datetime) so multiple daily snapshots are preserved
    for key in ("scraped_at", "date", "chart_date", "day"):
        val = clean_str(row.get(key))
        if val:
            return val
    return ""


def normalize_song_entry(row: dict[str, Any]) -> dict[str, Any]:
    previous_rank = to_int(row.get("previous_rank") or row.get("prev_rank"))
    genre_names_raw = clean_str(row.get("genre_names"))
    video_name = clean_str(row.get("video_name"))
    return {
        "song_name": clean_str(row.get("song_name") or row.get("title") or row.get("track_name") or video_name),
        "video_name": video_name,
        "apple_music_id": clean_str(row.get("apple_music_id") or row.get("song_id") or row.get("id")),
        "rank": to_int(row.get("rank")),
        "previous_rank": previous_rank if previous_rank else None,
        "image_url": clean_str(row.get("image_url") or row.get("artwork_url")),
        "url": clean_str(row.get("url") or row.get("song_url")),
        "artist_name": clean_str(row.get("artist_name") or row.get("artist") or "Taylor Swift"),
        "album_name": clean_str(row.get("album_name")),
        "duration_ms": to_int(row.get("duration_ms")),
        "release_date": clean_str(row.get("release_date")),
        "isrc": clean_str(row.get("isrc")),
        "content_rating": clean_str(row.get("content_rating")),
        "genre_names": [part.strip() for part in genre_names_raw.split("|") if part.strip()] if genre_names_raw else [],
    }


def normalize_album_entry(row: dict[str, Any]) -> dict[str, Any]:
    previous_rank = to_int(row.get("previous_rank") or row.get("prev_rank"))
    genre_names_raw = clean_str(row.get("genre_names"))
    return {
        "album_name": clean_str(row.get("album_name") or row.get("name") or row.get("title")),
        "apple_music_id": clean_str(row.get("apple_music_id") or row.get("album_id") or row.get("id")),
        "rank": to_int(row.get("rank")),
        "previous_rank": previous_rank if previous_rank else None,
        "image_url": clean_str(row.get("image_url") or row.get("artwork_url")),
        "url": clean_str(row.get("url")),
        "artist_name": clean_str(row.get("artist_name") or row.get("artist") or "Taylor Swift"),
        "release_date": clean_str(row.get("release_date")),
        "genre_names": [part.strip() for part in genre_names_raw.split("|") if part.strip()] if genre_names_raw else [],
    }


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    candidates = []
    if path.exists():
        candidates.append(path)
    archived = ARCHIVE_DB_DIR / path.name
    if archived.exists() and archived not in candidates:
        candidates.append(archived)
    candidates.extend(sorted(DATA_ROOT.glob(f"????/??/????-??-??/apple_music/{path.name}")))
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


def _get_entries(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return v.get("entries") or []
    return []


def _build_rank_lookup(entries: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    by_id: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for entry in entries or []:
        rank = entry.get("rank")
        if rank is None:
            continue
        am_id = clean_str(entry.get("apple_music_id"))
        name = _song_key(entry.get("song_name") or entry.get("album_name") or "")
        if am_id and am_id not in by_id:
            by_id[am_id] = rank
        if name and name not in by_name:
            by_name[name] = rank
    return by_id, by_name


def _backfill_entries(entries: list[dict[str, Any]], by_id: dict[str, int], by_name: dict[str, int]) -> None:
    for entry in entries:
        if entry.get("previous_rank") not in (None, ""):
            continue
        am_id = clean_str(entry.get("apple_music_id"))
        name = _song_key(entry.get("song_name") or entry.get("album_name") or "")
        rank = (by_id.get(am_id) if am_id else None) or by_name.get(name)
        if rank is not None:
            entry["previous_rank"] = rank


def _backfill_flat(current: dict[str, Any] | None, prev_section: Any) -> None:
    if not current or not prev_section:
        return
    by_id, by_name = _build_rank_lookup(_get_entries(prev_section))
    _backfill_entries(current.get("entries") or [], by_id, by_name)


def _backfill_by_country(current: dict[str, Any] | None, prev_section: Any) -> None:
    if not current or not prev_section:
        return
    prev_cc = prev_section if isinstance(prev_section, dict) else {}
    if "countries" in prev_cc:
        prev_cc = prev_cc["countries"]
    current_cc = current.get("countries") or {}
    for country, entries in current_cc.items():
        by_id, by_name = _build_rank_lookup(_get_entries(prev_cc.get(country)))
        _backfill_entries(entries if isinstance(entries, list) else _get_entries(entries), by_id, by_name)


def _backfill_by_genre(current: dict[str, Any] | None, prev_section: Any) -> None:
    if not current or not prev_section:
        return
    prev_top = prev_section if isinstance(prev_section, dict) else {}
    prev_by_country = prev_top.get("by_country") or prev_top.get("countries") or prev_top
    current_by_country = current.get("by_country") or {}
    for country, genres in current_by_country.items():
        if not isinstance(genres, dict):
            continue
        prev_genres = prev_by_country.get(country) or {}
        for genre, entries in genres.items():
            by_id, by_name = _build_rank_lookup(_get_entries(prev_genres.get(genre)))
            _backfill_entries(entries if isinstance(entries, list) else _get_entries(entries), by_id, by_name)


def sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda x: (
            x.get("rank") is None,
            x.get("rank") if x.get("rank") is not None else 10**9,
            x.get("song_name", "").lower(),
        ),
    )


def _song_key(name: Any) -> str:
    return str(name or "").strip().casefold()


def build_ranked_series(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    song_dates: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        d = normalize_date(row)
        if not d:
            continue
        entry = normalize_song_entry(row)
        by_date[d].append(entry)
        song_dates[_song_key(entry["song_name"])].add(d)

    dates = sorted(by_date.keys())
    latest = dates[-1] if dates else None

    if latest:
        for entry in by_date[latest]:
            if entry.get("previous_rank") is None:
                past = song_dates.get(_song_key(entry["song_name"]), set()) - {latest}
                if past:
                    entry["is_reentry"] = True

    for d in list(by_date.keys()):
        by_date[d] = sort_entries(by_date[d])

    current = {
        "date": latest,
        "entries": by_date.get(latest, []),
    }

    return current, by_date, dates


def build_ranked_album_series(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        d = normalize_date(row)
        if not d:
            continue
        by_date[d].append(normalize_album_entry(row))
    for d in list(by_date.keys()):
        by_date[d] = sort_entries(by_date[d])
    dates = sorted(by_date.keys())
    latest = dates[-1] if dates else None
    current = {"date": latest, "entries": by_date.get(latest, [])}
    return current, by_date, dates


def build_ranked_video_series(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        d = normalize_date(row)
        if not d:
            continue
        by_date[d].append(normalize_song_entry(row))
    for d in list(by_date.keys()):
        by_date[d] = sort_entries(by_date[d])
    dates = sorted(by_date.keys())
    latest = dates[-1] if dates else None
    current = {"date": latest, "entries": by_date.get(latest, [])}
    return current, by_date, dates


def build_global(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    return build_ranked_series(rows)


def build_top_songs(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    return build_ranked_series(rows)


def build_country(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, dict[str, list[dict[str, Any]]]], list[str]]:
    # history format attendu par ton JS:
    # historyData.country[date][country] = [...]
    by_date: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    song_dates: dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in rows:
        d = normalize_date(row)
        country = clean_str(row.get("country")).lower()
        if not d or not country:
            continue
        entry = normalize_song_entry(row)
        by_date[d][country].append(entry)
        song_dates[(country, _song_key(entry["song_name"]))].add(d)

    dates = sorted(by_date.keys())
    latest = dates[-1] if dates else None

    if latest:
        for country, entries in by_date[latest].items():
            for entry in entries:
                if entry.get("previous_rank") is None:
                    past = song_dates.get((country, _song_key(entry["song_name"])), set()) - {latest}
                    if past:
                        entry["is_reentry"] = True

    for d, countries in by_date.items():
        for country, entries in list(countries.items()):
            countries[country] = sort_entries(entries)

    current = None
    if latest:
        current = {
            "date": latest,
            "countries": by_date[latest],
        }

    return current, by_date, dates


def detect_genre_key(row: dict[str, Any]) -> str:
    # Supporte plusieurs noms de colonnes possibles
    for key in ("genre", "genre_name", "chart_name", "subchart", "section"):
        value = clean_str(row.get(key))
        if value:
            return value

    # fallback si chart_type contient la valeur de genre
    chart_type = clean_str(row.get("chart_type"))
    if chart_type and chart_type.lower() != "genre":
        return chart_type

    return ""


def build_genre(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, dict[str, dict[str, list[dict[str, Any]]]]], list[str]]:
    # history format attendu:
    # historyData.genre[date][country][genre] = [...]
    by_date: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for row in rows:
        d = normalize_date(row)
        country = clean_str(row.get("country")).lower()
        genre = detect_genre_key(row)
        if not d or not country or not genre:
            continue

        by_date[d][country][genre].append(normalize_song_entry(row))

    for d, countries in by_date.items():
        for country, genres in countries.items():
            for genre, entries in list(genres.items()):
                genres[genre] = sort_entries(entries)

    dates = sorted(by_date.keys())
    latest = dates[-1] if dates else None

    current = None
    if latest:
        current = {
            "date": latest,
            "by_country": by_date[latest],
        }

    return current, by_date, dates


def build_genre_albums(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, dict[str, dict[str, list[dict[str, Any]]]]], list[str]]:
    by_date: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for row in rows:
        d = normalize_date(row)
        country = clean_str(row.get("country")).lower()
        genre = detect_genre_key(row)
        if not d or not country or not genre:
            continue

        by_date[d][country][genre].append(normalize_album_entry(row))

    for d, countries in by_date.items():
        for country, genres in countries.items():
            for genre, entries in list(genres.items()):
                genres[genre] = sort_entries(entries)

    dates = sorted(by_date.keys())
    latest = dates[-1] if dates else None

    current = None
    if latest:
        current = {
            "date": latest,
            "by_country": by_date[latest],
        }

    return current, by_date, dates


def build_country_albums(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, dict[str, list[dict[str, Any]]]], list[str]]:
    by_date: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    album_dates: dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in rows:
        d = normalize_date(row)
        country = clean_str(row.get("country")).lower()
        if not d or not country:
            continue
        entry = normalize_album_entry(row)
        by_date[d][country].append(entry)
        album_dates[(country, _song_key(entry["album_name"]))].add(d)

    dates = sorted(by_date.keys())
    latest = dates[-1] if dates else None

    if latest:
        for country, entries in by_date[latest].items():
            for entry in entries:
                if entry.get("previous_rank") is None:
                    past = album_dates.get((country, _song_key(entry["album_name"])), set()) - {latest}
                    if past:
                        entry["is_reentry"] = True

    for d, countries in by_date.items():
        for country, entries in list(countries.items()):
            countries[country] = sort_entries(entries)

    current = None
    if latest:
        current = {
            "date": latest,
            "countries": by_date[latest],
        }

    return current, by_date, dates


def main() -> None:
    ensure_out_dir()

    # Load previous snapshot before overwriting — used to backfill previous_rank
    # when collectors run without local CSV history (e.g. fresh CI checkout).
    prev_data = _load_prev_snapshot(OUT_DATA)
    if prev_data:
        log("snapshot précédent chargé pour backfill previous_rank")

    global_rows = read_csv_rows(GLOBAL_CSV)
    top_rows = read_csv_rows(TOP_SONGS_CSV)
    top_video_rows = read_csv_rows(TOP_VIDEOS_CSV)
    country_rows = read_csv_rows(COUNTRY_CSV)
    country_album_rows = read_csv_rows(COUNTRY_ALBUMS_CSV)
    genre_album_rows = read_csv_rows(GENRE_ALBUMS_CSV)
    music_video_chart_rows = read_csv_rows(MUSIC_VIDEO_CHARTS_CSV)
    genre_rows = read_csv_rows(GENRE_CSV)

    global_current, global_history, global_dates = build_global(global_rows)
    top_current, top_history, top_dates = build_top_songs(top_rows)
    top_video_current, top_video_history, top_video_dates = build_ranked_video_series(top_video_rows)
    country_current, country_history, country_dates = build_country(country_rows)
    country_album_current, country_album_history, country_album_dates = build_country_albums(country_album_rows)
    genre_album_current, genre_album_history, genre_album_dates = build_genre_albums(genre_album_rows)
    music_video_chart_current, music_video_chart_history, music_video_chart_dates = build_country(music_video_chart_rows)
    genre_current, genre_history, genre_dates = build_genre(genre_rows)

    all_dates = sorted(set(
        global_dates + top_dates + top_video_dates +
        country_dates + country_album_dates + genre_album_dates + music_video_chart_dates + genre_dates
    ))
    latest_any = all_dates[-1] if all_dates else None

    applemusic_data = {
        "scraped_at": latest_any,
        "dates": all_dates,
        "global_chart": global_current,
        "ts_top_songs": top_current,
        "ts_top_videos": top_video_current,
        "country_charts": country_current,
        "country_album_charts": country_album_current,
        "genre_album_charts": genre_album_current,
        "music_video_charts": music_video_chart_current,
        "genre_charts": genre_current,
    }

    applemusic_history = {
        "dates": all_dates,
        "global": global_history,
        "top_songs": top_history,
        "top_videos": top_video_history,
        "country": country_history,
        "country_albums": country_album_history,
        "genre_albums": genre_album_history,
        "music_video_charts": music_video_chart_history,
        "genre": genre_history,
    }

    # Backfill previous_rank from previous snapshot (covers CI where only current run's CSVs exist)
    if prev_data:
        _backfill_flat(global_current, prev_data.get("global_chart"))
        _backfill_flat(top_current, prev_data.get("ts_top_songs"))
        _backfill_flat(top_video_current, prev_data.get("ts_top_videos"))
        _backfill_by_country(country_current, prev_data.get("country_charts"))
        _backfill_by_country(country_album_current, prev_data.get("country_album_charts"))
        _backfill_by_genre(genre_current, prev_data.get("genre_charts"))
        _backfill_by_genre(genre_album_current, prev_data.get("genre_album_charts"))
        _backfill_by_country(music_video_chart_current, prev_data.get("music_video_charts"))

    OUT_DATA.write_text(json.dumps(applemusic_data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HISTORY.write_text(json.dumps(applemusic_history, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"écrit: {OUT_DATA}")
    log(f"écrit: {OUT_HISTORY}")
    log(f"dates détectées: {len(all_dates)}")


if __name__ == "__main__":
    main()
