#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import boto3
from botocore.client import BaseClient
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import LEGACY_WEBSITE_DATA_DIR, WEB_EXPORT_DATA_DIR, deezer_daily_csv_paths  # noqa: E402
import r2_keys  # noqa: E402

DB_DIR = ROOT / "db"
DATA_ROOT = ROOT / "data"
ARCHIVE_DB_DIR = DATA_ROOT / "_archive" / "original" / "db"
SITE_DATA_DIR = WEB_EXPORT_DATA_DIR if WEB_EXPORT_DATA_DIR.exists() else LEGACY_WEBSITE_DATA_DIR

DEEZER_JSON = SITE_DATA_DIR / "deezer.json"
DEEZER_HISTORY_JSON = SITE_DATA_DIR / "deezer_history.json"

GLOBAL_CSV = DB_DIR / "deezer_global_chart.csv"
ARTIST_TOP_CSV = DB_DIR / "deezer_artist_top_tracks.csv"
ARTIST_STATS_CSV = DB_DIR / "deezer_artist_stats.csv"

R2_PREFIX = r2_keys.DEEZER_HISTORY_BY_SONG_PREFIX
CSV_R2_PREFIX = r2_keys.DEEZER_DB_PREFIX
SNAPSHOT_R2_PREFIX = r2_keys.DEEZER_SNAPSHOTS_PREFIX
NO_CACHE_CONTROL = "no-cache, no-store, must-revalidate"

DEEZER_CSV_NAMES = [
    "deezer_global_chart.csv",
    "deezer_artist_top_tracks.csv",
    "deezer_artist_stats.csv",
]


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def get_r2_client() -> BaseClient:
    account_id = get_env("R2_ACCOUNT_ID")
    access_key_id = get_env("R2_ACCESS_KEY_ID")
    secret_access_key = get_env("R2_SECRET_ACCESS_KEY")

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )


def get_bucket_name() -> str:
    return os.getenv("R2_BUCKET", "taylor-data").strip() or "taylor-data"


def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = value.replace("&", "and")
    value = re.sub(r"\s+", " ", value)
    return value


