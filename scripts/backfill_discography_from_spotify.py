#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DISCOGRAPHY_DIR = ROOT / "db" / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
SONGS_PATH = DISCOGRAPHY_DIR / "songs.json"
FEATURES_PATH = DISCOGRAPHY_DIR / "features.json"
MISC_PATH = DISCOGRAPHY_DIR / "misc.json"

SPOTIFY_SCRIPT_DIR = ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts"
sys.path.insert(0, str(SPOTIFY_SCRIPT_DIR))

from catalog_gap_report import (  # noqa: E402
    ARTIST_DISCOGRAPHY_HASH,
    ARTIST_URI,
    PAGE_LIMIT,
    _album_tracks,
    _artist_release_tracks,
    _normalize_title,
    _request_partner_json,
)
from history_store import get_all_last_history_totals  # noqa: E402
from spotify_api import TokenManager  # noqa: E402


TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")
TAYLOR_ARTIST_ID = "06HL4z0CvFAxyc27GXpf02"
STANDALONE_ALBUM = "Standalone & Extras"
STANDALONE_SECTION = "kworb_extras"
STANDALONE_DISPLAY_SECTION = "Extras"
NON_SONG_TITLE_RE = re.compile(
    r"(?:\bcommentary\b|\bkaraoke\b|\binstrumental\b|\btrack by track\b|"
    r"\binstrumental with\b|\binstrumental w/)",
    re.I,
)


@dataclass
class TrackLocation:
    path: Path
    data: Any
    section: dict[str, Any]
    track: dict[str, Any]


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
        target = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, target)
        print(f"[backup] {target.relative_to(ROOT)}")


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def extract_track_id(url: str | None) -> str:
    match = TRACK_ID_RE.search(url or "")
    return match.group(1) if match else ""


def spotify_track_url(track_id: str) -> str:
    return f"https://open.spotify.com/intl-fr/track/{track_id}"


def normalized_title(value: str) -> str:
    return _normalize_title(value or "")


def song_family(value: str) -> str:
    slug = slugify(value)
    return slug or "unknown"


def clean_base_title(title: str) -> str:
    value = re.sub(r"\s+-\s+(acoustic|live|remix|instrumental|karaoke).*$", "", title, flags=re.I)
    return value.strip() or title.strip()


def release_type(release: dict[str, Any]) -> str:
    return str(release.get("type") or "").strip().upper()


def release_date(release: dict[str, Any]) -> str:
    return str(release.get("release_date") or "").strip()


def release_name(release: dict[str, Any]) -> str:
    return str(release.get("name") or "").strip()


def choose_primary_release(releases: list[dict[str, Any]]) -> dict[str, Any]:
    def score(release: dict[str, Any]) -> tuple[int, str]:
        rtype = release_type(release)
        name = release_name(release).casefold()
        if rtype == "SINGLE":
            type_score = 0
        elif rtype == "EP":
            type_score = 1
        else:
            type_score = 2
        if "karaoke" in name or "commentary" in name:
            type_score += 5
        return (type_score, release_date(release) or "9999-99-99")

    return sorted(releases or [{}], key=score)[0]


