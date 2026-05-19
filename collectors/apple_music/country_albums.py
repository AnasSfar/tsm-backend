"""
Collect Taylor Swift appearances in Apple Music country top albums charts.

Uses the MusicKit API for real-time data.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from threading import local

from core.config import CHART_LIMIT, DB_DIR, SCRIPTS_DIR, WORKERS
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.storefronts import resolve_storefronts
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_country_albums.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
_THREAD_LOCAL = local()
FIELDNAMES = [
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Apple Music country album charts for Taylor Swift albums.")
    parser.add_argument("--countries", nargs="*", default=None)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def fetch_country(session, country: str) -> list[dict]:
    """Fetch TS albums from the country top albums chart."""
    url = f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts?types=albums&limit={CHART_LIMIT}"
    resp = session.get(url)
    if resp.status_code == 400:
        return []
    if resp.status_code == 401:
        raise RuntimeError("Unauthorized while calling Apple Music charts API")
    resp.raise_for_status()

    albums_block = ((resp.json().get("results") or {}).get("albums") or [])
    if not albums_block:
        return []
    items = (albums_block[0] or {}).get("data", [])

    albums = []
    for idx, item in enumerate(items, start=1):
        attrs = item.get("attributes", {}) or {}
        if not is_taylor_swift_song(item, attrs):
            continue
        genre_names = " | ".join([g for g in (attrs.get("genreNames") or []) if g])
        albums.append(
            {
                "album_name": clean_text(attrs.get("name", "")),
                "apple_music_id": str(item.get("id", "")),
                "rank": idx,
                "image_url": build_artwork_url(attrs.get("artwork"), size=500),
                "url": attrs.get("url", ""),
                "artist_name": clean_text(attrs.get("artistName", "")),
                "release_date": attrs.get("releaseDate", ""),
                "genre_names": genre_names,
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


def fetch_country_task(token: str, country: str) -> tuple[str, list[dict]]:
    session = worker_session(token)
    return country, fetch_country(session, country)


def build_row(
    *,
    today: str,
    scraped_at: str,
    country: str,
    album: dict,
    previous_by_id: dict[tuple[str, ...], int],
    previous_by_name: dict[tuple[str, ...], int],
) -> dict:
    key_by_id = (country, album["apple_music_id"])
    key_by_name = (country, rank_key(album["album_name"]))
    prev_rank = previous_by_id.get(key_by_id)
    if prev_rank is None:
        prev_rank = previous_by_name.get(key_by_name)
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


def main() -> None:
    args = parse_args()
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    session = build_session()
    token = fetch_musickit_token(session) or fetch_musickit_token(session, refresh=True)
    if not token:
        raise RuntimeError("Could not extract Apple Music developer token")
    session.headers.update(build_auth_headers(token))
    countries = [c.lower() for c in (args.countries if args.countries is not None else resolve_storefronts(session))]
    print(f"[Apple Music] Country album storefronts: {len(countries)}")
    print(f"[Apple Music] Country album workers: {WORKERS}")

    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "apple_music_id"],
        today=scraped_at,
    )
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "album_name"],
        today=scraped_at,
        song_field="album_name",
    )

    results_by_country: dict[str, list[dict]] = {}
    if WORKERS == 1:
        for country in countries:
            try:
                albums = fetch_country(session, country)
            except RuntimeError:
                token = fetch_musickit_token(session, refresh=True)
                if not token:
                    raise
                session.headers.update(build_auth_headers(token))
                albums = fetch_country(session, country)
            results_by_country[country] = albums
            print(f"{country}: {len(albums)} Taylor Swift album(s)")
    else:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_country_task, token, country): country for country in countries}
            for future in as_completed(futures):
                _country, albums = future.result()
                results_by_country[_country] = albums
                print(f"{_country}: {len(albums)} Taylor Swift album(s)")

    rows: list[dict] = []
    for country in countries:
        albums = results_by_country.get(country, [])
        for album in albums:
            rows.append(
                build_row(
                    today=today,
                    scraped_at=scraped_at,
                    country=country,
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
