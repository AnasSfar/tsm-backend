#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DISCOGRAPHY_DIR = ROOT / "db" / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
SONGS_PATH = DISCOGRAPHY_DIR / "songs.json"
FEATURES_PATH = DISCOGRAPHY_DIR / "features.json"
MISC_PATH = DISCOGRAPHY_DIR / "misc.json"
DEFAULT_REPORT_CSV = ROOT / "data" / "track_flags_audit.csv"
DEFAULT_REPORT_JSON = ROOT / "data" / "track_flags_audit.json"

TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")
DEFAULT_ARTIST = "Taylor Swift"

TAG_ORDER = (
    "solo",
    "feature",
    "collab",
    "single",
    "album_track",
    "standalone",
    "soundtrack",
    "remix",
    "live",
    "acoustic",
    "karaoke",
    "demo",
    "voice_memo",
    "track_by_track",
    "taylor_version",
    "from_the_vault",
    "alternate_version",
    "christmas",
)

TAG_FIELDS = ("filter_tags", "filter_tag_sources")


@dataclass
class TrackLocation:
    path: Path
    payload: Any
    track: dict[str, Any]
    track_id: str


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def backup(path: Path) -> None:
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = path.with_suffix(path.suffix + f".track-flags-{stamp}.bak")
        shutil.copy2(path, target)
        print(f"[backup] {target.relative_to(ROOT)}")


def extract_track_id(url: str | None) -> str:
    match = TRACK_ID_RE.search(url or "")
    return match.group(1) if match else ""


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return text.casefold()


def compact_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", norm(value)).strip("_")


def iter_tracks_from_payload(path: Path, payload: Any) -> list[TrackLocation]:
    if isinstance(payload, list):
        sections = payload
    elif isinstance(payload, dict):
        sections = payload.get("sections") or []
    else:
        sections = []

    locations: list[TrackLocation] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        for track in section.get("tracks") or []:
            if not isinstance(track, dict):
                continue
            track_id = extract_track_id(track.get("url") or track.get("spotify_url"))
            if track_id:
                locations.append(TrackLocation(path, payload, track, track_id))
    return locations


def load_discography_locations() -> tuple[dict[Path, Any], list[TrackLocation]]:
    payloads: dict[Path, Any] = {}
    locations: list[TrackLocation] = []

    for path in (SONGS_PATH, FEATURES_PATH, MISC_PATH):
        if not path.exists():
            continue
        payload = read_json(path)
        payloads[path] = payload
        locations.extend(iter_tracks_from_payload(path, payload))

    for path in sorted(ALBUMS_DIR.glob("*.json"), key=lambda item: item.name.casefold()):
        payload = read_json(path)
        payloads[path] = payload
        locations.extend(iter_tracks_from_payload(path, payload))

    return payloads, locations


def text_blob(track: dict[str, Any]) -> str:
    values = [
        track.get("title"),
        track.get("title_clean"),
        track.get("base_title"),
        track.get("album"),
        track.get("section"),
        track.get("primary_section"),
        track.get("display_section"),
        track.get("edition"),
        track.get("version_tag"),
        track.get("type"),
    ]
    return " | ".join(norm(value) for value in values if value)


def artist_names(track: dict[str, Any]) -> list[str]:
    artists = track.get("artists") or []
    if not isinstance(artists, list):
        artists = []
    primary = track.get("primary_artist")
    names = [str(name).strip() for name in artists if str(name).strip()]
    if primary and str(primary).strip() not in names:
        names.insert(0, str(primary).strip())
    return names


def non_default_artists(track: dict[str, Any]) -> list[str]:
    return [name for name in artist_names(track) if norm(name) != norm(DEFAULT_ARTIST)]


def add_tag(
    tags: set[str],
    sources: dict[str, str],
    tag: str,
    reason: str,
    *,
    confidence: str = "high",
) -> None:
    tags.add(tag)
    sources.setdefault(tag, f"{confidence}:{reason}")


