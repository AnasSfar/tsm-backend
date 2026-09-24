#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import csv
import gzip
import hashlib
import io
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
from botocore.config import Config
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import LEGACY_WEBSITE_DATA_DIR, WEB_EXPORT_DATA_DIR, apple_music_daily_csv_paths  # noqa: E402
import r2_keys  # noqa: E402

DB_DIR = ROOT / "db"
DATA_ROOT = ROOT / "data"
ARCHIVE_DB_DIR = DATA_ROOT / "_archive" / "original" / "db"
SITE_DATA_DIR = WEB_EXPORT_DATA_DIR if WEB_EXPORT_DATA_DIR.exists() else LEGACY_WEBSITE_DATA_DIR

APPLEMUSIC_JSON = SITE_DATA_DIR / "applemusic.json"
APPLEMUSIC_HISTORY_JSON = SITE_DATA_DIR / "applemusic_history.json"
APPLEMUSIC_HISTORY_DATES_DIR = SITE_DATA_DIR / "applemusic_history_dates"

COUNTRY_CSV = DB_DIR / "apple_music_country_charts.csv"
GENRE_CSV = DB_DIR / "apple_music_genre_charts.csv"
GLOBAL_CSV = DB_DIR / "apple_music_global.csv"
TS_TOP_CSV = DB_DIR / "apple_music_ts_top_songs_global.csv"

R2_PREFIX = r2_keys.APPLE_MUSIC_HISTORY_BY_SONG_PREFIX
CSV_R2_PREFIX = r2_keys.APPLE_MUSIC_DB_PREFIX
SNAPSHOT_R2_PREFIX = r2_keys.APPLE_MUSIC_SNAPSHOTS_PREFIX
HISTORY_BY_DATE_R2_PREFIX = r2_keys.APPLE_MUSIC_HISTORY_BY_DATE_PREFIX
NO_CACHE_CONTROL = "no-cache, no-store, must-revalidate"

APPLE_MUSIC_CSV_NAMES = [
    "apple_music_global.csv",
    "apple_music_country_charts.csv",
    "apple_music_genre_charts.csv",
    "apple_music_ts_top_songs_global.csv",
    "apple_music_country_albums.csv",
    "apple_music_genre_album_charts.csv",
]


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


# Parallel R2 requests per phase (was 4-8). R2 has no practical per-client
# request cap at this volume; most per-song objects are a HEAD + skip.
R2_WORKERS = max(1, int(os.getenv("APPLE_MUSIC_R2_WORKERS", "24")))


def get_r2_client() -> BaseClient:
    account_id = get_env("R2_ACCOUNT_ID")
    access_key_id = get_env("R2_ACCESS_KEY_ID")
    secret_access_key = get_env("R2_SECRET_ACCESS_KEY")

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    # Without socket timeouts a stalled R2 connection blocks put_object forever
    # (2026-09-24 11h: all 8 workers stuck in ssl.sendall, cycle never ended and
    # IgnoreNew would have skipped the next hourly trigger).
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
        config=Config(
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
            max_pool_connections=R2_WORKERS + 8,
        ),
    )


def get_bucket_name() -> str:
    return os.getenv("R2_BUCKET", "taylor-data").strip() or "taylor-data"


# Pure string functions called ~2.3M times per run on a few thousand distinct
# titles: memoized (was ~19s of the per-song build).
@functools.lru_cache(maxsize=None)
def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = value.replace("&", "and")
    value = re.sub(r"\s+", " ", value)
    return value


@functools.lru_cache(maxsize=None)
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

    candidates.extend(apple_music_daily_csv_paths(path.name))

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
    compress: bool = False,
) -> bool:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # Hash of the uncompressed JSON either way, so switching an object to gzip
    # doesn't force a re-upload and r2.py (same serialization) still matches.
    local_hash = hashlib.sha256(body).hexdigest()

    if object_has_same_body_hash(client, bucket, key, body):
        return False

    if dry_run:
        print(f"[dry-run][upload] {key}")
        return True

    extra: dict[str, str] = {}
    if compress:
        # ~10x smaller. Readers: tsm-frontend api/data/loader.py::_r2_json
        # gunzips on the magic bytes; public/CDN fetches honor Content-Encoding.
        body = gzip.compress(body, compresslevel=6, mtime=0)
        extra["ContentEncoding"] = "gzip"

    for attempt in range(1, retries + 1):
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                # A file-like body is sent in 16 KB chunks, so the socket timeout
                # applies per chunk: a stall aborts fast, a big file still fits.
                # (Raw bytes go out in one sendall bounded by the 10s timeout.)
                Body=io.BytesIO(body),
                ContentType="application/json; charset=utf-8",
                CacheControl=NO_CACHE_CONTROL,
                Metadata={"sha256": local_hash},
                **extra,
            )
            break
        except Exception:
            if attempt == retries:
                raise
            time.sleep(min(2 ** attempt, 5))

    return True


