#!/usr/bin/env python3
"""
update_all_track_images.py
==========================
Scrape and update image URLs for ALL tracks from Spotify (albumss+ songs.json).
Runs daily to ensure new versions/music videos are detected with updated covers.

Usage:
  python update_all_track_images.py          # scrape and update
  python update_all_track_images.py --force  # re-scrape everything, no cache
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR   = Path(__file__).resolve().parent
_REPO_ROOT    = _SCRIPT_DIR.parents[2]
DISCO_DIR     = _REPO_ROOT / "db" / "discography"
ALBUMS_DIR    = DISCO_DIR / "albums"
CACHE_FILE    = DISCO_DIR / ".image_url_cache.json"

FORCE = "--force" in sys.argv

def load_cache() -> dict:
    """Load last-updated times for each track."""
    if CACHE_FILE.exists() and not FORCE:
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_cache(cache: dict) -> None:
    """Save last-updated times."""
    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def should_refresh_image(track_id: str, track_type: str, cache: dict) -> bool:
    """Decide if we should re-scrape this track's image.
    
    Rules:
      - Singles/alternate versions: daily refresh (may change with new versions)
      - Regular album tracks: refresh every 30 days
    """
    if FORCE:
        return True
    if track_id not in cache:
        return True  # Never seen before
    try:
        last_time = cache[track_id]
        last_dt = datetime.fromisoformat(last_time)
        age_days = (datetime.now() - last_dt).days
        
        # Refresh frequently for singles and versions (may get updated with new releases)
        if track_type in ("standalone", "alternate_version"):
            return age_days >= 1  # Refresh daily
        
        # Regular tracks: less frequent updates
        return age_days >= 30
    except Exception:
        return True

def fetch_image_url(spotify_url: str) -> str | None:
    """Fetch og:image from Spotify track page."""
    try:
        req = Request(spotify_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            # Look for og:image meta tag
            match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
            if match:
                url = match.group(1).strip()
                # normalize to 640px size
                url = url.replace("ab67616d00004851", "ab67616d0000b273")
                url = url.replace("ab67616d00001e02", "ab67616d0000b273")
                return url
    except Exception as e:
        print(f"    [ERROR] {spotify_url}: {e}")
    return None

def update_track_images() -> int:
    """Update image URLs for albums/*.json + songs.json tracks."""
    cache = load_cache()
    updated_count = 0
    
    all_files = []
    updated_files = set()

    # Process albums/*.json
    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json")):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                if not isinstance(section, dict):
                    continue
                for track in section.get("tracks", []):
                    spotify_url = track.get("url", "").strip()
                    if not spotify_url:
                        continue
                    
                    # Extract track ID
                    m = re.search(r"/track/([A-Za-z0-9]+)", spotify_url)
                    if not m:
                        continue
                    
                    track_id = m.group(1)
                    track_type = track.get("type", "album")
                    
                    if should_refresh_image(track_id, track_type, cache):
                        print(f"  Scraping {track.get('title', '?')} ... ", end="", flush=True)
                        new_img = fetch_image_url(spotify_url)
                        if new_img:
                            old_img = track.get("image_url", "")
                            if old_img != new_img:
                                track["image_url"] = new_img
                                updated_count += 1
                                updated_files.add(album_file)
                                print(f"✓ updated")
                            else:
                                print(f"✓ unchanged")
                            cache[track_id] = datetime.now().isoformat()
                        else:
                            print(f"✗ (no image)")
                        time.sleep(0.2)

    # Process songs.json (standalone tracks)
    songs_file = DISCO_DIR / "songs.json"
    if songs_file.exists():
        try:
            songs_data = json.loads(songs_file.read_text(encoding="utf-8"))
            for section in songs_data if isinstance(songs_data, list) else []:
                for track in section.get("tracks", []) if isinstance(section, dict) else []:
                    spotify_url = track.get("url", "").strip()
                    if not spotify_url:
                        continue
                    
                    m = re.search(r"/track/([A-Za-z0-9]+)", spotify_url)
                    if not m:
                        continue
                    
                    track_id = m.group(1)
                    track_type = track.get("type", "album")
                    if should_refresh_image(track_id, track_type, cache):
                        print(f"  Scraping {track.get('title', '?')} ... ", end="", flush=True)
                        new_img = fetch_image_url(spotify_url)
                        if new_img:
                            old_img = track.get("image_url", "")
                            if old_img != new_img:
                                track["image_url"] = new_img
                                updated_count += 1
                                updated_files.add(songs_file)
                                print(f"✓ updated")
                            else:
                                print(f"✓ unchanged")
                            cache[track_id] = datetime.now().isoformat()
                        else:
                            print(f"✗ (no image)")
                        time.sleep(0.2)
        except Exception:
            pass

    # Save updated files
    for file_path in updated_files:
        try:
            if file_path == songs_file:
                payload = songs_data
            else:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            
            file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  [SAVED] {file_path.name}")
        except Exception as e:
            print(f"  [ERROR] Failed to save {file_path.name}: {e}")

    save_cache(cache)
    return updated_count

if __name__ == "__main__":
    print("Updating track image URLs from Spotify...")
    print(f"(Force rescrape: {FORCE})")
    print()
    
    count = update_track_images()
    
    print()
    print(f"✓ Updated {count} image URL(s)")
