#!/usr/bin/env python3
"""Backfill local Apple Music daily chart CSVs from R2 apple-music/snapshots/.

Since 2026-07-30, collectors/apple_music runs exclusively on the OVH VPS and
never syncs its CSV history back to git or to the local machine (db/apple_music_*.csv
and snapshots/apple_music_charts/ are all gitignored, see OVH.md). swift_top_100.py
runs locally and reads those local files directly, so any day the VPS collected
but the local machine never saw is silently scored as 0 Apple Music
global/country/genre points (see collector-billboard/CONTEXTE.md piege).

apple-music/snapshots/{timestamp}.json on R2 is the durable historical record
(never deleted, see r2_keys.APPLE_MUSIC_SNAPSHOTS_PREFIX) written by every VPS
run. This script reconstructs the same per-day CSV files the local pipeline
used to write itself, at snapshots/apple_music_charts/YYYY/MM/YYYY-MM-DD/, so
_active_apple_music_csvs() picks them up exactly like a local run would have.

Dry-run by default; pass --apply to write.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import apple_music_charts_dir  # noqa: E402

import boto3  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

GLOBAL_FIELDS = [
    "date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id",
    "rank", "previous_rank", "image_url", "url", "artist_name", "album_name",
    "duration_ms", "release_date", "isrc", "content_rating", "genre_names",
]
COUNTRY_FIELDS = [
    "date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id",
    "rank", "previous_rank", "image_url", "url", "artist_name",
]
COUNTRY_ALBUM_FIELDS = [
    "date", "scraped_at", "country", "chart_type", "album_name", "apple_music_id",
    "rank", "previous_rank", "image_url", "url", "artist_name", "release_date",
    "genre_names",
]
MUSIC_VIDEO_FIELDS = [
    "date", "scraped_at", "country", "chart_type", "video_name", "apple_music_id",
    "rank", "previous_rank", "image_url", "url", "artist_name", "album_name",
    "duration_ms", "release_date", "genre_names",
]
GENRE_FIELDS = [
    "date", "scraped_at", "country", "genre_id", "genre_name", "chart_type",
    "song_name", "apple_music_id", "rank", "previous_rank", "image_url", "url",
    "artist_name", "album_name", "duration_ms", "release_date", "isrc",
    "content_rating", "genre_names",
]
GENRE_ALBUM_FIELDS = [
    "date", "scraped_at", "country", "genre_id", "genre_name", "chart_type",
    "album_name", "apple_music_id", "rank", "previous_rank", "image_url", "url",
    "artist_name", "release_date", "genre_names",
]
TS_FIELDS = [
    "date", "scraped_at", "storefront", "song_name", "apple_music_id", "rank",
    "previous_rank", "image_url", "url", "artist_name", "album_name",
    "duration_ms", "release_date", "isrc", "content_rating", "genre_names",
]


def get_r2_client():
    account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
    access_key = os.getenv("R2_ACCESS_KEY_ID", "").strip()
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
    if not (account_id and access_key and secret_key):
        raise RuntimeError("Missing R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY in .env")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def get_bucket_name() -> str:
    return os.getenv("R2_BUCKET", "taylor-data").strip() or "taylor-data"


def _entry_row(entry: dict[str, Any], fields: list[str], extra: dict[str, Any]) -> dict[str, Any]:
    row = dict(extra)
    for f in fields:
        if f in row and row[f] != "":
            continue
        if f == "genre_names":
            names = entry.get("genre_names") or []
            row[f] = " | ".join(names) if isinstance(names, list) else (names or "")
        else:
            row[f] = entry.get(f, "")
    return row


def rows_from_payload(payload: dict[str, Any], day: str, scraped_at: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {
        "apple_music_global.csv": [],
        "apple_music_country_charts.csv": [],
        "apple_music_country_albums.csv": [],
        "apple_music_genre_charts.csv": [],
        "apple_music_genre_album_charts.csv": [],
        "apple_music_music_video_charts.csv": [],
        "apple_music_ts_top_songs_global.csv": [],
    }

    for entry in payload.get("global_chart") or []:
        out["apple_music_global.csv"].append(
            _entry_row(entry, GLOBAL_FIELDS, {"date": day, "scraped_at": scraped_at, "country": "", "chart_type": "global"})
        )

    for entry in payload.get("ts_top_songs") or []:
        out["apple_music_ts_top_songs_global.csv"].append(
            _entry_row(entry, TS_FIELDS, {"date": day, "scraped_at": scraped_at, "storefront": "global"})
        )

    country_charts = payload.get("country_charts") or {}
    if isinstance(country_charts, dict):
        for country, entries in country_charts.items():
            for entry in entries or []:
                out["apple_music_country_charts.csv"].append(
                    _entry_row(entry, COUNTRY_FIELDS, {"date": day, "scraped_at": scraped_at, "country": country, "chart_type": "country"})
                )

    country_album_charts = payload.get("country_album_charts") or {}
    if isinstance(country_album_charts, dict):
        for country, entries in country_album_charts.items():
            for entry in entries or []:
                out["apple_music_country_albums.csv"].append(
                    _entry_row(
                        entry,
                        COUNTRY_ALBUM_FIELDS,
                        {"date": day, "scraped_at": scraped_at, "country": country, "chart_type": "country_albums"},
                    )
                )

    music_video_charts = payload.get("music_video_charts") or payload.get("top_videos") or {}
    if isinstance(music_video_charts, dict):
        for country, entries in music_video_charts.items():
            for entry in entries or []:
                out["apple_music_music_video_charts.csv"].append(
                    _entry_row(
                        entry,
                        MUSIC_VIDEO_FIELDS,
                        {"date": day, "scraped_at": scraped_at, "country": country, "chart_type": "music_videos"},
                    )
                )
    elif isinstance(music_video_charts, list):
        for entry in music_video_charts:
            out["apple_music_music_video_charts.csv"].append(
                _entry_row(
                    entry,
                    MUSIC_VIDEO_FIELDS,
                    {"date": day, "scraped_at": scraped_at, "country": "", "chart_type": "music_videos"},
                )
            )

    genre_charts = payload.get("genre_charts") or {}
    if isinstance(genre_charts, dict):
        for country, genres in genre_charts.items():
            if not isinstance(genres, dict):
                continue
            for genre_name, entries in genres.items():
                for entry in entries or []:
                    out["apple_music_genre_charts.csv"].append(
                        _entry_row(
                            entry, GENRE_FIELDS,
                            {
                                "date": day, "scraped_at": scraped_at, "country": country,
                                "genre_id": "", "genre_name": genre_name, "chart_type": "genre",
                            },
                        )
                    )

    genre_album_charts = payload.get("genre_album_charts") or {}
    if isinstance(genre_album_charts, dict):
        for country, genres in genre_album_charts.items():
            if not isinstance(genres, dict):
                continue
            for genre_name, entries in genres.items():
                for entry in entries or []:
                    out["apple_music_genre_album_charts.csv"].append(
                        _entry_row(
                            entry, GENRE_ALBUM_FIELDS,
                            {
                                "date": day, "scraped_at": scraped_at, "country": country,
                                "genre_id": "", "genre_name": genre_name, "chart_type": "genre_albums",
                            },
                        )
                    )

    return out


def write_day_csvs(day: str, rows_by_file: dict[str, list[dict[str, Any]]], apply: bool) -> list[str]:
    field_map = {
        "apple_music_global.csv": GLOBAL_FIELDS,
        "apple_music_country_charts.csv": COUNTRY_FIELDS,
        "apple_music_country_albums.csv": COUNTRY_ALBUM_FIELDS,
        "apple_music_genre_charts.csv": GENRE_FIELDS,
        "apple_music_genre_album_charts.csv": GENRE_ALBUM_FIELDS,
        "apple_music_music_video_charts.csv": MUSIC_VIDEO_FIELDS,
        "apple_music_ts_top_songs_global.csv": TS_FIELDS,
    }
    out_dir = apple_music_charts_dir(day)
    written: list[str] = []
    for filename, rows in rows_by_file.items():
        if not rows:
            continue
        target = out_dir / filename
        written.append(str(target))
        if not apply:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=field_map[filename])
        writer.writeheader()
        writer.writerows(rows)
        target.write_text(buf.getvalue(), encoding="utf-8")
    return written


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def local_day_complete(day: str) -> bool:
    out_dir = apple_music_charts_dir(day)
    return out_dir.exists() and any(out_dir.glob("apple_music_*.csv"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", help="YYYY-MM-DD, default: day after latest local apple_music_charts snapshot")
    p.add_argument("--end", help="YYYY-MM-DD, default: today")
    p.add_argument("--force", action="store_true", help="Overwrite days that already have local CSVs")
    p.add_argument("--apply", action="store_true", help="Write files (default: dry-run, list only)")
    return p.parse_args()


def find_latest_local_day() -> date | None:
    root = ROOT / "snapshots" / "apple_music_charts"
    if not root.exists():
        return None
    days = []
    for p in root.glob("20??/??/????-??-??"):
        if p.is_dir() and any(p.glob("apple_music_*.csv")):
            try:
                days.append(datetime.strptime(p.name, "%Y-%m-%d").date())
            except ValueError:
                continue
    return max(days) if days else None


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()

    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        latest = find_latest_local_day()
        start = (latest + timedelta(days=1)) if latest else date.today() - timedelta(days=7)

    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()

    if start > end:
        print(f"[skip] start {start} is after end {end}, nothing to do")
        return

    client = get_r2_client()
    bucket = get_bucket_name()
    prefix_root = "apple-music/snapshots/"

    print(f"[range] {start} .. {end}  bucket={bucket}  mode={'apply' if args.apply else 'dry-run'}")

    total_written_days = 0
    for d in daterange(start, end):
        day_str = d.isoformat()
        if not args.force and local_day_complete(day_str):
            print(f"[skip] {day_str} already has local CSVs (use --force to overwrite)")
            continue

        paginator = client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix_root}{day_str}"):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        keys.sort()

        if not keys:
            print(f"[miss] {day_str} : no R2 snapshot found")
            continue

        merged: dict[str, list[dict[str, Any]]] = {
            "apple_music_global.csv": [],
            "apple_music_country_charts.csv": [],
            "apple_music_country_albums.csv": [],
            "apple_music_genre_charts.csv": [],
            "apple_music_genre_album_charts.csv": [],
            "apple_music_music_video_charts.csv": [],
            "apple_music_ts_top_songs_global.csv": [],
        }
        for key in keys:
            body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            payload = json.loads(body)
            scraped_at = payload.get("scraped_at") or payload.get("date") or day_str
            per_key = rows_from_payload(payload, day_str, scraped_at)
            for fname, rows in per_key.items():
                merged[fname].extend(rows)

        written = write_day_csvs(day_str, merged, args.apply)
        counts = ", ".join(f"{k}={len(v)}" for k, v in merged.items() if v)
        print(f"[{'wrote' if args.apply else 'would-write'}] {day_str} : {len(keys)} snapshot(s) -> {counts}")
        if written:
            total_written_days += 1

    print(f"\n[done] {total_written_days} day(s) {'written' if args.apply else 'would be written'}")
    if not args.apply:
        print("(dry-run: pass --apply to write)")


if __name__ == "__main__":
    main()