def clean_row(row: dict[str, str], keep_fields: list[str]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for field in keep_fields:
        if field not in row:
            continue
        value = row.get(field, "")
        if field == "storefront_ranks" and value:
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = {}
            clean[field] = parsed if isinstance(parsed, dict) else {}
        else:
            clean[field] = value
    return clean


def normalize_song_identity(song_name: str) -> str:
    return normalize_text(song_name)


def append_rows(
    grouped: dict[str, dict[str, Any]],
    rows: list[dict[str, str]],
    source_name: str,
    keep_fields: list[str],
    name_field: str = "song_name",
) -> None:
    for row in rows:
        name = (row.get(name_field) or "").strip()
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
                    "music_video_charts": [],
                },
            },
        )

        # garder le premier nom rencontré comme canonique, mais si vide on remplit
        if not bucket.get("song_name"):
            bucket["song_name"] = name

        bucket["sources"].setdefault(source_name, []).append(clean_row(row, keep_fields))


def sort_points(points: list[dict[str, str]]) -> list[dict[str, str]]:
    def key(row: dict[str, str]) -> tuple:
        return (
            row.get("date", ""),
            row.get("country", ""),
            row.get("genre_id", ""),
            row.get("rank", ""),
        )
    return sorted(points, key=key)


# Only read by tsm-frontend api/routes/apple_music.py::_song_history_rows_from_r2
# from the most recent point carrying an image_url; every other point only
# needs its date/rank/previous_rank/country/genre.
_POINT_META_FIELDS = ("image_url", "url", "apple_music_id", "album_name", "song_name", "storefront_ranks")


def _point_date_key(point: dict[str, Any]) -> str:
    # Same key the API uses to pick the metadata point.
    return str(point.get("scraped_at") or point.get("date") or "").strip()


def _dedupe_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # read_csv() unions db/, _archive/ and every daily CSV, so the same row can
    # appear 2-3 times (27% of Global points) and showed up as repeated hits.
    seen: set[Any] = set()
    unique: list[dict[str, Any]] = []
    for point in points:
        # Same identity as a sorted-keys JSON dump, without serializing every
        # point (was ~30s/run); JSON only for points holding parsed dict/list.
        fingerprint: Any = tuple(sorted(point.items()))
        try:
            hash(fingerprint)
        except TypeError:
            fingerprint = json.dumps(point, sort_keys=True, ensure_ascii=False)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(point)
    return unique


