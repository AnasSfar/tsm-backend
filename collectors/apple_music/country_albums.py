"""
Collect Taylor Swift appearances in Apple Music country top albums charts.

Uses the public Apple RSS feeds, which require no authentication.
"""

from __future__ import annotations

import argparse
from datetime import date

from core.config import ARTIST_ID, COUNTRIES, DB_DIR, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_date
from core.export import maybe_run_export
from core.filters import clean_text, rank_key
from core.http import build_session

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
    url = f"https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/100/albums.json"
    resp = session.get(url)
    resp.raise_for_status()
    results = (resp.json().get("feed") or {}).get("results", [])

    albums: list[dict] = []
    for idx, item in enumerate(results, start=1):
        artist_id = str(item.get("artistId", ""))
        artist_name = clean_text(item.get("artistName", ""))
        if artist_id != ARTIST_ID and "taylor swift" not in artist_name.casefold():
            continue
        genres = [clean_text((g or {}).get("name", "")) for g in item.get("genres", [])]
        albums.append(
            {
                "album_name": clean_text(item.get("name", "")),
                "apple_music_id": str(item.get("id", "")),
                "rank": idx,
                "image_url": item.get("artworkUrl100", "").replace("100x100bb", "500x500bb"),
                "url": item.get("url", ""),
                "artist_name": artist_name,
                "release_date": item.get("releaseDate", ""),
                "genre_names": " | ".join([g for g in genres if g]),
            }
        )
    return albums


def main() -> None:
    args = parse_args()
    countries = [c.lower() for c in args.countries]
    today = args.run_date
    session = build_session()

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
