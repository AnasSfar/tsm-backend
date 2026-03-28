#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "db"
DISCO_DIR = DB_DIR / "discography"

ALBUMS_DIR = DISCO_DIR / "albums"
FEATURES_PATH = DISCO_DIR / "features.json"
MISC_PATH = DISCO_DIR / "misc.json"
SONGS_PATH = DISCO_DIR / "songs.json"

APPLE_MUSIC_SOURCES = [
    DB_DIR / "apple_music_country_charts.csv",
    DB_DIR / "apple_music_genre_charts.csv",
    DB_DIR / "apple_music_global.csv",
    DB_DIR / "apple_music_ts_top_songs.csv",
]

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

DISPLAY_ERAS = [
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
    "Standalone & Extras",
]

FEATURE_SECTION_DEFAULT = "collabs_and_features"
MISC_SECTION_DEFAULT = "misc_standalone"


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


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = value.replace("&", "and")
    value = re.sub(r"\s+", " ", value)
    return value


def slugify(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    m = re.search(r"track/([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    return url.rstrip("/").lower()


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


def ensure_album_file(album_name: str) -> Path:
    path = album_file_path(album_name)
    if not path.exists():
        write_json(
            path,
            {
                "album": album_name,
                "section_count": 0,
                "track_count": 0,
                "sections": [],
            },
        )
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

        clean_tracks = [dict(t) for t in tracks if isinstance(t, dict)]
        clean_sections.append(
            {
                "section": str(section.get("section", "")).strip(),
                "track_count": len(clean_tracks),
                "tracks": sort_tracks(clean_tracks),
                **{k: section[k] for k in ("edition", "display_section", "notes") if k in section},
            }
        )

    clean_sections = sort_sections(clean_sections)
    return {
        "album": payload.get("album", album_name) or album_name,
        "section_count": len(clean_sections),
        "track_count": sum(len(s["tracks"]) for s in clean_sections),
        "sections": clean_sections,
    }


def save_album_payload(path: Path, payload: dict[str, Any]) -> None:
    payload["sections"] = sort_sections(payload.get("sections", []))
    payload["section_count"] = len(payload["sections"])
    payload["track_count"] = sum(len(s.get("tracks", [])) for s in payload["sections"])
    for section in payload["sections"]:
        section["tracks"] = sort_tracks(section.get("tracks", []))
        section["track_count"] = len(section["tracks"])
    write_json(path, payload)


def load_bucket(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = read_json(path)
    if not isinstance(payload, list):
        return []
    clean = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        tracks = entry.get("tracks", [])
        if not isinstance(tracks, list):
            continue
        entry_copy = dict(entry)
        entry_copy["tracks"] = sort_tracks([dict(t) for t in tracks if isinstance(t, dict)])
        entry_copy["track_count"] = len(entry_copy["tracks"])
        clean.append(entry_copy)
    return sort_sections(clean)


def save_bucket(path: Path, payload: list[dict[str, Any]]) -> None:
    payload = sort_sections(payload)
    for entry in payload:
        entry["tracks"] = sort_tracks(entry.get("tracks", []))
        entry["track_count"] = len(entry["tracks"])
    write_json(path, payload)


def find_or_create_section(container: list[dict[str, Any]], incoming_section: dict[str, Any]) -> dict[str, Any]:
    target_album = str(incoming_section.get("album", "")).strip()
    target_section = str(incoming_section.get("section", "")).strip()
    target_display = incoming_section.get("display_section")
    target_edition = incoming_section.get("edition")

    for entry in container:
        if (
            str(entry.get("album", "")).strip() == target_album
            and str(entry.get("section", "")).strip() == target_section
            and (entry.get("display_section") == target_display if target_display is not None else True)
            and (entry.get("edition") == target_edition if target_edition is not None else True)
        ):
            return entry

    new_entry = {
        "album": target_album,
        "section": target_section,
        "track_count": 0,
        "tracks": [],
    }
    if target_display is not None:
        new_entry["display_section"] = target_display
    if target_edition is not None:
        new_entry["edition"] = target_edition
    container.append(new_entry)
    return new_entry


def merge_track(target_section: dict[str, Any], track: dict[str, Any]) -> bool:
    existing = target_section.setdefault("tracks", [])
    existing_keys = {
        (
            normalize_url(str(t.get("url", ""))),
            normalize_text(str(t.get("title", ""))),
            normalize_text(str(t.get("version_tag", ""))),
        )
        for t in existing
        if isinstance(t, dict)
    }
    key = (
        normalize_url(str(track.get("url", ""))),
        normalize_text(str(track.get("title", ""))),
        normalize_text(str(track.get("version_tag", ""))),
    )
    if key in existing_keys:
        return False
    existing.append(track)
    target_section["tracks"] = sort_tracks(existing)
    target_section["track_count"] = len(target_section["tracks"])
    return True


def remove_version_suffixes(title: str) -> str:
    s = title.strip()
    patterns = [
        r"\s*\(Taylor's Version\)",
        r"\s*\(From The Vault\)",
        r"\s*\(feat\.[^)]+\)",
        r"\s*\(with [^)]+\)",
        r"\s*-\s*Remix.*$",
        r"\s*-\s*Live.*$",
        r"\s*-\s*Acoustic.*$",
        r"\s*-\s*Voice Memo.*$",
        r"\s*\([^)]*live[^)]*\)",
        r"\s*\([^)]*acoustic[^)]*\)",
    ]
    for pattern in patterns:
        s = re.sub(pattern, "", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip(" -")


def extract_featured_artists(title: str) -> list[str]:
    artists: list[str] = []
    for pattern in [r"\(feat\. ([^)]+)\)", r"\(with ([^)]+)\)"]:
        m = re.search(pattern, title, flags=re.IGNORECASE)
        if m:
            raw = m.group(1)
            parts = re.split(r",|&| and ", raw)
            artists.extend([p.strip() for p in parts if p.strip()])
    return artists


def title_clean_from_title(title: str) -> str:
    base = remove_version_suffixes(title)
    base = re.sub(r"\([^)]*\)", "", base).strip()
    return re.sub(r"\s+", " ", base).strip(" -")


def compute_song_family(title: str) -> str:
    return slugify(title_clean_from_title(title))


def is_taylors_version(title: str) -> bool:
    t = title.lower()
    return "taylor's version" in t or "taylors version" in t


def infer_display_era_from_title(title: str) -> str | None:
    t = normalize_text(title)

    hints = {
        "all of the girls you loved before": "Lover",
        "i don't wanna live forever": "reputation",
        "today was a fairytale": "Fearless",
        "eyes open": "Red",
        "safe and sound": "Red",
        "safe & sound": "Red",
        "ronan": "Red",
        "sweeter than fiction": "1989",
        "carolina": "Midnights",
        "if this was a movie": "Speak Now",
        "christmas tree farm": "Holiday",
    }
    for k, v in hints.items():
        if k in t:
            return v
    return None


def resolve_album_destination(display_era: str, title: str) -> str:
    if display_era in TV_ELIGIBLE_ERAS and is_taylors_version(title):
        return f"{display_era} (Taylor's Version)"
    return display_era


def infer_type(title: str, is_album_track: bool, is_feature: bool) -> str:
    t = normalize_text(title)
    if "remix" in t:
        return "remix"
    if "live" in t:
        return "live"
    if "acoustic" in t:
        return "acoustic"
    if "voice memo" in t:
        return "voice_memo"
    if is_feature and ("feat." in title.lower() or "with " in title.lower()):
        return "alternate_version"
    if is_album_track:
        return "album_track"
    if is_feature:
        return "feature"
    return "standalone"


def infer_edition_and_section(title: str, destination_kind: str) -> tuple[str, str, str]:
    t = normalize_text(title)

    if "voice memo" in t:
        return ("extras", "voice memos", "voice_memos")
    if "remix" in t:
        return ("extras", "Extras", "remixes")
    if "acoustic" in t:
        return ("extras", "Extras", "acoustic")
    if "live" in t:
        return ("extras", "Extras", "live")
    if "from the vault" in t:
        return ("vault", "vault tracks", "vault_tracks")
    if destination_kind == "features":
        return ("extras", "collabs and features", FEATURE_SECTION_DEFAULT)
    if destination_kind == "misc":
        return ("extras", "Extras", MISC_SECTION_DEFAULT)
    return ("standard", "standard", "standard")


def infer_version_tag(title: str, featured_artists: list[str], edition: str, track_type: str) -> str:
    tags: list[str] = []
    t = title.lower()

    if featured_artists:
        tags.append("featured")
    if edition and edition != "standard":
        tags.append(slugify(edition))
    if is_taylors_version(title):
        tags.append("taylors_version")
    if "from the vault" in t:
        tags.append("vault_track")
    if "remix" in t:
        tags.append("remix")
    elif "live" in t:
        tags.append("live")
    elif "acoustic" in t:
        tags.append("acoustic")
    elif track_type:
        tags.append(slugify(track_type))

    tags = [x for x in tags if x]
    return "__".join(dict.fromkeys(tags)) if tags else slugify(track_type or "standalone")


def infer_destination(title: str, display_era: str | None) -> tuple[str, str | None]:
    lower = title.lower()

    if "(feat. taylor swift)" in lower or "(with taylor swift)" in lower:
        return ("features", None)

    if display_era and display_era in ALBUM_ERAS:
        return ("album", resolve_album_destination(display_era, title))

    if "feat." in lower or "with " in lower:
        return ("features", None)

    return ("misc", None)


def print_track_candidate(candidate: dict[str, Any]) -> None:
    print("\n" + "=" * 100)
    print("APPLE MUSIC CANDIDATE")
    print(f"title:          {candidate['title']}")
    print(f"display_era:    {candidate.get('display_era')}")
    print(f"destination:    {candidate['destination_kind']}")
    print(f"album_target:   {candidate.get('album_target')}")
    print(f"section:        {candidate['section']}")
    print(f"type:           {candidate['track']['type']}")
    print(f"edition:        {candidate['track']['edition']}")
    print(f"song_family:    {candidate['track']['song_family']}")
    print(f"version_tag:    {candidate['track']['version_tag']}")
    print(f"source:         {candidate.get('source_file')}")
    if candidate.get("apple_music_url"):
        print(f"apple url:      {candidate['apple_music_url']}")
    if candidate.get("image_url"):
        print(f"image_url:      {candidate['image_url']}")


def ask_choice() -> str:
    print("\nActions:")
    print("  y = add")
    print("  n = skip")
    print("  e = edit")
    print("  q = quit")
    while True:
        answer = input("> ").strip().lower()
        if answer in {"y", "n", "e", "q"}:
            return answer
        print("Type y / n / e / q")


def ask_display_era(current: str | None) -> str | None:
    print(f"Current display_era: {current}")
    for i, era in enumerate(DISPLAY_ERAS, start=1):
        print(f"  {i}. {era}")
    print("  0. no display era")
    while True:
        answer = input("display_era > ").strip()
        if answer == "0":
            return None
        if answer.isdigit():
            idx = int(answer)
            if 1 <= idx <= len(DISPLAY_ERAS):
                return DISPLAY_ERAS[idx - 1]
        if answer in DISPLAY_ERAS:
            return answer
        print("Invalid choice.")


def ask_destination(candidate: dict[str, Any]) -> tuple[str, str | None, str]:
    print("Destination kind:")
    print("  1. album")
    print("  2. features")
    print("  3. misc")
    while True:
        answer = input("destination > ").strip()
        if answer == "1":
            album_target = input(f"album target [{candidate.get('album_target') or ''}] > ").strip() or candidate.get("album_target") or ""
            section = input(f"section [{candidate['section']}] > ").strip() or candidate["section"]
            return ("album", album_target, section)
        if answer == "2":
            section = input(f"section [{FEATURE_SECTION_DEFAULT}] > ").strip() or FEATURE_SECTION_DEFAULT
            return ("features", None, section)
        if answer == "3":
            section = input(f"section [{MISC_SECTION_DEFAULT}] > ").strip() or MISC_SECTION_DEFAULT
            return ("misc", None, section)
        print("Invalid choice.")


def load_discography_index() -> set[str]:
    keys: set[str] = set()

    def add_track(track: dict[str, Any]) -> None:
        title = str(track.get("title", "")).strip()
        base_title = str(track.get("base_title", "")).strip()
        title_clean = str(track.get("title_clean", "")).strip()
        song_family = str(track.get("song_family", "")).strip()

        for value in (title, base_title, title_clean):
            if value:
                keys.add(normalize_text(value))
        if song_family:
            keys.add(f"family::{normalize_text(song_family)}")

    if ALBUMS_DIR.exists():
        for path in ALBUMS_DIR.glob("*.json"):
            payload = read_json(path)
            if not isinstance(payload, dict):
                continue
            sections = payload.get("sections", [])
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                for track in section.get("tracks", []):
                    if isinstance(track, dict):
                        add_track(track)

    for path in (FEATURES_PATH, MISC_PATH, SONGS_PATH):
        if not path.exists():
            continue
        payload = read_json(path)
        if not isinstance(payload, list):
            continue
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            if isinstance(entry.get("tracks"), list):
                for track in entry["tracks"]:
                    if isinstance(track, dict):
                        add_track(track)
            else:
                add_track(entry)

    return keys


def build_existing_candidate_keys(title: str) -> set[str]:
    keys = {
        normalize_text(title),
        normalize_text(remove_version_suffixes(title)),
        normalize_text(title_clean_from_title(title)),
        f"family::{normalize_text(compute_song_family(title))}",
    }
    return {k for k in keys if k}


def build_apple_candidates() -> list[dict[str, Any]]:
    existing_index = load_discography_index()
    seen_titles: set[str] = set()
    candidates: list[dict[str, Any]] = []

    for source_path in APPLE_MUSIC_SOURCES:
        rows = read_csv(source_path)
        for row in rows:
            title = (row.get("song_name") or "").strip()
            if not title:
                continue

            title_key = normalize_text(title)
            if title_key in seen_titles:
                continue

            candidate_keys = build_existing_candidate_keys(title)
            if any(k in existing_index for k in candidate_keys):
                continue

            seen_titles.add(title_key)

            display_era = infer_display_era_from_title(title)
            destination_kind, album_target = infer_destination(title, display_era)

            featured_artists = extract_featured_artists(title)
            is_feature = destination_kind == "features"
            is_album_track = destination_kind == "album"

            edition, display_section, section = infer_edition_and_section(title, destination_kind)
            if destination_kind == "album" and "deluxe" in normalize_text(title):
                edition, display_section, section = ("deluxe", "deluxe edition", "deluxe_edition")

            base_title = remove_version_suffixes(title)
            title_clean = title_clean_from_title(title)
            track_type = infer_type(title, is_album_track, is_feature)
            version_tag = infer_version_tag(title, featured_artists, edition, track_type)

            album_value = album_target if destination_kind == "album" else (display_era or "Standalone & Extras")

            candidate_track = {
                "title": title,
                "url": "",
                "type": track_type,
                "edition": edition,
                "display_section": display_section,
                "display_order": 999,
                "base_title": base_title,
                "album": album_value,
                "primary_artist": "Taylor Swift",
                "featured_artists": featured_artists,
                "artists": ["Taylor Swift", *featured_artists] if featured_artists else ["Taylor Swift"],
                "title_clean": title_clean,
                "song_family": compute_song_family(title),
                "version_tag": version_tag,
                "image_url": row.get("image_url", ""),
            }

            if display_era:
                candidate_track["display_era"] = display_era
            if row.get("url"):
                candidate_track["apple_music_url"] = row["url"]
            if row.get("apple_music_id"):
                candidate_track["apple_music_id"] = row["apple_music_id"]

            candidates.append(
                {
                    "title": title,
                    "display_era": display_era,
                    "destination_kind": destination_kind,
                    "album_target": album_target,
                    "section": section,
                    "source_file": source_path.name,
                    "apple_music_url": row.get("url", ""),
                    "image_url": row.get("image_url", ""),
                    "track": candidate_track,
                }
            )

    candidates.sort(key=lambda x: normalize_text(x["title"]))
    return candidates


def main() -> None:
    if not ALBUMS_DIR.exists():
        raise FileNotFoundError(f"Missing directory: {ALBUMS_DIR}")

    backup_file(FEATURES_PATH)
    backup_file(MISC_PATH)

    features_payload = load_bucket(FEATURES_PATH)
    misc_payload = load_bucket(MISC_PATH)

    candidates = build_apple_candidates()
    if not candidates:
        print("No missing Apple Music tracks found.")
        return

    print(f"Found {len(candidates)} missing Apple Music candidates.")

    albums_cache: dict[str, tuple[Path, dict[str, Any]]] = {}
    added = 0
    skipped = 0

    for candidate in candidates:
        print_track_candidate(candidate)
        choice = ask_choice()

        if choice == "q":
            print("\n[stopped by user]")
            break

        if choice == "n":
            skipped += 1
            continue

        if choice == "e":
            new_display_era = ask_display_era(candidate.get("display_era"))
            candidate["display_era"] = new_display_era
            candidate["track"]["display_era"] = new_display_era or ""
            destination_kind, album_target, section = ask_destination(candidate)
            candidate["destination_kind"] = destination_kind
            candidate["album_target"] = album_target
            candidate["section"] = section

            if destination_kind == "album" and album_target:
                candidate["track"]["album"] = album_target
            elif destination_kind in {"features", "misc"}:
                candidate["track"]["album"] = new_display_era or "Standalone & Extras"

            print_track_candidate(candidate)
            confirm = ask_choice()
            if confirm == "q":
                print("\n[stopped by user]")
                break
            if confirm != "y":
                skipped += 1
                continue

        if candidate["destination_kind"] == "album":
            album_name = candidate["album_target"]
            if not album_name:
                print("[skip] missing album target")
                skipped += 1
                continue

            if album_name not in albums_cache:
                path = ensure_album_file(album_name)
                albums_cache[album_name] = (path, normalize_album_payload(read_json(path), album_name))

            path, payload = albums_cache[album_name]
            incoming_section = {
                "album": album_name,
                "section": candidate["section"],
                "edition": candidate["track"].get("edition"),
                "display_section": candidate["track"].get("display_section"),
                "tracks": [],
            }
            section = find_or_create_section(payload.setdefault("sections", []), incoming_section)
            track = dict(candidate["track"])
            track["album"] = album_name
            was_added = merge_track(section, track)
            if was_added:
                added += 1
                print(f"[added] {candidate['title']} -> albums/{path.name} :: {candidate['section']}")
            else:
                print(f"[duplicate] {candidate['title']}")
            continue

        if candidate["destination_kind"] == "features":
            incoming_section = {
                "album": candidate["track"].get("album", "Standalone & Extras"),
                "section": candidate["section"],
                "edition": candidate["track"].get("edition"),
                "display_section": candidate["track"].get("display_section"),
                "tracks": [],
            }
            section = find_or_create_section(features_payload, incoming_section)
            was_added = merge_track(section, dict(candidate["track"]))
            if was_added:
                added += 1
                print(f"[added] {candidate['title']} -> features.json :: {candidate['section']}")
            else:
                print(f"[duplicate] {candidate['title']}")
            continue

        incoming_section = {
            "album": candidate["track"].get("album", "Standalone & Extras"),
            "section": candidate["section"],
            "edition": candidate["track"].get("edition"),
            "display_section": candidate["track"].get("display_section"),
            "tracks": [],
        }
        section = find_or_create_section(misc_payload, incoming_section)
        was_added = merge_track(section, dict(candidate["track"]))
        if was_added:
            added += 1
            print(f"[added] {candidate['title']} -> misc.json :: {candidate['section']}")
        else:
            print(f"[duplicate] {candidate['title']}")

    for album_name, (path, payload) in albums_cache.items():
        save_album_payload(path, payload)

    save_bucket(FEATURES_PATH, features_payload)
    save_bucket(MISC_PATH, misc_payload)

    print()
    print("[done]")
    print(f"  candidates: {len(candidates)}")
    print(f"  added: {added}")
    print(f"  skipped: {skipped}")

if __name__ == "__main__":
    main()