def slugify(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown_track"


def track_key(title: str) -> str:
    return slugify(title)


def csv_candidates(path: Path) -> list[Path]:
    candidates = []
    if path.exists():
        candidates.append(path)

    archived = ARCHIVE_DB_DIR / path.name
    if archived.exists():
        candidates.append(archived)

    candidates.extend(deezer_daily_csv_paths(path.name))

    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique


def read_csv(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for candidate in csv_candidates(path):
        if not candidate.exists() or candidate.stat().st_size == 0:
            continue
        with candidate.open("r", newline="", encoding="utf-8-sig") as f:
            rows.extend(csv.DictReader(f))
    return rows


def head_object_safe(client: BaseClient, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception:
        return None


def object_has_same_body_hash(client: BaseClient, bucket: str, key: str, body: bytes) -> bool:
    local_hash = hashlib.sha256(body).hexdigest()
    meta = head_object_safe(client, bucket, key)
    if not meta:
        return False
    remote_hash = (meta.get("Metadata") or {}).get("sha256", "")
    return remote_hash == local_hash


def upload_json_if_changed(
    client: BaseClient,
    bucket: str,
    key: str,
    payload: Any,
    *,
    dry_run: bool,
    retries: int = 3,
) -> bool:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    local_hash = hashlib.sha256(body).hexdigest()

    if object_has_same_body_hash(client, bucket, key, body):
        return False

    if dry_run:
        print(f"[dry-run][upload] {key}")
        return True

    for attempt in range(1, retries + 1):
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="application/json; charset=utf-8",
                CacheControl=NO_CACHE_CONTROL,
                Metadata={"sha256": local_hash},
            )
            break
        except Exception:
            if attempt == retries:
                raise
            time.sleep(min(2 ** attempt, 5))

    return True


def clean_row(row: dict[str, str], keep_fields: list[str]) -> dict[str, str]:
    return {field: row.get(field, "") for field in keep_fields if field in row}


def append_rows(
    grouped: dict[str, dict[str, Any]],
    rows: list[dict[str, str]],
    source_name: str,
    keep_fields: list[str],
) -> None:
    for row in rows:
        name = (row.get("title") or "").strip()
        if not name:
            continue

        key = normalize_text(name)
        slug = track_key(name)
        bucket = grouped.setdefault(
            key,
            {
                "track_key": slug,
                "title": name,
                "normalized_title": key,
                "sources": {
                    "global": [],
                    "ts_top_tracks": [],
                },
            },
        )

        if not bucket.get("title"):
            bucket["title"] = name

        bucket["sources"].setdefault(source_name, []).append(clean_row(row, keep_fields))


def sort_points(points: list[dict[str, str]]) -> list[dict[str, str]]:
    def key(row: dict[str, str]) -> tuple:
        return (row.get("date", ""), row.get("rank", ""))
    return sorted(points, key=key)


def finalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for source_name, points in payload["sources"].items():
        payload["sources"][source_name] = sort_points(points)
    return payload


def build_history_objects() -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    append_rows(
        grouped=grouped,
        rows=read_csv(GLOBAL_CSV),
        source_name="global",
        keep_fields=["date", "scraped_at", "title", "rank", "previous_rank", "cover_url", "link", "deezer_track_id"],
    )

    append_rows(
        grouped=grouped,
        rows=read_csv(ARTIST_TOP_CSV),
        source_name="ts_top_tracks",
        keep_fields=["date", "scraped_at", "title", "rank", "previous_rank", "cover_url", "link", "deezer_track_id", "deezer_popularity"],
    )

    for normalized_title in list(grouped.keys()):
        grouped[normalized_title] = finalize_payload(grouped[normalized_title])

    return grouped


def object_key(payload: dict[str, Any], prefix: str) -> str:
    normalized = payload.get("normalized_title") or normalize_text(payload.get("title", ""))
    suffix = hashlib.sha1(str(normalized).encode("utf-8")).hexdigest()[:10]
    slug = payload.get("track_key") or track_key(payload.get("title", ""))
    return f"{prefix}/{slug}--{suffix}.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload Deezer history-by-song data to R2.")
    parser.add_argument("--bucket", default=get_bucket_name())
    parser.add_argument("--prefix", default=R2_PREFIX)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def upload_main_json_files(client: BaseClient, bucket: str, dry_run: bool) -> int:
    pairs = [
        (DEEZER_JSON, "data/deezer.json"),
        (DEEZER_HISTORY_JSON, "data/deezer_history.json"),
    ]

    def _upload(item: tuple) -> tuple:
        local_path, r2_key = item
        if not local_path.exists():
            return r2_key, None
        payload = json.loads(local_path.read_text(encoding="utf-8-sig"))
        changed = upload_json_if_changed(client, bucket, r2_key, payload, dry_run=dry_run)
        return r2_key, changed

    uploaded = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        for r2_key, changed in pool.map(_upload, pairs):
            if changed is None:
                print(f"[skip] {r2_key} not found locally")
            elif changed:
                print(f"[uploaded] {r2_key}")
                uploaded += 1
            else:
                print(f"[unchanged] {r2_key}")
    return uploaded


def upload_daily_csvs(client: BaseClient, bucket: str, dry_run: bool) -> int:
    """Upload the most recent daily CSV for each Deezer source to R2."""
    uploaded = 0
    for name in DEEZER_CSV_NAMES:
        candidates = sorted(deezer_daily_csv_paths(name), reverse=True)
        if not candidates:
            print(f"[skip] {name} not found locally")
            continue
        path = candidates[0]
        key = f"{CSV_R2_PREFIX}/{name}"
        body = path.read_bytes()
        local_hash = hashlib.sha256(body).hexdigest()
        if object_has_same_body_hash(client, bucket, key, body):
            print(f"[unchanged] {key}")
            continue
        if dry_run:
            print(f"[dry-run][upload] {key}")
            uploaded += 1
            continue
        for attempt in range(1, 4):
            try:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body,
                    ContentType="text/csv; charset=utf-8",
                    CacheControl=NO_CACHE_CONTROL,
                    Metadata={"sha256": local_hash},
                )
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(min(2 ** attempt, 5))
        print(f"[uploaded] {key}")
        uploaded += 1
    return uploaded


