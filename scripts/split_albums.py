#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DISCO_DIR = ROOT / "db" / "discography"
SOURCE_PATH = DISCO_DIR / "albums.json"
OUTPUT_DIR = DISCO_DIR / "albums"


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown_album"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_track_sort_key(track: dict[str, Any]) -> tuple[int, str]:
    display_order = track.get("display_order")
    if isinstance(display_order, int):
        return (display_order, track.get("title", ""))
    return (10**9, track.get("title", ""))


def get_section_sort_key(section: dict[str, Any]) -> tuple[int, str]:
    tracks = section.get("tracks", [])
    first_order = 10**9

    if isinstance(tracks, list) and tracks:
        orders = [
            t.get("display_order")
            for t in tracks
            if isinstance(t, dict) and isinstance(t.get("display_order"), int)
        ]
        if orders:
            first_order = min(orders)

    return (first_order, str(section.get("section", "")))


def normalize_section(section: dict[str, Any], album_name: str) -> dict[str, Any]:
    tracks = section.get("tracks", [])
    if not isinstance(tracks, list):
        tracks = []

    clean_tracks: list[dict[str, Any]] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_copy = dict(track)
        if not track_copy.get("album"):
            track_copy["album"] = album_name
        clean_tracks.append(track_copy)

    clean_tracks.sort(key=get_track_sort_key)

    normalized = {
        "section": section.get("section", ""),
        "track_count": len(clean_tracks),
        "tracks": clean_tracks,
    }

    for optional_key in ("edition", "display_section", "notes"):
        if optional_key in section:
            normalized[optional_key] = section[optional_key]

    return normalized


def build_album_file(album_name: str, sections: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_sections = [normalize_section(section, album_name) for section in sections]
    normalized_sections.sort(key=get_section_sort_key)

    total_tracks = sum(len(section.get("tracks", [])) for section in normalized_sections)

    return {
        "album": album_name,
        "section_count": len(normalized_sections),
        "track_count": total_tracks,
        "sections": normalized_sections,
    }


def main() -> None:
    if SOURCE_PATH.exists():
        data = read_json(SOURCE_PATH)

        if not isinstance(data, list):
            raise ValueError(
                f"Expected {SOURCE_PATH} to contain a top-level list of album sections."
            )

        by_album: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise ValueError(f"Entry #{i} is not an object.")
            album_name = entry.get("album")
            if not isinstance(album_name, str) or not album_name.strip():
                raise ValueError(f"Entry #{i} is missing a valid 'album' field.")
            by_album[album_name.strip()].append(entry)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        written_files: list[Path] = []
        for album_name in sorted(by_album.keys(), key=lambda s: s.casefold()):
            album_data = build_album_file(album_name, by_album[album_name])
            filename = f"{slugify(album_name)}.json"
            output_path = OUTPUT_DIR / filename
            write_json(output_path, album_data)
            written_files.append(output_path)

        print(f"Split complete: {len(written_files)} album file(s) written to {OUTPUT_DIR}")
        for path in written_files:
            print(f" - {path.relative_to(ROOT)}")
        return

    if OUTPUT_DIR.exists() and any(OUTPUT_DIR.glob("*.json")):
        print(f"Albums are already split in {OUTPUT_DIR}.")
        return

    raise FileNotFoundError(
        f"No legacy source at {SOURCE_PATH} and no split directory at {OUTPUT_DIR}."
    )


if __name__ == "__main__":
    main()