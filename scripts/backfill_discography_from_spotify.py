#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
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
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
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


@dataclass(frozen=True)
class CatalogClassification:
    tags: tuple[str, ...]
    music_track: bool
    category: str
    reason: str = ""


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


def normalize_apostrophes(value: str) -> str:
    return (value or "").replace("\u2018", "'").replace("\u2019", "'").replace("\u02bc", "'")


def song_family(value: str) -> str:
    slug = slugify(value)
    return slug or "unknown"


VERSION_SUFFIX_PATTERNS = (
    r"\s+-\s+track by track.*$",
    r"\s+-\s+voice memo.*$",
    r"\s+-\s+songwriting voice memo.*$",
    r"\s+-\s+demo.*$",
    r"\s+-\s+commentary.*$",
    r"\s+-\s+karaoke.*$",
    r"\s+-\s+instrumental.*$",
    r"\s+-\s+instrumental\s+(?:with|w/).*$",
    r"\s+-\s+.*\binstrumental\b.*$",
    r"\s+-\s+.*\bacoustic\b.*$",
    r"\s+-\s+.*\blive\b.*$",
    r"\s+-\s+.*\bremix\b.*$",
    r"\s+-\s+.*\bversion\b.*$",
    r"\s+-\s+.*\bedit\b.*$",
    r"\s+-\s+.*\bmix\b.*$",
    r"\s+-\s+from .*$",
)

VERSION_PAREN_PATTERNS = (
    r"\s+\((?:[^)]*\btrack by track\b[^)]*)\)",
    r"\s+\((?:[^)]*\bvoice memo\b[^)]*)\)",
    r"\s+\((?:[^)]*\bdemo\b[^)]*)\)",
    r"\s+\((?:[^)]*\bcommentary\b[^)]*)\)",
    r"\s+\((?:[^)]*\bkaraoke\b[^)]*)\)",
    r"\s+\((?:[^)]*\binstrumental\b[^)]*)\)",
    r"\s+\((?:[^)]*\bacoustic\b[^)]*)\)",
    r"\s+\((?:[^)]*\blive\b[^)]*)\)",
    r"\s+\((?:[^)]*\bremix\b[^)]*)\)",
    r"\s+\((?:[^)]*\bversion\b[^)]*)\)",
    r"\s+\((?:[^)]*\bedit\b[^)]*)\)",
    r"\s+\((?:[^)]*\bmix\b[^)]*)\)",
    r"\s+\((?:from [^)]*)\)",
)


def clean_base_title(title: str) -> str:
    original = (title or "").strip()
    value = original
    value = re.sub(r"\s+", " ", value).strip()
    for pattern in VERSION_PAREN_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.I)
    for pattern in VERSION_SUFFIX_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" -")
    return value or original


def track_base_title(track: dict[str, Any]) -> str:
    return str(track.get("base_title") or clean_base_title(str(track.get("title") or ""))).strip()


def track_family_key(track: dict[str, Any]) -> str:
    return str(track.get("song_family") or song_family(track_base_title(track))).strip()


def version_bucket(tag: str | None) -> str:
    value = str(tag or "").strip().casefold()
    if value in {"", "standalone", "standard"}:
        return "plain"
    parts = set(value.split("__"))
    for non_music in ("track_by_track", "commentary", "karaoke", "instrumental"):
        if non_music in parts:
            return non_music
    priority = (
        "taylors_version",
        "from_the_vault",
        "remix",
        "live",
        "acoustic",
        "piano",
        "pop_mix",
        "pop_version",
        "radio",
        "edit",
        "international",
        "demo",
        "voice_memo",
        "feature",
        "featured",
    )
    bucket = [part for part in priority if part in parts]
    return "__".join(bucket) if bucket else value


def dedupe_identity_for_track(track: dict[str, Any], total: int | None) -> tuple[str, str, str, int] | None:
    if total is None:
        return None
    base_title = track_base_title(track)
    family = track_family_key(track)
    if not base_title or not family:
        return None
    return (
        family,
        normalized_title(base_title),
        version_bucket(track.get("version_tag")),
        total,
    )


def dedupe_identity_for_api_track(
    track: dict[str, Any],
    *,
    kind: str,
    total: int | None,
) -> tuple[str, str, str, int] | None:
    if total is None:
        return None
    base_title = clean_base_title(str(track.get("title") or ""))
    if not base_title:
        return None
    return (
        song_family(base_title),
        normalized_title(base_title),
        version_bucket(version_tag(str(track.get("title") or ""), kind)),
        total,
    )


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


