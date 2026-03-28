#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SONGS_PATH = ROOT / "db" / "discography" / "songs.json"

ERAS = [
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
    "Standalone & Extras",
    "Holiday",
    "Features",
    "Misc",
]

ERA_ALIASES = {
    "debut": "Taylor Swift",
    "taylor swift": "Taylor Swift",
    "fearless": "Fearless",
    "speak now": "Speak Now",
    "red": "Red",
    "1989": "1989",
    "reputation": "reputation",
    "lover": "Lover",
    "folklore": "folklore",
    "evermore": "evermore",
    "midnights": "Midnights",
    "ttpd": "THE TORTURED POETS DEPARTMENT",
    "the tortured poets department": "THE TORTURED POETS DEPARTMENT",
    "showgirl": "The Life of a Showgirl",
    "the life of a showgirl": "The Life of a Showgirl",
    "standalone": "Standalone & Extras",
    "standalone & extras": "Standalone & Extras",
    "holiday": "Holiday",
    "christmas": "Holiday",
}

KNOWN_TITLE_HINTS = {
    "i don't wanna live forever": "reputation",
    "all of the girls you loved before": "Lover",
    "eyes open": "Red",
    "safe & sound": "Red",
    "sweeter than fiction": "1989",
    "carolina": "Midnights",
    "today was a fairytale": "Fearless",
    "ronan": "Red",
    "you all over me": "Fearless",
    "if this was a movie": "Speak Now",
    "christmas tree farm": "Holiday",
}


class _Sentinel:
    pass


SKIP = _Sentinel()
QUIT = _Sentinel()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def backup_file(path: Path) -> None:
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    print(f"[backup] {backup.relative_to(ROOT)}")


def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    return value


def canonicalize_era(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None

    for era in ERAS:
        if raw == era:
            return era

    norm = normalize_text(raw)
    if norm in ERA_ALIASES:
        return ERA_ALIASES[norm]

    for era in ERAS:
        if normalize_text(era) == norm:
            return era

    return None


def print_era_options() -> None:
    print("Available eras:")
    for i, era in enumerate(ERAS, start=1):
        print(f"  {i}. {era}")
    print("  s. skip")
    print("  q. quit")


def parse_user_era(answer: str) -> str | None | object:
    raw = answer.strip()
    if not raw:
        return None

    lowered = raw.lower()
    if lowered == "s":
        return SKIP
    if lowered == "q":
        return QUIT

    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(ERAS):
            return ERAS[idx - 1]

    canonical = canonicalize_era(raw)
    if canonical:
        return canonical

    return None


def infer_era_for_track(track: dict[str, Any], parent_entry: dict[str, Any] | None = None) -> str | None:
    album = str(track.get("album", "")).strip()
    if album:
        exact = canonicalize_era(album)
        if exact:
            return exact

    title = normalize_text(str(track.get("title", "")))
    base_title = normalize_text(str(track.get("base_title", "")))
    title_clean = normalize_text(str(track.get("title_clean", "")))
    song_family = normalize_text(str(track.get("song_family", "")))
    text = " ".join(
        x for x in [
            title, base_title, title_clean, song_family,
            normalize_text(str(track.get("type", ""))),
            normalize_text(str(track.get("edition", ""))),
            normalize_text(str(track.get("version_tag", ""))),
        ] if x
    )

    for hint, era in KNOWN_TITLE_HINTS.items():
        if hint in text:
            return era

    if "christmas tree farm" in text or "christmas" in text:
        return "Holiday"
    if "all of the girls you loved before" in text:
        return "Lover"
    if "i don't wanna live forever" in text:
        return "reputation"
    if "carolina" in text:
        return "Midnights"
    if "sweeter than fiction" in text:
        return "1989"
    if "eyes open" in text or "safe & sound" in text or "ronan" in text:
        return "Red"
    if "today was a fairytale" in text or "you all over me" in text:
        return "Fearless"
    if "if this was a movie" in text:
        return "Speak Now"

    if parent_entry:
        parent_album = str(parent_entry.get("album", "")).strip()
        exact = canonicalize_era(parent_album)
        if exact:
            return exact

        parent_section = normalize_text(str(parent_entry.get("section", "")))
        parent_album_norm = normalize_text(parent_album)

        if parent_album_norm == "standalone & extras":
            return "Standalone & Extras"
        if "holiday" in parent_album_norm:
            return "Holiday"
        if "showgirl" in parent_album_norm:
            return "The Life of a Showgirl"
        if parent_section in {"soundtracks", "collabs_and_features", "misc_standalone", "kworb_extras"}:
            return "Standalone & Extras"

    return None


def ask_for_track_era(
    track: dict[str, Any],
    parent_entry: dict[str, Any] | None,
    suggested: str | None,
) -> str | None | object:
    print("\n" + "=" * 100)
    print("TRACK")
    print(f"title: {track.get('title')}")
    print(f"album: {track.get('album')}")
    print(f"type: {track.get('type')}")
    print(f"edition: {track.get('edition')}")
    print(f"version_tag: {track.get('version_tag')}")
    print(f"song_family: {track.get('song_family')}")
    if parent_entry and isinstance(parent_entry.get("tracks"), list):
        print(f"parent section: {parent_entry.get('section')}")
        print(f"parent album bucket: {parent_entry.get('album')}")
    if track.get("display_era"):
        print(f"current display_era: {track.get('display_era')}")
    if suggested:
        print(f"suggested display_era: {suggested}")

    print_era_options()
    print("Enter = accept suggestion")
    print()

    while True:
        answer = input("> ").strip()

        if not answer:
            if suggested:
                return suggested
            print("Enter an era, a number, s, or q.")
            continue

        parsed = parse_user_era(answer)
        if parsed is QUIT:
            return QUIT
        if parsed is SKIP:
            return None
        if isinstance(parsed, str):
            return parsed

        print("Invalid choice.")


def iter_song_targets(data: list[Any]):
    """
    Yield tuples:
    (entry_index, track_index_or_none, track_dict, parent_entry_or_none)
    """
    for entry_index, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue

        if isinstance(entry.get("tracks"), list):
            for track_index, track in enumerate(entry["tracks"]):
                if isinstance(track, dict):
                    yield entry_index, track_index, track, entry
        else:
            yield entry_index, None, entry, None


def main() -> None:
    if not SONGS_PATH.exists():
        raise FileNotFoundError(f"Missing file: {SONGS_PATH}")

    data = read_json(SONGS_PATH)
    if not isinstance(data, list):
        raise ValueError("songs.json must contain a top-level list.")

    backup_file(SONGS_PATH)

    updated = 0
    skipped = 0

    for entry_index, track_index, track, parent_entry in iter_song_targets(data):
        current = track.get("display_era")
        if isinstance(current, str) and current.strip():
            continue

        suggested = infer_era_for_track(track, parent_entry)
        choice = ask_for_track_era(track, parent_entry, suggested)

        if choice is QUIT:
            print("\n[stopped by user]")
            break

        if not isinstance(choice, str):
            skipped += 1
            continue

        track["display_era"] = choice
        updated += 1

        if track_index is None:
            print(f"[set] entry {entry_index} -> {choice}")
        else:
            print(f"[set] entry {entry_index} / track {track_index} -> {choice}")

    write_json(SONGS_PATH, data)

    print()
    print("[done]")
    print(f"  updated tracks: {updated}")
    print(f"  skipped tracks: {skipped}")
    print(f"  file: {SONGS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()