def infer_tags(track: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    tags: set[str] = set()
    sources: dict[str, str] = {}
    blob = text_blob(track)
    title = norm(track.get("title"))
    track_type = compact_key(track.get("type"))
    section = compact_key(track.get("section") or track.get("primary_section"))
    display_section = compact_key(track.get("display_section"))
    edition = compact_key(track.get("edition"))
    album_type = compact_key(track.get("album_type"))
    primary_artist = norm(track.get("primary_artist"))
    featured_artists = track.get("featured_artists") or []
    if not isinstance(featured_artists, list):
        featured_artists = []
    featured_names = [str(name).strip() for name in featured_artists if str(name).strip()]
    other_artists = non_default_artists(track)

    if "taylor's version" in title or section.endswith("_tv") or "_tv" in section:
        add_tag(tags, sources, "taylor_version", "title_or_section")
    if "from the vault" in blob or "vault" in section or "vault" in edition:
        add_tag(tags, sources, "from_the_vault", "title_section_or_edition")

    title_names_default_as_feature = bool(re.search(r"\bfeat(?:\.|uring)?\s+taylor swift\b", title))
    title_has_feature_credit = bool(re.search(r"\bfeat(?:\.|uring)?\b", title))

    if track_type == "feature" or primary_artist and primary_artist != norm(DEFAULT_ARTIST) or title_names_default_as_feature:
        add_tag(tags, sources, "feature", "type_or_primary_artist")
    elif any(norm(name) != norm(DEFAULT_ARTIST) for name in featured_names) or other_artists or title_has_feature_credit:
        add_tag(tags, sources, "collab", "artists_or_featured_artists")

    if "feature" not in tags and "collab" not in tags and not other_artists:
        add_tag(tags, sources, "solo", "artist_credit")

    if album_type == "single":
        add_tag(tags, sources, "single", "spotify_album_type")
    elif section in {"standalone", "taylor_versions_standalone"} or track_type == "standalone":
        add_tag(tags, sources, "standalone", "section_or_type", confidence="medium")

    album_track_sections = {
        "standard_edition",
        "standard_edition_tv",
        "deluxe_edition",
        "deluxe_edition_tv",
        "original_edition",
        "3am_edition",
        "anthology",
        "from_the_vault",
        "from_the_vault_tv",
    }
    if section in album_track_sections or display_section in {
        "standard_edition",
        "other_editions",
        "from_the_vault",
        "3am_edition",
        "the_anthology",
    }:
        add_tag(tags, sources, "album_track", "section_or_display_section")

    if (
        "soundtrack" in blob
        or "from the motion picture" in blob
        or any(name in blob for name in ("cats", "hunger games", "fifty shades", "valentine's day", "one chance"))
    ):
        add_tag(tags, sources, "soundtrack", "title_album_or_section")

    if track_type == "remix" or re.search(r"\b(remix|mix)\b", title):
        add_tag(tags, sources, "remix", "type_or_title")
    if track_type == "live" or re.search(r"\blive\b|live/", title) or "live_from" in section or "live" in display_section:
        add_tag(tags, sources, "live", "type_title_or_section")
    if "acoustic" in blob:
        add_tag(tags, sources, "acoustic", "title_or_section")
    if "karaoke" in blob:
        add_tag(tags, sources, "karaoke", "title_or_section")
    if "demo" in blob:
        add_tag(tags, sources, "demo", "title_or_section")
    if "voice memo" in blob or "voice_memo" in blob:
        add_tag(tags, sources, "voice_memo", "title_or_section")
    if "track by track" in blob or "track_by_track" in blob:
        add_tag(tags, sources, "track_by_track", "title_or_section")
    if track_type == "alternate_version" or (
        "version" in title and not {"live", "remix", "karaoke", "taylor_version"} & tags
    ):
        add_tag(tags, sources, "alternate_version", "type_or_title", confidence="medium")
    if any(word in blob for word in ("christmas", "holiday collection", "holiday_collection")):
        add_tag(tags, sources, "christmas", "title_album_or_section")

    ordered = [tag for tag in TAG_ORDER if tag in tags]
    return ordered, {tag: sources[tag] for tag in ordered}


def aggregate_track_tags(
    suggestions: dict[str, list[tuple[list[str], dict[str, str]]]]
) -> dict[str, tuple[list[str], dict[str, str]]]:
    aggregated: dict[str, tuple[list[str], dict[str, str]]] = {}
    for track_id, values in suggestions.items():
        tags: set[str] = set()
        sources: dict[str, str] = {}
        for suggested_tags, suggested_sources in values:
            tags.update(suggested_tags)
            for tag, source in suggested_sources.items():
                sources.setdefault(tag, source)

        if "feature" in tags or "collab" in tags:
            tags.discard("solo")
            sources.pop("solo", None)

        ordered = [tag for tag in TAG_ORDER if tag in tags]
        aggregated[track_id] = (ordered, {tag: sources[tag] for tag in ordered if tag in sources})
    return aggregated

def row_for(location: TrackLocation, tags: list[str], sources: dict[str, str]) -> dict[str, str]:
    track = location.track
    return {
        "file": str(location.path.relative_to(ROOT)),
        "track_id": location.track_id,
        "title": str(track.get("title") or ""),
        "album": str(track.get("album") or ""),
        "primary_artist": str(track.get("primary_artist") or ""),
        "artists": "; ".join(artist_names(track)),
        "type": str(track.get("type") or ""),
        "section": str(track.get("section") or track.get("primary_section") or ""),
        "display_section": str(track.get("display_section") or ""),
        "edition": str(track.get("edition") or ""),
        "album_type": str(track.get("album_type") or ""),
        "existing_filter_tags": ";".join(track.get("filter_tags") or []),
        "suggested_filter_tags": ";".join(tags),
        "sources": json.dumps(sources, ensure_ascii=False, sort_keys=True),
    }


def should_include(location: TrackLocation, only: str | None) -> bool:
    if not only:
        return True
    needle = norm(only)
    haystack = " ".join(
        norm(location.track.get(key))
        for key in ("title", "base_title", "title_clean", "album", "section", "display_section")
    )
    return needle in haystack or needle in location.track_id.casefold()


def write_reports(rows: list[dict[str, str]], csv_path: Path, json_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else [
        "file",
        "track_id",
        "title",
        "album",
        "primary_artist",
        "artists",
        "type",
        "section",
        "display_section",
        "edition",
        "album_type",
        "existing_filter_tags",
        "suggested_filter_tags",
        "sources",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def run(args: argparse.Namespace) -> int:
    payloads, locations = load_discography_locations()
    locations = [location for location in locations if should_include(location, args.track)]
    if args.limit:
        locations = locations[:args.limit]

    rows: list[dict[str, str]] = []
    touched: set[Path] = set()
    tag_counts: Counter[str] = Counter()
    changed = 0

    suggestions: dict[str, list[tuple[list[str], dict[str, str]]]] = {}
    for location in locations:
        tags, sources = infer_tags(location.track)
        suggestions.setdefault(location.track_id, []).append((tags, sources))

    aggregated = aggregate_track_tags(suggestions)

    for location in locations:
        tags, sources = aggregated[location.track_id]
        tag_counts.update(tags)
        rows.append(row_for(location, tags, sources))
        if location.track.get("filter_tags") != tags or location.track.get("filter_tag_sources") != sources:
            changed += 1
            if args.apply:
                location.track["filter_tags"] = tags
                location.track["filter_tag_sources"] = sources
                touched.add(location.path)

    write_reports(rows, args.csv, args.json)
    print(f"[report] {display_path(args.csv)}")
    print(f"[report] {display_path(args.json)}")
    print(f"[load] {len(locations)} track occurrence(s)")
    print("[tags] " + ", ".join(f"{tag}={tag_counts[tag]}" for tag in TAG_ORDER if tag_counts[tag]))

    if args.apply:
        for path in sorted(touched, key=lambda item: str(item).casefold()):
            backup(path)
            write_json(path, payloads[path])
            print(f"[write] {path.relative_to(ROOT)}")
    else:
        print("[dry-run] No discography JSON files written. Re-run with --apply to save filter_tags.")

    print(f"[summary] changed={changed}, files_touched={len(touched)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Infer UI filter tags for Spotify stream tracks from db/discography metadata."
    )
    parser.add_argument("--apply", action="store_true", help="Write filter_tags into db/discography JSON files.")
    parser.add_argument("--track", help="Only process matching title, album, section, or Spotify track ID.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum track occurrences to process.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_REPORT_CSV, help="Audit CSV output path.")
    parser.add_argument("--json", type=Path, default=DEFAULT_REPORT_JSON, help="Audit JSON output path.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
