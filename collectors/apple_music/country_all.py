"""
Apple Music combined country charts collector — Taylor Swift only.

Fetches songs + albums + music videos for each storefront in a SINGLE request
(types=songs,albums,music-videos) instead of three separate passes, then writes
the same three CSVs as the legacy per-type collectors (country_charts.py,
country_albums.py, music_video_charts.py — kept as manual tools).

- Storefronts that reject the combined call (400) fall back to per-type requests.
- 401s go through a coordinated TokenManager refresh (one refresh for the pool).
- Network failures skip the storefront (missing, never fake) but abort the run
  if they exceed MAX_FAILURE_PCT of storefronts.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from threading import local

from requests import RequestException

from core.config import CHART_LIMIT, DB_DIR, SCRIPTS_DIR, WORKERS
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.storefronts import resolve_storefronts
from core.token import TokenManager, build_auth_headers

SONGS_CSV = DB_DIR / "apple_music_country_charts.csv"
ALBUMS_CSV = DB_DIR / "apple_music_country_albums.csv"
VIDEOS_CSV = DB_DIR / "apple_music_music_video_charts.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
_THREAD_LOCAL = local()

COMBINED_TYPES = "songs,albums,music-videos"
MAX_FAILURE_PCT = 5.0

SONG_FIELDNAMES = [
    "date",
    "scraped_at",
    "country",
    "chart_type",
    "song_name",
    "apple_music_id",
    "rank",
    "previous_rank",
    "image_url",
    "url",
    "artist_name",
]
ALBUM_FIELDNAMES = [
    "date",
    "scraped_at",
    "country",
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
VIDEO_FIELDNAMES = [
    "date",
    "scraped_at",
    "country",
    "chart_type",
    "video_name",
    "apple_music_id",
    "rank",
    "previous_rank",
    "image_url",
    "url",
    "artist_name",
    "album_name",
    "duration_ms",
    "release_date",
    "genre_names",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Apple Music country songs/albums/music-videos charts for Taylor Swift in one pass."
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
                "genre_names": " | ".join([g for g in (attrs.get("genreNames") or []) if g]),
            }
        )
    return albums


def parse_videos(items: list[dict]) -> list[dict]:
    videos: list[dict] = []
    for idx, item in enumerate(items, start=1):
        attrs = item.get("attributes", {}) or {}
        artist_name = clean_text(attrs.get("artistName", ""))
        if "taylor swift" not in artist_name.lower():
            continue
        videos.append(
            {
                "video_name": clean_text(attrs.get("name", "")),
                "apple_music_id": str(item.get("id", "")),
                "rank": idx,
                "image_url": build_artwork_url(attrs.get("artwork")),
                "url": attrs.get("url", ""),
                "artist_name": artist_name,
                "album_name": clean_text(attrs.get("albumName", "")),
                "duration_ms": attrs.get("durationInMillis", ""),
                "release_date": attrs.get("releaseDate", ""),
                "genre_names": " | ".join(attrs.get("genreNames") or []),
            }
        )
    return videos


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


def _fetch_one_type(session, manager: TokenManager, country: str, chart_type: str) -> list[dict]:
    url = f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts?types={chart_type}&limit={CHART_LIMIT}"
    resp = _get_with_auth(session, manager, url)
    if resp.status_code == 400:
        return []
    if resp.status_code == 401:
        raise RuntimeError(f"Unauthorized after token refresh ({country}/{chart_type})")
    resp.raise_for_status()
    return _chart_items(resp.json(), chart_type)


def fetch_country(session, manager: TokenManager, country: str) -> tuple[list[dict], list[dict], list[dict]]:
    url = f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts?types={COMBINED_TYPES}&limit={CHART_LIMIT}"
    resp = _get_with_auth(session, manager, url)
    if resp.status_code == 400:
        # Storefront rejects the combined call (e.g. one type unsupported):
        # keep legacy per-type semantics where each type 400s independently.
        return (
            parse_songs(_fetch_one_type(session, manager, country, "songs")),
            parse_albums(_fetch_one_type(session, manager, country, "albums")),
            parse_videos(_fetch_one_type(session, manager, country, "music-videos")),
        )
    if resp.status_code == 401:
        raise RuntimeError(f"Unauthorized after token refresh ({country})")
    resp.raise_for_status()
    payload = resp.json()
    return (
        parse_songs(_chart_items(payload, "songs")),
        parse_albums(_chart_items(payload, "albums")),
        parse_videos(_chart_items(payload, "music-videos")),
    )


def fetch_country_task(manager: TokenManager, country: str):
    session = worker_session()
    return country, fetch_country(session, manager, country)


def build_song_row(*, today, scraped_at, country, song, previous_by_id, previous_by_name) -> dict:
    prev_rank = previous_by_id.get((country, song["apple_music_id"]))
    if prev_rank is None:
        prev_rank = previous_by_name.get((country, rank_key(song["song_name"])))
    return {
        "date": today,
        "scraped_at": scraped_at,
        "country": country,
        "chart_type": "country",
        "song_name": song["song_name"],
        "apple_music_id": song["apple_music_id"],
        "rank": song["rank"],
        "previous_rank": prev_rank if prev_rank is not None else "",
        "image_url": song["image_url"],
        "url": song["url"],
        "artist_name": song["artist_name"],
    }


def build_album_row(*, today, scraped_at, country, album, previous_by_id, previous_by_name) -> dict:
    prev_rank = previous_by_id.get((country, album["apple_music_id"]))
    if prev_rank is None:
        prev_rank = previous_by_name.get((country, rank_key(album["album_name"])))
    return {
        "date": today,
        "scraped_at": scraped_at,
        "country": country,
        "chart_type": "country_albums",
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


def build_video_row(*, today, scraped_at, country, video, previous_by_id, previous_by_name) -> dict:
    prev_rank = previous_by_id.get((country, video["apple_music_id"]))
    if prev_rank is None:
        prev_rank = previous_by_name.get((country, rank_key(video["video_name"])))
    return {
        "date": today,
        "scraped_at": scraped_at,
        "country": country,
        "chart_type": "music_videos",
        "video_name": video["video_name"],
        "apple_music_id": video["apple_music_id"],
        "rank": video["rank"],
        "previous_rank": prev_rank if prev_rank is not None else "",
        "image_url": video["image_url"],
        "url": video["url"],
        "artist_name": video["artist_name"],
        "album_name": video["album_name"],
        "duration_ms": video["duration_ms"],
        "release_date": video["release_date"],
        "genre_names": video["genre_names"],
    }


def main() -> None:
    args = parse_args()
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    base_session = build_session()
    manager = TokenManager(base_session)
    base_session.headers.update(build_auth_headers(manager.get()))

    countries = [c.lower() for c in (args.countries if args.countries is not None else resolve_storefronts(base_session))]
    print(f"[Apple Music] Country combined storefronts: {len(countries)}")
    print(f"[Apple Music] Country combined workers: {WORKERS}")

    previous = {
        "songs_by_id": load_previous_ranks(SONGS_CSV, key_fields=["country", "apple_music_id"], today=scraped_at),
        "songs_by_name": load_previous_ranks(SONGS_CSV, key_fields=["country", "song_name"], today=scraped_at),
        "albums_by_id": load_previous_ranks(ALBUMS_CSV, key_fields=["country", "apple_music_id"], today=scraped_at),
        "albums_by_name": load_previous_ranks(
            ALBUMS_CSV, key_fields=["country", "album_name"], today=scraped_at, song_field="album_name"
        ),
        "videos_by_id": load_previous_ranks(VIDEOS_CSV, key_fields=["country", "apple_music_id"], today=scraped_at),
        "videos_by_name": load_previous_ranks(
            VIDEOS_CSV, key_fields=["country", "video_name"], today=scraped_at, song_field="video_name"
        ),
    }

    results: dict[str, tuple[list[dict], list[dict], list[dict]]] = {}
    failures: list[tuple[str, str]] = []

    if WORKERS == 1:
        for country in countries:
            try:
                results[country] = fetch_country(base_session, manager, country)
            except (RequestException, RuntimeError) as exc:
                failures.append((country, str(exc)))
                continue
            songs, albums, videos = results[country]
            print(f"{country}: {len(songs)} song(s), {len(albums)} album(s), {len(videos)} video(s)")
    else:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_country_task, manager, country): country for country in countries}
            for future in as_completed(futures):
                country = futures[future]
                try:
                    _country, result = future.result()
                except (RequestException, RuntimeError) as exc:
                    failures.append((country, str(exc)))
                    continue
                results[country] = result
                songs, albums, videos = result
                print(f"{country}: {len(songs)} song(s), {len(albums)} album(s), {len(videos)} video(s)")

    if failures:
        for country, error in failures:
            print(f"[Apple Music] Warning: storefront {country} skipped: {error}")
        failure_pct = len(failures) * 100.0 / max(len(countries), 1)
        if failure_pct > MAX_FAILURE_PCT:
            print(
                f"[Apple Music] ERROR: {len(failures)}/{len(countries)} storefronts failed "
                f"({failure_pct:.1f}% > {MAX_FAILURE_PCT}%), aborting to avoid publishing a partial day"
            )
            sys.exit(1)

    song_rows: list[dict] = []
    album_rows: list[dict] = []
    video_rows: list[dict] = []
    for country in countries:
        if country not in results:
            continue
        songs, albums, videos = results[country]
        for song in songs:
            song_rows.append(
                build_song_row(
                    today=today,
                    scraped_at=scraped_at,
                    country=country,
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
                    album=album,
                    previous_by_id=previous["albums_by_id"],
                    previous_by_name=previous["albums_by_name"],
                )
            )
        for video in videos:
            video_rows.append(
                build_video_row(
                    today=today,
                    scraped_at=scraped_at,
                    country=country,
                    video=video,
                    previous_by_id=previous["videos_by_id"],
                    previous_by_name=previous["videos_by_name"],
                )
            )

    rewrite_for_snapshot(SONGS_CSV, SONG_FIELDNAMES, scraped_at, song_rows)
    print(f"Wrote {len(song_rows)} rows -> {SONGS_CSV}")
    rewrite_for_snapshot(ALBUMS_CSV, ALBUM_FIELDNAMES, scraped_at, album_rows)
    print(f"Wrote {len(album_rows)} rows -> {ALBUMS_CSV}")
    rewrite_for_snapshot(VIDEOS_CSV, VIDEO_FIELDNAMES, scraped_at, video_rows)
    print(f"Wrote {len(video_rows)} rows -> {VIDEOS_CSV}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
