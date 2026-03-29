#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import boto3
from dotenv import load_dotenv

load_dotenv(str(Path(__file__).resolve().parents[1] / ".env"), override=True)

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "website" / "site" / "history"
SITE_DATA_DIR = ROOT / "website" / "site" / "data"
APPLE_MUSIC_IMAGES_DIR = SITE_DATA_DIR / "apple-music-images"
DB_DIR = ROOT / "db"

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
R2_REQUIRED_ENV_VARS = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def get_missing_env_vars(names: tuple[str, ...]) -> list[str]:
    return [name for name in names if not os.getenv(name, "").strip()]


def get_s3_client():
    account_id = get_env("R2_ACCOUNT_ID")
    access_key_id = get_env("R2_ACCESS_KEY_ID")
    secret_access_key = get_env("R2_SECRET_ACCESS_KEY")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def head_object_safe(client, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception:
        return None


def object_has_same_hash(client, bucket: str, key: str, data: bytes) -> bool:
    local_hash = hashlib.sha256(data).hexdigest()
    meta = head_object_safe(client, bucket, key)
    if not meta:
        return False
    remote_hash = (meta.get("Metadata") or {}).get("sha256", "")
    return local_hash == remote_hash


def upload_bytes_if_changed(
    *,
    client,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str,
    dry_run: bool,
    retries: int = 3,
) -> bool:
    if object_has_same_hash(client, bucket, key, data):
        return False

    if dry_run:
        print(f"[DRY-RUN][UPLOAD] {key}")
        return True

    body_hash = hashlib.sha256(data).hexdigest()
    for attempt in range(1, retries + 1):
        try:
            client.upload_fileobj(
                io.BytesIO(data),
                bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {"sha256": body_hash},
                },
            )
            return True
        except Exception:
            if attempt == retries:
                raise
            time.sleep(min(2 ** attempt, 5))

    return True


def upload_json_if_changed(*, client, bucket: str, key: str, payload: dict[str, Any], dry_run: bool) -> bool:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return upload_bytes_if_changed(
        client=client,
        bucket=bucket,
        key=key,
        data=data,
        content_type="application/json; charset=utf-8",
        dry_run=dry_run,
    )


def upload_raw_if_changed(*, client, bucket: str, key: str, data: bytes, content_type: str, dry_run: bool) -> bool:
    return upload_bytes_if_changed(
        client=client,
        bucket=bucket,
        key=key,
        data=data,
        content_type=content_type,
        dry_run=dry_run,
    )


