#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
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

from dotenv import load_dotenv

try:
    import boto3
    import botocore.config
    import botocore.exceptions
except ImportError:
    boto3 = None
    botocore = None

load_dotenv(str(Path(__file__).resolve().parents[1] / ".env"), override=True)

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "website" / "site" / "history"
SITE_DATA_DIR = ROOT / "website" / "site" / "data"
APPLE_MUSIC_IMAGES_DIR = SITE_DATA_DIR / "apple-music-images"
DB_DIR = ROOT / "db"
ARCHIVE_STREAMS_HISTORY = ROOT / "data" / "_archive" / "original" / "db" / "streams_history.csv"
WORLDWIDE_CHARTS_HISTORY_DIR = (
    ROOT
    / "collectors"
    / "spotify"
    / "charts"
    / "worldwide"
    / "history"
)
WORLDWIDE_TOTAL_DAYS_PATH = (
    ROOT
    / "collectors"
    / "spotify"
    / "charts"
    / "worldwide"
    / "tools"
    / "json"
    / "total_days.json"
)

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
NO_CACHE_CONTROL = "no-cache, no-store, must-revalidate"
SHORT_CACHE_CONTROL = "max-age=60, stale-while-revalidate=30"
R2_REQUIRED_ENV_VARS = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)

MAX_WORKERS = 32


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
        config=botocore.config.Config(
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def head_object_safe(client, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception:
        return None


def object_has_same_hash(client, bucket: str, key: str, body_hash: str) -> bool:
    meta = head_object_safe(client, bucket, key)
    if not meta:
        return False
    remote_hash = (meta.get("Metadata") or {}).get("sha256", "")
    return body_hash == remote_hash


def upload_bytes_if_changed(
    *,
    client,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str,
    dry_run: bool,
    cache_control: str | None = None,
    retries: int = 3,
) -> bool:
    body_hash = hashlib.sha256(data).hexdigest()

    if object_has_same_hash(client, bucket, key, body_hash):
        return False

    if dry_run:
        return True

    for attempt in range(1, retries + 1):
        try:
            client.upload_fileobj(
                io.BytesIO(data),
                bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "CacheControl": cache_control or SHORT_CACHE_CONTROL,
                    "Metadata": {"sha256": body_hash},
                },
            )
            return True
        except Exception as exc:
            if attempt == retries:
                raise
            # SSL EOF / connection reset needs a fresh boto3 client
            if isinstance(exc, (botocore.exceptions.SSLError, botocore.exceptions.ConnectionError)):
                client = get_s3_client()
            time.sleep(min(2 ** attempt, 8))

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
        cache_control=NO_CACHE_CONTROL,
    )


def upload_raw_if_changed(*, client, bucket: str, key: str, data: bytes, content_type: str, dry_run: bool) -> bool:
    cache_control = NO_CACHE_CONTROL if (
        content_type.startswith("application/json") or
        content_type.startswith("text/csv")
    ) else SHORT_CACHE_CONTROL
    return upload_bytes_if_changed(
        client=client,
        bucket=bucket,
        key=key,
        data=data,
        content_type=content_type,
        dry_run=dry_run,
        cache_control=cache_control,
    )


def _upload_db_file(client, bucket: str, path: Path, db_prefix: str, dry_run: bool) -> bool:
    rel_path = path.relative_to(DB_DIR).as_posix()
    full_key = f"{db_prefix}/{rel_path}"
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return upload_raw_if_changed(
        client=client,
        bucket=bucket,
        key=full_key,
        data=path.read_bytes(),
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
    db_files = sorted(p for p in DB_DIR.rglob("*") if p.is_file())

    uploaded = 0
    unchanged = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(_upload_db_file, client, bucket, path, db_prefix, dry_run)
            for path in db_files
        ]
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                uploaded += 1
            else:
                unchanged += 1

    # Always persist the archive streams_history.csv under a fixed R2 key so
    # the GitHub Actions runner can download it before each run.
    if ARCHIVE_STREAMS_HISTORY.exists():
        key = f"{db_prefix}/streams_history.csv"
        if upload_raw_if_changed(
            client=client,
            bucket=bucket,
            key=key,
            data=ARCHIVE_STREAMS_HISTORY.read_bytes(),
            content_type="text/csv",
            dry_run=dry_run,
        ):
            uploaded += 1
        else:
            unchanged += 1

    print(f"  db         : {uploaded} uploaded, {unchanged} unchanged")
    return uploaded, unchanged


