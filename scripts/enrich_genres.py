#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


ROOT = Path(__file__).resolve().parents[1]
DISCOGRAPHY_DIR = ROOT / "db" / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
SONGS_PATH = DISCOGRAPHY_DIR / "songs.json"
CACHE_PATH = ROOT / "data" / "genre_enrichment_cache.json"
OVERRIDES_PATH = DISCOGRAPHY_DIR / "genre_overrides.json"
LEGACY_FR_SONGS_DB_PATH = (
    ROOT / "collectors" / "spotify" / "charts" / "fr" / "tools" / "json" / "songs_db.json"
)
LEGACY_FR_CONFIG_PATH = (
    ROOT / "collectors" / "spotify" / "charts" / "fr" / "tools" / "scripts" / "config.py"
)
# Local Apple Music export — genre_names is Apple's own catalog classification
# (present on every song entry across global/country/genre/ts_top_songs charts),
# a stronger signal than crowd-sourced Last.fm tags. No network calls.
APPLE_MUSIC_JSON_CANDIDATES = [
    ROOT / "runtime" / "exports" / "web" / "site" / "data" / "applemusic.json",
    ROOT / "website" / "site" / "data" / "applemusic.json",
]

TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")
DEFAULT_ARTIST = "Taylor Swift"
_LEGACY_FR_DB: dict[str, Any] | None = None
_GENRE_OVERRIDES: dict[str, list[str]] | None = None
_APPLE_MUSIC_INDEX: dict[str, list[str]] | None = None

SOURCE_WEIGHTS = {
    "legacy_fr": 1.0,
    "lastfm": 1.0,
    "musicbrainz": 1.0,
    "theaudiodb": 0.9,
    "discogs": 1.0,
    "apple_music": 1.2,
}

CANONICAL_GENRES = {
    "alternative",
    "alternative rock",
    "americana",
    "bluegrass",
    "christmas",
    "country",
    "country pop",
    "dance-pop",
    "dream pop",
    "electropop",
    "folk",
    "folk pop",
    "indie folk",
    "indie pop",
    "pop",
    "pop rock",
    "r&b",
    "rock",
    "singer-songwriter",
    "soft rock",
    "soundtrack",
    "synth-pop",
}

TAG_ALIASES = {
    "adult contemporary": "pop",
    "alt-pop": "alternative",
    "alternative pop": "alternative",
    "alternative/indie rock": "alternative rock",
    "christmas music": "christmas",
    "contemporary country": "country pop",
    "country-pop": "country pop",
    "dance pop": "dance-pop",
    "dancepop": "dance-pop",
    "electro pop": "electropop",
    "electro-pop": "electropop",
    "folk-pop": "folk pop",
    "folk/country": "folk",
    "folk rock": "folk",
    "folklore": "indie folk",
    "indie": "indie pop",
    "indie-folk": "indie folk",
    "indie-pop": "indie pop",
    "pop/country": "country pop",
    "pop-rock": "pop rock",
    "poprock": "pop rock",
    "rhythm and blues": "r&b",
    "singer songwriter": "singer-songwriter",
    "singer/songwriter": "singer-songwriter",
    "soft-rock": "soft rock",
    "soundtracks": "soundtrack",
    "synth pop": "synth-pop",
    "synthpop": "synth-pop",
    "teen pop": "pop",
}

IGNORED_TAGS = {
    "00s",
    "10s",
    "20s",
    "2010s",
    "2020s",
    "acoustic",
    "album",
    "american",
    "ballad",
    "beautiful",
    "best",
    "better than radiohead",
    "chill",
    "cover",
    "covers",
    "duet",
    "favorite",
    "favorites",
    "female",
    "female vocalist",
    "female vocalists",
    "females",
    "happy",
    "live",
    "love",
    "loved",
    "mellow",
    "remix",
    "sad",
    "seen live",
    "single",
    "taylor",
    "taylor swift",
    "ts",
    "usa",
    "vocal",
    "vocalist",
}


