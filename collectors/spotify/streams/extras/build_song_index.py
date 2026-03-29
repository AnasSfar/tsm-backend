import json
import re
from pathlib import Path

_REPO_ROOT  = Path(__file__).resolve().parents[3]
_DB_ROOT    = _REPO_ROOT / "db"
DISCOGRAPHY = _DB_ROOT / "discography"
ALBUMS_DIR  = DISCOGRAPHY / "albums"
SONG_DIR    = _DB_ROOT / "songs"

SONG_DIR.mkdir(parents=True, exist_ok=True)

def get_track_id(url):
    m = re.search(r"track/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None

songs = {}

all_sections = []
if ALBUMS_DIR.exists():
    for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            payload = json.loads(album_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        album_name = payload.get("album", "") if isinstance(payload, dict) else ""
        for section in (payload.get("sections", []) if isinstance(payload, dict) else []):
            if not isinstance(section, dict):
                continue
            item = dict(section)
            if not item.get("album"):
                item["album"] = album_name
            all_sections.append(item)

songs_file = DISCOGRAPHY / "songs.json"
if songs_file.exists():
    all_sections.extend(json.loads(songs_file.read_text(encoding="utf-8")))

for data in all_sections:
    album = data.get("album")
    section = data.get("section")

    for track in data.get("tracks", []):
        title = track.get("title", "")
        url = track.get("url") or track.get("spotify_url", "")

        track_id = get_track_id(url)
        if not track_id:
            continue

        if track_id not in songs:
            songs[track_id] = {
                "title": title,
                "spotify_url": url,
                "streams": None,
                "daily_streams": None,
                "last_updated": None,
                "appearances": []
            }

        songs[track_id]["appearances"].append({
            "album": album,
            "section": section
        })

for track_id, song in songs.items():
    slug = re.sub(r"[^a-z0-9]+", "_", song["title"].lower()).strip("_")
    path = SONG_DIR / f"{slug}.json"

    path.write_text(
        json.dumps(song, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

print(f"{len(songs)} songs indexed")