def _upload_image_file(client, bucket: str, path: Path, images_prefix: str, dry_run: bool) -> bool:
    full_key = f"{images_prefix}/{path.name}"
    return upload_raw_if_changed(
        client=client,
        bucket=bucket,
        key=full_key,
        data=path.read_bytes(),
        content_type="image/jpeg",
        dry_run=dry_run,
    )


def upload_apple_music_images(
    *,
    client,
    bucket: str,
    images_prefix: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Upload Apple Music track images if they exist locally."""
    if not APPLE_MUSIC_IMAGES_DIR.exists():
        return 0, 0

    image_files = sorted(p for p in APPLE_MUSIC_IMAGES_DIR.glob("*") if p.is_file())
    if not image_files:
        return 0, 0

    uploaded = 0
    unchanged = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(_upload_image_file, client, bucket, path, images_prefix, dry_run)
            for path in image_files
        ]
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                uploaded += 1
            else:
                unchanged += 1

    print(f"  images     : {uploaded} uploaded, {unchanged} unchanged ({len(image_files)} total)")
    return uploaded, unchanged


def _upload_static_item(client, bucket: str, key: str, data: bytes, content_type: str, dry_run: bool) -> bool:
    return upload_bytes_if_changed(
        client=client,
        bucket=bucket,
        key=key,
        data=data,
        content_type=content_type,
        dry_run=dry_run,
    )


def _collect_slug_tasks(slug: str, data_prefix: str) -> list[tuple[str, bytes, str]]:
    """Return upload tasks for a single chart slug (latest + snapshots + index + png)."""
    tasks: list[tuple[str, bytes, str]] = []

    # Latest
    latest = SITE_DATA_DIR / f"{slug}.json"
    if latest.exists():
        try:
            obj = load_json(latest)
            data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            tasks.append((f"{data_prefix}/{slug}.json", data, "application/json; charset=utf-8"))
        except Exception:
            print(f"[SKIP] invalid: {latest}")

    # Index
    index = SITE_DATA_DIR / f"{slug}_index.json"
    if index.exists():
        tasks.append((f"{data_prefix}/{slug}_index.json", index.read_bytes(), "application/json; charset=utf-8"))

    # Dated snapshots
    for snapshot in sorted(SITE_DATA_DIR.glob(f"{slug}_????-??-??.json")):
        try:
            obj = load_json(snapshot)
            data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            tasks.append((f"{data_prefix}/{snapshot.name}", data, "application/json; charset=utf-8"))
        except Exception:
            print(f"[SKIP] invalid snapshot: {snapshot}")

    # PNG image
    png = SITE_DATA_DIR / f"{slug}.png"
    if png.exists():
        tasks.append((f"{data_prefix}/{slug}.png", png.read_bytes(), "image/png"))

    return tasks


def upload_static_data(
    *,
    client,
    bucket: str,
    data_prefix: str,
    history_prefix: str,
    dry_run: bool,
    new_date: str | None = None,
    slugs: list[str] | None = None,
) -> tuple[int, int]:
    """Upload generated JSON/CSV/static history files used by the frontend.

    If *slugs* is provided, only the files belonging to those chart slugs are
    uploaded (latest JSON, dated snapshots, index, PNG).  All other sections
    (history, CSVs, songs.json …) are skipped — use this for targeted uploads
    after a single chart is regenerated.
    """
    tasks: list[tuple[str, bytes, str]] = []  # (key, data, content_type)

    if slugs is not None:
        for slug in slugs:
            tasks.extend(_collect_slug_tasks(slug, data_prefix))

        uploaded = 0
        unchanged = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(_upload_static_item, client, bucket, key, data, content_type, dry_run)
                for key, data, content_type in tasks
            ]
            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    uploaded += 1
                else:
                    unchanged += 1
        print(f"  static     : {uploaded} uploaded, {unchanged} unchanged (slugs: {', '.join(slugs)})")
        return uploaded, unchanged

    json_mappings = [
        ("songs.json",               "data/songs.json"),
        ("albums.json",              "data/albums.json"),
        ("artist.json",              "data/artist.json"),
        ("expected_milestones.json", "data/milestones.json"),
        ("billboard.json",           "data/billboard.json"),
        ("swift_top_100.json",       "data/swift_top_100.json"),
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
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        tasks.append((full_key, data, "application/json; charset=utf-8"))

    # Upload worldwide total_days store (produced by backfill_total_days.py / daily.py)
    if WORLDWIDE_TOTAL_DAYS_PATH.exists():
        obj = load_json(WORLDWIDE_TOTAL_DAYS_PATH)
        full_key = f"{data_prefix}/charts_worldwide_total_days.json"
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        tasks.append((full_key, payload, "application/json; charset=utf-8"))
    else:
        print(f"[SKIP] absent: {WORLDWIDE_TOTAL_DAYS_PATH}")

    # TayBoard top-100: index, per-song files, snapshots, PNG
    tasks.extend(_collect_slug_tasks("swift_top_100", data_prefix))
    tasks.extend(_collect_slug_tasks("swift_top_100_not_combined", data_prefix))

    # Per-song TayBoard history files: swift_top_100_songs/{track_id}.json
    songs_dir = SITE_DATA_DIR / "swift_top_100_songs"
    if songs_dir.exists():
        for song_path in sorted(songs_dir.glob("*.json")):
            r2_key = f"{data_prefix}/swift_top_100_songs/{song_path.name}"
            try:
                tasks.append((r2_key, song_path.read_bytes(), "application/json; charset=utf-8"))
            except Exception:
                print(f"[SKIP] invalid song file: {song_path}")

    # Charts CSVs: store in db/ as charts_history_<region>.csv.
    # Frontend API loader may try both `charts_history_<region>.csv` and `charts_<region>.csv`.
    # Upload both keys so R2-only production does not 404 on the first attempt.
    csv_mappings = [
        ("charts_history_global.csv",       ["data/charts_global.csv",      "data/charts_history_global.csv"]),
        ("charts_history_fr.csv",           ["data/charts_fr.csv",          "data/charts_history_fr.csv"]),
        ("charts_history_us.csv",           ["data/charts_us.csv",          "data/charts_history_us.csv"]),
        ("charts_history_uk.csv",           ["data/charts_uk.csv",          "data/charts_history_uk.csv"]),
        ("youtube_views_history.csv",       ["data/youtube_views.csv",      "data/youtube_views_history.csv"]),
    ]
    for filename, r2_keys in csv_mappings:
        src = DB_DIR / filename
        if not src.exists():
            print(f"[SKIP] absent: {src}")
            continue
        data = src.read_bytes()
        for r2_key in r2_keys:
            full_key = f"{data_prefix}/{r2_key.split('/', 1)[1]}"
            tasks.append((full_key, data, "text/csv; charset=utf-8"))

    index_path = HISTORY_DIR / "index.json"
    if index_path.exists():
        full_key = f"{history_prefix}/index.json"
        obj = load_json(index_path)
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        tasks.append((full_key, data, "application/json; charset=utf-8"))

    if new_date:
        # Only upload the one new date file — skip re-checking all 500+ existing files.
        single_path = HISTORY_DIR / f"{new_date}.json"
        if single_path.exists():
            obj = load_json(single_path)
            data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            tasks.append((f"{history_prefix}/{new_date}.json", data, "application/json; charset=utf-8"))
    else:
        daily_files = sorted(
            p for p in HISTORY_DIR.glob("*.json")
            if p.name != "index.json"
        )
        for path in daily_files:
            m = DATE_RE.search(path.stem)
            if not m:
                continue
            full_key = f"{history_prefix}/{m.group(1)}.json"
            obj = load_json(path)
            data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            tasks.append((full_key, data, "application/json; charset=utf-8"))

    # Worldwide charts per-date snapshots
    # Produced by collectors/spotify/charts/worldwide/daily.py
    # Upload to history/charts_worldwide/YYYY-MM-DD.json so the API can serve historical worldwide data.
    if WORLDWIDE_CHARTS_HISTORY_DIR.exists():
        worldwide_files = sorted(WORLDWIDE_CHARTS_HISTORY_DIR.rglob("ts_worldwide_*.json"))
        for path in worldwide_files:
            m = DATE_RE.search(path.name)
            if not m:
                continue
            chart_date = m.group(1)
            full_key = f"{history_prefix}/charts_worldwide/{chart_date}.json"
            try:
                obj = load_json(path)
            except Exception:
                # Keep upload robust even if one file is malformed.
                print(f"[SKIP] invalid worldwide snapshot: {path}")
                continue
            data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            tasks.append((full_key, data, "application/json; charset=utf-8"))

    uploaded = 0
    unchanged = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(_upload_static_item, client, bucket, key, data, content_type, dry_run)
            for key, data, content_type in tasks
        ]
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                uploaded += 1
            else:
                unchanged += 1

    print(f"  static     : {uploaded} uploaded, {unchanged} unchanged")
    return uploaded, unchanged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload Spotify history, static data, and db files to R2.")
    parser.add_argument("--bucket", default=os.getenv("R2_BUCKET", "taylor-data"))
    parser.add_argument("--track-prefix", default=os.getenv("SPOTIFY_R2_TRACK_PREFIX", "history-by-track"))
    parser.add_argument("--data-prefix", default=os.getenv("R2_STATIC_DATA_PREFIX", "data"))
    parser.add_argument("--history-prefix", default=os.getenv("R2_STATIC_HISTORY_PREFIX", "history"))
    parser.add_argument("--db-prefix", default=os.getenv("R2_DB_PREFIX", "db"))
    parser.add_argument("--new-date", default=None, help="Only upload this date's history file (YYYY-MM-DD) instead of all history files.")
    parser.add_argument("--slugs", default=None, help="Comma-separated chart slugs to upload (e.g. swift_top_albums,swift_top_eras). Skips all other sections.")
    parser.add_argument("--skip-history-upload", action="store_true")
    parser.add_argument("--skip-static-upload", action="store_true")
    parser.add_argument("--skip-db-upload", action="store_true")
    parser.add_argument("--skip-images-upload", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _run_history_upload(client, bucket, track_prefix, daily_files, dry_run) -> tuple[int, int]:
    by_track: dict[str, list[dict]] = defaultdict(list)

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
            point: dict[str, Any] = {
                "date": date,
                "streams": values.get("s"),
                "daily_streams": values.get("d"),
            }
            if "rank" in values:
                point["rank"] = values.get("rank")
            by_track[track_id].append(point)

    def _upload_track(track_id: str, points: list[dict]) -> bool:
        points.sort(key=lambda x: x["date"])
        payload = {"track_id": track_id, "points": points}
        bucket_key = f"{track_prefix}/{track_id}.json"
        return upload_json_if_changed(
            client=client,
            bucket=bucket,
            key=bucket_key,
            payload=payload,
            dry_run=dry_run,
        )

    uploaded = 0
    unchanged = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(_upload_track, track_id, points)
            for track_id, points in by_track.items()
        ]
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                uploaded += 1
            else:
                unchanged += 1

    print(f"  history    : {uploaded} uploaded, {unchanged} unchanged ({len(by_track)} tracks)")
    return uploaded, unchanged


def main() -> int:
    args = parse_args()

    if boto3 is None or botocore is None:
        upload_to_r2 = os.getenv("UPLOAD_TO_R2", "").strip().lower()
        if upload_to_r2 in ("1", "true", "yes", "on"):
            raise RuntimeError(
                "boto3/botocore are required for R2 upload. "
                "Install dependencies with: python -m pip install -r requirements.txt"
            )
        print("[WARN] boto3/botocore not installed; skipping R2 upload.")
        print("[INFO] Install dependencies with: python -m pip install -r requirements.txt")
        return 0

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

    # Run all 4 sections in parallel
    section_futures: dict[str, concurrent.futures.Future] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        if not args.skip_history_upload and daily_files:
            section_futures["history"] = executor.submit(
                _run_history_upload, client, bucket, args.track_prefix, daily_files, args.dry_run
            )
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()] if args.slugs else None
        # --slugs implies skipping everything except static (chart files only)
        if slugs:
            args.skip_history_upload = True
            args.skip_db_upload = True
            args.skip_images_upload = True
        if not args.skip_static_upload:
            section_futures["static"] = executor.submit(
                upload_static_data,
                client=client,
                bucket=bucket,
                data_prefix=args.data_prefix,
                history_prefix=args.history_prefix,
                dry_run=args.dry_run,
                new_date=args.new_date,
                slugs=slugs,
            )
        if not args.skip_db_upload:
            section_futures["db"] = executor.submit(
                upload_db_files,
                client=client,
                bucket=bucket,
                db_prefix=args.db_prefix,
                dry_run=args.dry_run,
            )
        if not args.skip_images_upload:
            section_futures["images"] = executor.submit(
                upload_apple_music_images,
                client=client,
                bucket=bucket,
                images_prefix=os.getenv("R2_IMAGES_PREFIX", "images/apple-music"),
                dry_run=args.dry_run,
            )

    history_uploaded, history_unchanged = section_futures["history"].result() if "history" in section_futures else (0, 0)
    static_uploaded, static_unchanged = section_futures["static"].result() if "static" in section_futures else (0, 0)
    db_uploaded, db_unchanged = section_futures["db"].result() if "db" in section_futures else (0, 0)
    images_uploaded, images_unchanged = section_futures["images"].result() if "images" in section_futures else (0, 0)

    total_uploaded = history_uploaded + static_uploaded + db_uploaded + images_uploaded
    total_unchanged = history_unchanged + static_unchanged + db_unchanged + images_unchanged
    suffix = " [dry-run]" if args.dry_run else ""
    print(f"\n[done]{suffix}  {total_uploaded} uploaded, {total_unchanged} unchanged  (bucket: {bucket})")

    return 0


# ---------------------------------------------------------------------------
# Public API — importable by other scripts
# ---------------------------------------------------------------------------

def _r2_ready() -> tuple[bool, str]:
    """Return (ok, skip_reason). Loads .env before checking."""
    try:
        load_dotenv(str(Path(__file__).resolve().parents[1] / ".env"), override=True)
    except Exception:
        pass
    if boto3 is None or botocore is None:
        return False, "boto3/botocore not installed"
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        return False, "UPLOAD_TO_R2=0"
    missing = get_missing_env_vars(R2_REQUIRED_ENV_VARS)
    if missing:
        return False, "missing env: " + ", ".join(missing)
    return True, ""


def upload_slugs(
    slugs: list[str],
    *,
    dry_run: bool = False,
    bucket: str | None = None,
    data_prefix: str | None = None,
) -> bool:
    """Upload only the files for the given chart slugs (latest, snapshots, index, PNG).

    Returns True on success, False when skipped due to missing credentials / config.
    """
    ok, reason = _r2_ready()
    if not ok:
        print(f"[r2] skipped — {reason}")
        return False

    _bucket = bucket or os.getenv("R2_BUCKET", "taylor-data")
    _data_prefix = data_prefix or os.getenv("R2_STATIC_DATA_PREFIX", "data")
    client = get_s3_client()
    upload_static_data(
        client=client,
        bucket=_bucket,
        data_prefix=_data_prefix,
        history_prefix="",  # unused when slugs is set
        dry_run=dry_run,
        slugs=slugs,
    )
    return True


def upload_all(
    *,
    new_date: str | None = None,
    dry_run: bool = False,
    skip_history: bool = False,
    skip_db: bool = False,
    skip_images: bool = False,
    bucket: str | None = None,
    data_prefix: str | None = None,
    history_prefix: str | None = None,
    db_prefix: str | None = None,
) -> bool:
    """Full R2 upload — equivalent to running r2.py with no arguments.

    Returns True on success, False when skipped due to missing credentials / config.
    """
    ok, reason = _r2_ready()
    if not ok:
        print(f"[r2] skipped — {reason}")
        return False

    _bucket = bucket or os.getenv("R2_BUCKET", "taylor-data")
    _data_prefix = data_prefix or os.getenv("R2_STATIC_DATA_PREFIX", "data")
    _history_prefix = history_prefix or os.getenv("R2_STATIC_HISTORY_PREFIX", "history")
    _db_prefix = db_prefix or os.getenv("R2_DB_PREFIX", "db")
    _track_prefix = os.getenv("SPOTIFY_R2_TRACK_PREFIX", "history-by-track")
    _images_prefix = os.getenv("R2_IMAGES_PREFIX", "images/apple-music")
    client = get_s3_client()

    section_futures: dict[str, concurrent.futures.Future] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        if not skip_history and HISTORY_DIR.exists():
            daily_files = sorted(p for p in HISTORY_DIR.glob("*.json") if p.name != "index.json")
            if daily_files:
                section_futures["history"] = executor.submit(
                    _run_history_upload, client, _bucket, _track_prefix, daily_files, dry_run
                )
        section_futures["static"] = executor.submit(
            upload_static_data,
            client=client, bucket=_bucket, data_prefix=_data_prefix,
            history_prefix=_history_prefix, dry_run=dry_run, new_date=new_date,
        )
        if not skip_db:
            section_futures["db"] = executor.submit(
                upload_db_files, client=client, bucket=_bucket, db_prefix=_db_prefix, dry_run=dry_run,
            )
        if not skip_images:
            section_futures["images"] = executor.submit(
                upload_apple_music_images, client=client, bucket=_bucket,
                images_prefix=_images_prefix, dry_run=dry_run,
            )

    ok = True
    for name, future in section_futures.items():
        try:
            future.result()
        except Exception as exc:
            print(f"[r2] {name} failed — {exc}")
            ok = False
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
