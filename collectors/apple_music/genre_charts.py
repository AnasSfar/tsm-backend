"""
Apple Music genre charts collector — Taylor Swift songs only.

Uses the MusicKit API to fetch genre-specific charts for multiple countries.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from core.config import COUNTRIES, DB_DIR, GENRES, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_genre_charts.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
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
    parser.add_argument("--countries", nargs="*", default=COUNTRIES)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    return parser.parse_args()



def fetch_genre_chart(session, country: str, genre_id: str) -> list[dict] | None:
    url = f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts?types=songs&genre={genre_id}&limit=100"
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



def main() -> None:
    args = parse_args()
    countries = [c.lower() for c in args.countries]
    today = args.run_date
    scraped_at = f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    base_session = build_session()
    token = fetch_musickit_token(base_session) or fetch_musickit_token(base_session, refresh=True)
    if not token:
        raise RuntimeError("Could not extract Apple Music developer token")
    base_session.headers.update(build_auth_headers(token))

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

    rows: list[dict] = []
    for country in countries:
        hits = 0
        for genre_id, genre_name in GENRES:
            try:
                songs = fetch_genre_chart(base_session, country, genre_id)
            except RuntimeError:
                token = fetch_musickit_token(base_session, refresh=True)
                if not token:
                    raise
                base_session.headers.update(build_auth_headers(token))
                songs = fetch_genre_chart(base_session, country, genre_id)

            if songs is None:
                continue
            for song in songs:
                hits += 1
                key_by_id = (country, genre_id, song["apple_music_id"])
                key_by_name = (country, genre_id, rank_key(song["song_name"]))
                prev_rank = previous_by_id.get(key_by_id)
                if prev_rank is None:
                    prev_rank = previous_by_name.get(key_by_name)
                rows.append(
                    {
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
                )
        print(f"{country}: {hits} Taylor Swift chart hit(s)")

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