def choose_canonical_duplicate(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    def score(track: dict[str, Any]) -> tuple[int, str, str]:
        releases = track.get("releases") or []
        primary = choose_primary_release(releases)
        rtype = release_type(primary)
        if rtype == "SINGLE":
            type_score = 0
        elif rtype == "EP":
            type_score = 1
        else:
            type_score = 2
        return (type_score, release_date(primary) or "9999-99-99", track.get("track_id") or "")

    return sorted(tracks, key=score)[0]


def track_artists(track: dict[str, Any]) -> list[str]:
    artists = [str(a).strip() for a in (track.get("artists") or []) if str(a).strip()]
    return artists or ["Taylor Swift"]


def primary_artist(track: dict[str, Any]) -> str:
    artists = track_artists(track)
    return artists[0] if artists else "Taylor Swift"


def featured_artists(track: dict[str, Any]) -> list[str]:
    artists = track_artists(track)
    if primary_artist(track) == "Taylor Swift":
        return [artist for artist in artists[1:] if artist != "Taylor Swift"]
    return ["Taylor Swift"] if "Taylor Swift" in artists else []


def track_type(track: dict[str, Any], primary_release: dict[str, Any]) -> str:
    if primary_artist(track) != "Taylor Swift":
        return "feature"
    if release_type(primary_release) == "SINGLE":
        return "standalone"
    return "album_track"


def version_tag(title: str, kind: str) -> str | None:
    lowered = title.casefold()
    tags = []
    for marker, tag in (
        ("taylor's version", "taylors_version"),
        ("from the vault", "from_the_vault"),
        ("acoustic", "acoustic"),
        ("live", "live"),
        ("remix", "remix"),
        ("feat.", "featured"),
    ):
        if marker in lowered:
            tags.append(tag)
    if kind == "feature" and "featured" not in tags:
        tags.append("feature")
    if tags:
        return "__".join(tags)
    return "standalone" if kind in {"standalone", "feature"} else None


def image_url(track: dict[str, Any]) -> str:
    for release in track.get("releases") or []:
        for key in ("cover_url", "image_url"):
            value = str(release.get(key) or "").strip()
            if value:
                return value
    return ""


def album_file_for_release(name: str) -> Path | None:
    direct = ALBUMS_DIR / f"{slugify(name)}.json"
    if direct.exists():
        return direct
    normalized = normalized_title(name)
    for path in sorted(ALBUMS_DIR.glob("*.json")):
        try:
            data = read_json(path)
        except Exception:
            continue
        if normalized_title(str(data.get("album") or "")) == normalized:
            return path
    return None


def section_list_for_data(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        sections = data.get("sections") or []
        return sections if isinstance(sections, list) else []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def iter_track_locations() -> list[TrackLocation]:
    locations: list[TrackLocation] = []
    paths = sorted(ALBUMS_DIR.glob("*.json")) + [SONGS_PATH, FEATURES_PATH, MISC_PATH]
    for path in paths:
        if not path.exists():
            continue
        data = read_json(path)
        for section in section_list_for_data(data):
            for track in section.get("tracks") or []:
                if isinstance(track, dict):
                    locations.append(TrackLocation(path, data, section, track))
    return locations


def load_discography() -> tuple[dict[Path, Any], list[TrackLocation]]:
    by_path: dict[Path, Any] = {}
    locations: list[TrackLocation] = []
    paths = sorted(ALBUMS_DIR.glob("*.json")) + [SONGS_PATH, FEATURES_PATH, MISC_PATH]
    for path in paths:
        if not path.exists():
            continue
        data = read_json(path)
        by_path[path] = data
        for section in section_list_for_data(data):
            for track in section.get("tracks") or []:
                if isinstance(track, dict):
                    locations.append(TrackLocation(path, data, section, track))
    return by_path, locations


def section_track_count(section: dict[str, Any]) -> None:
    tracks = section.get("tracks") or []
    section["track_count"] = len(tracks)


def refresh_counts(path: Path, data: Any) -> None:
    sections = section_list_for_data(data)
    for section in sections:
        section_track_count(section)
    if isinstance(data, dict):
        data["section_count"] = len(sections)
        data["track_count"] = sum(len(section.get("tracks") or []) for section in sections)


def max_display_order(section: dict[str, Any]) -> int:
    values = [t.get("display_order") for t in section.get("tracks") or [] if isinstance(t.get("display_order"), int)]
    return max(values, default=0)


def path_rank(path: Path) -> int:
    if path.parent == ALBUMS_DIR:
        return 0
    if path == SONGS_PATH:
        return 1
    if path == FEATURES_PATH:
        return 2
    if path == MISC_PATH:
        return 3
    return 9


def choose_existing_canonical(locations: list[TrackLocation]) -> TrackLocation:
    def score(loc: TrackLocation) -> tuple[int, int, str, str]:
        order = loc.track.get("display_order")
        if not isinstance(order, int):
            order = 10**9
        title = str(loc.track.get("title") or "").casefold()
        track_id = extract_track_id(loc.track.get("url") or loc.track.get("spotify_url"))
        return (path_rank(loc.path), order, title, track_id)

    return sorted(locations, key=score)[0]


def merge_historical_track_ids(target: dict[str, Any], ids: list[str]) -> bool:
    before = list(target.get("historical_track_ids") or [])
    target_id = extract_track_id(target.get("url") or target.get("spotify_url"))
    merged: list[str] = []
    for track_id in before + ids:
        if not track_id or track_id == target_id or track_id in merged:
            continue
        merged.append(track_id)
    if before == merged:
        return False
    target["historical_track_ids"] = merged
    return True


def remove_track_from_section(section: dict[str, Any], track: dict[str, Any]) -> bool:
    tracks = section.get("tracks") or []
    kept = [item for item in tracks if item is not track]
    if len(kept) == len(tracks):
        return False
    section["tracks"] = kept
    return True


def track_is_present(section: dict[str, Any], track: dict[str, Any]) -> bool:
    return any(item is track for item in (section.get("tracks") or []))


def dedupe_existing_discography_by_title_streams(
    locations: list[TrackLocation],
    history_totals: dict[str, int],
) -> tuple[int, set[Path]]:
    groups: dict[tuple[str, int], list[TrackLocation]] = defaultdict(list)
    for loc in locations:
        track_id = extract_track_id(loc.track.get("url") or loc.track.get("spotify_url"))
        title_key = normalized_title(str(loc.track.get("title") or ""))
        total = history_totals.get(track_id)
        if not track_id or not title_key or total is None:
            continue
        groups[(title_key, total)].append(loc)

    removed = 0
    touched: set[Path] = set()
    for (_title_key, total), matches in groups.items():
        if len(matches) < 2:
            continue
        canonical = choose_existing_canonical(matches)
        duplicate_ids: list[str] = []
        for loc in matches:
            if loc is canonical:
                continue
            duplicate_id = extract_track_id(loc.track.get("url") or loc.track.get("spotify_url"))
            duplicate_ids.append(duplicate_id)
            duplicate_ids.extend(str(item) for item in (loc.track.get("historical_track_ids") or []))
            if remove_track_from_section(loc.section, loc.track):
                removed += 1
                touched.add(loc.path)

        if merge_historical_track_ids(canonical.track, duplicate_ids):
            touched.add(canonical.path)

        kept_id = extract_track_id(canonical.track.get("url") or canonical.track.get("spotify_url"))
        print(
            f"[dedupe-db] {canonical.track.get('title')} total={total} "
            f"kept={kept_id} merged={', '.join(track_id for track_id in duplicate_ids if track_id)}"
        )

    return removed, touched


def ensure_standalone_section(data_by_path: dict[Path, Any]) -> dict[str, Any]:
    if SONGS_PATH not in data_by_path:
        data_by_path[SONGS_PATH] = []
    data = data_by_path[SONGS_PATH]
    if not isinstance(data, list):
        raise RuntimeError(f"{SONGS_PATH.relative_to(ROOT)} must be a list")
    for section in data:
        if section.get("section") == STANDALONE_SECTION:
            return section
    section = {
        "album": STANDALONE_ALBUM,
        "section": STANDALONE_SECTION,
        "track_count": 0,
        "tracks": [],
    }
    data.append(section)
    return section


def ensure_feature_section(data_by_path: dict[Path, Any]) -> dict[str, Any]:
    if FEATURES_PATH not in data_by_path:
        data_by_path[FEATURES_PATH] = []
    data = data_by_path[FEATURES_PATH]
    if not isinstance(data, list):
        raise RuntimeError(f"{FEATURES_PATH.relative_to(ROOT)} must be a list")
    for section in data:
        if section.get("section") == "collabs_and_features":
            return section
    section = {
        "album": STANDALONE_ALBUM,
        "section": "collabs_and_features",
        "track_count": 0,
        "tracks": [],
    }
    data.append(section)
    return section


def ensure_album_section(album_data: dict[str, Any], release: dict[str, Any]) -> dict[str, Any]:
    sections = album_data.setdefault("sections", [])
    name = "standard_edition"
    display = "Standard Edition"
    for section in sections:
        if section.get("section") in {"standard", "standard_edition"}:
            return section
    section = {
        "section": name,
        "track_count": 0,
        "chart_extra": False,
        "tracks": [],
    }
    if display:
        section["display_section"] = display
    sections.append(section)
    return section


def make_track_entry(track: dict[str, Any], primary_release: dict[str, Any], target_album: str, display_order: int) -> dict[str, Any]:
    title = str(track.get("title") or "").strip()
    kind = track_type(track, primary_release)
    base = clean_base_title(title)
    artists = track_artists(track)
    release_date_value = release_date(primary_release)
    entry = {
        "title": title,
        "url": spotify_track_url(track["track_id"]),
        "type": kind,
        "edition": "extras" if target_album == STANDALONE_ALBUM else None,
        "display_section": STANDALONE_DISPLAY_SECTION if target_album == STANDALONE_ALBUM else "Standard Edition",
        "display_order": display_order,
        "base_title": base,
        "album": target_album,
        "primary_artist": primary_artist(track),
        "featured_artists": featured_artists(track),
        "artists": artists,
        "title_clean": title,
        "song_family": song_family(base),
        "version_tag": version_tag(title, kind),
        "image_url": image_url(track),
        "release_date": release_date_value,
        "historical_track_ids": [],
    }
    if entry["edition"] is None:
        entry.pop("edition")
    if not entry["image_url"]:
        entry.pop("image_url")
    return entry


def sorted_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        tracks,
        key=lambda track: (
            track.get("display_order") if isinstance(track.get("display_order"), int) else 10**9,
            str(track.get("title") or "").casefold(),
        ),
    )


def capture_tokens() -> dict[str, str]:
    token_mgr = TokenManager()
    if not token_mgr.capture():
        raise RuntimeError("Could not capture Spotify tokens.")
    tokens = token_mgr.get()
    if not tokens.get("bearer") or not tokens.get("client_token"):
        raise RuntimeError("Spotify partner tokens are missing.")
    return tokens


def _extract_spotify_id(uri: str, kind: str) -> str:
    prefix = f"spotify:{kind}:"
    return uri[len(prefix):] if uri.startswith(prefix) else ""


def _recent_artist_releases(session: Any, *, tokens: dict[str, str], limit: int) -> list[dict[str, Any]]:
    payload = _request_partner_json(
        session,
        tokens=tokens,
        operation_name="queryArtistDiscographyAll",
        variables={"uri": ARTIST_URI, "offset": 0, "limit": min(PAGE_LIMIT, max(1, limit)), "order": "DATE_DESC"},
        query_hash=ARTIST_DISCOGRAPHY_HASH,
    )
    groups = (
        (((payload.get("data") or {}).get("artistUnion") or {}).get("discography") or {})
        .get("all", {})
        .get("items", [])
    )
    releases_by_id: dict[str, dict[str, Any]] = {}
    for group in groups:
        for release in ((group.get("releases") or {}).get("items") or []):
            if not isinstance(release, dict):
                continue
            release_id = str(release.get("id") or "").strip()
            if release_id:
                releases_by_id[release_id] = release
            if len(releases_by_id) >= limit:
                return list(releases_by_id.values())
    return list(releases_by_id.values())


def _release_tracks_for_releases(session: Any, *, tokens: dict[str, str], releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tracks_by_id: dict[str, dict[str, Any]] = {}
    for release in releases:
        release_id = str(release.get("id") or "").strip()
        if not release_id or not str(release.get("uri") or "").strip():
            continue
        for track in _album_tracks(session, tokens=tokens, release=release):
            track_uri = str(track.get("uri") or "").strip()
            track_id = _extract_spotify_id(track_uri, "track")
            if not track_id:
                continue
            artists = [
                ((artist.get("profile") or {}).get("name") or "")
                for artist in (((track.get("artists") or {}).get("items")) or [])
                if isinstance(artist, dict)
            ]
            summary = tracks_by_id.setdefault(
                track_id,
                {
                    "track_id": track_id,
                    "title": track.get("name") or "",
                    "spotify_url": spotify_track_url(track_id),
                    "artists": [artist for artist in artists if artist],
                    "playcount": int(track["playcount"]) if str(track.get("playcount") or "").isdigit() else None,
                    "releases": [],
                },
            )
            summary["releases"].append(
                {
                    "id": release_id,
                    "name": release.get("name") or "",
                    "type": release.get("type") or "",
                    "release_date": ((release.get("date") or {}).get("isoString") or ""),
                    "cover_url": (
                        ((((release.get("coverArt") or {}).get("sources") or [{}])[0]) or {}).get("url")
                        or ""
                    ),
                }
            )
    return sorted(tracks_by_id.values(), key=lambda item: (item["title"].casefold(), item["track_id"]))


def build_api_catalog(tokens: dict[str, str] | None = None, *, recent_release_limit: int | None = None) -> list[dict[str, Any]]:
    tokens = tokens or capture_tokens()
    import requests

    with requests.Session() as session:
        if recent_release_limit is not None:
            releases = _recent_artist_releases(session, tokens=tokens, limit=recent_release_limit)
            print(f"[spotify] Recent release scan: {len(releases)} release(s)")
            return _release_tracks_for_releases(session, tokens=tokens, releases=releases)
        return _artist_release_tracks(session, tokens=tokens)


def canonicalize_api_tracks(catalog_tracks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], list[dict[str, Any]]]]]:
    groups: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for track in catalog_tracks:
        groups[(normalized_title(track.get("title") or ""), track.get("playcount"))].append(track)

    canonical: list[dict[str, Any]] = []
    duplicate_groups: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for tracks in groups.values():
        if len(tracks) == 1 or tracks[0].get("playcount") is None:
            canonical.extend(tracks)
            continue
        chosen = choose_canonical_duplicate(tracks)
        canonical.append(chosen)
        duplicate_groups.append((chosen, [track for track in tracks if track is not chosen]))
    return canonical, duplicate_groups


