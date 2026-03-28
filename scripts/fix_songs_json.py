#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DISCO_DIR = ROOT / "db" / "discography"
SONGS_PATH = DISCO_DIR / "songs.json"
ALBUMS_DIR = DISCO_DIR / "albums"
FEATURES_PATH = DISCO_DIR / "features.json"
MISC_PATH = DISCO_DIR / "misc.json"

ALBUM_ERAS = {
    "Taylor Swift",
    "Fearless",
    "Speak Now",
    "Red",
    "1989",
    "reputation",
    "Lover",
    "folklore",
    "evermore",
    "Midnights",
    "THE TORTURED POETS DEPARTMENT",
    "The Life of a Showgirl",
    "Holiday",
}

TV_ELIGIBLE_ERAS = {
    "Taylor Swift",
    "Fearless",
    "Speak Now",
    "Red",
    "1989",
    "reputation",
}

AUTO_MOVE_SECTIONS = {
    "standard",
    "standard_edition",
    "deluxe",
    "deluxe_edition",
    "platinum_edition",
    "target_edition",
    "bonus_tracks",
    "vault_tracks",
    "from_the_vault",
    "voice_memos",
    "remixes",
    "remix",
    "acoustic",
    "acoustic_versions",
    "live",
    "live_versions",
    "bonus_versions",
    "alternate_versions",
    "alternate_version",
    "extras",
}

AUTO_KEEP_SECTIONS = {
    "standalone",
    "collabs_and_features",
    "misc_standalone",
    "soundtracks",
    "taylor_versions_standalone",
    "kworb_extras",
}

AUTO_MOVE_TYPES = {
    "album_track",
    "bonus_track",
    "vault_track",
    "remix",
    "live",
    "acoustic",
    "alternate_version",
    "demo",
    "karaoke",
    "instrumental",
}

AUTO_KEEP_TYPES = {
    "standalone",
    "feature",
    "featured",
    "collaboration",
    "collab",
    "soundtrack",
    "single",
    "holiday",
}

FEATURE_SECTION_HINTS = {
    "collabs_and_features",
}

FEATURE_TYPE_HINTS = {
    "feature",
    "featured",
    "collaboration",
    "collab",
}