def classify_title(title: str, kind: str) -> CatalogClassification:
    normalized = normalize_apostrophes(title)
    lowered = normalized.casefold()
    tags: list[str] = []

    non_music_rules = (
        (r"\btrack by track\b", "track_by_track", "track by track"),
        (r"\bcommentary\b", "commentary", "commentary"),
        (r"\bkaraoke\b", "karaoke", "karaoke"),
        (
            r"\binstrumental\b|\bbackground vocals\b|\bbg vocals\b|\bbgv\b|\bno bv\b",
            "instrumental",
            "instrumental/backing vocals",
        ),
    )
    for pattern, tag, reason in non_music_rules:
        if re.search(pattern, lowered):
            tags.append(tag)
            return CatalogClassification(tuple(tags), False, tag, reason)

    music_rules = (
        ("taylor's version", "taylors_version", "taylor_version"),
        ("from the vault", "from_the_vault", "vault"),
        ("voice memo", "voice_memo", "demo_voice_memo"),
        ("songwriting voice memo", "voice_memo", "demo_voice_memo"),
        ("demo", "demo", "demo_voice_memo"),
        ("acoustic", "acoustic", "acoustic"),
        ("piano", "piano", "piano"),
        ("live", "live", "live"),
        ("remix", "remix", "remix"),
        ("mix", "mix", "mix"),
        ("radio", "radio", "radio_edit"),
        ("edit", "edit", "radio_edit"),
        ("international", "international", "regional_mix"),
        ("pop version", "pop_version", "pop_version"),
        ("pop mix", "pop_mix", "pop_version"),
        ("clean version", "clean", "clean"),
        ("intro", "intro", "intro_outro"),
        ("outro", "outro", "intro_outro"),
    )
    categories: list[str] = []
    for marker, tag, category in music_rules:
        if marker in lowered and tag not in tags:
            tags.append(tag)
            categories.append(category)
    if "remix" in tags and "mix" in tags:
        tags.remove("mix")
    if ("pop_mix" in tags or "pop_version" in tags) and "mix" in tags:
        tags.remove("mix")

    if "feat." in lowered or "feat " in lowered or "(feat" in lowered:
        tags.append("featured")
        categories.append("featured")
    if kind == "feature" and "feature" not in tags:
        tags.append("feature")
        categories.append("feature")

    if not tags:
        tags.append("standalone" if kind in {"standalone", "feature"} else "standard")

    category = categories[0] if categories else ("feature" if kind == "feature" else "standard")
    return CatalogClassification(tuple(dict.fromkeys(tags)), True, category)


def version_tag(title: str, kind: str) -> str | None:
    classification = classify_title(title, kind)
    tags = [tag for tag in classification.tags if tag not in {"standard"}]
    if not tags:
        return "standalone" if kind in {"standalone", "feature"} else None
    return "__".join(tags)


def image_url(track: dict[str, Any]) -> str:
    for release in track.get("releases") or []:
        for key in ("cover_url", "image_url"):
            value = str(release.get(key) or "").strip()
            if value:
                return value
    return ""


def apply_catalog_classification_fields(entry: dict[str, Any], classification: CatalogClassification) -> bool:
    changed = False
    desired = {
        "version_tags": list(classification.tags),
        "catalog_category": classification.category,
        "music_track": classification.music_track,
    }
    for key, value in desired.items():
        if entry.get(key) != value:
            entry[key] = value
            changed = True
    if not classification.music_track:
        non_music_fields = {
            "chart_extra": True,
            "excluded_from_public_stats": True,
            "catalog_exclusion_reason": classification.reason,
        }
        for key, value in non_music_fields.items():
            if entry.get(key) != value:
                entry[key] = value
                changed = True
        if entry.pop("exclude_from_stream_collection", None) is not None:
            changed = True
    return changed


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
    groups: dict[tuple[str, str, str, int], list[TrackLocation]] = defaultdict(list)
    for loc in locations:
        track_id = extract_track_id(loc.track.get("url") or loc.track.get("spotify_url"))
        total = history_totals.get(track_id)
        identity = dedupe_identity_for_track(loc.track, total)
        if not track_id or identity is None:
            continue
        groups[identity].append(loc)

    removed = 0
    touched: set[Path] = set()
    for (_family_key, _base_title_key, _version_key, total), matches in groups.items():
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
    classification = classify_title(title, kind)
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
    apply_catalog_classification_fields(entry, classification)
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
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
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


