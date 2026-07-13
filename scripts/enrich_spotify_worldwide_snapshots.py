#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = ROOT / "snapshots" / "spotify_charts"
DB_DIR = ROOT / "db"

COUNTRY_BY_REGION = {
    "global": "global",
    "fr": "fr",
    "us": "us",
    "uk": "gb",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def norm_title(value: object) -> str:
    text = str(value or "").lower().strip()
    text = text.replace("â€™", "'").replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def to_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def date_from_snapshot_path(path: Path) -> str:
    for part in path.parts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", part):
            return part
    return path.stem[-10:]


def snapshot_files(start: str | None, end: str | None) -> list[Path]:
    files = sorted(SNAPSHOT_ROOT.glob("20??/??/????-??-??/worldwide/ts_worldwide_*.json"))
    if start:
        files = [path for path in files if date_from_snapshot_path(path) >= start]
    if end:
        files = [path for path in files if date_from_snapshot_path(path) <= end]
    return files


def metadata_from_song(song: dict[str, Any]) -> dict[str, str]:
    title = str(song.get("title_clean") or song.get("title") or song.get("base_title") or "").strip()
    artists = song.get("artists")
    artist_name = str(song.get("primary_artist") or "").strip()
    if not artist_name and isinstance(artists, list):
        artist_name = ", ".join(str(item) for item in artists if item)
    return {
        "song_name": title,
        "image_url": str(song.get("apple_music_image_url") or song.get("image_url") or "").strip(),
        "album_name": str(song.get("primary_album") or song.get("album") or "").strip(),
        "artist_name": artist_name,
        "spotify_url": str(song.get("spotify_url") or song.get("url") or "").strip(),
    }


def load_song_metadata() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    by_track_id: dict[str, dict[str, str]] = {}
    by_title: dict[str, dict[str, str]] = {}
    candidates = [
        ROOT / "website" / "site" / "data" / "songs.json",
        ROOT / "runtime" / "exports" / "web" / "site" / "data" / "songs.json",
    ]

    for path in candidates:
        if not path.exists():
            continue
        data = load_json(path)
        songs = data.get("songs", data) if isinstance(data, dict) else data
        if not isinstance(songs, list):
            continue
        for song in songs:
            if not isinstance(song, dict):
                continue
            meta = {k: v for k, v in metadata_from_song(song).items() if v}
            if not meta.get("song_name"):
                continue
            track_ids = [str(song.get("track_id") or song.get("id") or "").strip()]
            historical = song.get("historical_track_ids")
            if isinstance(historical, list):
                track_ids.extend(str(item).strip() for item in historical)
            for track_id in track_ids:
                if track_id:
                    by_track_id.setdefault(track_id, meta)
            by_title.setdefault(norm_title(meta["song_name"]), meta)
    return by_track_id, by_title


def load_apple_music_metadata() -> dict[str, dict[str, str]]:
    path = ROOT / "website" / "site" / "data" / "applemusic.json"
    if not path.exists():
        return {}
    data = load_json(path)
    out: dict[str, dict[str, str]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            title = str(value.get("song_name") or value.get("video_name") or "").strip()
            if title:
                meta = {
                    "song_name": title,
                    "image_url": str(value.get("image_url") or value.get("artwork_url") or "").strip(),
                    "album_name": str(value.get("album_name") or "").strip(),
                    "artist_name": str(value.get("artist_name") or value.get("artist") or "Taylor Swift").strip(),
                    "spotify_url": "",
                }
                key = norm_title(title)
                current = out.get(key, {})
                if meta.get("image_url") and not current.get("image_url"):
                    out[key] = {k: v for k, v in meta.items() if v}
                else:
                    out.setdefault(key, {k: v for k, v in meta.items() if v})
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    return out


def load_csv_metadata() -> dict[tuple[str, str, int, int], dict[str, str]]:
    out: dict[tuple[str, str, int, int], dict[str, str]] = {}
    for region, country in COUNTRY_BY_REGION.items():
        path = DB_DIR / f"charts_history_{region}.csv"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                chart_date = str(row.get("date") or "").strip()
                rank = to_int(row.get("rank"))
                streams = to_int(row.get("streams"))
                title = str(row.get("song_name") or "").strip()
                if not chart_date or rank is None or streams is None or not title:
                    continue
                out[(chart_date, country, rank, streams)] = {
                    "song_name": title.replace("â€™", "'"),
                    "track_id": str(row.get("track_id") or "").strip(),
                }
    return out


def merge_meta(*items: dict[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for item in items:
        if not item:
            continue
        for key, value in item.items():
            if value and not merged.get(key):
                merged[key] = value
    return merged


def enrich_file(
    path: Path,
    *,
    song_by_track_id: dict[str, dict[str, str]],
    song_by_title: dict[str, dict[str, str]],
    apple_by_title: dict[str, dict[str, str]],
    csv_meta: dict[tuple[str, str, int, int], dict[str, str]],
    dry_run: bool,
) -> tuple[int, int]:
    data = load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("by_track"), dict):
        return (0, 0)

    chart_date = str(data.get("date") or date_from_snapshot_path(path))
    changed_entries = 0
    resolved_tracks = 0

    for track_id, entries in data["by_track"].items():
        if not isinstance(entries, list):
            continue

        title_meta: dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            country = str(entry.get("country") or "").strip().lower()
            if country == "uk":
                country = "gb"
            rank = to_int(entry.get("rank"))
            streams = to_int(entry.get("streams"))
            if rank is None or streams is None:
                continue
            match = csv_meta.get((chart_date, country, rank, streams))
            if match:
                title_meta = match
                break

        title = title_meta.get("song_name") or ""
        meta = merge_meta(
            song_by_track_id.get(str(track_id)),
            song_by_title.get(norm_title(title)),
            apple_by_title.get(norm_title(title)),
            title_meta,
            {"spotify_url": f"https://open.spotify.com/track/{track_id}"} if track_id else None,
        )
        if not meta.get("song_name"):
            continue

        resolved_tracks += 1
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            before = dict(entry)
            entry.setdefault("song_name", meta.get("song_name", ""))
            entry.setdefault("track_id", str(track_id))
            for key in ("image_url", "album_name", "artist_name", "spotify_url"):
                if meta.get(key) and not entry.get(key):
                    entry[key] = meta[key]
            if entry != before:
                changed_entries += 1

    if changed_entries and not dry_run:
        write_json(path, data)
    return (resolved_tracks, changed_entries)


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich Spotify worldwide snapshots with display metadata.")
    parser.add_argument("--start", help="Start date inclusive (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date inclusive (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    song_by_track_id, song_by_title = load_song_metadata()
    apple_by_title = load_apple_music_metadata()
    csv_meta = load_csv_metadata()

    files = snapshot_files(args.start, args.end)
    total_tracks = 0
    total_entries = 0
    changed_files = 0

    for path in files:
        tracks, entries = enrich_file(
            path,
            song_by_track_id=song_by_track_id,
            song_by_title=song_by_title,
            apple_by_title=apple_by_title,
            csv_meta=csv_meta,
            dry_run=args.dry_run,
        )
        if entries:
            changed_files += 1
            total_tracks += tracks
            total_entries += entries
            print(f"[OK] {path.relative_to(ROOT)} tracks={tracks} entries={entries}" + (" (dry-run)" if args.dry_run else ""))

    print(
        f"[DONE] files={len(files)} changed_files={changed_files} "
        f"resolved_tracks={total_tracks} changed_entries={total_entries}"
        + (" (dry-run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