@dataclass
class TrackLocation:
    path: Path
    payload: Any
    track: dict[str, Any]


@dataclass
class SourceResult:
    tags: list[str]
    raw_tags: list[str]
    error: str | None = None


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


def strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return value.encode("ascii", "ignore").decode("ascii")


def clean_text(value: str) -> str:
    value = strip_accents(value)
    value = value.replace("&", " and ")
    value = re.sub(r"['`]", "", value)
    value = re.sub(r"[^a-zA-Z0-9/+ -]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().lower()


def cache_key(source: str, artist: str, title: str) -> str:
    return f"{source}:{clean_text(artist)}:{clean_text(title)}"


def legacy_key(artist: str, title: str) -> str:
    return f"{clean_text(artist)}|||{clean_text(title)}"


def extract_track_id(url: str | None) -> str:
    match = TRACK_ID_RE.search(url or "")
    return match.group(1) if match else ""


def display_title(track: dict[str, Any]) -> str:
    return str(track.get("base_title") or track.get("title_clean") or track.get("title") or "").strip()


def _strip_version_suffix(value: str) -> str:
    cleaned = value
    cleaned = re.sub(r"\s+\(Taylor's Version\)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+\(.*?From The Vault.*?\)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+\(feat\..*?\)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+\(featuring .*?\)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+-\s+Taylor's Version.*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+-\s+.*?version.*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+-\s+.*?remix.*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+-\s+.*?live.*$", "", cleaned, flags=re.I)
    return cleaned


def title_variants(*raw_candidates: str) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        value = re.sub(r"\s+", " ", candidate or "").strip()
        if not value:
            continue
        cleaned = _strip_version_suffix(value)
        for variant in (value, cleaned):
            variant = re.sub(r"\s+", " ", variant).strip(" -")
            key = clean_text(variant)
            if variant and key and key not in seen:
                variants.append(variant)
                seen.add(key)
    return variants


def lastfm_title_variants(track: dict[str, Any]) -> list[str]:
    return title_variants(
        str(track.get("base_title") or ""),
        str(track.get("title_clean") or ""),
        str(track.get("title") or ""),
        display_title(track),
    )


def display_artist(track: dict[str, Any]) -> str:
    artists = track.get("artists")
    if isinstance(artists, list) and artists:
        return str(artists[0] or DEFAULT_ARTIST)
    return str(track.get("primary_artist") or DEFAULT_ARTIST)


def canonical_song_key(track: dict[str, Any]) -> str:
    family = str(track.get("song_family") or "").strip()
    if family:
        return f"family:{family}"
    return f"title:{clean_text(display_title(track))}"


def version_penalty(track: dict[str, Any]) -> int:
    text = clean_text(" ".join(
        str(track.get(key) or "")
        for key in ("title", "title_clean", "version_tag", "edition", "type")
    ))
    penalty = 0
    for word in ("live", "remix", "acoustic", "demo", "karaoke", "instrumental", "commentary"):
        if word in text:
            penalty += 3
    if "taylor s version" in text:
        penalty += 1
    if str(track.get("type") or "").strip() == "main":
        penalty -= 2
    return penalty


def choose_representative(locations: list[TrackLocation]) -> TrackLocation:
    return sorted(
        locations,
        key=lambda loc: (
            version_penalty(loc.track),
            len(display_title(loc.track)),
            display_title(loc.track).casefold(),
        ),
    )[0]


def normalize_genre_tag(tag: str) -> str | None:
    text = clean_text(tag)
    if not text or text in IGNORED_TAGS:
        return None
    text = text.replace(" / ", "/").replace("/", " and ")
    text = re.sub(r"\s+", " ", text).strip()
    if text in IGNORED_TAGS:
        return None
    text = TAG_ALIASES.get(text, text)
    if text in CANONICAL_GENRES:
        return text
    for genre in sorted(CANONICAL_GENRES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(genre)}\b", text):
            return genre
    return None


def normalize_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        normalized = normalize_genre_tag(tag)
        if normalized and normalized not in seen:
            out.append(normalized)
            seen.add(normalized)
    return out


def parse_manual_genres(value: str) -> list[str]:
    genres: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;|]", value or ""):
        genre = normalize_genre_tag(part) or clean_text(part)
        if genre and genre not in seen:
            genres.append(genre)
            seen.add(genre)
    if not genres:
        raise SystemExit("--set-genres needs at least one genre.")
    return genres


