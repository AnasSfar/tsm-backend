"""
Apple Music combined genre charts collector — Taylor Swift only.

Fetches genre songs + genre albums for each (storefront, genre) pair in a
SINGLE request (types=songs,albums&genre=...) instead of two separate passes,
then writes the same two CSVs as the legacy per-type collectors
(genre_charts.py, genre_album_charts.py — kept as manual tools).

- Pairs that reject the combined call (400/404) fall back to per-type requests.
- 401s go through a coordinated TokenManager refresh (one refresh for the pool).
- Network failures skip the (storefront, genre) pair (missing, never fake) but
  abort the run if they exceed MAX_FAILURE_PCT of pairs.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from threading import local

from requests import RequestException

from core.config import CHART_LIMIT, DB_DIR, GENRES, SCRIPTS_DIR, WORKERS
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.storefronts import resolve_storefronts
from core.token import TokenManager, build_auth_headers

SONGS_CSV = DB_DIR / "apple_music_genre_charts.csv"
ALBUMS_CSV = DB_DIR / "apple_music_genre_album_charts.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
_THREAD_LOCAL = local()

COMBINED_TYPES = "songs,albums"
MAX_FAILURE_PCT = 5.0

SONG_FIELDNAMES = [
    "date",
    "scraped_at",
    "country",
    "genre_id",
    "genre_name",
    "chart_type",
    "song_name",
    "apple_music_id",
    "rank",
    "previous_rank",
    "image_url",
    "url",
    "artist_name",
    "album_name",
    "duration_ms",
    "release_date",
    "isrc",
    "content_rating",
    "genre_names",
]
ALBUM_FIELDNAMES = [
    "date",
    "scraped_at",
    "country",
    "genre_id",
    "genre_name",
    "chart_type",
    "album_name",
    "apple_music_id",
    "rank",
    "previous_rank",
    "image_url",
    "url",
    "artist_name",
    "release_date",
    "genre_names",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Apple Music genre songs/albums charts for Taylor Swift in one pass."
    )
    parser.add_argument("--countries", nargs="*", default=None)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def parse_songs(items: list[dict]) -> list[dict]:
    songs: list[dict] = []
    for idx, item in enumerate(items, start=1):
        attrs = item.get("attributes", {}) or {}
        if not is_taylor_swift_song(item, attrs):
            continue
        songs.append(
            {
                "song_name": clean_text(attrs.get("name", "")),
                "apple_music_id": str(item.get("id", "")),
                "rank": idx,
                "image_url": build_artwork_url(attrs.get("artwork")),
                "url": attrs.get("url", ""),
                "artist_name": clean_text(attrs.get("artistName", "")),
                "album_name": clean_text(attrs.get("albumName", "")),
                "duration_ms": attrs.get("durationInMillis", ""),
                "release_date": attrs.get("releaseDate", ""),
                "isrc": attrs.get("isrc", ""),
                "content_rating": attrs.get("contentRating", ""),
                "genre_names": " | ".join(attrs.get("genreNames") or []),
            }
        )
    return songs


def parse_albums(items: list[dict]) -> list[dict]:
    albums: list[dict] = []
    for idx, item in enumerate(items, start=1):
        attrs = item.get("attributes", {}) or {}
        if not is_taylor_swift_song(item, attrs):
            continue
        albums.append(
            {
                "album_name": clean_text(attrs.get("name", "")),
                "apple_music_id": str(item.get("id", "")),
                "rank": idx,
                "image_url": build_artwork_url(attrs.get("artwork"), size=500),
                "url": attrs.get("url", ""),
                "artist_name": clean_text(attrs.get("artistName", "")),
                "release_date": attrs.get("releaseDate", ""),
                "genre_names": " | ".join(attrs.get("genreNames") or []),
            }
        )
    return albums


def _chart_items(payload: dict, chart_key: str) -> list[dict]:
    block = ((payload.get("results") or {}).get(chart_key) or [])
    if not block:
        return []
    return (block[0] or {}).get("data", []) or []


def worker_session():
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = build_session()
        _THREAD_LOCAL.session = session
    return session


def _get_with_auth(session, manager: TokenManager, url: str):
    token = manager.get()
    resp = session.get(url, headers=build_auth_headers(token))
    if resp.status_code == 401:
        token = manager.refresh(token)
        resp = session.get(url, headers=build_auth_headers(token))
    return resp


def _fetch_one_type(session, manager: TokenManager, country: str, genre_id: str, chart_type: str) -> list[dict]:
    url = (
        f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts"
        f"?types={chart_type}&genre={genre_id}&limit={CHART_LIMIT}"
    )
    resp = _get_with_auth(session, manager, url)
    if resp.status_code in (400, 404):
        return []
    if resp.status_code == 401:
        raise RuntimeError(f"Unauthorized after token refresh ({country}/{genre_id}/{chart_type})")
    resp.raise_for_status()
    return _chart_items(resp.json(), chart_type)


def fetch_genre(session, manager: TokenManager, country: str, genre_id: str) -> tuple[list[dict], list[dict]]:
    url = (
        f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts"
        f"?types={COMBINED_TYPES}&genre={genre_id}&limit={CHART_LIMIT}"
    )
    resp = _get_with_auth(session, manager, url)
    if resp.status_code in (400, 404):
        # Pair rejects the combined call: keep legacy per-type semantics where
        # each type 400s/404s independently.
        return (
            parse_songs(_fetch_one_type(session, manager, country, genre_id, "songs")),
            parse_albums(_fetch_one_type(session, manager, country, genre_id, "albums")),
        )
    if resp.status_code == 401:
        raise RuntimeError(f"Unauthorized after token refresh ({country}/{genre_id})")
    resp.raise_for_status()
    payload = resp.json()
    return (
        parse_songs(_chart_items(payload, "songs")),
        parse_albums(_chart_items(payload, "albums")),
    )


def fetch_genre_task(manager: TokenManager, country: str, genre_id: str, genre_name: str):
    session = worker_session()
    songs, albums = fetch_genre(session, manager, country, genre_id)
    return country, genre_id, genre_name, songs, albums


def build_song_row(*, today, scraped_at, country, genre_id, genre_name, song, previous_by_id, previous_by_name) -> dict:
    prev_rank = previous_by_id.get((country, genre_id, song["apple_music_id"]))
    if prev_rank is None:
        prev_rank = previous_by_name.get((country, genre_id, rank_key(song["song_name"])))
    return {
        "date": today,
        "scraped_at": scraped_at,
        "country": country,
        "genre_id": genre_id,
        "genre_name": genre_name,
        "chart_type": "genre",
        "song_name": song["song_name"],
        "apple_music_id": song["apple_music_id"],
        "rank": song["rank"],
        "previous_rank": prev_rank if prev_rank is not None else "",
        "image_url": song["image_url"],
        "url": song["url"],
        "artist_name": song["artist_name"],
        "album_name": song["album_name"],
        "duration_ms": song["duration_ms"],
        "release_date": song["release_date"],
        "isrc": song["isrc"],
        "content_rating": song["content_rating"],
        "genre_names": song["genre_names"],
    }


def build_album_row(*, today, scraped_at, country, genre_id, genre_name, album, previous_by_id, previous_by_name) -> dict:
    prev_rank = previous_by_id.get((country, genre_id, album["apple_music_id"]))
    if prev_rank is None:
        prev_rank = previous_by_name.get((country, genre_id, rank_key(album["album_name"])))
    return {
        "date": today,
        "scraped_at": scraped_at,
        "country": country,
        "genre_id": genre_id,
        "genre_name": genre_name,
        "chart_type": "genre_albums",
        "album_name": album["album_name"],
        "apple_music_id": album["apple_music_id"],
        "rank": album["rank"],
        "previous_rank": prev_rank if prev_rank is not None else "",
        "image_url": album["image_url"],
        "url": album["url"],
        "artist_name": album["artist_name"],
        "release_date": album["release_date"],
        "genre_names": album["genre_names"],
    }


def log_country_summary(country: str, per_genre: dict[str, tuple[str, list[dict], list[dict]]]) -> None:
    song_hits: Counter[str] = Counter()
    album_hits: Counter[str] = Counter()
    for _genre_id, (genre_name, songs, albums) in per_genre.items():
        if songs:
            song_hits[genre_name] += len(songs)
        if albums:
            album_hits[genre_name] += len(albums)
    total_songs = sum(song_hits.values())
    total_albums = sum(album_hits.values())
    if total_songs or total_albums:
        detail = ", ".join(f"{genre}: {count}" for genre, count in song_hits.items())
        print(
            f"{country}: {total_songs} song placement(s), {total_albums} album placement(s) "
            f"across genre charts ({detail or 'albums only'})"
        )
    else:
        print(f"{country}: 0 Taylor Swift placement(s) across genre charts")


def main() -> None:
    args = parse_args()
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    base_session = build_session()
    manager = TokenManager(base_session)
    base_session.headers.update(build_auth_headers(manager.get()))

    countries = [c.lower() for c in (args.countries if args.countries is not None else resolve_storefronts(base_session))]
    print(f"[Apple Music] Genre combined storefronts: {len(countries)}")
    print(f"[Apple Music] Genre combined workers: {WORKERS}")

    previous = {
        "songs_by_id": load_previous_ranks(SONGS_CSV, key_fields=["country", "genre_id", "apple_music_id"], today=scraped_at),
        "songs_by_name": load_previous_ranks(SONGS_CSV, key_fields=["country", "genre_id", "song_name"], today=scraped_at),
        "albums_by_id": load_previous_ranks(ALBUMS_CSV, key_fields=["country", "genre_id", "apple_music_id"], today=scraped_at),
        "albums_by_name": load_previous_ranks(
            ALBUMS_CSV, key_fields=["country", "genre_id", "album_name"], today=scraped_at, song_field="album_name"
        ),
    }

    results: dict[str, dict[str, tuple[str, list[dict], list[dict]]]] = {country: {} for country in countries}
    failures: list[tuple[str, str, str]] = []
    total_pairs = len(countries) * len(GENRES)

    if WORKERS == 1:
        for country in countries:
            for genre_id, genre_name in GENRES:
                try:
                    songs, albums = fetch_genre(base_session, manager, country, genre_id)
                except (RequestException, RuntimeError) as exc:
                    failures.append((country, genre_id, str(exc)))
                    songs, albums = [], []
                results[country][genre_id] = (genre_name, songs, albums)
            log_country_summary(country, results[country])
    else:
        remaining_by_country = {country: len(GENRES) for country in countries}
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(fetch_genre_task, manager, country, genre_id, genre_name): (country, genre_id, genre_name)
                for country in countries
                for genre_id, genre_name in GENRES
            }
            for future in as_completed(futures):
                country, genre_id, genre_name = futures[future]
                try:
                    _c, _g, _n, songs, albums = future.result()
                except (RequestException, RuntimeError) as exc:
                    failures.append((country, genre_id, str(exc)))
                    songs, albums = [], []
                results[country][genre_id] = (genre_name, songs, albums)
                remaining_by_country[country] -= 1
                if remaining_by_country[country] == 0:
                    log_country_summary(country, results[country])

    if failures:
        for country, genre_id, error in failures:
            print(f"[Apple Music] Warning: genre chart {country}/{genre_id} skipped: {error}")
        failure_pct = len(failures) * 100.0 / max(total_pairs, 1)
        if failure_pct > MAX_FAILURE_PCT:
            print(
                f"[Apple Music] ERROR: {len(failures)}/{total_pairs} genre charts failed "
                f"({failure_pct:.1f}% > {MAX_FAILURE_PCT}%), aborting to avoid publishing a partial day"
            )
            sys.exit(1)

    song_rows: list[dict] = []
    album_rows: list[dict] = []
    for country in countries:
        for genre_id, genre_name in GENRES:
            entry = results[country].get(genre_id)
            if not entry:
                continue
            _name, songs, albums = entry
            for song in songs:
                song_rows.append(
                    build_song_row(
                        today=today,
                        scraped_at=scraped_at,
                        country=country,
                        genre_id=genre_id,
                        genre_name=genre_name,
                        song=song,
                        previous_by_id=previous["songs_by_id"],
                        previous_by_name=previous["songs_by_name"],
                    )
                )
            for album in albums:
                album_rows.append(
                    build_album_row(
                        today=today,
                        scraped_at=scraped_at,
                        country=country,
                        genre_id=genre_id,
                        genre_name=genre_name,
                        album=album,
                        previous_by_id=previous["albums_by_id"],
                        previous_by_name=previous["albums_by_name"],
                    )
                )

    rewrite_for_snapshot(SONGS_CSV, SONG_FIELDNAMES, scraped_at, song_rows)
    print(f"Wrote {len(song_rows)} rows -> {SONGS_CSV}")
    rewrite_for_snapshot(ALBUMS_CSV, ALBUM_FIELDNAMES, scraped_at, album_rows)
    print(f"Wrote {len(album_rows)} rows -> {ALBUMS_CSV}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
