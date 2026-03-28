#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import boto3
from botocore.client import BaseClient
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "db"

COUNTRY_CSV = DB_DIR / "apple_music_country_charts.csv"
GENRE_CSV = DB_DIR / "apple_music_genre_charts.csv"
GLOBAL_CSV = DB_DIR / "apple_music_global.csv"
TS_TOP_CSV = DB_DIR / "apple_music_ts_top_songs.csv"

R2_PREFIX = "apple-music/history-by-song"


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


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def upload_json_if_changed(client: BaseClient, bucket: str, key: str, payload: Any) -> bool:
    import hashlib

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    local_hash = hashlib.sha256(body).hexdigest()

    if object_has_same_body_hash(client, bucket, key, body):
        return False

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
        Metadata={"sha256": local_hash},
    )
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
        name = (row.get("song_name") or "").strip()
        if not name:
            continue

        key = song_key(name)
        bucket = grouped.setdefault(
            key,
            {
                "song_key": key,
                "song_name": name,
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
        keep_fields=["date", "country", "song_name", "rank", "previous_rank", "image_url", "url", "apple_music_id"],
    )

    append_rows(
        grouped=grouped,
        rows=read_csv(GENRE_CSV),
        source_name="genre_charts",
        keep_fields=["date", "country", "genre_id", "genre_name", "song_name", "rank", "previous_rank", "image_url", "url", "apple_music_id"],
    )

    append_rows(
        grouped=grouped,
        rows=read_csv(GLOBAL_CSV),
        source_name="global",
        keep_fields=["date", "song_name", "rank", "previous_rank", "image_url", "url", "apple_music_id"],
    )

    append_rows(
        grouped=grouped,
        rows=read_csv(TS_TOP_CSV),
        source_name="ts_top_songs",
        keep_fields=["date", "storefront", "song_name", "rank", "previous_rank", "image_url", "url", "apple_music_id", "album_name"],
    )

    for key in list(grouped.keys()):
        grouped[key] = finalize_payload(grouped[key])

    return grouped


def main() -> None:
    load_dotenv()

    client = get_r2_client()
    bucket = get_bucket_name()

    objects = build_history_objects()

    if not objects:
        print("[error] no Apple Music history data found")
        sys.exit(1)

    uploaded = 0
    unchanged = 0

    for key, payload in sorted(objects.items()):
        r2_key = f"{R2_PREFIX}/{key}.json"
        changed = upload_json_if_changed(
            client=client,
            bucket=bucket,
            key=r2_key,
            payload=payload,
        )
        if changed:
            print(f"[uploaded] {r2_key}")
            uploaded += 1
        else:
            print(f"[unchanged] {r2_key}")
            unchanged += 1

    print()
    print("[done]")
    print(f"  bucket: {bucket}")
    print(f"  prefix: {R2_PREFIX}")
    print(f"  songs: {len(objects)}")
    print(f"  uploaded: {uploaded}")
    print(f"  unchanged: {unchanged}")


if __name__ == "__main__":
    main()