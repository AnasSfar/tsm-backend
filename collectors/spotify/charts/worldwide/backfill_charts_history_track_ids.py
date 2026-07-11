#!/usr/bin/env python3
"""Backfill chart history track_id values from exact worldwide snapshots.

This only fills a row when the local worldwide snapshot has one unambiguous
entry with the same date, country, rank, and streams. Rows without an exact
snapshot match are left blank instead of being guessed from the title.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
DB_DIR = ROOT / "db"
SNAPSHOT_ROOT = ROOT / "snapshots" / "spotify_charts"
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.history import save, update  # noqa: E402

REGION_TO_COUNTRY = {
    "global": "global",
    "fr": "fr",
    "us": "us",
    "uk": "gb",
}

HISTORY_FILES = {
    region: DB_DIR / f"charts_history_{region}.csv"
    for region in REGION_TO_COUNTRY
}

TS_HISTORY_PATHS = {
    "global": ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "ts_history.json",
    "fr": ROOT / "collectors" / "spotify" / "charts" / "fr" / "tools" / "json" / "ts_history.json",
    "us": ROOT / "collectors" / "spotify" / "charts" / "us" / "tools" / "json" / "ts_history.json",
    "uk": ROOT / "collectors" / "spotify" / "charts" / "uk" / "tools" / "json" / "ts_history.json",
}


def _to_int(value) -> int | None:
    try:
        return int(float(str(value or "").strip()))
    except Exception:
        return None


def _snapshot_path(chart_date: str) -> Path:
    year, month = chart_date[:4], chart_date[5:7]
    return (
        SNAPSHOT_ROOT
        / year
        / month
        / chart_date
        / "worldwide"
        / f"ts_worldwide_{chart_date}.json"
    )


def _load_snapshot_index(chart_date: str, country: str) -> dict[tuple[int, int], str | None]:
    path = _snapshot_path(chart_date)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}

    by_track = payload.get("by_track")
    if not isinstance(by_track, dict):
        return {}

    index: dict[tuple[int, int], str | None] = {}
    for track_id, entries in by_track.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("country") or "").strip().lower() != country:
                continue
            rank = _to_int(entry.get("rank"))
            streams = _to_int(entry.get("streams"))
            if rank is None or streams is None:
                continue
            key = (rank, streams)
            previous = index.get(key)
            index[key] = str(track_id).strip() if previous is None else None
    return index


def _fieldnames_with_track_id(fieldnames: list[str] | None) -> list[str]:
    names = list(fieldnames or [])
    if "track_id" in names:
        return names
    if "date" in names:
        idx = names.index("date") + 1
        return names[:idx] + ["track_id"] + names[idx:]
    return ["track_id", *names]


def backfill_file(path: Path, region: str, *, dry_run: bool) -> tuple[int, int, int]:
    country = REGION_TO_COUNTRY[region]
    if not path.exists():
        print(f"[SKIP] {path} missing")
        return (0, 0, 0)

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = _fieldnames_with_track_id(reader.fieldnames)

    indexes: dict[str, dict[tuple[int, int], str | None]] = {}
    filled = 0
    ambiguous = 0
    missing = 0

    for row in rows:
        if str(row.get("track_id") or "").strip():
            continue
        chart_date = str(row.get("date") or "").strip()
        rank = _to_int(row.get("rank"))
        streams = _to_int(row.get("streams"))
        if not chart_date or rank is None or streams is None:
            missing += 1
            continue
        if chart_date not in indexes:
            indexes[chart_date] = _load_snapshot_index(chart_date, country)
        track_id = indexes[chart_date].get((rank, streams))
        if track_id:
            row["track_id"] = track_id
            filled += 1
        elif track_id is None and (rank, streams) in indexes[chart_date]:
            ambiguous += 1
        else:
            missing += 1

    if not dry_run and filled:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in fieldnames})

    print(
        f"[{region}] filled={filled} missing_snapshot_or_match={missing} ambiguous={ambiguous}"
        + (" (dry-run)" if dry_run else "")
    )
    return (filled, missing, ambiguous)


def rebuild_ts_history(region: str, *, dry_run: bool) -> tuple[int, int]:
    csv_path = HISTORY_FILES[region]
    history_path = TS_HISTORY_PATHS[region]
    history: dict = {}
    rows_written = 0
    track_id_rows = 0

    if not csv_path.exists():
        print(f"[SKIP] {region} history rebuild: missing {csv_path}")
        return (0, 0)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = sorted(csv.DictReader(f), key=lambda row: (row.get("date") or "", row.get("rank") or ""))

    for row in rows:
        chart_date = str(row.get("date") or "").strip()
        track_name = str(row.get("song_name") or "").strip()
        rank = _to_int(row.get("rank"))
        if not chart_date or not track_name or rank is None:
            continue
        track_id = str(row.get("track_id") or "").strip()
        update(
            history,
            track_name,
            chart_date,
            rank,
            row.get("streams"),
            previous_rank=row.get("previous_rank"),
            peak_rank=row.get("peak_rank"),
            track_id=track_id or None,
        )
        rows_written += 1
        if track_id:
            track_id_rows += 1

    if not dry_run:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        save(history, history_path)

    print(
        f"[{region}] ts_history entries={rows_written} track_id_rows={track_id_rows} keys={len(history)}"
        + (" (dry-run)" if dry_run else "")
    )
    return (rows_written, track_id_rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--region", choices=sorted(REGION_TO_COUNTRY), action="append")
    parser.add_argument("--rebuild-ts-history", action="store_true")
    args = parser.parse_args()

    regions = args.region or sorted(REGION_TO_COUNTRY)
    totals = [0, 0, 0]
    for region in regions:
        result = backfill_file(HISTORY_FILES[region], region, dry_run=args.dry_run)
        totals = [a + b for a, b in zip(totals, result)]

    print(
        f"[DONE] filled={totals[0]} missing_snapshot_or_match={totals[1]} ambiguous={totals[2]}"
        + (" (dry-run)" if args.dry_run else "")
    )
    if args.rebuild_ts_history:
        history_totals = [0, 0]
        for region in regions:
            result = rebuild_ts_history(region, dry_run=args.dry_run)
            history_totals = [a + b for a, b in zip(history_totals, result)]
        print(
            f"[DONE] ts_history_entries={history_totals[0]} track_id_rows={history_totals[1]}"
            + (" (dry-run)" if args.dry_run else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
