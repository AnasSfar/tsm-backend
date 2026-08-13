#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "db"
FIELDNAMES = ["date", "track_id", "song_name", "rank", "streams", "previous_rank", "peak_rank", "total_days", "streak", "movement"]

BASE_COUNTRIES = {
    "global": ("global",),
    "fr": ("fr",),
    "us": ("us",),
    "uk": ("gb", "uk"),
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def title_lookup() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (ROOT / "website" / "site" / "data" / "songs.json", ROOT / "runtime" / "exports" / "web" / "site" / "data" / "songs.json"):
        if not path.exists():
            continue
        data = load_json(path)
        songs = data.get("songs", data) if isinstance(data, dict) else data
        if not isinstance(songs, list):
            continue
        for song in songs:
            if not isinstance(song, dict):
                continue
            track_id = str(song.get("track_id") or song.get("id") or "").strip()
            title = str(song.get("title_clean") or song.get("title") or song.get("base_title") or "").strip()
            if track_id and title:
                out.setdefault(track_id, title)
                for historical_id in song.get("historical_track_ids") or []:
                    historical_track_id = str(historical_id or "").strip()
                    if historical_track_id:
                        out.setdefault(historical_track_id, title)
    return out


def snapshot_files(chart: str) -> list[Path]:
    paths = []
    paths.extend((ROOT / "snapshots" / "spotify_charts").glob(f"20??/??/????-??-??/{chart}/ts_chart_*.json"))
    paths.extend((ROOT / "data").glob(f"20??/??/????-??-??/run_all_charts/spotify/{chart}/ts_chart_*.json"))
    paths.extend((ROOT / "collectors" / "spotify" / "charts" / chart / "history").glob("20??/??/????-??-??/ts_chart_*.json"))
    return sorted({p.resolve(): p for p in paths if p.is_file()}.values())


def worldwide_files() -> list[Path]:
    paths = []
    paths.extend((ROOT / "snapshots" / "spotify_charts").glob("20??/??/????-??-??/worldwide/ts_worldwide_*.json"))
    paths.extend((ROOT / "data").glob("20??/??/????-??-??/run_all_charts/spotify/worldwide/ts_worldwide_*.json"))
    paths.extend((ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "history").glob("20??/??/????-??-??/ts_worldwide_*.json"))
    return sorted({p.resolve(): p for p in paths if p.is_file()}.values())


def canonical_country(value: Any) -> str:
    country = str(value or "").strip().lower()
    return "uk" if country == "gb" else country


def discover_countries() -> dict[str, tuple[str, ...]]:
    countries: dict[str, set[str]] = {key: set(values) for key, values in BASE_COUNTRIES.items()}
    for path in worldwide_files():
        try:
            data = load_json(path)
        except Exception:
            continue
        by_track = data.get("by_track", {}) if isinstance(data, dict) else {}
        if not isinstance(by_track, dict):
            continue
        for entries in by_track.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                raw_country = str(entry.get("country") or entry.get("country_name") or "").strip().lower()
                country = canonical_country(raw_country)
                if not country:
                    continue
                countries.setdefault(country, set()).add(raw_country or country)
                countries[country].add(country)
    return {country: tuple(sorted(values)) for country, values in sorted(countries.items())}


def date_from_path(path: Path) -> str:
    for part in path.parts:
        if len(part) == 10 and part[4] == "-" and part[7] == "-":
            return part
    return path.stem[-10:]


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def csv_row(chart_date: str, track_id: str, song_name: str, entry: dict[str, Any]) -> dict[str, str]:
    previous_rank = to_int(entry.get("previous_rank"))
    return {
        "date": chart_date,
        "track_id": track_id,
        "song_name": song_name,
        "rank": str(to_int(entry.get("rank")) or ""),
        "streams": str(to_int(entry.get("streams")) or 0),
        "previous_rank": str(previous_rank) if previous_rank else "",
        "peak_rank": str(to_int(entry.get("peak_rank")) or ""),
        "total_days": str(to_int(entry.get("total_days")) or ""),
        "streak": str(to_int(entry.get("streak")) or ""),
        "movement": str(entry.get("movement") or ""),
    }


def rows_from_regional_snapshot(path: Path) -> list[dict[str, str]]:
    chart_date = date_from_path(path)
    data = load_json(path)
    rows = data if isinstance(data, list) else data.get("entries", [])
    out = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("track_name") or entry.get("song_name") or "").strip()
        track_id = str(entry.get("track_id") or entry.get("_track_id_uri") or "").strip()
        if name:
            out.append(csv_row(chart_date, track_id, name, entry))
    return out


