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
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import boto3
import requests
from dotenv import load_dotenv

load_dotenv()

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
DB_DIR = _REPO_ROOT / "db"
SITE_DATA_DIR = _REPO_ROOT / "website" / "site" / "data"
LOCAL_IMAGES_DIR = SITE_DATA_DIR / "apple-music-images"  # Store locally for r2.py to upload

APPLE_MUSIC_CSV = DB_DIR / "apple_music_country_charts.csv"
SONGS_JSON_PATH = SITE_DATA_DIR / "songs.json"

# Configuration
DOWNLOAD_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 1
IMAGE_SIZE_MB_LIMIT = 10  # Max 10MB per image


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


def load_apple_music_images() -> dict[str, str]:
    """Load Apple Music image URLs from CSV (deduplicated by URL)."""
    images = {}  # URL -> filename
    
    if not APPLE_MUSIC_CSV.exists():
        print(f"[WARN] Apple Music CSV not found: {APPLE_MUSIC_CSV}")
        return images
    
    try:
        with open(APPLE_MUSIC_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                image_url = (row.get("image_url") or "").strip()
                if image_url and image_url not in images:
                    images[image_url] = extract_filename_from_url(image_url)
    except Exception as e:
        print(f"[ERROR] Failed to read Apple Music CSV: {e}")
    
    return images


def load_songs() -> list[dict]:
    """Load songs.json."""
    if not SONGS_JSON_PATH.exists():
        return []
    
    try:
        return json.loads(SONGS_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_songs(songs: list[dict]) -> None:
    """Save songs.json."""
    payload = json.dumps(songs, ensure_ascii=False, separators=(",", ":"))
    SONGS_JSON_PATH.write_text(payload, encoding="utf-8")


def map_urls_to_track_ids(apple_images: dict[str, str]) -> dict[str, list[str]]:
    """Map Apple Music image URLs to track IDs from songs.json."""
    url_to_track_ids = defaultdict(list)
    
    for section in load_songs():
        for track in section.get("tracks", []):
            track_id = track.get("track_id")
            if not track_id:
                continue
            
            # Check if track has an image_url that matches Apple Music
            image_url = track.get("image_url", "").strip()
            if image_url and image_url in apple_images:
                url_to_track_ids[image_url].append(track_id)
    
    return url_to_track_ids


def update_songs_with_r2_urls(url_to_r2_urls: dict[str, str]) -> None:
    """Update songs.json with apple_music_image_url field containing R2 URLs."""
    songs = load_songs()
    updated_count = 0
    
    for section in songs:
        for track in section.get("tracks", []):
            image_url = track.get("image_url", "").strip()
            if image_url and image_url in url_to_r2_urls:
                r2_url = url_to_r2_urls[image_url]
                track["apple_music_image_url"] = r2_url
                updated_count += 1
    
    save_songs(songs)
    print(f"[INFO] Updated {updated_count} tracks with apple_music_image_url")
    print(f"[INFO] Saved updated songs.json: {SONGS_JSON_PATH}")


def main() -> None:
    print("[STEP] Loading Apple Music images from CSV...")
    apple_images = load_apple_music_images()
    print(f"Found {len(apple_images)} unique Apple Music image URLs")
    
    if not apple_images:
        print("[INFO] No Apple Music images found. Exiting.")
        return
    
    print("[STEP] Downloading and uploading images to R2...")
    client = get_s3_client()
    bucket = os.getenv("R2_BUCKET", "taylor-data")
    images_prefix = os.getenv("R2_IMAGES_PREFIX", "images/apple-music")
    dry_run = os.getenv("DRY_RUN", "0").strip().lower() in ("1", "true", "yes")
    
    # Map URLs to track IDs
    url_to_track_ids = map_urls_to_track_ids(apple_images)
    
    # Store mapping of URL -> R2 URL
    url_to_r2_urls = {}
    
    uploaded = 0
    saved_locally = 0
    failed = 0
    skipped = 0
    
    for url, filename in sorted(apple_images.items()):
        track_ids = url_to_track_ids.get(url, [])
        
        print(f"\nProcessing: {url[:60]}...")
        print(f"  Tracks: {len(track_ids)} | File: {filename}")
        
        # Download image
        image_data = download_image(url)
        if not image_data:
            skipped += 1
            continue
        
        # Save locally (for r2.py to upload if needed)
        if not dry_run:
            if save_image_locally(filename, image_data):
                saved_locally += 1
                print(f"  ✓ Saved locally: {LOCAL_IMAGES_DIR / filename}")
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
            
            print(f"  ✓ Uploaded to R2: {r2_key}")
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
        update_songs_with_r2_urls(url_to_r2_urls)
    elif dry_run:
        print(f"\n[DRY-RUN] Would update songs.json with {len(url_to_r2_urls)} R2 image URLs")


if __name__ == "__main__":
    main()