def _slim_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Keep metadata only on the points the API can pick it from (latest date
    # key with an image_url, all ties kept so its first-match choice is
    # unchanged); strip it everywhere else (~60% of each object).
    meta_key = max(
        (_point_date_key(p) for p in points if p.get("image_url") and _point_date_key(p)),
        default=None,
    )
    for point in points:
        if meta_key is not None and point.get("image_url") and _point_date_key(point) == meta_key:
            continue
        for field in _POINT_META_FIELDS:
            point.pop(field, None)
    return points


def finalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for source_name, points in payload["sources"].items():
        payload["sources"][source_name] = _slim_points(sort_points(_dedupe_points(points)))
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
        keep_fields=["date", "scraped_at", "storefront", "song_name", "rank", "previous_rank", "image_url", "url", "apple_music_id", "album_name", "storefront_ranks"],
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
        # applemusic_history.json is ~125 MB raw: gzip it (API fallback reader
        # handles both). applemusic.json stays plain.
        changed = upload_json_if_changed(
            client, bucket, r2_key, payload, dry_run=dry_run,
            compress=r2_key.endswith("applemusic_history.json"),
        )
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
    """Upload the most recent daily CSV for each Apple Music chart to R2."""
    uploaded = 0
    for name in APPLE_MUSIC_CSV_NAMES:
        candidates = sorted(apple_music_daily_csv_paths(name), reverse=True)
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
                    Body=io.BytesIO(body),
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


def _bucket_value_at(history: dict[str, Any], bucket: str, snapshot_key: str, default: Any) -> tuple[Any, bool]:
    # An unchanged chart writes no CSV row for its hour, so the exact key is often
    # missing: use the latest same-day snapshot at or before it (never a later one).
    source = history.get(bucket) or {}
    if not isinstance(source, dict):
        return default, False
    if snapshot_key in source:
        return source[snapshot_key], True
    day = snapshot_key[:10]
    earlier = [k for k in source if isinstance(k, str) and k[:10] == day and k <= snapshot_key]
    if not earlier:
        return default, False
    return source[max(earlier)], True


def _snapshot_payload(history: dict[str, Any], snapshot_key: str) -> dict[str, Any] | None:
    fields = (
        ("global_chart", "global", []),
        ("global_album_chart", "global_albums", []),
        ("ts_top_songs", "top_songs", []),
        ("top_videos", "top_videos", []),
        ("country_charts", "country", {}),
        ("country_album_charts", "country_albums", {}),
        ("genre_charts", "genre", {}),
        ("genre_album_charts", "genre_albums", {}),
    )
    payload: dict[str, Any] = {"date": snapshot_key, "scraped_at": snapshot_key}
    found_any = False
    for field, bucket, default in fields:
        value, found = _bucket_value_at(history, bucket, snapshot_key, default)
        payload[field] = value
        found_any = found_any or found
    payload["music_video_charts"] = {}
    return payload if found_any else None


def upload_snapshot_jsons(client: BaseClient, bucket: str, dry_run: bool) -> int:
    """Upload date/hour snapshots so API consumers can avoid the huge history JSON."""
    if not APPLEMUSIC_HISTORY_JSON.exists():
        print(f"[skip] {SNAPSHOT_R2_PREFIX} history source not found locally")
        return 0

    history = json.loads(APPLEMUSIC_HISTORY_JSON.read_text(encoding="utf-8-sig"))
    dates = [date for date in history.get("dates", []) if isinstance(date, str) and date]
    if not dates:
        print(f"[skip] {SNAPSHOT_R2_PREFIX} no dates found")
        return 0

    uploaded = 0
    unchanged = 0
    current_day = max(dates)[:10]

    def _upload(snapshot_key: str) -> tuple[str, bool]:
        r2_key = f"{SNAPSHOT_R2_PREFIX}/{snapshot_key}.json"
        # Past days are collapsed to one snapshot per chart in the export, so
        # rebuilding them now would be poorer than what was uploaded that day.
        if snapshot_key[:10] != current_day and head_object_safe(client, bucket, r2_key):
            return r2_key, False
        payload = _snapshot_payload(history, snapshot_key)
        if payload is None:
            return r2_key, False
        changed = upload_json_if_changed(client, bucket, r2_key, payload, dry_run=dry_run)
        return r2_key, changed

    with ThreadPoolExecutor(max_workers=R2_WORKERS) as pool:
        for r2_key, changed in pool.map(_upload, dates):
            if changed:
                print(f"[uploaded] {r2_key}")
                uploaded += 1
            else:
                unchanged += 1

    print(f"[done] {SNAPSHOT_R2_PREFIX}: uploaded={uploaded} unchanged={unchanged}")
    return uploaded


def upload_history_by_date_jsons(client: BaseClient, bucket: str, dry_run: bool) -> int:
    """Upload pre-split date payloads so the API avoids applemusic_history.json."""
    if not APPLEMUSIC_HISTORY_DATES_DIR.exists():
        print(f"[skip] {HISTORY_BY_DATE_R2_PREFIX} split history dir not found locally")
        return 0

    files = sorted(APPLEMUSIC_HISTORY_DATES_DIR.glob("*.json"))
    if not files:
        print(f"[skip] {HISTORY_BY_DATE_R2_PREFIX} no split files found")
        return 0

    uploaded = 0
    unchanged = 0

    def _upload(path: Path) -> tuple[str, bool]:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        r2_key = f"{HISTORY_BY_DATE_R2_PREFIX}/{path.name}"
        changed = upload_json_if_changed(client, bucket, r2_key, payload, dry_run=dry_run)
        return r2_key, changed

    with ThreadPoolExecutor(max_workers=R2_WORKERS) as pool:
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

    # The per-song build (~30s parsing ~2.3M CSV rows) doesn't depend on the
    # phases below, which mostly wait on R2: build it alongside them. Upload
    # order is unchanged (critical files still go first).
    history_builder = ThreadPoolExecutor(max_workers=1)
    history_future = history_builder.submit(build_history_objects)

    # Upload main JSON files first (what the API reads)
    print("\n=== Uploading main Apple Music JSON files ===")
    upload_main_json_files(client, bucket, args.dry_run)

    print("\n=== Uploading Apple Music snapshot JSON files ===")
    upload_snapshot_jsons(client, bucket, args.dry_run)

    print("\n=== Uploading Apple Music history-by-date JSON files ===")
    upload_history_by_date_jsons(client, bucket, args.dry_run)

    # Upload daily CSVs so the next CI run can compute previous_rank
    print("\n=== Uploading Apple Music CSV history ===")
    upload_daily_csvs(client, bucket, args.dry_run)

    # Upload per-song history objects
    print("\n=== Uploading per-song history objects ===")
    objects = history_future.result()
    history_builder.shutdown()

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
        changed = upload_json_if_changed(c, b, r2_key, payload, dry_run=dry_run, compress=True)
        return r2_key, changed

    uploaded = 0
    unchanged = 0

    with ThreadPoolExecutor(max_workers=R2_WORKERS) as pool:
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
