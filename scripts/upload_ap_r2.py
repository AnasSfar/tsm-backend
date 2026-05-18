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
DB_DIR = ROOT / "db"
DATA_ROOT = ROOT / "data"
ARCHIVE_DB_DIR = DATA_ROOT / "_archive" / "original" / "db"
SITE_DATA_DIR = ROOT / "website" / "site" / "data"

APPLEMUSIC_JSON = SITE_DATA_DIR / "applemusic.json"
APPLEMUSIC_HISTORY_JSON = SITE_DATA_DIR / "applemusic_history.json"

COUNTRY_CSV = DB_DIR / "apple_music_country_charts.csv"
GENRE_CSV = DB_DIR / "apple_music_genre_charts.csv"
GLOBAL_CSV = DB_DIR / "apple_music_global.csv"
TS_TOP_CSV = DB_DIR / "apple_music_ts_top_songs.csv"

R2_PREFIX = "apple-music/history-by-song"
NO_CACHE_CONTROL = "no-cache, no-store, must-revalidate"


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
    return value or "unknown_song"


def song_key(song_name: str) -> str:
    return slugify(song_name)


def csv_candidates(path: Path) -> list[Path]:
    candidates = []
    if path.exists():
        candidates.append(path)

    archived = ARCHIVE_DB_DIR / path.name
    if archived.exists():
        candidates.append(archived)

    candidates.extend(sorted(DATA_ROOT.glob(f"????/??/????-??-??/apple_music/{path.name}")))

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
    import hashlib

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


def normalize_song_identity(song_name: str) -> str:
    return normalize_text(song_name)


def append_rows(
    grouped: dict[str, dict[str, Any]],
    rows: list[dict[str, str]],
    source_name: str,
    keep_fields: list[str],
) -> None:
    for row in rows:
        name = (row.get("song_name") or "").strip()
        if not name:
            continue

        key = normalize_song_identity(name)
        slug = song_key(name)
        bucket = grouped.setdefault(
            key,
            {
                "song_key": slug,
                "song_name": name,
                "normalized_song_name": key,
                "sources": {
                    "country_charts": [],
                    "genre_charts": [],
                    "global": [],
                    "ts_top_songs": [],
                },
            },
        )

        # garder le premier nom rencontré comme canonique, mais si vide on remplit
        if not bucket.get("song_name"):
            bucket["song_name"] = name

        bucket["sources"][source_name].append(clean_row(row, keep_fields))


def sort_points(points: list[dict[str, str]]) -> list[dict[str, str]]:
    def key(row: dict[str, str]) -> tuple:
        return (
            row.get("date", ""),
            row.get("country", ""),
            row.get("genre_id", ""),
            row.get("rank", ""),
        )
    return sorted(points, key=key)


def finalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for source_name, points in payload["sources"].items():
        payload["sources"][source_name] = sort_points(points)
    return payload


def build_history_objects() -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    append_rows(
        grouped=grouped,
        rows=read_csv(COUNTRY_CSV),
        source_name="country_charts",
        keep_fields=["date", "scraped_at", "country", "song_name", "rank", "previous_rank", "image_url", "url", "apple_music_id"],
    )

    append_rows(
        grouped=grouped,
        rows=read_csv(GENRE_CSV),
        source_name="genre_charts",
        keep_fields=["date", "scraped_at", "country", "genre_id", "genre_name", "song_name", "rank", "previous_rank", "image_url", "url", "apple_music_id"],
    )

    append_rows(
        grouped=grouped,
        rows=read_csv(GLOBAL_CSV),
        source_name="global",
        keep_fields=["date", "scraped_at", "song_name", "rank", "previous_rank", "image_url", "url", "apple_music_id"],
    )

    append_rows(
        grouped=grouped,
        rows=read_csv(TS_TOP_CSV),
        source_name="ts_top_songs",
        keep_fields=["date", "scraped_at", "storefront", "song_name", "rank", "previous_rank", "image_url", "url", "apple_music_id", "album_name"],
    )

    for normalized_name in list(grouped.keys()):
        grouped[normalized_name] = finalize_payload(grouped[normalized_name])

    return grouped


def object_key(payload: dict[str, Any], prefix: str) -> str:
    normalized = payload.get("normalized_song_name") or normalize_song_identity(payload.get("song_name", ""))
    suffix = hashlib.sha1(str(normalized).encode("utf-8")).hexdigest()[:10]
    slug = payload.get("song_key") or song_key(payload.get("song_name", ""))
    return f"{prefix}/{slug}--{suffix}.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload Apple Music history-by-song data to R2.")
    parser.add_argument("--bucket", default=get_bucket_name())
    parser.add_argument("--prefix", default=R2_PREFIX)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def upload_main_json_files(client: BaseClient, bucket: str, dry_run: bool) -> int:
    """Upload applemusic.json and applemusic_history.json to R2 data/ prefix (parallel)."""
    pairs = [
        (APPLEMUSIC_JSON, "data/applemusic.json"),
        (APPLEMUSIC_HISTORY_JSON, "data/applemusic_history.json"),
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


def main() -> None:
    load_dotenv()
    args = parse_args()

    client = get_r2_client()
    bucket = args.bucket

    # Upload main JSON files first (what the API reads)
    print("\n=== Uploading main Apple Music JSON files ===")
    upload_main_json_files(client, bucket, args.dry_run)

    # Upload per-song history objects
    print("\n=== Uploading per-song history objects ===")
    objects = build_history_objects()

    if not objects:
        print("[error] no Apple Music history data found")
        sys.exit(1)

    tasks = [
        (client, bucket, payload, args.dry_run, args.prefix)
        for _, payload in sorted(objects.items(), key=lambda item: item[1].get("song_name", ""))
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
    print(f"  songs: {len(objects)}")
    print(f"  uploaded: {uploaded}")
    print(f"  unchanged: {unchanged}")
    if args.dry_run:
        print("  mode: dry-run")


if __name__ == "__main__":
    main()