def upload_db_files(
    *,
    client,
    bucket: str,
    db_prefix: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Upload all files under db/ to R2 while preserving relative paths."""
    uploaded = 0
    unchanged = 0

    db_files = sorted(p for p in DB_DIR.rglob("*") if p.is_file())
    for path in db_files:
        rel_path = path.relative_to(DB_DIR).as_posix()
        full_key = f"{db_prefix}/{rel_path}"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        changed = upload_raw_if_changed(
            client=client,
            bucket=bucket,
            key=full_key,
            data=path.read_bytes(),
            content_type=content_type,
            dry_run=dry_run,
        )
        if changed:
            uploaded += 1
            print(f"[UPLOADED] {full_key}")
        else:
            unchanged += 1
            print(f"[UNCHANGED] {full_key}")

    return uploaded, unchanged


def upload_apple_music_images(
    *,
    client,
    bucket: str,
    images_prefix: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Upload Apple Music track images if they exist locally."""
    uploaded = 0
    unchanged = 0

    if not APPLE_MUSIC_IMAGES_DIR.exists():
        return uploaded, unchanged

    image_files = sorted(p for p in APPLE_MUSIC_IMAGES_DIR.glob("*") if p.is_file())
    if not image_files:
        return uploaded, unchanged

    print(f"[INFO] Found {len(image_files)} Apple Music images to upload")

    for path in image_files:
        rel_path = path.name
        full_key = f"{images_prefix}/{rel_path}"
        content_type = "image/jpeg"
        changed = upload_raw_if_changed(
            client=client,
            bucket=bucket,
            key=full_key,
            data=path.read_bytes(),
            content_type=content_type,
            dry_run=dry_run,
        )
        if changed:
            uploaded += 1
            print(f"[UPLOADED] {full_key}")
        else:
            unchanged += 1
            print(f"[UNCHANGED] {full_key}")

    return uploaded, unchanged


def upload_static_data(
    *,
    client,
    bucket: str,
    data_prefix: str,
    history_prefix: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Upload generated JSON/CSV/static history files used by the frontend."""
    uploaded = 0
    unchanged = 0

    json_mappings = [
        ("songs.json",               "data/songs.json"),
        ("albums.json",              "data/albums.json"),
        ("artist.json",              "data/artist.json"),
        ("expected_milestones.json", "data/milestones.json"),
        ("billboard.json",           "data/billboard.json"),
        ("applemusic.json",          "data/applemusic.json"),
        ("applemusic_history.json",  "data/applemusic_history.json"),
        ("songs-appearances.json",   "data/songs-appearances.json"),
        ("charts_worldwide.json",    "data/charts_worldwide.json"),
    ]
    for filename, r2_key in json_mappings:
        src = SITE_DATA_DIR / filename
        if not src.exists():
            print(f"[SKIP] absent: {src}")
            continue
        obj = load_json(src)
        full_key = f"{data_prefix}/{r2_key.split('/', 1)[1]}"
        changed = upload_json_if_changed(client=client, bucket=bucket, key=full_key, payload=obj, dry_run=dry_run)
        if changed:
            uploaded += 1
            print(f"[UPLOADED] {full_key}")
        else:
            unchanged += 1
            print(f"[UNCHANGED] {full_key}")

    csv_mappings = [
        ("charts_history_global.csv", "data/charts_global.csv"),
        ("charts_history_fr.csv",     "data/charts_fr.csv"),
    ]
    for filename, r2_key in csv_mappings:
        src = DB_DIR / filename
        if not src.exists():
            print(f"[SKIP] absent: {src}")
            continue
        full_key = f"{data_prefix}/{r2_key.split('/', 1)[1]}"
        changed = upload_raw_if_changed(
            client=client,
            bucket=bucket,
            key=full_key,
            data=src.read_bytes(),
            content_type="text/csv; charset=utf-8",
            dry_run=dry_run,
        )
        if changed:
            uploaded += 1
            print(f"[UPLOADED] {full_key}")
        else:
            unchanged += 1
            print(f"[UNCHANGED] {full_key}")

    index_path = HISTORY_DIR / "index.json"
    if index_path.exists():
        full_key = f"{history_prefix}/index.json"
        changed = upload_json_if_changed(
            client=client,
            bucket=bucket,
            key=full_key,
            payload=load_json(index_path),
            dry_run=dry_run,
        )
        if changed:
            uploaded += 1
            print(f"[UPLOADED] {full_key}")
        else:
            unchanged += 1
            print(f"[UNCHANGED] {full_key}")

    daily_files = sorted(
        p for p in HISTORY_DIR.glob("*.json")
        if p.name != "index.json"
    )
    for path in daily_files:
        m = DATE_RE.search(path.stem)
        if not m:
            continue
        full_key = f"{history_prefix}/{m.group(1)}.json"
        changed = upload_json_if_changed(
            client=client,
            bucket=bucket,
            key=full_key,
            payload=load_json(path),
            dry_run=dry_run,
        )
        if changed:
            uploaded += 1
            print(f"[UPLOADED] {full_key}")
        else:
            unchanged += 1
            print(f"[UNCHANGED] {full_key}")

    return uploaded, unchanged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload Spotify history, static data, and db files to R2.")
    parser.add_argument("--bucket", default=os.getenv("R2_BUCKET", "taylor-data"))
    parser.add_argument("--track-prefix", default=os.getenv("SPOTIFY_R2_TRACK_PREFIX", "history-by-track"))
    parser.add_argument("--data-prefix", default=os.getenv("R2_STATIC_DATA_PREFIX", "data"))
    parser.add_argument("--history-prefix", default=os.getenv("R2_STATIC_HISTORY_PREFIX", "history"))
    parser.add_argument("--db-prefix", default=os.getenv("R2_DB_PREFIX", "db"))
    parser.add_argument("--skip-history-upload", action="store_true")
    parser.add_argument("--skip-static-upload", action="store_true")
    parser.add_argument("--skip-db-upload", action="store_true")
    parser.add_argument("--skip-images-upload", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    missing_vars = get_missing_env_vars(R2_REQUIRED_ENV_VARS)
    if missing_vars:
        upload_to_r2 = os.getenv("UPLOAD_TO_R2", "").strip().lower()
        if upload_to_r2 in ("1", "true", "yes", "on"):
            raise RuntimeError(
                "Missing required R2 environment variable(s) while UPLOAD_TO_R2 is enabled: "
                + ", ".join(missing_vars)
            )

        print(
            "[WARN] R2 credentials are not configured; skipping upload "
            f"({', '.join(missing_vars)})."
        )
        print("[INFO] Set UPLOAD_TO_R2=1 to enforce upload and fail fast on missing credentials.")
        return 0

    client = get_s3_client()
    bucket = args.bucket

    daily_files: list[Path] = []
    if not args.skip_history_upload:
        if not HISTORY_DIR.exists():
            raise FileNotFoundError(f"History folder not found: {HISTORY_DIR}")

        daily_files = sorted(
            p for p in HISTORY_DIR.glob("*.json")
            if p.name != "index.json"
        )

        if not daily_files:
            print("No history files found. History upload will be skipped.")

    history_uploaded = 0
    history_unchanged = 0
    static_uploaded = 0
    static_unchanged = 0
    db_uploaded = 0
    db_unchanged = 0
    images_uploaded = 0
    images_unchanged = 0

    if not args.skip_history_upload and daily_files:
        by_track = defaultdict(list)

        for path in daily_files:
            m = DATE_RE.search(path.stem)
            if not m:
                print(f"[SKIP] date not found in filename: {path}")
                continue

            date = m.group(1)
            data = load_json(path)

            if not isinstance(data, dict):
                print(f"[SKIP] invalid format: {path}")
                continue

            for track_id, values in data.items():
                if not isinstance(values, dict):
                    continue

                point = {
                    "date": date,
                    "streams": values.get("s"),
                    "daily_streams": values.get("d"),
                }

                if "rank" in values:
                    point["rank"] = values.get("rank")

                by_track[track_id].append(point)

        for track_id, points in by_track.items():
            points.sort(key=lambda x: x["date"])
            payload = {"track_id": track_id, "points": points}
            bucket_key = f"{args.track_prefix}/{track_id}.json"
            changed = upload_json_if_changed(
                client=client,
                bucket=bucket,
                key=bucket_key,
                payload=payload,
                dry_run=args.dry_run,
            )
            if changed:
                history_uploaded += 1
                print(f"[UPLOADED] {bucket_key}")
            else:
                history_unchanged += 1
                print(f"[UNCHANGED] {bucket_key}")

    if not args.skip_static_upload:
        static_uploaded, static_unchanged = upload_static_data(
            client=client,
            bucket=bucket,
            data_prefix=args.data_prefix,
            history_prefix=args.history_prefix,
            dry_run=args.dry_run,
        )

    if not args.skip_db_upload:
        db_uploaded, db_unchanged = upload_db_files(
            client=client,
            bucket=bucket,
            db_prefix=args.db_prefix,
            dry_run=args.dry_run,
        )

    # Upload Apple Music images if they exist
    if not args.skip_images_upload:
        images_uploaded, images_unchanged = upload_apple_music_images(
            client=client,
            bucket=bucket,
            images_prefix=os.getenv("R2_IMAGES_PREFIX", "images/apple-music"),
            dry_run=args.dry_run,
        )
    else:
        images_uploaded = 0
        images_unchanged = 0

    print("\n[done]")
    print(f"  bucket: {bucket}")
    print(f"  track_prefix: {args.track_prefix}")
    print(f"  db_prefix: {args.db_prefix}")
    print(f"  history uploaded: {history_uploaded}")
    print(f"  history unchanged: {history_unchanged}")
    print(f"  static uploaded: {static_uploaded}")
    print(f"  static unchanged: {static_unchanged}")
    print(f"  db uploaded: {db_uploaded}")
    print(f"  db unchanged: {db_unchanged}")
    print(f"  images uploaded: {images_uploaded}")
    print(f"  images unchanged: {images_unchanged}")
    if args.dry_run:
        print("  mode: dry-run")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
