#!/usr/bin/env python3
"""
Download Apple Music track images and upload them to R2.

This script:
1. Scans songs.json to find tracks that chart on Apple Music
2. Downloads Apple Music artwork images from the URLs in apple_music_country_charts.csv
3. Uploads images to R2 (images/apple-music/)
4. Updates songs.json with the R2 URL (apple_music_image_url field)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import requests
from dotenv import load_dotenv

load_dotenv()

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "collectors" / "spotify"))
from core.data_paths import LEGACY_WEBSITE_DATA_DIR, WEB_EXPORT_DATA_DIR, first_existing  # noqa: E402
import r2_keys  # noqa: E402

DB_DIR = _REPO_ROOT / "db"
SITE_DATA_DIR = WEB_EXPORT_DATA_DIR
LOCAL_IMAGES_DIR = SITE_DATA_DIR / "apple-music-images"  # Store locally for r2.py to upload

APPLE_MUSIC_CSV = DB_DIR / "apple_music_country_charts.csv"
SONGS_JSON_PATH = first_existing(SITE_DATA_DIR / "songs.json", LEGACY_WEBSITE_DATA_DIR / "songs.json")

# Configuration
DOWNLOAD_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 1
IMAGE_SIZE_MB_LIMIT = 10  # Max 10MB per image


def _norm_title(s: str) -> str:
    """Same normalization convention as collectors/comp/discography.py's _norm()."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


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


def extract_filename_from_url(url: str) -> str:
    """Extract a meaningful filename from the Apple Music URL."""
    # Example: https://is1-ssl.mzstatic.com/image/thumb/Music211/v4/.../300x300bb.jpg
    # We'll use a hash of the URL to create a unique filename
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return f"{url_hash}.jpg"


def download_image(url: str) -> bytes | None:
    """Download image from URL with retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
            resp.raise_for_status()
            
            # Check file size
            if len(resp.content) > IMAGE_SIZE_MB_LIMIT * 1024 * 1024:
                print(f"[SKIP] Image too large ({len(resp.content) / 1024 / 1024:.1f}MB): {url}")
                return None
            
            return resp.content
        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES:
                print(f"[ERROR] Failed to download after {MAX_RETRIES} attempts: {url} - {e}")
                return None
            print(f"[RETRY] Downloading {url} (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(RETRY_DELAY * attempt)
    
    return None


def save_image_locally(filename: str, data: bytes) -> bool:
    """Save image locally for r2.py to upload."""
    try:
        LOCAL_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        local_path = LOCAL_IMAGES_DIR / filename
        local_path.write_bytes(data)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save image locally {filename}: {e}")
        return False


def upload_image_to_r2(
    client,
    bucket: str,
    key: str,
    data: bytes,
    dry_run: bool = False,
) -> bool:
    """Upload image to R2."""
    if dry_run:
        print(f"[DRY-RUN] Upload {key}")
        return True
    
    try:
        body_hash = hashlib.sha256(data).hexdigest()
        client.upload_fileobj(
            io.BytesIO(data),
            bucket,
            key,
            ExtraArgs={
                "ContentType": "image/jpeg",
                "Metadata": {"sha256": body_hash},
            },
        )
        return True
    except Exception as e:
        print(f"[ERROR] Failed to upload {key}: {e}")
        return False


def load_apple_music_images() -> tuple[dict[str, str], dict[str, str]]:
    """Load Apple Music image URLs from CSV (deduplicated by URL).

    Returns (images, url_to_title):
      images: {image_url -> filename}
      url_to_title: {image_url -> normalized song_name}, keeping the most
      recent row (by date) per image_url since the CSV has one row per
      country/date and titles are otherwise the only usable join key
      (apple_music_id / artist_name are empty in practice).
    """
    images: dict[str, str] = {}
    url_to_title: dict[str, str] = {}
    url_to_date: dict[str, str] = {}

    if not APPLE_MUSIC_CSV.exists():
        print(f"[WARN] Apple Music CSV not found: {APPLE_MUSIC_CSV}")
        return images, url_to_title

    try:
        with open(APPLE_MUSIC_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                image_url = (row.get("image_url") or "").strip()
                song_name = (row.get("song_name") or "").strip()
                date = (row.get("date") or "").strip()
                if not image_url or not song_name:
                    continue
                if image_url not in images:
                    images[image_url] = extract_filename_from_url(image_url)
                if image_url not in url_to_date or date > url_to_date[image_url]:
                    url_to_date[image_url] = date
                    url_to_title[image_url] = _norm_title(song_name)
    except Exception as e:
        print(f"[ERROR] Failed to read Apple Music CSV: {e}")

    return images, url_to_title


def load_songs_payload() -> Any:
    """Load songs.json as-is (whatever its top-level shape is)."""
    if not SONGS_JSON_PATH.exists():
        return None
    try:
        return json.loads(SONGS_JSON_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _iter_tracks(payload: Any):
    """Yield every track dict in songs.json, regardless of shape:
    {"songs": [track, ...]} (current export shape), {"...": [{"tracks": [...]}]}
    (older section-wrapped shape), or a bare list of either."""
    if isinstance(payload, dict):
        payload = payload.get("songs") or payload.get("tracks") or payload.get("sections") or []
    if not isinstance(payload, list):
        return
    for item in payload:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("tracks"), list):
            yield from (t for t in item["tracks"] if isinstance(t, dict))
        else:
            yield item


def save_songs(payload: Any) -> None:
    """Save songs.json, preserving its original top-level shape (e.g. summary + songs)."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    SONGS_JSON_PATH.write_text(text, encoding="utf-8")


