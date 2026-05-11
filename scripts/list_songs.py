from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DISCO_DIR = ROOT / "db" / "discography"

def print_track(track):
    title = track.get("title", "?")
    url = track.get("url", "")
    track_id = url.split("/track/")[-1] if "/track/" in url else "N/A"
    print(f"{title} | {track_id}")

# Albums (sections > tracks)
for album_file in sorted((DISCO_DIR / "albums").glob("*.json")):
    with open(album_file, encoding="utf-8") as f:
        album = json.load(f)
    for section in album.get("sections", []):
        for track in section.get("tracks", []):
            print_track(track)

# Standalone & extras (direct tracks)
with open(DISCO_DIR / "songs.json", encoding="utf-8-sig") as f:
    extras = json.load(f)
for group in extras:
    for track in group.get("tracks", []):
        print_track(track)