def _snapshot_payload(history: dict[str, Any], snapshot_key: str) -> dict[str, Any]:
    return {
        "date": snapshot_key,
        "scraped_at": snapshot_key,
        "global_chart": (history.get("global") or {}).get(snapshot_key, []),
        "ts_top_tracks": (history.get("ts_top_tracks") or {}).get(snapshot_key, []),
    }


def upload_snapshot_jsons(client: BaseClient, bucket: str, dry_run: bool) -> int:
    if not DEEZER_HISTORY_JSON.exists():
        print(f"[skip] {SNAPSHOT_R2_PREFIX} history source not found locally")
        return 0

    history = json.loads(DEEZER_HISTORY_JSON.read_text(encoding="utf-8-sig"))
    dates = [date for date in history.get("dates", []) if isinstance(date, str) and date]
    if not dates:
        print(f"[skip] {SNAPSHOT_R2_PREFIX} no dates found")
        return 0

    uploaded = 0
    unchanged = 0

    def _upload(snapshot_key: str) -> tuple[str, bool]:
        payload = _snapshot_payload(history, snapshot_key)
        r2_key = f"{SNAPSHOT_R2_PREFIX}/{snapshot_key}.json"
        changed = upload_json_if_changed(client, bucket, r2_key, payload, dry_run=dry_run)
        return r2_key, changed

    with ThreadPoolExecutor(max_workers=4) as pool:
        for r2_key, changed in pool.map(_upload, dates):
            if changed:
                print(f"[uploaded] {r2_key}")
                uploaded += 1
            else:
                unchanged += 1

    print(f"[done] {SNAPSHOT_R2_PREFIX}: uploaded={uploaded} unchanged={unchanged}")
    return uploaded


def main() -> None:
    load_dotenv()
    args = parse_args()

    client = get_r2_client()
    bucket = args.bucket

    print("\n=== Uploading main Deezer JSON files ===")
    upload_main_json_files(client, bucket, args.dry_run)

    print("\n=== Uploading Deezer snapshot JSON files ===")
    upload_snapshot_jsons(client, bucket, args.dry_run)

    print("\n=== Uploading Deezer CSV history ===")
    upload_daily_csvs(client, bucket, args.dry_run)

    print("\n=== Uploading per-track history objects ===")
    objects = build_history_objects()

    if not objects:
        print("[error] no Deezer history data found")
        sys.exit(1)

    tasks = [
        (client, bucket, payload, args.dry_run, args.prefix)
        for _, payload in sorted(objects.items(), key=lambda item: item[1].get("title", ""))
    ]

    def _upload_one(args_tuple: tuple) -> tuple:
        c, b, payload, dry_run, prefix = args_tuple
        r2_key = object_key(payload, prefix)
        changed = upload_json_if_changed(c, b, r2_key, payload, dry_run=dry_run)
        return r2_key, changed

    uploaded = 0
    unchanged = 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        for r2_key, changed in pool.map(_upload_one, tasks):
            if changed:
                print(f"[uploaded] {r2_key}")
                uploaded += 1
            else:
                print(f"[unchanged] {r2_key}")
                unchanged += 1

    print()
    print("[done]")
    print(f"  bucket: {bucket}")
    print(f"  prefix: {args.prefix}")
    print(f"  tracks: {len(objects)}")
    print(f"  uploaded: {uploaded}")
    print(f"  unchanged: {unchanged}")
    if args.dry_run:
        print("  mode: dry-run")


if __name__ == "__main__":
    main()