def is_non_song_track(track: dict[str, Any]) -> bool:
    return bool(NON_SONG_TITLE_RE.search(str(track.get("title") or "")))


def run_backfill(
    *,
    apply: bool = False,
    no_backup: bool = False,
    include_non_songs: bool = False,
    skip_api: bool = False,
    tokens: dict[str, str] | None = None,
    recent_release_limit: int | None = None,
    verbose: bool = True,
) -> dict[str, int | bool | list[str]]:
    data_by_path, locations = load_discography()
    history_totals = get_all_last_history_totals()
    db_duplicates_removed, db_dedupe_touched = dedupe_existing_discography_by_title_streams(locations, history_totals)
    for path in db_dedupe_touched:
        refresh_counts(path, data_by_path[path])
    locations = [
        loc
        for loc in locations
        if track_is_present(loc.section, loc.track)
    ]
    by_id = {extract_track_id(loc.track.get("url") or loc.track.get("spotify_url")): loc for loc in locations}
    by_title: dict[str, list[TrackLocation]] = defaultdict(list)
    for loc in locations:
        title = str(loc.track.get("title") or "")
        if title:
            by_title[normalized_title(title)].append(loc)

    if skip_api:
        print(f"[summary] db_duplicates_removed={db_duplicates_removed} api_skipped=True")
        if not apply:
            print("[dry-run] No files written. Re-run with --apply --skip-api to update local duplicates only.")
            return {
                "api_skipped": True,
                "db_duplicates_removed": db_duplicates_removed,
                "updates": 0,
                "additions": 0,
                "matched_existing_by_title_streams": 0,
                "written_files": 0,
                "added_track_ids": [],
            }
        for path in sorted(db_dedupe_touched, key=lambda p: str(p).casefold()):
            if not no_backup:
                backup(path)
            write_json(path, data_by_path[path])
            print(f"[write] {path.relative_to(ROOT)}")
        return {
            "api_skipped": True,
            "db_duplicates_removed": db_duplicates_removed,
            "updates": 0,
            "additions": 0,
            "matched_existing_by_title_streams": 0,
            "written_files": len(db_dedupe_touched),
            "added_track_ids": [],
        }

    print("[spotify] Fetching artist catalog...")
    catalog_tracks = build_api_catalog(tokens, recent_release_limit=recent_release_limit)
    canonical_tracks, duplicate_groups = canonicalize_api_tracks(catalog_tracks)

    updates = 0
    additions = 0
    matched_existing_by_title_streams = 0
    added_track_ids: list[str] = []
    touched: set[Path] = set(db_dedupe_touched)

    for chosen, skipped in duplicate_groups:
        if not verbose:
            continue
        skipped_ids = ", ".join(track.get("track_id") or "?" for track in skipped)
        print(f"[dedupe] single-preferred: {chosen.get('title')} -> {chosen.get('track_id')} (skipped {skipped_ids})")

    for track in canonical_tracks:
        track_id = str(track.get("track_id") or "").strip()
        if not track_id:
            continue
        if is_non_song_track(track) and track_id not in by_id and not include_non_songs:
            continue

        primary = choose_primary_release(track.get("releases") or [])
        api_release_date = release_date(primary)
        existing = by_id.get(track_id)
        if existing is not None:
            if api_release_date and existing.track.get("release_date") != api_release_date:
                existing.track["release_date"] = api_release_date
                updates += 1
                touched.add(existing.path)
            continue

        title_key = normalized_title(track.get("title") or "")
        title_matches = by_title.get(title_key, [])
        playcount = track.get("playcount")
        if playcount is not None:
            same_stream_matches = [
                loc for loc in title_matches
                if history_totals.get(extract_track_id(loc.track.get("url") or loc.track.get("spotify_url"))) == playcount
            ]
            if same_stream_matches:
                matched_existing_by_title_streams += 1
                for loc in same_stream_matches:
                    if api_release_date and loc.track.get("release_date") != api_release_date:
                        loc.track["release_date"] = api_release_date
                        updates += 1
                        touched.add(loc.path)
                continue

        kind = track_type(track, primary)
        release_album_name = release_name(primary)
        album_path = album_file_for_release(release_album_name)
        if kind == "feature":
            target_section = ensure_feature_section(data_by_path)
            target_path = FEATURES_PATH
            target_album = STANDALONE_ALBUM
        elif release_type(primary) == "SINGLE" or album_path is None:
            target_section = ensure_standalone_section(data_by_path)
            target_path = SONGS_PATH
            target_album = STANDALONE_ALBUM
        else:
            album_data = data_by_path.setdefault(album_path, read_json(album_path))
            target_section = ensure_album_section(album_data, primary)
            target_path = album_path
            target_album = str(album_data.get("album") or release_album_name)

        display_order = max_display_order(target_section) + 1
        entry = make_track_entry(track, primary, target_album, display_order)
        target_section.setdefault("tracks", []).append(entry)
        target_section["tracks"] = sorted_tracks(target_section["tracks"])
        additions += 1
        added_track_ids.append(track_id)
        touched.add(target_path)
        by_id[track_id] = TrackLocation(target_path, data_by_path[target_path], target_section, entry)
        by_title[title_key].append(by_id[track_id])
        if verbose:
            print(f"[add] {entry['title']} -> {target_path.relative_to(ROOT)}")

    for path in touched:
        refresh_counts(path, data_by_path[path])

    print(
        f"[summary] API tracks={len(catalog_tracks)} canonical={len(canonical_tracks)} "
        f"db_duplicates_removed={db_duplicates_removed} updates={updates} additions={additions} "
        f"matched_existing_by_title_streams={matched_existing_by_title_streams}"
    )

    if not apply:
        print("[dry-run] No files written. Re-run with --apply to update db/discography.")
        return {
            "api_skipped": False,
            "db_duplicates_removed": db_duplicates_removed,
            "updates": updates,
            "additions": additions,
            "matched_existing_by_title_streams": matched_existing_by_title_streams,
            "written_files": 0,
            "added_track_ids": added_track_ids,
        }

    for path in sorted(touched, key=lambda p: str(p).casefold()):
        if not no_backup:
            backup(path)
        write_json(path, data_by_path[path])
        print(f"[write] {path.relative_to(ROOT)}")

    return {
        "api_skipped": False,
        "db_duplicates_removed": db_duplicates_removed,
        "updates": updates,
        "additions": additions,
        "matched_existing_by_title_streams": matched_existing_by_title_streams,
        "written_files": len(touched),
        "added_track_ids": added_track_ids,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill db/discography JSON files from the Spotify web-player API."
    )
    parser.add_argument("--apply", action="store_true", help="Write JSON changes. Defaults to dry-run.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak copies when --apply is used.")
    parser.add_argument(
        "--include-non-songs",
        action="store_true",
        help="Also add commentary, karaoke and instrumental tracks. Skipped by default.",
    )
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="Only run local discography dedupe; do not call Spotify.",
    )
    parser.add_argument("--quiet", action="store_true", help="Reduce per-track logging.")
    parser.add_argument(
        "--recent-releases",
        type=int,
        default=None,
        help="Only scan the N most recent Spotify releases instead of the full catalog.",
    )
    args = parser.parse_args()

    run_backfill(
        apply=args.apply,
        no_backup=args.no_backup,
        include_non_songs=args.include_non_songs,
        skip_api=args.skip_api,
        recent_release_limit=args.recent_releases,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
