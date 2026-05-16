"""
Collect Taylor Swift appearances in Apple Music country genre album charts.

Uses the MusicKit charts endpoint with types=albums and genre filters.
"""

from __future__ import annotations

import argparse
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
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_genre_album_charts.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
_THREAD_LOCAL = local()
FIELDNAMES = [
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
    parser = argparse.ArgumentParser(description="Collect Apple Music genre album charts for Taylor Swift albums.")
    parser.add_argument("--countries", nargs="*", default=None)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def fetch_genre_album_chart(session, country: str, genre_id: str) -> list[dict] | None:
    url = f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts?types=albums&genre={genre_id}&limit={CHART_LIMIT}"
    resp = session.get(url)
    if resp.status_code in (400, 404):
        return None
    if resp.status_code == 401:
        raise RuntimeError("Unauthorized while calling Apple Music charts API")
    resp.raise_for_status()

    albums_block = ((resp.json().get("results") or {}).get("albums") or [])
    if not albums_block:
        return []
    items = (albums_block[0] or {}).get("data", [])

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


def worker_session(token: str):
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = build_session()
        session.headers.update(build_auth_headers(token))
        _THREAD_LOCAL.session = session
    return session


def fetch_task(token: str, country: str, genre_id: str, genre_name: str) -> tuple[str, str, str, list[dict] | None]:
    session = worker_session(token)
    albums = fetch_genre_album_chart(session, country, genre_id)
    return country, genre_id, genre_name, albums


def log_fetch_warning(country: str, genre_id: str, genre_name: str, exc: BaseException) -> None:
    print(
        "[Apple Music] Warning: skipping genre album chart "
        f"{country}/{genre_id} ({genre_name}): {exc}"
    )


def build_row(
    *,
    today: str,
    scraped_at: str,
    country: str,
    genre_id: str,
    genre_name: str,
    album: dict,
    previous_by_id: dict[tuple[str, ...], int],
    previous_by_name: dict[tuple[str, ...], int],
) -> dict:
    key_by_id = (country, genre_id, album["apple_music_id"])
    key_by_name = (country, genre_id, rank_key(album["album_name"]))
    prev_rank = previous_by_id.get(key_by_id)
    if prev_rank is None:
        prev_rank = previous_by_name.get(key_by_name)
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


def log_country_summary(country: str, results: dict[str, tuple[str, list[dict] | None]]) -> None:
    hits_by_genre: Counter[str] = Counter()
    unique_album_ids: set[str] = set()
    for _genre_id, (genre_name, albums) in results.items():
        if not albums:
            continue
        hits_by_genre[genre_name] += len(albums)
        unique_album_ids.update(album["apple_music_id"] for album in albums)

    total_hits = sum(hits_by_genre.values())
    if total_hits:
        detail = ", ".join(f"{genre}: {count}" for genre, count in hits_by_genre.items())
        duplicate_count = total_hits - len(unique_album_ids)
        duplicate_note = f", {duplicate_count} duplicate genre placement(s)" if duplicate_count else ""
        print(f"{country}: {len(unique_album_ids)} unique Taylor Swift album(s) across genre album charts ({detail}{duplicate_note})")
    else:
        print(f"{country}: 0 Taylor Swift album(s) across genre album charts")


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
    print(f"[Apple Music] Genre album storefronts: {len(countries)}")
    print(f"[Apple Music] Genre album chart workers: {WORKERS}")

    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "genre_id", "apple_music_id"],
        today=scraped_at,
    )
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "genre_id", "album_name"],
        today=scraped_at,
        song_field="album_name",
    )

    results_by_country: dict[str, dict[str, tuple[str, list[dict] | None]]] = {
        country: {} for country in countries
    }

    if WORKERS == 1:
        for genre_id, genre_name in GENRES:
            for country in countries:
                try:
                    albums = fetch_genre_album_chart(base_session, country, genre_id)
                except RequestException as exc:
                    log_fetch_warning(country, genre_id, genre_name, exc)
                    albums = None
                results_by_country[country][genre_id] = (genre_name, albums)
                if len(results_by_country[country]) == len(GENRES):
                    log_country_summary(country, results_by_country[country])
    else:
        remaining_by_country = {country: len(GENRES) for country in countries}
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(fetch_task, token, country, genre_id, genre_name): (country, genre_id, genre_name)
                for country in countries
                for genre_id, genre_name in GENRES
            }
            for future in as_completed(futures):
                country, genre_id, genre_name = futures[future]
                try:
                    _country, _genre_id, _genre_name, albums = future.result()
                except RequestException as exc:
                    log_fetch_warning(country, genre_id, genre_name, exc)
                    albums = None
                results_by_country[country][genre_id] = (genre_name, albums)
                remaining_by_country[country] -= 1
                if remaining_by_country[country] == 0:
                    log_country_summary(country, results_by_country[country])

    rows: list[dict] = []
    for country in countries:
        country_results = results_by_country[country]
        for genre_id, genre_name in GENRES:
            albums = country_results.get(genre_id, (genre_name, None))[1]
            if not albums:
                continue
            for album in albums:
                rows.append(
                    build_row(
                        today=today,
                        scraped_at=scraped_at,
                        country=country,
                        genre_id=genre_id,
                        genre_name=genre_name,
                        album=album,
                        previous_by_id=previous_by_id,
                        previous_by_name=previous_by_name,
                    )
                )

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
