#!/usr/bin/env python3
"""
Régénère les images de mise à jour d'album pour TOUS les albums disponibles.
"""
import json
import sys
from pathlib import Path
import subprocess

REPO_ROOT = Path(__file__).parent
DB_DIR = REPO_ROOT / "db"
ALBUMS_JSON = DB_DIR / "discography" / "albums.json"
SCRIPT = REPO_ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts" / "generate_album_update_image.py"

def get_all_albums() -> list[str]:
    """Extrage tous les noms d'albums uniques de albums.json"""
    if not ALBUMS_JSON.exists():
        print(f"❌ {ALBUMS_JSON} not found")
        return []
    
    try:
        data = json.loads(ALBUMS_JSON.read_text(encoding="utf-8"))
        albums = {}
        for section in data:
            album = section.get("album")
            if album and album not in albums:
                albums[album] = True
        return sorted(albums.keys())
    except Exception as e:
        print(f"❌ Error reading albums.json: {e}")
        return []

def main():
    albums = get_all_albums()
    if not albums:
        print("❌ No albums found")
        sys.exit(1)
    
    print(f"📚 Found {len(albums)} album(s):\n")
    for album in albums:
        print(f"  • {album}")
    
    print(f"\n{'='*70}")
    print(f"🎨 Regenerating images for {len(albums)} albums...\n")
    
    failed = []
    for i, album in enumerate(albums, 1):
        print(f"[{i}/{len(albums)}] Generating: {album}...")
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), album],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode == 0:
                print(f"     ✅ Generated")
            else:
                print(f"     ❌ Failed: {result.stderr.split(chr(10))[0]}")
                failed.append(album)
        except subprocess.TimeoutExpired:
            print(f"     ⏱️  Timeout")
            failed.append(album)
        except Exception as e:
            print(f"     ❌ Error: {e}")
            failed.append(album)
    
    print(f"\n{'='*70}")
    print(f"✅ Complete: {len(albums) - len(failed)}/{len(albums)} successfully generated")
    
    if failed:
        print(f"\n❌ Failed albums ({len(failed)}):")
        for album in failed:
            print(f"  • {album}")
        sys.exit(1)
    else:
        print(f"\n🎉 All images regenerated successfully!")
        sys.exit(0)

if __name__ == "__main__":
    main()