def _release_iso_date(release: dict[str, Any]) -> str:
    return str(((release.get("date") or {}).get("isoString") or "")).strip()


def _recent_artist_releases(
    session: Any,
    *,
    tokens: dict[str, str],
    limit: int,
    target_release_date: str | None = None,
    expand_target_date: bool = False,
) -> list[dict[str, Any]]:
    releases_by_id: dict[str, dict[str, Any]] = {}
    offset = 0
    found_target_release = False
    limit = max(1, limit)

    while True:
        payload = _request_partner_json(
            session,
            tokens=tokens,
            operation_name="queryArtistDiscographyAll",
            variables={"uri": ARTIST_URI, "offset": offset, "limit": min(PAGE_LIMIT, limit), "order": "DATE_DESC"},
            query_hash=ARTIST_DISCOGRAPHY_HASH,
        )
        groups = (
            (((payload.get("data") or {}).get("artistUnion") or {}).get("discography") or {})
            .get("all", {})
            .get("items", [])
        )
        page_releases: list[dict[str, Any]] = []
        for group in groups:
            page_releases.extend(
                release for release in ((group.get("releases") or {}).get("items") or [])
                if isinstance(release, dict)
            )

        if not page_releases:
            break

        for release in page_releases:
            release_id = str(release.get("id") or "").strip()
            release_day = _release_iso_date(release)

            if target_release_date and release_day == target_release_date:
                found_target_release = True

            if (
                expand_target_date
                and target_release_date
                and found_target_release
                and len(releases_by_id) >= limit
                and release_day != target_release_date
            ):
                return list(releases_by_id.values())

            if release_id:
                releases_by_id[release_id] = release

        if len(releases_by_id) >= limit and not (expand_target_date and target_release_date and found_target_release):
            break
        if len(groups) < min(PAGE_LIMIT, limit):
            break
        offset += min(PAGE_LIMIT, limit)

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


def build_api_catalog(
    tokens: dict[str, str] | None = None,
    *,
    recent_release_limit: int | None = None,
    target_release_date: str | None = None,
    expand_target_date: bool = False,
) -> list[dict[str, Any]]:
    tokens = tokens or capture_tokens()
    import requests

    with requests.Session() as session:
        if recent_release_limit is not None:
            releases = _recent_artist_releases(
                session,
                tokens=tokens,
                limit=recent_release_limit,
                target_release_date=target_release_date,
                expand_target_date=expand_target_date,
            )
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
    title = str(track.get("title") or "")
    releases = track.get("releases") or []
    kind = str(track.get("type") or "")
    if not kind and releases:
        kind = track_type(track, choose_primary_release(releases))
    return not classify_title(title, kind or "standalone").music_track


def _search_headers(tokens: dict[str, str]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {tokens['bearer']}",
        "Accept": "application/json",
        "User-Agent": "TSM stream catalog backfill",
    }


def _artist_name_key(value: str) -> str:
    return normalized_title(value).replace(" ", "")


def _expected_artist_keys(track: dict[str, Any]) -> set[str]:
    raw_artists = track.get("artists") or []
    if not raw_artists:
        raw_artists = [
            track.get("primary_artist"),
            *(track.get("featured_artists") or []),
        ]
    return {
        _artist_name_key(str(artist))
        for artist in raw_artists
        if str(artist or "").strip()
    }


def _search_track_candidates(
    session: Any,
    *,
    tokens: dict[str, str],
    title: str,
) -> list[dict[str, Any]]:
    try:
        response = session.get(
            SPOTIFY_SEARCH_URL,
            headers=_search_headers(tokens),
            params={"q": f'track:"{title}"', "type": "track", "limit": 10},
            timeout=(5, 20),
        )
    except Exception:
        return []
    if response.status_code != 200:
        return []
    payload = response.json()
    return [
        item for item in (((payload.get("tracks") or {}).get("items")) or [])
        if isinstance(item, dict)
    ]


