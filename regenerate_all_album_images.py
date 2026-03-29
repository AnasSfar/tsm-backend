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
ALBUMS_DIR = DB_DIR / "discography" / "albums"
SCRIPT = REPO_ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts" / "generate_album_update_image.py"

def get_all_albums() -> list[str]:
    """Extrage tous les noms d'albums uniques de db/discography/albums/*.json"""
    if not ALBUMS_DIR.exists():
        print(f"❌ {ALBUMS_DIR} not found")
        return []
    
    try:
        albums = {}
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            payload = json.loads(album_file.read_text(encoding="utf-8"))
            album = payload.get("album") if isinstance(payload, dict) else None
            if album and album not in albums:
                albums[album] = True
        return sorted(albums.keys())
    except Exception as e:
        print(f"❌ Error reading albums directory: {e}")
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
                print("     ✅ Generated")
            else:
                print(f"     ❌ Failed: {result.stderr.split(chr(10))[0]}")
                failed.append(album)
        except subprocess.TimeoutExpired:
            print("     ⏱️  Timeout")
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
        print("\n🎉 All images regenerated successfully!")
        sys.exit(0)

if __name__ == "__main__":
    main()