YES_VALUES = {"y", "yes", "o", "oui"}
NO_VALUES = {"n", "no", "non"}
ALWAYS_MOVE_VALUES = {"a", "all"}
ALWAYS_KEEP_VALUES = {"k", "keepall"}


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_name_token(value: Any) -> str:
    return normalize_text(str(value or "")).replace(" ", "_")


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    m = re.search(r"track/([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    return url.rstrip("/").lower()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def backup_file(path: Path) -> None:
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        print(f"[backup] {backup.relative_to(ROOT)}")


def track_identity(track: dict[str, Any]) -> tuple[str, str, str]:
    return (
        normalize_url(str(track.get("url", ""))),
        normalize_text(str(track.get("title", ""))),
        normalize_text(str(track.get("version_tag", ""))),
    )


def sort_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(track: dict[str, Any]) -> tuple[int, str]:
        order = track.get("display_order")
        if not isinstance(order, int):
            order = 10**9
        return (order, str(track.get("title", "")).casefold())

    return sorted(tracks, key=key)


def sort_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def section_key(section: dict[str, Any]) -> tuple[int, str]:
        tracks = section.get("tracks", [])
        best = 10**9
        for t in tracks:
            order = t.get("display_order")
            if isinstance(order, int):
                best = min(best, order)
        return (best, str(section.get("section", "")).casefold())

    return sorted(sections, key=section_key)


def album_file_path(album_name: str) -> Path:
    return ALBUMS_DIR / f"{slugify(album_name)}.json"


def album_exists(album_name: str) -> bool:
    return album_file_path(album_name).exists()


def ensure_album_file(album_name: str) -> Path:
    path = album_file_path(album_name)
    if not path.exists():
        payload = {
            "album": album_name,
            "section_count": 0,
            "track_count": 0,
            "sections": [],
        }
        write_json(path, payload)
        print(f"[create] {path.relative_to(ROOT)}")
    return path


def normalize_album_payload(payload: dict[str, Any], album_name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}

    sections = payload.get("sections", [])
    if not isinstance(sections, list):
        sections = []

    clean_sections: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue

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

        clean_section = {
            "section": str(section.get("section", "")).strip(),
            "track_count": len(clean_tracks),
            "tracks": sort_tracks(clean_tracks),
        }

        for opt in ("edition", "display_section", "notes"):
            if opt in section:
                clean_section[opt] = section[opt]

        clean_sections.append(clean_section)

    clean_sections = sort_sections(clean_sections)

    return {
        "album": payload.get("album", album_name) or album_name,
        "section_count": len(clean_sections),
        "track_count": sum(len(s["tracks"]) for s in clean_sections),
        "sections": clean_sections,
    }


def load_album_file(album_name: str) -> tuple[Path, dict[str, Any]]:
    path = ensure_album_file(album_name)
    payload = normalize_album_payload(read_json(path), album_name)
    return path, payload


def save_album_file(path: Path, payload: dict[str, Any]) -> None:
    payload["sections"] = sort_sections(payload.get("sections", []))
    payload["section_count"] = len(payload["sections"])
    payload["track_count"] = sum(len(s.get("tracks", [])) for s in payload["sections"])
    for section in payload["sections"]:
        section["tracks"] = sort_tracks(section.get("tracks", []))
        section["track_count"] = len(section["tracks"])
    write_json(path, payload)


def normalize_bucket_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []

    clean_entries: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue

        if not isinstance(entry.get("tracks"), list):
            continue

        tracks = [t for t in entry["tracks"] if isinstance(t, dict)]
        entry_copy = dict(entry)
        entry_copy["tracks"] = sort_tracks(tracks)
        entry_copy["track_count"] = len(entry_copy["tracks"])
        clean_entries.append(entry_copy)

    return sort_sections(clean_entries)


def load_bucket_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return normalize_bucket_payload(read_json(path))


def save_bucket_file(path: Path, payload: list[dict[str, Any]]) -> None:
    payload = normalize_bucket_payload(payload)
    write_json(path, payload)


def find_or_create_section(container: list[dict[str, Any]], incoming_section: dict[str, Any]) -> dict[str, Any]:
    target_album = str(incoming_section.get("album", "")).strip()
    target_name = str(incoming_section.get("section", "")).strip()
    target_display = incoming_section.get("display_section")
    target_edition = incoming_section.get("edition")

    for section in container:
        same_album = str(section.get("album", "")).strip() == target_album
        same_name = str(section.get("section", "")).strip() == target_name
        same_display = section.get("display_section") == target_display if target_display else True
        same_edition = section.get("edition") == target_edition if target_edition else True
        if same_album and same_name and same_display and same_edition:
            return section

    new_section = {
        "album": target_album,
        "section": target_name,
        "track_count": 0,
        "tracks": [],
    }
    if target_display is not None:
        new_section["display_section"] = target_display
    if target_edition is not None:
        new_section["edition"] = target_edition
    if "notes" in incoming_section:
        new_section["notes"] = incoming_section["notes"]

    container.append(new_section)
    return new_section


def find_or_create_album_section(album_payload: dict[str, Any], incoming_section: dict[str, Any]) -> dict[str, Any]:
    return find_or_create_section(album_payload.setdefault("sections", []), incoming_section)


def merge_tracks(target_section: dict[str, Any], tracks: list[dict[str, Any]]) -> tuple[int, int]:
    existing = target_section.setdefault("tracks", [])
    existing_ids = {track_identity(t) for t in existing if isinstance(t, dict)}

    moved = 0
    skipped = 0

    for track in tracks:
        if not isinstance(track, dict):
            skipped += 1
            continue

        key = track_identity(track)
        if key in existing_ids:
            skipped += 1
            continue

        existing.append(track)
        existing_ids.add(key)
        moved += 1

    target_section["tracks"] = sort_tracks(existing)
    target_section["track_count"] = len(target_section["tracks"])
    return moved, skipped


def dedupe_song_entries(entries: list[Any]) -> tuple[list[Any], int]:
    duplicates_removed = 0
    deduped_entries: list[Any] = []
    seen_flat: set[tuple[str, str, str]] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            deduped_entries.append(entry)
            continue

        if isinstance(entry.get("tracks"), list):
            original_tracks = entry["tracks"]
            kept_tracks: list[dict[str, Any]] = []
            seen_tracks: set[tuple[str, str, str]] = set()

            for track in original_tracks:
                if not isinstance(track, dict):
                    continue
                key = track_identity(track)
                if key in seen_tracks:
                    duplicates_removed += 1
                    continue
                seen_tracks.add(key)
                kept_tracks.append(track)

            entry_copy = dict(entry)
            entry_copy["tracks"] = sort_tracks(kept_tracks)
            entry_copy["track_count"] = len(entry_copy["tracks"])

            if entry_copy["tracks"]:
                deduped_entries.append(entry_copy)
            continue

        key = track_identity(entry)
        if key in seen_flat:
            duplicates_removed += 1
            continue

        seen_flat.add(key)
        deduped_entries.append(entry)

    return deduped_entries, duplicates_removed


def section_preview(entry: dict[str, Any], limit: int = 5) -> str:
    tracks = entry.get("tracks", [])
    titles = [str(t.get("title", "")) for t in tracks[:limit] if isinstance(t, dict)]
    suffix = "" if len(tracks) <= limit else f" ... (+{len(tracks) - limit} more)"
    joined = " | ".join(titles) if titles else "no tracks"
    return joined + suffix


def track_preview(track: dict[str, Any]) -> str:
    return (
        f"title={track.get('title')} | display_era={track.get('display_era')} | "
        f"type={track.get('type')} | edition={track.get('edition')} | version_tag={track.get('version_tag')}"
    )


def is_taylors_version_track(track: dict[str, Any]) -> bool:
    haystack = " | ".join(
        [
            str(track.get("title", "")),
            str(track.get("base_title", "")),
            str(track.get("title_clean", "")),
            str(track.get("version_tag", "")),
            str(track.get("edition", "")),
        ]
    ).lower()
    return "taylor's version" in haystack or "taylors version" in haystack


def resolve_album_destination(track: dict[str, Any]) -> str | None:
    display_era = str(track.get("display_era", "")).strip()
    if display_era not in ALBUM_ERAS:
        return None

    if display_era in TV_ELIGIBLE_ERAS and is_taylors_version_track(track):
        return f"{display_era} (Taylor's Version)"

    return display_era


def is_feature_track(track: dict[str, Any], parent_section: dict[str, Any] | None = None) -> bool:
    title = normalize_text(str(track.get("title", "")))
    track_type = normalize_name_token(track.get("type"))
    version_tag = normalize_name_token(track.get("version_tag"))
    section = normalize_name_token(track.get("section"))
    parent_section_name = normalize_name_token(parent_section.get("section")) if parent_section else ""

    if parent_section_name in FEATURE_SECTION_HINTS:
        return True
    if section in FEATURE_SECTION_HINTS:
        return True
    if track_type in FEATURE_TYPE_HINTS or version_tag in FEATURE_TYPE_HINTS:
        return True
    if "(feat." in title or "(with " in title:
        return True

    primary_artist = normalize_text(str(track.get("primary_artist", "")))
    artists = [normalize_text(str(a)) for a in track.get("artists", []) if isinstance(a, str)]
    featured_artists = [normalize_text(str(a)) for a in track.get("featured_artists", []) if isinstance(a, str)]

    if primary_artist and primary_artist != "taylor swift":
        if "taylor swift" in artists or "taylor swift" in featured_artists:
            return True

    return False


def classify_section(entry: dict[str, Any]) -> str:
    tracks = [t for t in entry.get("tracks", []) if isinstance(t, dict)]
    if not tracks:
        return "keep"

    album_destinations = {resolve_album_destination(track) for track in tracks}
    album_destinations.discard(None)
    if album_destinations:
        return "move"

    section = normalize_name_token(entry.get("section"))
    display_section = normalize_name_token(entry.get("display_section"))
    edition = normalize_name_token(entry.get("edition"))

    if section in AUTO_KEEP_SECTIONS or display_section in AUTO_KEEP_SECTIONS:
        return "keep"

    if all(is_feature_track(track, entry) for track in tracks):
        return "keep"

    if section in AUTO_MOVE_SECTIONS or display_section in AUTO_MOVE_SECTIONS or edition in AUTO_MOVE_SECTIONS:
        return "move"

    return "ask"


def classify_track(entry: dict[str, Any]) -> str:
    if resolve_album_destination(entry):
        return "move"

    section = normalize_name_token(entry.get("section"))
    display_section = normalize_name_token(entry.get("display_section"))
    edition = normalize_name_token(entry.get("edition"))
    track_type = normalize_name_token(entry.get("type"))
    version_tag = normalize_name_token(entry.get("version_tag"))
    title = normalize_text(str(entry.get("title", "")))

    if is_feature_track(entry):
        return "keep"

    if (
        section in AUTO_MOVE_SECTIONS
        or display_section in AUTO_MOVE_SECTIONS
        or edition in AUTO_MOVE_SECTIONS
        or track_type in AUTO_MOVE_TYPES
        or version_tag in AUTO_MOVE_TYPES
    ):
        return "move"

    if (
        section in AUTO_KEEP_SECTIONS
        or display_section in AUTO_KEEP_SECTIONS
        or track_type in AUTO_KEEP_TYPES
        or version_tag in AUTO_KEEP_TYPES
    ):
        return "keep"

    if "remix" in title or "acoustic" in title or "voice memo" in title or "live" in title:
        return "move"

    if "soundtrack" in title:
        return "keep"

    return "ask"


def ask_user(entry_kind: str, entry: dict[str, Any], decision_cache: dict[str, str]) -> str:
    section_name = normalize_name_token(entry.get("section"))
    display_section = normalize_name_token(entry.get("display_section"))
    edition = normalize_name_token(entry.get("edition"))
    track_type = normalize_name_token(entry.get("type"))
    display_era = normalize_name_token(entry.get("display_era"))

    cache_key = f"{entry_kind}|{section_name}|{display_section}|{edition}|{track_type}|{display_era}"
    if cache_key in decision_cache:
        return decision_cache[cache_key]

    print("\n" + "=" * 100)
    print("[AMBIGUOUS ENTRY]")
    print(f"album: {entry.get('album')}")
    if entry_kind == "section":
        print(f"section: {entry.get('section')}")
        print(f"display_section: {entry.get('display_section')}")
        print(f"edition: {entry.get('edition')}")
        print(f"track_count: {len(entry.get('tracks', []))}")
        print("preview:", section_preview(entry))
    else:
        print(track_preview(entry))

    print("\nMove out of songs.json?")
    print("  y = yes")
    print("  n = keep in songs.json")
    print("  a = always move entries like this")
    print("  k = always keep entries like this")

    while True:
        answer = input("> ").strip().lower()
        if answer in YES_VALUES:
            return "move"
        if answer in NO_VALUES:
            return "keep"
        if answer in ALWAYS_MOVE_VALUES:
            decision_cache[cache_key] = "move"
            return "move"
        if answer in ALWAYS_KEEP_VALUES:
            decision_cache[cache_key] = "keep"
            return "keep"
        print("Type y / n / a / k")


def build_section_from_flat_track(track: dict[str, Any]) -> dict[str, Any]:
    section_name = (
        track.get("section")
        or track.get("display_section")
        or track.get("edition")
        or track.get("type")
        or "extras"
    )
    return {
        "album": track.get("album", ""),
        "section": str(section_name).strip(),
        "edition": track.get("edition"),
        "display_section": track.get("display_section"),
        "tracks": [track],
    }


def route_track(track: dict[str, Any], parent_entry: dict[str, Any] | None = None) -> tuple[str, str | None]:
    album_destination = resolve_album_destination(track)
    if album_destination:
        return ("album", album_destination)

    if is_feature_track(track, parent_entry):
        return ("features", None)

    return ("misc", None)


def clean_track_for_album(track: dict[str, Any], album_name: str) -> dict[str, Any]:
    track_copy = dict(track)
    track_copy["album"] = album_name
    return track_copy


def move_section_tracks(
    entry: dict[str, Any],
    albums_cache: dict[str, tuple[Path, dict[str, Any]]],
    features_payload: list[dict[str, Any]],
    misc_payload: list[dict[str, Any]],
    moved_by_album: dict[str, int],
    skipped_duplicates_by_album: dict[str, int],
    moved_by_bucket: dict[str, int],
    skipped_duplicates_by_bucket: dict[str, int],
) -> None:
    tracks = [t for t in entry.get("tracks", []) if isinstance(t, dict)]
    grouped_album_tracks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_features_tracks: list[dict[str, Any]] = []
    grouped_misc_tracks: list[dict[str, Any]] = []

    for track in tracks:
        destination_kind, destination_value = route_track(track, entry)
        if destination_kind == "album":
            grouped_album_tracks[destination_value].append(clean_track_for_album(track, destination_value))
        elif destination_kind == "features":
            grouped_features_tracks.append(track)
        else:
            grouped_misc_tracks.append(track)

    for album_name, album_tracks in grouped_album_tracks.items():
        if album_name not in albums_cache:
            albums_cache[album_name] = load_album_file(album_name)

        _, album_payload = albums_cache[album_name]
        incoming_section = dict(entry)
        incoming_section["album"] = album_name
        target_section = find_or_create_album_section(album_payload, incoming_section)
        moved, skipped = merge_tracks(target_section, album_tracks)
        moved_by_album[album_name] += moved
        skipped_duplicates_by_album[album_name] += skipped

    if grouped_features_tracks:
        target_section = find_or_create_section(features_payload, entry)
        moved, skipped = merge_tracks(target_section, grouped_features_tracks)
        moved_by_bucket["features"] += moved
        skipped_duplicates_by_bucket["features"] += skipped

    if grouped_misc_tracks:
        target_section = find_or_create_section(misc_payload, entry)
        moved, skipped = merge_tracks(target_section, grouped_misc_tracks)
        moved_by_bucket["misc"] += moved
        skipped_duplicates_by_bucket["misc"] += skipped


def move_flat_track(
    track: dict[str, Any],
    albums_cache: dict[str, tuple[Path, dict[str, Any]]],
    features_payload: list[dict[str, Any]],
    misc_payload: list[dict[str, Any]],
    moved_by_album: dict[str, int],
    skipped_duplicates_by_album: dict[str, int],
    moved_by_bucket: dict[str, int],
    skipped_duplicates_by_bucket: dict[str, int],
) -> None:
    destination_kind, destination_value = route_track(track)
    synthetic_section = build_section_from_flat_track(track)

    if destination_kind == "album":
        album_name = destination_value
        if album_name not in albums_cache:
            albums_cache[album_name] = load_album_file(album_name)

        _, album_payload = albums_cache[album_name]
        synthetic_section["album"] = album_name
        target_section = find_or_create_album_section(album_payload, synthetic_section)
        moved, skipped = merge_tracks(target_section, [clean_track_for_album(track, album_name)])
        moved_by_album[album_name] += moved
        skipped_duplicates_by_album[album_name] += skipped
        return

    if destination_kind == "features":
        target_section = find_or_create_section(features_payload, synthetic_section)
        moved, skipped = merge_tracks(target_section, [track])
        moved_by_bucket["features"] += moved
        skipped_duplicates_by_bucket["features"] += skipped
        return

    target_section = find_or_create_section(misc_payload, synthetic_section)
    moved, skipped = merge_tracks(target_section, [track])
    moved_by_bucket["misc"] += moved
    skipped_duplicates_by_bucket["misc"] += skipped


def main() -> None:
    if not SONGS_PATH.exists():
        raise FileNotFoundError(f"Missing file: {SONGS_PATH}")
    if not ALBUMS_DIR.exists():
        raise FileNotFoundError(f"Missing directory: {ALBUMS_DIR}")

    songs_data = read_json(SONGS_PATH)
    if not isinstance(songs_data, list):
        raise ValueError("songs.json must contain a top-level list.")

    backup_file(SONGS_PATH)
    backup_file(FEATURES_PATH)
    backup_file(MISC_PATH)

    features_payload = load_bucket_file(FEATURES_PATH)
    misc_payload = load_bucket_file(MISC_PATH)

    albums_cache: dict[str, tuple[Path, dict[str, Any]]] = {}
    moved_by_album: dict[str, int] = defaultdict(int)
    skipped_duplicates_by_album: dict[str, int] = defaultdict(int)
    moved_by_bucket: dict[str, int] = defaultdict(int)
    skipped_duplicates_by_bucket: dict[str, int] = defaultdict(int)

    kept_entries: list[Any] = []
    decision_cache: dict[str, str] = {}

    for entry in songs_data:
        if not isinstance(entry, dict):
            kept_entries.append(entry)
            continue

        if isinstance(entry.get("tracks"), list):
            decision = classify_section(entry)
            if decision == "ask":
                decision = ask_user("section", entry, decision_cache)

            if decision != "move":
                kept_entries.append(entry)
                continue

            move_section_tracks(
                entry=entry,
                albums_cache=albums_cache,
                features_payload=features_payload,
                misc_payload=misc_payload,
                moved_by_album=moved_by_album,
                skipped_duplicates_by_album=skipped_duplicates_by_album,
                moved_by_bucket=moved_by_bucket,
                skipped_duplicates_by_bucket=skipped_duplicates_by_bucket,
            )
            continue

        decision = classify_track(entry)
        if decision == "ask":
            decision = ask_user("track", entry, decision_cache)

        if decision != "move":
            kept_entries.append(entry)
            continue

        move_flat_track(
            track=entry,
            albums_cache=albums_cache,
            features_payload=features_payload,
            misc_payload=misc_payload,
            moved_by_album=moved_by_album,
            skipped_duplicates_by_album=skipped_duplicates_by_album,
            moved_by_bucket=moved_by_bucket,
            skipped_duplicates_by_bucket=skipped_duplicates_by_bucket,
        )

    for album_name, (path, album_payload) in albums_cache.items():
        save_album_file(path, album_payload)
        print(
            f"[album] {path.relative_to(ROOT)} | moved={moved_by_album[album_name]} | "
            f"duplicates_skipped={skipped_duplicates_by_album[album_name]}"
        )

    save_bucket_file(FEATURES_PATH, features_payload)
    save_bucket_file(MISC_PATH, misc_payload)

    deduped_kept_entries, songs_duplicates_removed = dedupe_song_entries(kept_entries)
    write_json(SONGS_PATH, deduped_kept_entries)

    total_album_moved = sum(moved_by_album.values())
    total_album_skipped = sum(skipped_duplicates_by_album.values())
    total_bucket_moved = sum(moved_by_bucket.values())
    total_bucket_skipped = sum(skipped_duplicates_by_bucket.values())

    print()
    print("[done]")
    print(f"  moved tracks to albums: {total_album_moved}")
    print(f"  duplicate tracks skipped while merging into albums: {total_album_skipped}")
    print(f"  moved tracks to features/misc: {total_bucket_moved}")
    print(f"  duplicate tracks skipped while merging into features/misc: {total_bucket_skipped}")
    print(f"  duplicate entries removed from songs.json: {songs_duplicates_removed}")
    print(f"  updated songs.json: {SONGS_PATH.relative_to(ROOT)}")
    print(f"  updated features.json: {FEATURES_PATH.relative_to(ROOT)}")
    print(f"  updated misc.json: {MISC_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()