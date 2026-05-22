#!/usr/bin/env python3
"""Copy app-owned objects from the public R2 bucket to the app R2 bucket."""
from __future__ import annotations

import argparse
import os
from collections.abc import Iterable
from typing import Any

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

APP_KEYS = (
    "about.json",
    "nav_button.json",
    "news.json",
    "quiz-leaderboard.json",
    "quiz-leaderboard-snapshot.json",
    "track13-rankings.json",
    "track13-leaderboard-snapshot.json",
    "album-rankings.json",
    "album-ranking-leaderboard-snapshot.json",
    "album-ranking-next-poll.json",
    "track1-rankings.json",
    "track1-ranking-leaderboard-snapshot.json",
    "track1-ranking-next-poll.json",
)
APP_PREFIXES = ("hiring/", "report-", "report-img-")
COPY_FIELDS = (
    "CacheControl",
    "ContentDisposition",
    "ContentEncoding",
    "ContentLanguage",
    "ContentType",
    "Expires",
    "Metadata",
)


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def get_client(*, account_id: str, access_key_id: str, secret_access_key: str):
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )


def is_missing(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}


def object_exists(client, bucket: str, key: str, *, forbidden_is_missing: bool = False) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if forbidden_is_missing and exc.response.get("Error", {}).get("Code") == "403":
            return False
        if is_missing(exc):
            return False
        raise


def iter_prefix_keys(client, bucket: str, prefixes: Iterable[str]) -> Iterable[str]:
    paginator = client.get_paginator("list_objects_v2")
    for prefix in prefixes:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item.get("Key", "")
                if key:
                    yield key


def collect_keys(client, bucket: str, keys: Iterable[str], prefixes: Iterable[str]) -> list[str]:
    found = set(iter_prefix_keys(client, bucket, prefixes))
    for key in keys:
        if object_exists(client, bucket, key):
            found.add(key)
    return sorted(found)


def copy_object(source, source_bucket: str, dest, dest_bucket: str, key: str) -> None:
    obj = source.get_object(Bucket=source_bucket, Key=key)
    put_args: dict[str, Any] = {
        "Bucket": dest_bucket,
        "Key": key,
        "Body": obj["Body"].read(),
    }
    for field in COPY_FIELDS:
        value = obj.get(field)
        if value:
            put_args[field] = value
    dest.put_object(**put_args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy app-owned R2 objects from R2_BUCKET to R2_APP_BUCKET."
    )
    parser.add_argument("--dry-run", action="store_true", help="List copies without writing them.")
    parser.add_argument("--overwrite", action="store_true", help="Replace objects already in the app bucket.")
    parser.add_argument("--key", action="append", default=[], help="Additional exact key to migrate.")
    parser.add_argument("--prefix", action="append", default=[], help="Additional prefix to migrate.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    public_account_id = get_env("R2_ACCOUNT_ID")
    app_account_id = os.getenv("R2_APP_ACCOUNT_ID", "").strip() or public_account_id
    public_bucket = get_env("R2_BUCKET")
    app_bucket = get_env("R2_APP_BUCKET")

    source = get_client(
        account_id=public_account_id,
        access_key_id=get_env("R2_ACCESS_KEY_ID"),
        secret_access_key=get_env("R2_SECRET_ACCESS_KEY"),
    )
    dest = get_client(
        account_id=app_account_id,
        access_key_id=get_env("R2_APP_ACCESS_KEY_ID"),
        secret_access_key=get_env("R2_APP_SECRET_ACCESS_KEY"),
    )

    keys = collect_keys(
        source,
        public_bucket,
        (*APP_KEYS, *args.key),
        (*APP_PREFIXES, *args.prefix),
    )
    print(f"Found {len(keys)} app object(s) in {public_bucket}.")

    copied = 0
    skipped = 0
    for key in keys:
        if not args.overwrite and object_exists(dest, app_bucket, key, forbidden_is_missing=True):
            print(f"[skip] {key} already exists in {app_bucket}")
            skipped += 1
            continue
        if args.dry_run:
            print(f"[dry-run] {key} -> {app_bucket}/{key}")
            copied += 1
            continue
        copy_object(source, public_bucket, dest, app_bucket, key)
        print(f"[copy] {key} -> {app_bucket}/{key}")
        copied += 1

    suffix = " dry-run" if args.dry_run else ""
    print(f"Done{suffix}: {copied} copy target(s), {skipped} skipped.")
    print("Source objects were not deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