def rows_from_worldwide_snapshot(path: Path, chart: str, countries: dict[str, tuple[str, ...]], names: dict[str, str]) -> list[dict[str, str]]:
    chart_date = date_from_path(path)
    data = load_json(path)
    by_track = data.get("by_track", {}) if isinstance(data, dict) else {}
    wanted = set(countries[chart])
    out = []
    for track_id, entries in by_track.items():
        track_id = str(track_id).strip()
        name = names.get(track_id, track_id)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and canonical_country(entry.get("country") or entry.get("country_name")) in wanted:
                out.append(csv_row(chart_date, track_id, name, entry))
    return out


def read_existing(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [{field: (row.get(field) or "") for field in FIELDNAMES} for row in reader]


def append_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_newline = path.exists() and path.stat().st_size > 0
    if needs_newline:
        raw = path.read_bytes()
        needs_newline = not raw.endswith((b"\n", b"\r"))
    with path.open("a", encoding="utf-8", newline="") as handle:
        if needs_newline:
            handle.write("\n")
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        if path.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(rows)


def sync_chart(chart: str, countries: dict[str, tuple[str, ...]], names: dict[str, str], dry_run: bool) -> tuple[int, str | None, str | None, int]:
    csv_path = DB_DIR / f"charts_history_{chart}.csv"
    existing_rows = read_existing(csv_path)
    existing_keys: set[tuple[str, str]] = set()
    for row in existing_rows:
        key = (row["date"], row.get("track_id") or row["song_name"])
        existing_keys.add(key)

    before_dates = {date for date, _ in existing_keys}
    additions: dict[tuple[str, str], dict[str, str]] = {}
    if chart in BASE_COUNTRIES:
        for path in snapshot_files(chart):
            for row in rows_from_regional_snapshot(path):
                key = (row["date"], row.get("track_id") or row["song_name"])
                if key not in existing_keys:
                    additions.setdefault(key, row)

    for path in worldwide_files():
        for row in rows_from_worldwide_snapshot(path, chart, countries, names):
            key = (row["date"], row.get("track_id") or row["song_name"])
            if key not in existing_keys:
                additions.setdefault(key, row)

    new_keys = sorted(additions, key=lambda key: (key[0], to_int(additions[key]["rank"]) or 9999, key[1].casefold()))
    rows = [additions[key] for key in new_keys]
    after_dates = before_dates | {row["date"] for row in rows}
    added_dates = after_dates - before_dates
    if not dry_run:
        append_csv(csv_path, rows)
    return len(added_dates), (min(after_dates) if after_dates else None), (max(after_dates) if after_dates else None), len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Spotify country chart CSVs from regional and worldwide snapshots.")
    parser.add_argument("--charts", nargs="*", help="Region codes to sync. Defaults to every country found in worldwide snapshots.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    countries = discover_countries()
    charts = [canonical_country(chart) for chart in (args.charts or sorted(countries))]
    unknown = sorted(set(charts) - set(countries))
    if unknown:
        raise SystemExit(f"Unknown chart region(s): {', '.join(unknown)}")

    names = title_lookup()
    total_rows = 0
    total_dates = 0
    for chart in charts:
        added_dates, first, last, added_rows = sync_chart(chart, countries, names, args.dry_run)
        total_dates += added_dates
        total_rows += added_rows
        print(f"{chart}: +{added_dates} date(s), +{added_rows} row(s), range {first} -> {last}")
    print(f"done: {len(charts)} region(s), +{total_dates} date-region(s), +{total_rows} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
