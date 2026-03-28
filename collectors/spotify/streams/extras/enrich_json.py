import json
import re
import unicodedata
from pathlib import Path

DISCOGRAPHY_DIR = Path("discography")
DATA_DIR = Path("data")

FEATURE_REGEX = re.compile(
    r"\((?:feat\.|ft\.|featuring)\s+([^)]+)\)",
    re.IGNORECASE
)

SPLIT_REGEX = re.compile(r"\s*(?:,|&| x | and )\s*", re.IGNORECASE)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def slugify(value: str) -> str:
    normalized = normalize_text(value)
    return normalized.replace(" ", "_")


def extract_featured_artists(title: str) -> list[str]:
    match = FEATURE_REGEX.search(title or "")
    if not match:
        return []

    raw = match.group(1).strip()
    parts = [p.strip() for p in SPLIT_REGEX.split(raw) if p.strip()]
    return parts

def clean_title(title: str) -> str:
    cleaned = title or ""

    cleaned = re.sub(r"\((?:feat\.|ft\.|featuring)\s+([^)]+)\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\((?:from the vault)\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\((?:taylor'?s version)\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\((?:deluxe)\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\((?:acoustic)\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\((?:live)\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\((?:demo)\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\((?:remix)\)", "", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_version_tag(track: dict, title: str, title_clean: str, featured_artists: list[str]) -> str:
    type_value = str(track.get("type") or "").strip().lower()
    edition = str(track.get("edition") or "").strip().lower()
    display_section = str(track.get("display_section") or "").strip().lower()
    base_title = str(track.get("base_title") or "").strip()

    tags = []

    if featured_artists:
        tags.append("featured")

    if "remix" in title.lower() or "remix" in type_value or "remix" in display_section:
        tags.append("remix")

    if "acoustic" in title.lower() or "acoustic" in type_value:
        tags.append("acoustic")

    if "live" in title.lower() or "live" in type_value:
        tags.append("live")

    if "demo" in title.lower() or "demo" in type_value:
        tags.append("demo")

    if "deluxe" in edition or "deluxe" in display_section:
        tags.append("deluxe")

    if "vault" in title.lower() or "vault" in type_value:
        tags.append("vault")

    if "taylor's version" in title.lower() or "taylors version" in normalize_text(title):
        tags.append("taylors_version")

    if type_value and type_value not in {"song", "track"}:
        tags.append(type_value)

    if not tags:
        if title.strip() == title_clean.strip() and (not base_title or base_title.strip() == title.strip()):
            return "standard"
        return "alternate"

    seen = []
    for tag in tags:
        if tag not in seen:
            seen.append(tag)

    return "__".join(seen)


def build_song_family(track: dict, title_clean: str) -> str:
    base_title = str(track.get("base_title") or "").strip()
    title = str(track.get("title") or "").strip()

    family_source = clean_title(base_title) or title_clean or clean_title(title) or title
    return slugify(family_source)


def enrich_track(track: dict, album_name: str) -> dict:
    title = track.get("title", "").strip()
    featured_artists = extract_featured_artists(title)

    primary_artist = "Taylor Swift"
    artists = [primary_artist, *featured_artists]

    title_clean = clean_title(title)
    version_tag = extract_version_tag(track, title, title_clean, featured_artists)
    song_family = build_song_family(track, title_clean)

    track["album"] = track.get("album") or album_name
    track["primary_artist"] = primary_artist
    track["featured_artists"] = featured_artists
    track["artists"] = artists
    track["title_clean"] = title_clean
    track["song_family"] = song_family
    track["version_tag"] = version_tag

    return track


def process_file(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))

    tracks = data.get("tracks")
    if not isinstance(tracks, list):
        return

    album_name = str(data.get("album") or "").strip()

    for track in tracks:
        if isinstance(track, dict):
            enrich_track(track, album_name)

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Updated: {path}")


def enrich_song_entry(song: dict) -> dict:
    title = str(song.get("title") or "").strip()
    featured_artists = extract_featured_artists(title)

    primary_artist = "Taylor Swift"
    artists = [primary_artist, *featured_artists]

    title_clean = clean_title(title)

    appearances = song.get("appearances") or []
    first_appearance = appearances[0] if appearances and isinstance(appearances[0], dict) else {}

    pseudo_track = {
        "title": title,
        "type": song.get("type") or first_appearance.get("type") or "",
        "edition": song.get("edition") or first_appearance.get("edition") or "",
        "display_section": song.get("display_section") or first_appearance.get("display_section") or "",
        "base_title": song.get("base_title") or first_appearance.get("base_title") or title,
        "album": song.get("primary_album") or first_appearance.get("album") or "",
    }

    version_tag = extract_version_tag(pseudo_track, title, title_clean, featured_artists)
    song_family = build_song_family(pseudo_track, title_clean)

    song["primary_artist"] = primary_artist
    song["featured_artists"] = featured_artists
    song["artists"] = artists
    song["title_clean"] = title_clean
    song["song_family"] = song_family
    song["version_tag"] = version_tag

    return song


def process_songs_json():
    path = DATA_DIR / "songs.json"
    if not path.exists():
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    songs = data.get("songs", [])

    if not isinstance(songs, list):
        return

    for song in songs:
        if isinstance(song, dict):
            enrich_song_entry(song)

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Updated: {path}")


def main() -> None:
    files = sorted(DISCOGRAPHY_DIR.rglob("*.json"))

    for path in files:
        process_file(path)

    process_songs_json()


if __name__ == "__main__":
    main()