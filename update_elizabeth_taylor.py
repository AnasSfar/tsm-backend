#!/usr/bin/env python3
import json
import ssl
import sys
from pathlib import Path
from urllib.request import Request, urlopen
import re

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPO_ROOT = Path(__file__).resolve().parent
ALBUM_FILE = _REPO_ROOT / "db" / "discography" / "albums" / "the_life_of_a_showgirl.json"

def fetch_og_image(track_url: str) -> str | None:
    """Fetch og:image from Spotify track with SSL disabled."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = Request(track_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10, context=ctx) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
            if match:
                img = match.group(1)
                # Normalize to 640px
                img = img.replace("ab67616d00004851", "ab67616d0000b273")
                img = img.replace("ab67616d00001e02", "ab67616d0000b273")
                return img
    except Exception as e:
        print(f"[ERROR] {e}")
    return None

print("Updating Elizabeth Taylor - So Glamorous Cabaret Version...")
print()

data = json.loads(ALBUM_FILE.read_text(encoding="utf-8"))

for section in data.get("sections", []):
    for track in section.get("tracks", []):
        if track.get("title") != "Elizabeth Taylor - So Glamorous Cabaret Version":
            continue
        
        url = track.get("url", "")
        old_img = track.get("image_url", "")
        
        print(f"Track: {track.get('title')}")
        print(f"  URL: {url}")
        print(f"  Old image: {old_img[:60]}...")
        print()
        print("  Fetching from Spotify...", end=" ", flush=True)
        
        new_img = fetch_og_image(url)
        
        if new_img:
            print(f"✓")
            print(f"  New image: {new_img[:60]}...")
            if new_img != old_img:
                print(f"  → CHANGED! Updating...")
                track["image_url"] = new_img
                ALBUM_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  ✓ Saved to {ALBUM_FILE.name}")
            else:
                print(f"  (unchanged)")
        else:
            print(f"✗ Failed to fetch")
