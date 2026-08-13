#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEB_EXPORT_DATA_DIR = ROOT / "runtime" / "exports" / "web" / "site" / "data"
LEGACY_WEBSITE_DATA_DIR = ROOT / "website" / "site" / "data"
WORLDWIDE_TOTAL_DAYS_PATH = (
    ROOT
    / "collectors"
    / "spotify"
    / "charts"
    / "worldwide"
    / "tools"
    / "json"
    / "total_days.json"
)
REGIONAL_CSVS = {
    "global": ROOT / "db" / "charts_history_global.csv",
    "fr": ROOT / "db" / "charts_history_fr.csv",
    "us": ROOT / "db" / "charts_history_us.csv",
    "uk": ROOT / "db" / "charts_history_uk.csv",
}

SPOTIFY_ID_RE = re.compile(r"(?:spotify:track:|/track/)([A-Za-z0-9]+)")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def track_id_from_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = SPOTIFY_ID_RE.search(text)
    return match.group(1) if match else text


def canonical_country(value: Any) -> str:
    country = str(value or "").strip().lower()
    return "uk" if country == "gb" else country


def date_from_path(path: Path) -> str:
    for part in path.parts:
        if len(part) == 10 and part[4] == "-" and part[7] == "-":
            return part
    return path.stem[-10:]


def worldwide_files() -> list[Path]:
    paths: list[Path] = []
    paths.extend((ROOT / "snapshots" / "spotify_charts").glob("20??/??/????-??-??/worldwide/ts_worldwide_*.json"))
    paths.extend((ROOT / "data").glob("20??/??/????-??-??/run_all_charts/spotify/worldwide/ts_worldwide_*.json"))
    paths.extend((ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "history").glob("20??/??/????-??-??/ts_worldwide_*.json"))
    return sorted({p.resolve(): p for p in paths if p.is_file()}.values())


def load_songs_by_track_id() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for base in (WEB_EXPORT_DATA_DIR, LEGACY_WEBSITE_DATA_DIR):
        path = base / "songs.json"
        if not path.exists():
            continue
        data = load_json(path)
        songs = data.get("songs", data) if isinstance(data, dict) else data
        if not isinstance(songs, list):
            continue
        for song in songs:
            if not isinstance(song, dict):
                continue
            track_id = track_id_from_url(song.get("track_id") or song.get("id"))
            if track_id:
                out.setdefault(track_id, song)
                for historical_id in song.get("historical_track_ids") or []:
                    historical_track_id = track_id_from_url(historical_id)
                    if historical_track_id:
                        out.setdefault(historical_track_id, song)
    return out