def _candidate_cover_url(candidate: dict[str, Any]) -> str:
    images = ((candidate.get("album") or {}).get("images") or [])
    if not images:
        return ""
    best = max(images, key=lambda image: image.get("width") or 0)
    return str(best.get("url") or "").strip()


def resolve_existing_blank_urls(
    data_by_path: dict[Path, Any],
    locations: list[TrackLocation],
    *,
    tokens: dict[str, str],
    verbose: bool,
) -> tuple[int, set[Path], list[str]]:
    """Fill existing DB entries that have a title but no Spotify URL.

    This is deliberately conservative: one exact title match, and the candidate
    must include one of the expected artists already recorded on the DB row.
    """
    import requests

    updates = 0
    touched: set[Path] = set()
    resolved_ids: list[str] = []

    with requests.Session() as session:
        for loc in locations:
            if extract_track_id(loc.track.get("url") or loc.track.get("spotify_url")):
                continue
            title = str(loc.track.get("title") or "").strip()
            if not title:
                continue
            expected_artists = _expected_artist_keys(loc.track)
            title_key = normalized_title(title)
            matches: list[dict[str, Any]] = []
            for candidate in _search_track_candidates(session, tokens=tokens, title=title):
                if normalized_title(str(candidate.get("name") or "")) != title_key:
                    continue
                candidate_artist_keys = {
                    _artist_name_key(str(artist.get("name") or ""))
                    for artist in (candidate.get("artists") or [])
                    if isinstance(artist, dict)
                }
                if expected_artists and not (candidate_artist_keys & expected_artists):
                    continue
                matches.append(candidate)

            unique_by_id = {
                str(candidate.get("id") or "").strip(): candidate
                for candidate in matches
                if str(candidate.get("id") or "").strip()
            }
            if len(unique_by_id) != 1:
                if verbose and matches:
                    print(f"[search-pending] {title}: {len(unique_by_id)} exact candidate(s), left unchanged")
                continue

            track_id, candidate = next(iter(unique_by_id.items()))
            loc.track["url"] = spotify_track_url(track_id)
            if not str(loc.track.get("image_url") or "").strip():
                cover = _candidate_cover_url(candidate)
                if cover:
                    loc.track["image_url"] = cover
            album = candidate.get("album") or {}
            release_day = str(album.get("release_date") or "").strip()
            if release_day and not str(loc.track.get("release_date") or "").strip():
                loc.track["release_date"] = f"{release_day}T00:00:00Z" if len(release_day) == 10 else release_day
            updates += 1
            touched.add(loc.path)
            resolved_ids.append(track_id)
            if verbose:
                print(f"[search-resolved] {title} -> {track_id}")

    for path in touched:
        refresh_counts(path, data_by_path[path])
    return updates, touched, resolved_ids


def _counter_summary(counter: Counter[str]) -> str:
    if not counter:
        return "none"
    return " ".join(f"{key}={value}" for key, value in sorted(counter.items()))


def _track_label(track: dict[str, Any]) -> str:
    track_id = str(track.get("track_id") or "").strip()
    title = str(track.get("title") or "").strip() or "?"
    return f"{title} [{track_id or '?'}]"


def _location_label(loc: TrackLocation) -> str:
    track_id = extract_track_id(loc.track.get("url") or loc.track.get("spotify_url")) or "?"
    title = str(loc.track.get("title") or "").strip() or "?"
    return f"{title} [{track_id}] in {loc.path.relative_to(ROOT)}"


