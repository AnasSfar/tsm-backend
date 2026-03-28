"""
Collect Taylor Swift appearances in Apple Music country charts.

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

CSV_PATH = DB_DIR / "apple_music_country_charts.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
FIELDNAMES = [
    "date",
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



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Apple Music country charts for Taylor Swift songs.")
    parser.add_argument("--countries", nargs="*", default=COUNTRIES)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    return parser.parse_args()



def fetch_country(session, country: str) -> list[dict]:
    url = f"https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/100/songs.json"
    resp = session.get(url)
    resp.raise_for_status()
    results = (resp.json().get("feed") or {}).get("results", [])

    songs: list[dict] = []
    for idx, item in enumerate(results, start=1):
        artist_id = str(item.get("artistId", ""))
        artist_name = clean_text(item.get("artistName", ""))
        if artist_id != ARTIST_ID and "taylor swift" not in artist_name.casefold():
            continue
        songs.append(
            {
                "song_name": clean_text(item.get("name", "")),
                "apple_music_id": str(item.get("id", "")),
                "rank": idx,
                "image_url": item.get("artworkUrl100", "").replace("100x100bb", "300x300bb"),
                "url": item.get("url", ""),
                "artist_name": artist_name,
            }
        )
    return songs



def main() -> None:
    args = parse_args()
    countries = [c.lower() for c in args.countries]
    today = args.run_date
    session = build_session()

    previous = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "song_name"],
        today=today,
    )

    rows: list[dict] = []
    for country in countries:
        songs = fetch_country(session, country)
        print(f"{country}: {len(songs)} Taylor Swift song(s)")
        for song in songs:
            key = (country, rank_key(song["song_name"]))
            rows.append(
                {
                    "date": today,
                    "country": country,
                    "chart_type": "country",
                    "song_name": song["song_name"],
                    "apple_music_id": song["apple_music_id"],
                    "rank": song["rank"],
                    "previous_rank": previous.get(key, ""),
                    "image_url": song["image_url"],
                    "url": song["url"],
                    "artist_name": song["artist_name"],
                }
            )

    rewrite_for_date(CSV_PATH, FIELDNAMES, today, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
