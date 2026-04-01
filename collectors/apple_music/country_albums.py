"""
Collect Taylor Swift appearances in Apple Music country top albums charts.

Uses the MusicKit API for real-time data.
"""

from __future__ import annotations

import argparse
from datetime import date

from core.config import COUNTRIES, DB_DIR, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_date
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_country_albums.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
FIELDNAMES = [
    "date",
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
    parser.add_argument("--countries", nargs="*", default=COUNTRIES)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    return parser.parse_args()


def fetch_country(session, country: str) -> list[dict]:
    url = f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts?types=albums&limit=100"
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

    albums: list[dict] = []
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


def main() -> None:
    args = parse_args()
    countries = [c.lower() for c in args.countries]
    today = args.run_date

    session = build_session()
    token = fetch_musickit_token(session) or fetch_musickit_token(session, refresh=True)
    if not token:
        raise RuntimeError("Could not extract Apple Music developer token")
    session.headers.update(build_auth_headers(token))

    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "apple_music_id"],
        today=today,
    )
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "album_name"],
        today=today,
        song_field="album_name",
    )

    rows: list[dict] = []
    for country in countries:
        try:
            albums = fetch_country(session, country)
        except RuntimeError:
            token = fetch_musickit_token(session, refresh=True)
            if not token:
                raise
            session.headers.update(build_auth_headers(token))
            albums = fetch_country(session, country)
        print(f"{country}: {len(albums)} Taylor Swift album(s)")
        for album in albums:
            key_by_id = (country, album["apple_music_id"])
            key_by_name = (country, rank_key(album["album_name"]))
            prev_rank = previous_by_id.get(key_by_id)
            if prev_rank is None:
                prev_rank = previous_by_name.get(key_by_name)
            rows.append(
                {
                    "date": today,
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
            )

    rewrite_for_date(CSV_PATH, FIELDNAMES, today, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
