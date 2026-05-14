"""
Apple Music genre charts collector — Taylor Swift songs only.

Uses the MusicKit API to fetch genre-specific charts for multiple countries.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from threading import local

from core.config import CHART_LIMIT, DB_DIR, GENRES, SCRIPTS_DIR, WORKERS
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.storefronts import resolve_storefronts
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_genre_charts.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
_THREAD_LOCAL = local()
FIELDNAMES = [
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



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Apple Music genre charts for Taylor Swift songs.")
    parser.add_argument("--countries", nargs="*", default=None)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()



def fetch_genre_chart(session, country: str, genre_id: str) -> list[dict] | None:
    url = f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts?types=songs&genre={genre_id}&limit={CHART_LIMIT}"
    resp = session.get(url)
    if resp.status_code == 400:
        return None
    if resp.status_code == 401:
        raise RuntimeError("Unauthorized while calling Apple Music charts API")
    resp.raise_for_status()

    songs_block = ((resp.json().get("results") or {}).get("songs") or [])
    if not songs_block:
        return []
    items = (songs_block[0] or {}).get("data", [])

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


def worker_session(token: str):
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = build_session()
        session.headers.update(build_auth_headers(token))
        _THREAD_LOCAL.session = session
    return session


def fetch_genre_task(token: str, country: str, genre_id: str, genre_name: str) -> tuple[str, str, str, list[dict] | None]:
    session = worker_session(token)
    songs = fetch_genre_chart(session, country, genre_id)
    return country, genre_id, genre_name, songs


def build_row(
    *,
    today: str,
    scraped_at: str,
    country: str,
    genre_id: str,
    genre_name: str,
    song: dict,
    previous_by_id: dict[tuple[str, ...], int],
    previous_by_name: dict[tuple[str, ...], int],
) -> dict:
    key_by_id = (country, genre_id, song["apple_music_id"])
    key_by_name = (country, genre_id, rank_key(song["song_name"]))
    prev_rank = previous_by_id.get(key_by_id)
    if prev_rank is None:
        prev_rank = previous_by_name.get(key_by_name)
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


def log_country_summary(country: str, results: dict[str, tuple[str, list[dict] | None]]) -> None:
    hits_by_genre: Counter[str] = Counter()
    unique_song_ids: set[str] = set()
    for _genre_id, (genre_name, songs) in results.items():
        if not songs:
            continue
        hits_by_genre[genre_name] += len(songs)
        unique_song_ids.update(song["apple_music_id"] for song in songs)

    total_hits = sum(hits_by_genre.values())
    if total_hits:
        detail = ", ".join(f"{genre}: {count}" for genre, count in hits_by_genre.items())
        duplicate_count = total_hits - len(unique_song_ids)
        duplicate_note = f", {duplicate_count} duplicate genre placement(s)" if duplicate_count else ""
        print(f"{country}: {len(unique_song_ids)} unique Taylor Swift song(s) across genre charts ({detail}{duplicate_note})")
    else:
        print(f"{country}: 0 Taylor Swift song(s) across genre charts")



def main() -> None:
    args = parse_args()
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    base_session = build_session()
    token = fetch_musickit_token(base_session) or fetch_musickit_token(base_session, refresh=True)
    if not token:
        raise RuntimeError("Could not extract Apple Music developer token")
    base_session.headers.update(build_auth_headers(token))
    countries = [c.lower() for c in (args.countries if args.countries is not None else resolve_storefronts(base_session))]
    print(f"[Apple Music] Genre storefronts: {len(countries)}")
    print(f"[Apple Music] Genre chart workers: {WORKERS}")

    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "genre_id", "apple_music_id"],
        today=scraped_at,
    )
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "genre_id", "song_name"],
        today=scraped_at,
    )

    results_by_country: dict[str, dict[str, tuple[str, list[dict] | None]]] = {
        country: {} for country in countries
    }

    if WORKERS == 1:
        for genre_id, genre_name in GENRES:
            for country in countries:
                songs = fetch_genre_chart(base_session, country, genre_id)
                results_by_country[country][genre_id] = (genre_name, songs)
                if len(results_by_country[country]) == len(GENRES):
                    log_country_summary(country, results_by_country[country])
    else:
        remaining_by_country = {country: len(GENRES) for country in countries}
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = [
                executor.submit(fetch_genre_task, token, country, genre_id, genre_name)
                for country in countries
                for genre_id, genre_name in GENRES
            ]
            for future in as_completed(futures):
                country, genre_id, genre_name, songs = future.result()
                results_by_country[country][genre_id] = (genre_name, songs)
                remaining_by_country[country] -= 1
                if remaining_by_country[country] == 0:
                    log_country_summary(country, results_by_country[country])

    rows: list[dict] = []
    for country in countries:
        country_results = results_by_country[country]
        for genre_id, genre_name in GENRES:
            songs = country_results.get(genre_id, (genre_name, None))[1]
            if not songs:
                continue
            for song in songs:
                rows.append(
                    build_row(
                        today=today,
                        scraped_at=scraped_at,
                        country=country,
                        genre_id=genre_id,
                        genre_name=genre_name,
                        song=song,
                        previous_by_id=previous_by_id,
                        previous_by_name=previous_by_name,
                    )
                )

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
