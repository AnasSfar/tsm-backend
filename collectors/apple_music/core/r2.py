#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.client import BaseClient


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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def guess_content_type(key: str) -> str:
    guessed, _ = mimetypes.guess_type(key)
    return guessed or "application/octet-stream"


def head_object_safe(client: BaseClient, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception:
        return None


def object_has_same_hash(client: BaseClient, bucket: str, key: str, local_hash: str) -> bool:
    meta = head_object_safe(client, bucket, key)
    if not meta:
        return False
    remote_hash = (meta.get("Metadata") or {}).get("sha256", "")
    return remote_hash == local_hash


def upload_bytes_if_changed(
    client: BaseClient,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str | None = None,
) -> bool:
    local_hash = sha256_bytes(data)

    if object_has_same_hash(client, bucket, key, local_hash):
        return False

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type or guess_content_type(key),
        Metadata={"sha256": local_hash},
    )
    return True


def upload_json_if_changed(
    client: BaseClient,
    bucket: str,
    key: str,
    payload: Any,
) -> bool:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return upload_bytes_if_changed(
        client=client,
        bucket=bucket,
        key=key,
        data=data,
        content_type="application/json; charset=utf-8",
    )


def upload_file_if_changed(
    client: BaseClient,
    bucket: str,
    key: str,
    file_path: str | Path,
) -> bool:
    file_path = Path(file_path)
    data = file_path.read_bytes()
    return upload_bytes_if_changed(
        client=client,
        bucket=bucket,
        key=key,
        data=data,
        content_type=guess_content_type(key),
    )