def manual_enrichment(genres: list[str]) -> dict[str, Any]:
    return {
        "genres": genres,
        "genre_status": "manual",
        "genre_scores": {genre: 999.0 for genre in genres},
        "genre_sources": {"manual": genres},
        "genre_raw_sources": {"manual": genres},
        "genre_errors": {},
    }


def load_genre_overrides() -> dict[str, list[str]]:
    global _GENRE_OVERRIDES
    if _GENRE_OVERRIDES is not None:
        return _GENRE_OVERRIDES
    if not OVERRIDES_PATH.exists():
        _GENRE_OVERRIDES = {}
        return _GENRE_OVERRIDES
    try:
        payload = read_json(OVERRIDES_PATH)
    except Exception:
        payload = {}
    overrides: dict[str, list[str]] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, str):
                overrides[str(key)] = parse_manual_genres(value)
            elif isinstance(value, list):
                overrides[str(key)] = parse_manual_genres(",".join(str(item) for item in value))
    _GENRE_OVERRIDES = overrides
    return _GENRE_OVERRIDES


def override_enrichment(group: list[TrackLocation]) -> dict[str, Any] | None:
    overrides = load_genre_overrides()
    for loc in group:
        key = canonical_song_key(loc.track)
        if key in overrides:
            return manual_enrichment(overrides[key])
    for loc in group:
        title_key = f"title:{clean_text(display_title(loc.track))}"
        if title_key in overrides:
            return manual_enrichment(overrides[title_key])
    return None


