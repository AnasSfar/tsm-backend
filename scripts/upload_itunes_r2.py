#!/usr/bin/env python3
"""Upload the iTunes Store purchase charts data to R2.

Uploads (bucket taylor-data by default):
  data/itunes.json               latest payload the API reads
  data/itunes_history.json       windowed history (loaded whole by the API)
  itunes/snapshots/<date>.json   per-snapshot payloads (arbitrary historical dates)
  itunes/history-by-date/*.json  pre-split date payloads
  itunes/db/*.csv                most recent daily CSV per chart

Modeled on scripts/upload_deezer_r2.py. No git commit anywhere in this pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import boto3
from botocore.client import BaseClient
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import (  # noqa: E402
    LEGACY_WEBSITE_DATA_DIR,
    WEB_EXPORT_DATA_DIR,
    itunes_daily_csv_paths,
)
import r2_keys  # noqa: E402

SITE_DATA_DIR = WEB_EXPORT_DATA_DIR if WEB_EXPORT_DATA_DIR.exists() else LEGACY_WEBSITE_DATA_DIR

ITUNES_JSON = SITE_DATA_DIR / "itunes.json"
ITUNES_HISTORY_JSON = SITE_DATA_DIR / "itunes_history.json"
ITUNES_HISTORY_DATES_DIR = SITE_DATA_DIR / "itunes_history_dates"

CSV_R2_PREFIX = r2_keys.ITUNES_DB_PREFIX
SNAPSHOT_R2_PREFIX = r2_keys.ITUNES_SNAPSHOTS_PREFIX
HISTORY_BY_DATE_R2_PREFIX = r2_keys.ITUNES_HISTORY_BY_DATE_PREFIX
NO_CACHE_CONTROL = "no-cache, no-store, must-revalidate"

ITUNES_CSV_NAMES = ["itunes_top_songs.csv", "itunes_top_albums.csv"]


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def get_r2_client() -> BaseClient:
    account_id = get_env("R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=get_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=get_env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def get_bucket_name() -> str:
    return os.getenv("R2_BUCKET", "taylor-data").strip() or "taylor-data"


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
    return (meta.get("Metadata") or {}).get("sha256", "") == local_hash


def _put(client: BaseClient, bucket: str, key: str, body: bytes, content_type: str) -> None:
    local_hash = hashlib.sha256(body).hexdigest()
    for attempt in range(1, 4):
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                CacheControl=NO_CACHE_CONTROL,
                Metadata={"sha256": local_hash},
            )
            return
        except Exception:
            if attempt == 3:
                raise
            time.sleep(min(2 ** attempt, 5))


def upload_json_if_changed(client: BaseClient, bucket: str, key: str, payload: Any, *, dry_run: bool) -> bool:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if object_has_same_body_hash(client, bucket, key, body):
        return False
    if dry_run:
        print(f"[dry-run][upload] {key}")
        return True
    _put(client, bucket, key, body, "application/json; charset=utf-8")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload iTunes Store purchase charts data to R2.")
    parser.add_argument("--bucket", default=get_bucket_name())
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def upload_main_json_files(client: BaseClient, bucket: str, dry_run: bool) -> int:
    pairs = [
        (ITUNES_JSON, "data/itunes.json"),
        (ITUNES_HISTORY_JSON, "data/itunes_history.json"),
    ]

    def _upload(item: tuple) -> tuple:
        local_path, r2_key = item
        if not local_path.exists():
            return r2_key, None
        payload = json.loads(local_path.read_text(encoding="utf-8-sig"))
        return r2_key, upload_json_if_changed(client, bucket, r2_key, payload, dry_run=dry_run)

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
    uploaded = 0
    for name in ITUNES_CSV_NAMES:
        candidates = sorted(itunes_daily_csv_paths(name), reverse=True)
        if not candidates:
            print(f"[skip] {name} not found locally")
            continue
        path = candidates[0]
        key = f"{CSV_R2_PREFIX}/{name}"
        body = path.read_bytes()
        if object_has_same_body_hash(client, bucket, key, body):
            print(f"[unchanged] {key}")
            continue
        if dry_run:
            print(f"[dry-run][upload] {key}")
            uploaded += 1
            continue
        _put(client, bucket, key, body, "text/csv; charset=utf-8")
        print(f"[uploaded] {key}")
        uploaded += 1
    return uploaded


def _snapshot_payload(history: dict[str, Any], snapshot_key: str) -> dict[str, Any]:
    return {
        "date": snapshot_key,
        "scraped_at": snapshot_key,
        "country_charts": (history.get("country") or {}).get(snapshot_key, {}),
        "country_album_charts": (history.get("country_albums") or {}).get(snapshot_key, {}),
    }


def upload_snapshot_jsons(client: BaseClient, bucket: str, dry_run: bool) -> int:
    if not ITUNES_HISTORY_JSON.exists():
        print(f"[skip] {SNAPSHOT_R2_PREFIX} history source not found locally")
        return 0
    history = json.loads(ITUNES_HISTORY_JSON.read_text(encoding="utf-8-sig"))
    dates = [d for d in history.get("dates", []) if isinstance(d, str) and d]
    if not dates:
        print(f"[skip] {SNAPSHOT_R2_PREFIX} no dates found")
        return 0

    uploaded = unchanged = 0

    def _upload(snapshot_key: str) -> tuple[str, bool]:
        payload = _snapshot_payload(history, snapshot_key)
        r2_key = f"{SNAPSHOT_R2_PREFIX}/{snapshot_key}.json"
        return r2_key, upload_json_if_changed(client, bucket, r2_key, payload, dry_run=dry_run)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for r2_key, changed in pool.map(_upload, dates):
            if changed:
                print(f"[uploaded] {r2_key}")
                uploaded += 1
            else:
                unchanged += 1
    print(f"[done] {SNAPSHOT_R2_PREFIX}: uploaded={uploaded} unchanged={unchanged}")
    return uploaded


def upload_history_by_date_jsons(client: BaseClient, bucket: str, dry_run: bool) -> int:
    if not ITUNES_HISTORY_DATES_DIR.exists():
        print(f"[skip] {HISTORY_BY_DATE_R2_PREFIX} split history dir not found locally")
        return 0
    files = sorted(ITUNES_HISTORY_DATES_DIR.glob("*.json"))
    if not files:
        print(f"[skip] {HISTORY_BY_DATE_R2_PREFIX} no split files found")
        return 0

    uploaded = unchanged = 0

    def _upload(path: Path) -> tuple[str, bool]:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        r2_key = f"{HISTORY_BY_DATE_R2_PREFIX}/{path.name}"
        return r2_key, upload_json_if_changed(client, bucket, r2_key, payload, dry_run=dry_run)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for r2_key, changed in pool.map(_upload, files):
            if changed:
                print(f"[uploaded] {r2_key}")
                uploaded += 1
            else:
                unchanged += 1
    print(f"[done] {HISTORY_BY_DATE_R2_PREFIX}: uploaded={uploaded} unchanged={unchanged}")
    return uploaded


def main() -> None:
    load_dotenv()
    args = parse_args()
    client = get_r2_client()
    bucket = args.bucket

    print("\n=== Uploading main iTunes JSON files ===")
    upload_main_json_files(client, bucket, args.dry_run)

    print("\n=== Uploading iTunes snapshot JSON files ===")
    upload_snapshot_jsons(client, bucket, args.dry_run)

    print("\n=== Uploading iTunes history-by-date JSON files ===")
    upload_history_by_date_jsons(client, bucket, args.dry_run)

    print("\n=== Uploading iTunes CSV history ===")
    upload_daily_csvs(client, bucket, args.dry_run)

    print("\n[done]")
    print(f"  bucket: {bucket}")
    if args.dry_run:
        print("  mode: dry-run")


if __name__ == "__main__":
    main()
