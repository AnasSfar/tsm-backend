"""
Apple Music — Taylor Swift top songs collector.

Collects the artist page "top songs" ranking from Apple Music for one storefront,
keeps a daily CSV history, and preserves rerun safety by rewriting today's rows.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from core.config import ARTIST_ID, DB_DIR, DEFAULT_STOREFRONT, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, rank_key
from core.http import build_session
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_ts_top_songs.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
FIELDNAMES = [
    "date",
    "scraped_at",
    "storefront",
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
    parser = argparse.ArgumentParser(description="Collect Taylor Swift Apple Music top songs.")
    parser.add_argument("storefront", nargs="?", default=DEFAULT_STOREFRONT)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    return parser.parse_args()



def fetch_top_songs(storefront: str) -> list[dict]:
    session = build_session()
    token = fetch_musickit_token(session)
    if not token:
        token = fetch_musickit_token(session, refresh=True)
    if not token:
        raise RuntimeError("Could not extract Apple Music developer token")

    session.headers.update(build_auth_headers(token))

    songs: list[dict] = []
    offset = 0
    limit = 100

    while True:
        url = (
            f"https://amp-api-edge.music.apple.com/v1/catalog/{storefront}/artists/{ARTIST_ID}"
            f"/view/top-songs?limit={limit}&offset={offset}"
        )
        resp = session.get(url)
        if resp.status_code == 401:
            token = fetch_musickit_token(session, refresh=True)
            if not token:
                raise RuntimeError("Developer token refresh failed")
            session.headers.update(build_auth_headers(token))
            resp = session.get(url)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])

        for item in items:
            attrs = item.get("attributes", {}) or {}
            song_name = clean_text(attrs.get("name", ""))
            if not song_name:
                continue
            songs.append(
                {
                    "song_name": song_name,
                    "apple_music_id": str(item.get("id", "")),
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

        if len(items) < limit or not data.get("next"):
            break
        offset += limit

    return songs



def main() -> None:
    args = parse_args()
    storefront = args.storefront.lower().strip()
    today = args.run_date
    scraped_at = f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    # Prefer Apple Music IDs to avoid collisions between different versions
    # sharing the same display name.
    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["storefront", "apple_music_id"],
        today=scraped_at,
    )
    # Backward-compatible fallback for any historical rows without IDs.
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["storefront", "song_name"],
        today=scraped_at,
    )

    songs = fetch_top_songs(storefront)
    rows: list[dict] = []
    for idx, song in enumerate(songs, start=1):
        key_by_id = (storefront, song["apple_music_id"])
        key_by_name = (storefront, rank_key(song["song_name"]))
        prev_rank = previous_by_id.get(key_by_id)
        if prev_rank is None:
            prev_rank = previous_by_name.get(key_by_name)
        rows.append(
            {
                "date": today,
                "scraped_at": scraped_at,
                "storefront": storefront,
                "song_name": song["song_name"],
                "apple_music_id": song["apple_music_id"],
                "rank": idx,
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
        prev = prev_rank
        if prev is None:
            marker = "NEW"
        elif prev > idx:
            marker = f"+{prev - idx}"
        elif prev < idx:
            marker = f"-{idx - prev}"
        else:
            marker = "="
        print(f"#{idx:>3} [{marker}] {song['song_name']}")

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