class ApiClient:
    def __init__(self, timeout: float, sleep_seconds: float) -> None:
        self.timeout = timeout
        self.sleep_seconds = sleep_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "tsm-backend-genre-enrichment/1.0 "
                "(https://github.com/sfara/tsm-backend)"
            }
        )

    def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        response = self.session.get(url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def legacy_lastfm_api_key() -> str:
    key = os.getenv("LASTFM_API_KEY", "").strip()
    if key:
        return key
    if not LEGACY_FR_CONFIG_PATH.exists():
        return ""
    try:
        text = LEGACY_FR_CONFIG_PATH.read_text(encoding="utf-8-sig")
    except Exception:
        return ""
    match = re.search(r"LASTFM_API_KEY\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1).strip() if match else ""


def load_legacy_fr_db() -> dict[str, Any]:
    global _LEGACY_FR_DB
    if _LEGACY_FR_DB is not None:
        return _LEGACY_FR_DB
    if not LEGACY_FR_SONGS_DB_PATH.exists():
        _LEGACY_FR_DB = {}
        return _LEGACY_FR_DB
    try:
        payload = read_json(LEGACY_FR_SONGS_DB_PATH)
    except Exception:
        payload = {}
    _LEGACY_FR_DB = payload if isinstance(payload, dict) else {}
    return _LEGACY_FR_DB


def fetch_legacy_fr(client: ApiClient, artist: str, title: str) -> SourceResult:
    del client
    db = load_legacy_fr_db()
    candidates = [legacy_key(artist, title), legacy_key(DEFAULT_ARTIST, title)]
    title_norm = clean_text(title)
    for key in candidates:
        item = db.get(key)
        if isinstance(item, dict):
            raw = [str(tag) for tag in item.get("tags") or [] if tag]
            return SourceResult(normalize_tags(raw)[:3], raw)
    for item in db.values():
        if not isinstance(item, dict):
            continue
        if clean_text(str(item.get("artist") or "")) != clean_text(artist):
            continue
        if clean_text(str(item.get("track") or "")) != title_norm:
            continue
        raw = [str(tag) for tag in item.get("tags") or [] if tag]
        return SourceResult(normalize_tags(raw)[:3], raw)
    return SourceResult([], [])


def fetch_lastfm(client: ApiClient, artist: str, title: str) -> SourceResult:
    api_key = legacy_lastfm_api_key()
    if not api_key:
        return SourceResult([], [], "LASTFM_API_KEY missing")
    raw: list[str] = []
    for method in ("track.getTopTags", "track.getInfo"):
        url = "https://ws.audioscrobbler.com/2.0/?" + urlencode(
            {
                "method": method,
                "artist": artist,
                "track": title,
                "api_key": api_key,
                "format": "json",
                "autocorrect": "1",
            }
        )
        payload = client.get_json(url)
        payload_tags = payload.get("toptags", {}).get("tag", [])
        if method == "track.getInfo":
            payload_tags = (payload.get("track") or {}).get("toptags", {}).get("tag", [])
        raw = [str(item.get("name") or "") for item in payload_tags if isinstance(item, dict)]
        normalized = normalize_tags(raw)[:3]
        if normalized:
            return SourceResult(normalized, raw)
    return SourceResult([], raw)


def _musicbrainz_tags(entity: dict[str, Any]) -> list[str]:
    raw: list[str] = []
    for key in ("genres", "tags"):
        values = entity.get(key) or []
        if isinstance(values, list):
            raw.extend(str(item.get("name") or "") for item in values if isinstance(item, dict))
    return [tag for tag in raw if tag]


def fetch_musicbrainz(client: ApiClient, artist: str, title: str) -> SourceResult:
    query = f'artist:"{artist}" AND recording:"{title}"'
    search_url = "https://musicbrainz.org/ws/2/recording/?" + urlencode(
        {"query": query, "fmt": "json", "limit": "5"}
    )
    payload = client.get_json(search_url)
    recordings = payload.get("recordings") or []
    raw: list[str] = []
    for recording in recordings[:3]:
        if not isinstance(recording, dict):
            continue
        raw.extend(_musicbrainz_tags(recording))
        mbid = recording.get("id")
        if mbid and not raw:
            lookup_url = (
                f"https://musicbrainz.org/ws/2/recording/{mbid}?"
                + urlencode({"inc": "genres+tags", "fmt": "json"})
            )
            raw.extend(_musicbrainz_tags(client.get_json(lookup_url)))
        if raw:
            break
    return SourceResult(normalize_tags(raw), raw)


def fetch_theaudiodb(client: ApiClient, artist: str, title: str) -> SourceResult:
    api_key = os.getenv("THEAUDIODB_API_KEY", "2").strip() or "2"
    url = f"https://www.theaudiodb.com/api/v1/json/{api_key}/searchtrack.php?" + urlencode(
        {"s": artist, "t": title}
    )
    payload = client.get_json(url)
    tracks = payload.get("track") or []
    raw: list[str] = []
    for item in tracks[:3]:
        if not isinstance(item, dict):
            continue
        for key in ("strGenre", "strStyle"):
            value = str(item.get(key) or "").strip()
            if value:
                raw.extend(part.strip() for part in re.split(r"[,/|]", value) if part.strip())
    return SourceResult(normalize_tags(raw), raw)


def fetch_discogs(client: ApiClient, artist: str, title: str) -> SourceResult:
    token = os.getenv("DISCOGS_TOKEN", "").strip()
    if not token:
        return SourceResult([], [], "DISCOGS_TOKEN missing")
    headers = {"Authorization": f"Discogs token={token}"}
    search_url = "https://api.discogs.com/database/search?" + urlencode(
        {"artist": artist, "track": title, "type": "release", "per_page": "5"}
    )
    payload = client.get_json(search_url, headers=headers)
    results = payload.get("results") or []
    raw: list[str] = []
    for result in results[:3]:
        release_id = result.get("id") if isinstance(result, dict) else None
        if not release_id:
            continue
        release = client.get_json(f"https://api.discogs.com/releases/{release_id}", headers=headers)
        for key in ("genres", "styles"):
            values = release.get(key) or []
            if isinstance(values, list):
                raw.extend(str(value) for value in values if value)
        if raw:
            break
    return SourceResult(normalize_tags(raw), raw)


def _apple_music_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    entries.extend(payload.get("ts_top_songs", {}).get("entries") or [])
    entries.extend(payload.get("global_chart", {}).get("entries") or [])
    for country_entries in (payload.get("country_charts", {}).get("countries") or {}).values():
        if isinstance(country_entries, list):
            entries.extend(country_entries)
    for genre_map in (payload.get("genre_charts", {}).get("by_country") or {}).values():
        if isinstance(genre_map, dict):
            for genre_entries in genre_map.values():
                if isinstance(genre_entries, list):
                    entries.extend(genre_entries)
    return entries


def load_apple_music_index() -> dict[str, list[str]]:
    """clean_text(title variant) -> union of raw Apple Music genre_names seen for it.

    Built once from the local Apple Music export (no network call) — genre_names
    is Apple's own catalog classification, e.g. a song charting on the France
    Country genre chart carries "Country" here. Broadest single surface is
    ts_top_songs (composite, ~full catalog); other surfaces only add coverage
    for anything ts_top_songs might miss.
    """
    global _APPLE_MUSIC_INDEX
    if _APPLE_MUSIC_INDEX is not None:
        return _APPLE_MUSIC_INDEX

    index: dict[str, list[str]] = {}
    payload: dict[str, Any] | None = None
    for path in APPLE_MUSIC_JSON_CANDIDATES:
        if path.exists():
            try:
                payload = read_json(path)
            except Exception:
                payload = None
            if payload:
                break

    if isinstance(payload, dict):
        for entry in _apple_music_entries(payload):
            if not isinstance(entry, dict):
                continue
            genre_names = [str(g) for g in entry.get("genre_names") or [] if g]
            if not genre_names:
                continue
            song_name = str(entry.get("song_name") or "").strip()
            if not song_name:
                continue
            for variant in title_variants(song_name):
                key = clean_text(variant)
                bucket = index.setdefault(key, [])
                for genre in genre_names:
                    if genre not in bucket:
                        bucket.append(genre)

    _APPLE_MUSIC_INDEX = index
    return index


def fetch_apple_music(client: ApiClient, artist: str, title: str) -> SourceResult:
    del client, artist
    index = load_apple_music_index()
    for variant in title_variants(title):
        raw = index.get(clean_text(variant))
        if raw:
            return SourceResult(normalize_tags(raw), raw)
    return SourceResult([], [])


FETCHERS = {
    "legacy_fr": fetch_legacy_fr,
    "lastfm": fetch_lastfm,
    "musicbrainz": fetch_musicbrainz,
    "theaudiodb": fetch_theaudiodb,
    "discogs": fetch_discogs,
    "apple_music": fetch_apple_music,
}


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def call_source(
    source: str,
    client: ApiClient,
    artist: str,
    title: str,
    cache: dict[str, Any],
    *,
    refresh: bool,
) -> SourceResult:
    key = cache_key(source, artist, title)
    if not refresh and key in cache:
        item = cache[key] if isinstance(cache[key], dict) else {}
        cached = SourceResult(
            tags=list(item.get("tags") or []),
            raw_tags=list(item.get("raw_tags") or []),
            error=item.get("error"),
        )
        if cached.tags or cached.error:
            return cached
    try:
        result = FETCHERS[source](client, artist, title)
    except Exception as exc:
        result = SourceResult([], [], str(exc))
    if not result.error or "ProxyError" not in result.error:
        cache[key] = {"tags": result.tags, "raw_tags": result.raw_tags, "error": result.error}
    return result


def aggregate_sources(source_results: dict[str, SourceResult]) -> tuple[list[str], dict[str, float]]:
    if len(source_results) == 1:
        result = next(iter(source_results.values()))
        return result.tags[:3], {tag: float(3 - index) for index, tag in enumerate(result.tags[:3])}

    scores: Counter[str] = Counter()
    for source, result in source_results.items():
        weight = SOURCE_WEIGHTS.get(source, 1.0)
        for tag in result.tags:
            scores[tag] += weight
    if not scores:
        return [], {}

    source_count = sum(1 for result in source_results.values() if result.tags)
    threshold = 2.0 if source_count >= 2 else 1.0
    final = [tag for tag, score in scores.items() if score >= threshold]
    if not final:
        best_score = max(scores.values())
        final = [tag for tag, score in scores.items() if score == best_score]

    final.sort(key=lambda tag: (-scores[tag], tag))
    return final[:3], {tag: float(scores[tag]) for tag in sorted(scores)}


def iter_locations() -> tuple[dict[Path, Any], list[TrackLocation]]:
    payloads: dict[Path, Any] = {}
    locations: list[TrackLocation] = []

    if SONGS_PATH.exists():
        payload = read_json(SONGS_PATH)
        payloads[SONGS_PATH] = payload
        if isinstance(payload, list):
            for section in payload:
                for track in section.get("tracks", []) if isinstance(section, dict) else []:
                    if isinstance(track, dict):
                        locations.append(TrackLocation(SONGS_PATH, payload, track))

    for path in sorted(ALBUMS_DIR.glob("*.json"), key=lambda item: item.name.casefold()):
        payload = read_json(path)
        payloads[path] = payload
        sections = payload.get("sections", []) if isinstance(payload, dict) else []
        for section in sections if isinstance(sections, list) else []:
            for track in section.get("tracks", []) if isinstance(section, dict) else []:
                if isinstance(track, dict):
                    locations.append(TrackLocation(path, payload, track))

    return payloads, locations


def enrich_track(
    track: dict[str, Any],
    client: ApiClient,
    cache: dict[str, Any],
    sources: list[str],
    *,
    refresh: bool,
) -> dict[str, Any]:
    artist = display_artist(track)
    variants = lastfm_title_variants(track)
    results: dict[str, SourceResult] = {}
    query_titles: dict[str, str] = {}
    for source in sources:
        source_result = SourceResult([], [])
        for title in variants:
            source_result = call_source(source, client, artist, title, cache, refresh=refresh)
            if source_result.tags or source_result.error:
                query_titles[source] = title
                break
        results[source] = source_result
    final_genres, scores = aggregate_sources(results)
    return {
        "genres": final_genres,
        "genre_status": "auto" if final_genres else "missing",
        "genre_scores": scores,
        "genre_sources": {source: result.tags for source, result in results.items()},
        "genre_raw_sources": {source: result.raw_tags for source, result in results.items()},
        "genre_query_titles": query_titles,
        "genre_errors": {
            source: result.error for source, result in results.items() if result.error
        },
    }


def existing_enrichment(locations: list[TrackLocation]) -> dict[str, Any] | None:
    for loc in locations:
        genres = loc.track.get("genres")
        if isinstance(genres, list) and genres:
            return {
                "genres": list(genres),
                "genre_status": loc.track.get("genre_status") or "auto",
                "genre_scores": loc.track.get("genre_scores") or {},
                "genre_sources": {},
                "genre_raw_sources": {},
                "genre_errors": {},
            }
    return None


def should_include(track: dict[str, Any], only: str | None) -> bool:
    if not only:
        return True
    needle = clean_text(only)
    haystack = " ".join(
        [
            clean_text(display_title(track)),
            clean_text(str(track.get("title") or "")),
            extract_track_id(track.get("url") or track.get("spotify_url")),
        ]
    )
    return needle in haystack


def group_locations(locations: list[TrackLocation]) -> dict[str, list[TrackLocation]]:
    groups: dict[str, list[TrackLocation]] = {}
    for loc in locations:
        groups.setdefault(canonical_song_key(loc.track), []).append(loc)
    return groups


def run(args: argparse.Namespace) -> int:
    sources = [source.strip().lower() for source in args.sources.split(",") if source.strip()]
    unknown = [source for source in sources if source not in FETCHERS]
    if unknown:
        raise SystemExit(f"Unknown source(s): {', '.join(unknown)}")

    payloads, locations = iter_locations()
    groups = group_locations(locations)
    selected_groups = [
        group
        for group in groups.values()
        if not args.track or any(should_include(loc.track, args.track) for loc in group)
    ]
    if args.skip_existing:
        selected_groups = [
            group
            for group in selected_groups
            if not all(loc.track.get("genres") for loc in group)
        ]
    if args.limit:
        selected_groups = selected_groups[: args.limit]

    cache = load_cache(args.cache_path)
    client = ApiClient(timeout=args.timeout, sleep_seconds=args.sleep)
    touched: set[Path] = set()

    for index, group in enumerate(selected_groups, 1):
        representative = choose_representative(group)
        title = display_title(representative.track)
        artist = display_artist(representative.track)
        manual_genres = parse_manual_genres(args.set_genres) if args.set_genres else []
        override = None if manual_genres else override_enrichment(group)
        enrichment = manual_enrichment(manual_genres) if manual_genres else override
        reused_existing = False
        if enrichment is None and not args.refresh_cache:
            enrichment = existing_enrichment(group)
            reused_existing = enrichment is not None
        if enrichment is None:
            enrichment = enrich_track(
                representative.track,
                client,
                cache,
                sources,
                refresh=args.refresh_cache,
            )
        print(
            f"[{index}/{len(selected_groups)}] {artist} - {title}: "
            f"{', '.join(enrichment['genres']) or 'no genre'} "
            f"({len(group)} version(s){', override' if override else ''}"
            f"{', reused existing' if reused_existing else ''}"
            f"{', manual' if manual_genres else ''})"
        )
        if args.verbose and enrichment["genre_errors"]:
            print(f"  errors: {enrichment['genre_errors']}")

        if args.apply:
            for loc in group:
                loc.track.update(enrichment)
                loc.track["genre_canonical_key"] = canonical_song_key(loc.track)
                touched.add(loc.path)

    if args.apply:
        for path in sorted(touched, key=lambda item: str(item).casefold()):
            backup(path)
            write_json(path, payloads[path])
            print(f"[write] {path.relative_to(ROOT)}")
        write_json(args.cache_path, cache)
        print(f"[cache] {args.cache_path.relative_to(ROOT)}")
    else:
        write_json(args.cache_path, cache)
        print("[dry-run] No discography files written. Re-run with --apply to save genres.")
        print(f"[cache] {args.cache_path.relative_to(ROOT)}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich song genre tags from configurable sources (Last.fm, local Apple Music export, ...), with manual overrides."
    )
    parser.add_argument("--apply", action="store_true", help="Write genre fields into db/discography JSON files.")
    parser.add_argument("--track", help="Only process tracks whose title or Spotify track ID contains this value.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of matching song groups to process.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip tracks that already have genres.")
    parser.add_argument(
        "--set-genres",
        help="Manually set one or more genres for every version in the matching song group.",
    )
    parser.add_argument(
        "--sources",
        default="lastfm,apple_music",
        help="Comma-separated source list.",
    )
    parser.add_argument("--cache-path", type=Path, default=CACHE_PATH)
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore cached API responses.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay between API requests.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