def run_backfill(
    *,
    apply: bool = False,
    no_backup: bool = False,
    include_non_songs: bool = False,
    skip_api: bool = False,
    tokens: dict[str, str] | None = None,
    recent_release_limit: int | None = None,
    target_release_date: str | None = None,
    expand_target_date: bool = False,
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
    by_dedupe_identity: dict[tuple[str, str, str, int], list[TrackLocation]] = defaultdict(list)
    for loc in locations:
        title = str(loc.track.get("title") or "")
        if title:
            by_title[normalized_title(title)].append(loc)
        track_id = extract_track_id(loc.track.get("url") or loc.track.get("spotify_url"))
        identity = dedupe_identity_for_track(loc.track, history_totals.get(track_id))
        if identity is not None:
            by_dedupe_identity[identity].append(loc)

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
                "resolved_existing_url_ids": [],
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
            "resolved_existing_url_ids": [],
            "written_files": len(db_dedupe_touched),
            "added_track_ids": [],
        }

    tokens = tokens or capture_tokens()
    updates = 0
    additions = 0
    matched_existing_by_title_streams = 0
    skipped_non_music = 0
    added_track_ids: list[str] = []
    resolved_existing_url_ids: list[str] = []
    ambiguous_track_ids: list[str] = []
    ambiguous_messages: list[str] = []
    add_category_counts: Counter[str] = Counter()
    add_target_counts: Counter[str] = Counter()
    matched_kind_counts: Counter[str] = Counter()
    update_kind_counts: Counter[str] = Counter()
    touched: set[Path] = set(db_dedupe_touched)

    search_updates, search_touched, search_ids = resolve_existing_blank_urls(
        data_by_path,
        locations,
        tokens=tokens,
        verbose=verbose,
    )
    updates += search_updates
    if search_updates:
        update_kind_counts["search_resolved_url"] += search_updates
    touched.update(search_touched)
    resolved_existing_url_ids.extend(search_ids)

    if search_updates:
        locations = [
            loc
            for loc in locations
            if track_is_present(loc.section, loc.track)
        ]
        by_id = {extract_track_id(loc.track.get("url") or loc.track.get("spotify_url")): loc for loc in locations}
        by_title = defaultdict(list)
        by_dedupe_identity = defaultdict(list)
        for loc in locations:
            title = str(loc.track.get("title") or "")
            if title:
                by_title[normalized_title(title)].append(loc)
            track_id = extract_track_id(loc.track.get("url") or loc.track.get("spotify_url"))
            identity = dedupe_identity_for_track(loc.track, history_totals.get(track_id))
            if identity is not None:
                by_dedupe_identity[identity].append(loc)

    print("[spotify] Fetching artist catalog...")
    catalog_tracks = build_api_catalog(
        tokens,
        recent_release_limit=recent_release_limit,
        target_release_date=target_release_date,
        expand_target_date=expand_target_date,
    )
    canonical_tracks, duplicate_groups = canonicalize_api_tracks(catalog_tracks)

    for chosen, skipped in duplicate_groups:
        if not verbose:
            continue
        skipped_ids = ", ".join(track.get("track_id") or "?" for track in skipped)
        print(f"[dedupe] single-preferred: {chosen.get('title')} -> {chosen.get('track_id')} (skipped {skipped_ids})")

    for track in canonical_tracks:
        track_id = str(track.get("track_id") or "").strip()
        if not track_id:
            continue
        primary = choose_primary_release(track.get("releases") or [])
        kind = track_type(track, primary)
        classification = classify_title(str(track.get("title") or ""), kind)
        if not classification.music_track and track_id not in by_id and not include_non_songs:
            skipped_non_music += 1
            continue

        api_release_date = release_date(primary)
        existing = by_id.get(track_id)
        if existing is not None:
            if apply_catalog_classification_fields(existing.track, classification):
                updates += 1
                update_kind_counts["catalog_classification"] += 1
                touched.add(existing.path)
            if api_release_date and existing.track.get("release_date") != api_release_date:
                existing.track["release_date"] = api_release_date
                updates += 1
                update_kind_counts["release_date_existing_id"] += 1
                touched.add(existing.path)
            continue

        playcount = track.get("playcount")
        identity = dedupe_identity_for_api_track(track, kind=kind, total=playcount)
        same_identity_matches = by_dedupe_identity.get(identity, []) if identity is not None else []
        if same_identity_matches:
            matched_existing_by_title_streams += 1
            matched_kind_counts["family_base_version_streams"] += 1
            duplicate_ids = [track_id]
            for loc in same_identity_matches:
                if merge_historical_track_ids(loc.track, duplicate_ids):
                    updates += 1
                    update_kind_counts["historical_track_ids"] += 1
                    touched.add(loc.path)
                if api_release_date and loc.track.get("release_date") != api_release_date:
                    loc.track["release_date"] = api_release_date
                    updates += 1
                    update_kind_counts["release_date_duplicate_match"] += 1
                    touched.add(loc.path)
            if verbose:
                kept_ids = ", ".join(
                    extract_track_id(loc.track.get("url") or loc.track.get("spotify_url"))
                    for loc in same_identity_matches
                )
                print(f"[dedupe] matched family/base/streams: {track.get('title')} -> {kept_ids}")
            continue

        title_key = normalized_title(track.get("title") or "")
        title_matches = by_title.get(title_key, [])
        if playcount is not None:
            same_stream_matches = [
                loc for loc in title_matches
                if history_totals.get(extract_track_id(loc.track.get("url") or loc.track.get("spotify_url"))) == playcount
            ]
            if same_stream_matches:
                matched_existing_by_title_streams += 1
                matched_kind_counts["exact_title_streams"] += 1
                for loc in same_stream_matches:
                    if api_release_date and loc.track.get("release_date") != api_release_date:
                        loc.track["release_date"] = api_release_date
                        updates += 1
                        update_kind_counts["release_date_title_stream_match"] += 1
                        touched.add(loc.path)
                continue
        same_version_title_matches = [
            loc for loc in title_matches
            if version_bucket(loc.track.get("version_tag")) == version_bucket(version_tag(str(track.get("title") or ""), kind))
        ]
        if same_version_title_matches:
            ambiguous_track_ids.append(track_id)
            candidates = "; ".join(_location_label(loc) for loc in same_version_title_matches[:5])
            message = (
                f"{_track_label(track)} has exact-title/same-version DB match but no verified same-stream match: "
                f"{candidates}"
            )
            ambiguous_messages.append(message)
            if verbose:
                print(f"[ambiguous] {message}")
            continue

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
        add_category_counts[classification.category] += 1
        add_target_counts[str(target_path.relative_to(ROOT))] += 1
        touched.add(target_path)
        by_id[track_id] = TrackLocation(target_path, data_by_path[target_path], target_section, entry)
        by_title[title_key].append(by_id[track_id])
        entry_identity = dedupe_identity_for_track(entry, playcount)
        if entry_identity is not None:
            by_dedupe_identity[entry_identity].append(by_id[track_id])
        if verbose:
            print(f"[add] {entry['title']} -> {target_path.relative_to(ROOT)}")

    for path in touched:
        refresh_counts(path, data_by_path[path])

    print(
        f"[summary] API tracks={len(catalog_tracks)} canonical={len(canonical_tracks)} "
        f"db_duplicates_removed={db_duplicates_removed} updates={updates} additions={additions} "
        f"matched_existing_by_title_streams={matched_existing_by_title_streams}"
    )
    print(
        f"[catalog-audit] single_preferred_groups={len(duplicate_groups)} "
        f"skipped_non_music={skipped_non_music} ambiguous_blocked={len(ambiguous_messages)}"
    )
    print(f"[catalog-audit] additions_by_category {_counter_summary(add_category_counts)}")
    print(f"[catalog-audit] additions_by_target {_counter_summary(add_target_counts)}")
    print(f"[catalog-audit] matched_existing {_counter_summary(matched_kind_counts)}")
    print(f"[catalog-audit] updates_by_kind {_counter_summary(update_kind_counts)}")
    if ambiguous_messages:
        print("[catalog-audit] ambiguous samples:")
        for message in ambiguous_messages[:20]:
            print(f"  - {message}")

    if not apply:
        print("[dry-run] No files written. Re-run with --apply to update db/discography.")
        return {
            "api_skipped": False,
            "db_duplicates_removed": db_duplicates_removed,
            "updates": updates,
            "additions": additions,
            "matched_existing_by_title_streams": matched_existing_by_title_streams,
            "resolved_existing_url_ids": resolved_existing_url_ids,
            "ambiguous_track_ids": ambiguous_track_ids,
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
        "resolved_existing_url_ids": resolved_existing_url_ids,
        "ambiguous_track_ids": ambiguous_track_ids,
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
        dest="include_non_songs",
        help="Also add commentary, karaoke and instrumental tracks. They are marked excluded from stream collection.",
    )
    parser.add_argument(
        "--exclude-non-songs",
        action="store_false",
        dest="include_non_songs",
        help="Skip commentary, karaoke and instrumental tracks. This is the default.",
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
    parser.add_argument("--target-release-date", default=None, help="Expand recent scan around this release date.")
    parser.set_defaults(include_non_songs=False)
    args = parser.parse_args()

    run_backfill(
        apply=args.apply,
        no_backup=args.no_backup,
        include_non_songs=args.include_non_songs,
        skip_api=args.skip_api,
        recent_release_limit=args.recent_releases,
        target_release_date=args.target_release_date,
        expand_target_date=bool(args.target_release_date),
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