def update_songs_with_r2_urls(url_to_r2_urls: dict[str, str], url_to_title: dict[str, str]) -> None:
    """Update songs.json with apple_music_image_url, joined by normalized title.

    The Spotify and Apple Music CDN URLs can never be equal (different domains),
    so the join key is the song title, not image_url — apple_music_id/artist_name
    are empty in the CSV in practice, so title is the only usable join field.
    """
    title_to_r2_url: dict[str, str] = {}
    for apple_url, r2_url in url_to_r2_urls.items():
        title = url_to_title.get(apple_url)
        if title:
            title_to_r2_url.setdefault(title, r2_url)

    payload = load_songs_payload()
    if payload is None:
        print(f"[WARN] Could not read songs.json: {SONGS_JSON_PATH}")
        return
    updated_count = 0

    for track in _iter_tracks(payload):
        title = (track.get("title") or track.get("name") or "").strip()
        if not title:
            continue
        r2_url = title_to_r2_url.get(_norm_title(title))
        if r2_url:
            track["apple_music_image_url"] = r2_url
            updated_count += 1

    save_songs(payload)
    print(f"[INFO] Updated {updated_count} tracks with apple_music_image_url")
    print(f"[INFO] Saved updated songs.json: {SONGS_JSON_PATH}")


def main() -> None:
    print("[STEP] Loading Apple Music images from CSV...")
    apple_images, url_to_title = load_apple_music_images()
    print(f"Found {len(apple_images)} unique Apple Music image URLs")

    if not apple_images:
        print("[INFO] No Apple Music images found. Exiting.")
        return

    print("[STEP] Downloading and uploading images to R2...")
    client = get_s3_client()
    bucket = os.getenv("R2_BUCKET", "taylor-data")
    images_prefix = r2_keys.IMAGES_APPLE_MUSIC_PREFIX
    dry_run = os.getenv("DRY_RUN", "0").strip().lower() in ("1", "true", "yes")

    # Store mapping of URL -> R2 URL
    url_to_r2_urls = {}

    uploaded = 0
    saved_locally = 0
    failed = 0
    skipped = 0

    for url, filename in sorted(apple_images.items()):
        title = url_to_title.get(url, "")

        print(f"\nProcessing: {url[:60]}...")
        print(f"  Title: {title or '(unknown)'} | File: {filename}")
        
        # Download image
        image_data = download_image(url)
        if not image_data:
            skipped += 1
            continue
        
        # Save locally (for r2.py to upload if needed)
        if not dry_run:
            if save_image_locally(filename, image_data):
                saved_locally += 1
                print(f"  [OK] Saved locally: {LOCAL_IMAGES_DIR / filename}")
            else:
                failed += 1
                continue
        
        # Upload to R2
        r2_key = f"{images_prefix}/{filename}"
        if upload_image_to_r2(client, bucket, r2_key, image_data, dry_run):
            uploaded += 1
            
            # Build R2 public URL
            account_id = get_env("R2_ACCOUNT_ID")
            public_url = f"https://{account_id}.r2.cloudflarestorage.com/{bucket}/{r2_key}"
            url_to_r2_urls[url] = public_url
            
            print(f"  [OK] Uploaded to R2: {r2_key}")
            print(f"  Public URL: {public_url}")
        else:
            failed += 1
    
    print(f"\n[SUMMARY]")
    print(f"  Downloaded: {len(apple_images) - skipped}")
    print(f"  Saved locally: {saved_locally}")
    print(f"  Uploaded to R2: {uploaded}")
    print(f"  Failed: {failed}")
    print(f"  Skipped: {skipped}")
    print(f"  Local images dir: {LOCAL_IMAGES_DIR}")
    
    # Update songs.json with R2 URLs
    if url_to_r2_urls and not dry_run:
        print(f"\n[STEP] Updating songs.json with R2 image URLs...")
        update_songs_with_r2_urls(url_to_r2_urls, url_to_title)
    elif dry_run:
        print(f"\n[DRY-RUN] Would update songs.json with {len(url_to_r2_urls)} R2 image URLs")


if __name__ == "__main__":
    main()