def normalize_song_name(value: Any, strip_parentheses: bool = False) -> str:
    text = str(value or "").lower().strip()
    if strip_parentheses:
        text = re.sub(r"\s*\([^)]*\)", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def load_songs_by_name(songs_by_track_id: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for song in {id(song): song for song in songs_by_track_id.values()}.values():
        for attr in ("title", "base_title", "title_clean"):
            for key in {
                normalize_song_name(song.get(attr)),
                normalize_song_name(song.get(attr), strip_parentheses=True),
            }:
                if key:
                    out.setdefault(key, []).append(song)
    return out


def pick_song_by_name(raw_song_name: Any, songs_by_name: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    if re.fullmatch(r"[A-Za-z0-9]{22}", str(raw_song_name or "").strip()):
        return None
    key = normalize_song_name(raw_song_name)
    stripped = normalize_song_name(raw_song_name, strip_parentheses=True)
    candidates = songs_by_name.get(key) or songs_by_name.get(stripped) or []
    if not candidates:
        return None
    exact = [song for song in candidates if normalize_song_name(song.get("title")) == key]
    if exact:
        candidates = exact
    return max(candidates, key=lambda song: to_int(song.get("streams")) or 0)


def load_total_days() -> dict[str, int]:
    if not WORLDWIDE_TOTAL_DAYS_PATH.exists():
        return {}
    data = load_json(WORLDWIDE_TOTAL_DAYS_PATH)
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in data.items():
        days = to_int(value)
        if days is not None:
            out[str(key)] = days
    return out


def song_name_for(track_id: str, entry: dict[str, Any], songs_by_track_id: dict[str, dict[str, Any]]) -> str:
    for key in ("song_name", "track_name", "title"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    song = songs_by_track_id.get(track_id) or {}
    return str(song.get("title_clean") or song.get("title") or song.get("base_title") or track_id).strip()


def enrich_summary(summary: dict[str, Any], songs_by_track_id: dict[str, dict[str, Any]]) -> None:
    track_id = str(summary.get("track_id") or "").strip()
    song = songs_by_track_id.get(track_id) or {}
    if song:
        summary["track_id"] = track_id_from_url(song.get("track_id")) or track_id
        summary["image_url"] = song.get("apple_music_image_url") or song.get("image_url")
        summary["album_name"] = song.get("primary_album", "")
        summary["artist_name"] = song.get("primary_artist", "")
        if song.get("title") and (
            not summary.get("song_name") or re.fullmatch(r"[A-Za-z0-9]{22}", str(summary.get("song_name") or "").strip())
        ):
            summary["song_name"] = song["title"]
    else:
        summary.setdefault("image_url", None)
        summary.setdefault("album_name", "")
        summary.setdefault("artist_name", "")


def add_entry(
    by_country: dict[str, dict[str, dict[str, Any]]],
    chart_date: str,
    track_id: str,
    entry: dict[str, Any],
    songs_by_track_id: dict[str, dict[str, Any]],
    songs_by_name: dict[str, list[dict[str, Any]]],
    total_days_store: dict[str, int],
) -> None:
    country = canonical_country(entry.get("country") or entry.get("country_name"))
    if not country:
        return

    country_name = str(entry.get("country_name") or entry.get("country") or country.upper()).strip()
    song = songs_by_track_id.get(track_id) or pick_song_by_name(song_name_for(track_id, entry, songs_by_track_id), songs_by_name)
    canonical_track_id = track_id_from_url(song.get("track_id")) if song else track_id
    if canonical_track_id:
        track_id = canonical_track_id
    rank = to_int(entry.get("rank")) or 0
    streams = to_int(entry.get("streams")) or 0
    peak_rank = to_int(entry.get("peak_rank")) or rank
    total_days = to_int(entry.get("total_days")) or 0
    store_days = total_days_store.get(f"{track_id}|{country}")
    if store_days is not None:
        total_days = max(total_days, store_days)

    region_rows = by_country.setdefault(country, {})
    summary = region_rows.get(track_id)
    if summary is None:
        summary = {
            "song_name": song_name_for(track_id, entry, songs_by_track_id),
            "last_date": chart_date,
            "last_rank": rank,
            "last_streams": streams,
            "last_country": country,
            "last_country_name": country_name,
            "country": country,
            "country_name": country_name,
            "countries": [{"country": country, "country_name": country_name}],
            "country_count": 1,
            "peak_rank": peak_rank,
            "peak_rank_country": country,
            "peak_rank_country_name": country_name,
            "peak_streams": streams,
            "peak_streams_date": chart_date,
            "best_streams": streams,
            "total_days": total_days,
            "track_id": track_id,
        }
        region_rows[track_id] = summary
        return

    if chart_date > str(summary.get("last_date") or ""):
        summary["last_date"] = chart_date
        summary["last_rank"] = rank
        summary["last_streams"] = streams
        summary["last_country"] = country
        summary["last_country_name"] = country_name
    if streams > (to_int(summary.get("best_streams")) or 0):
        summary["best_streams"] = streams
        summary["peak_streams"] = streams
        summary["peak_streams_date"] = chart_date
    if peak_rank and ((to_int(summary.get("peak_rank")) or 0) == 0 or peak_rank < (to_int(summary.get("peak_rank")) or 0)):
        summary["peak_rank"] = peak_rank
        summary["peak_rank_country"] = country
        summary["peak_rank_country_name"] = country_name
    if total_days > (to_int(summary.get("total_days")) or 0):
        summary["total_days"] = total_days


def build_discographies(limit_regions: set[str] | None = None) -> dict[str, dict[str, Any]]:
    songs_by_track_id = load_songs_by_track_id()
    songs_by_name = load_songs_by_name(songs_by_track_id)
    total_days_store = load_total_days()
    by_country: dict[str, dict[str, dict[str, Any]]] = {}

    for country, csv_path in REGIONAL_CSVS.items():
        if limit_regions is not None and country not in limit_regions:
            continue
        if not csv_path.exists():
            continue
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                song_name = row.get("song_name")
                track_id = track_id_from_url(row.get("track_id"))
                if not track_id:
                    song = pick_song_by_name(song_name, songs_by_name)
                    track_id = track_id_from_url(song.get("track_id")) if song else ""
                if not track_id:
                    continue
                entry = dict(row)
                entry["country"] = country
                entry["country_name"] = {
                    "global": "Global",
                    "fr": "France",
                    "us": "United States",
                    "uk": "United Kingdom",
                }.get(country, country.upper())
                add_entry(by_country, str(row.get("date") or ""), track_id, entry, songs_by_track_id, songs_by_name, total_days_store)

    for path in worldwide_files():
        payload = load_json(path)
        chart_date = str(payload.get("date") or date_from_path(path)) if isinstance(payload, dict) else date_from_path(path)
        by_track = payload.get("by_track") if isinstance(payload, dict) else None
        if not isinstance(by_track, dict):
            continue
        for raw_track_id, entries in by_track.items():
            track_id = track_id_from_url(raw_track_id)
            if not track_id or not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                country = canonical_country(entry.get("country") or entry.get("country_name"))
                if limit_regions is not None and country not in limit_regions:
                    continue
                add_entry(by_country, chart_date, track_id, entry, songs_by_track_id, songs_by_name, total_days_store)

    out: dict[str, dict[str, Any]] = {}
    for country, rows_by_track in sorted(by_country.items()):
        songs = list(rows_by_track.values())
        for summary in songs:
            enrich_summary(summary, songs_by_track_id)
        songs.sort(
            key=lambda x: (
                str(x.get("last_date") or ""),
                str(x.get("country_name") or x.get("country") or ""),
                -(to_int(x.get("last_rank")) or 999999),
                to_int(x.get("last_streams")) or 0,
                -(to_int(x.get("peak_rank")) or 999999),
                to_int(x.get("peak_streams") or x.get("best_streams")) or 0,
                to_int(x.get("total_days")) or 0,
            ),
            reverse=True,
        )
        out[country] = {
            "region": country,
            "latest_date": max((str(song.get("last_date") or "") for song in songs), default=""),
            "songs": songs,
        }
    return out


def write_payload(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build precomputed Spotify chart discography JSON per worldwide region.")
    parser.add_argument("--regions", nargs="*", help="Optional region codes to build, e.g. ph br jp.")
    parser.add_argument("--output-dir", type=Path, default=WEB_EXPORT_DATA_DIR / "charts_discography")
    args = parser.parse_args()

    limit_regions = {canonical_country(region) for region in args.regions or []} or None
    payloads = build_discographies(limit_regions)
    if limit_regions:
        missing = sorted(limit_regions - set(payloads))
        for region in missing:
            print(f"[WARN] no worldwide entries found for {region}")

    for region, payload in payloads.items():
        write_payload(args.output_dir / f"{region}.json", payload)

    index = {
        "regions": sorted(payloads),
        "latest_date": max((payload.get("latest_date", "") for payload in payloads.values()), default=""),
        "count": len(payloads),
    }
    write_payload(args.output_dir / "index.json", index)
    print(f"built {len(payloads)} spotify chart discography region file(s) in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
