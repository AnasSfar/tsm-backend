"""
Apple Music Top 100 Global collector — Taylor Swift songs only.

Uses Apple Music's public playlist endpoint for the international top 100 playlist.
This is more fragile than the RSS country feeds, but useful as a separate source.
"""

from __future__ import annotations

import argparse
from datetime import date

from core.config import DB_DIR, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_date
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_global.csv"
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
    "album_name",
    "duration_ms",
    "release_date",
    "isrc",
    "content_rating",
    "genre_names",
]
PLAYLIST_IDS = [
    ("fr", "pl.d25f5d1181894928af76c85c967f8f31"),
    ("us", "pl.d25f5d1181894928af76c85c967f8f31"),
]



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Apple Music global top playlist entries for Taylor Swift songs.")
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    return parser.parse_args()



def fetch_global_chart() -> list[dict]:
    session = build_session()
    token = fetch_musickit_token(session) or fetch_musickit_token(session, refresh=True)
    if not token:
        raise RuntimeError("Could not extract Apple Music developer token")
    session.headers.update(build_auth_headers(token))

    for storefront, playlist_id in PLAYLIST_IDS:
        url = f"https://amp-api-edge.music.apple.com/v1/catalog/{storefront}/playlists/{playlist_id}/tracks?limit=100"
        resp = session.get(url)
        if resp.status_code == 401:
            token = fetch_musickit_token(session, refresh=True)
            if not token:
                raise RuntimeError("Developer token refresh failed")
            session.headers.update(build_auth_headers(token))
            resp = session.get(url)
        if resp.status_code != 200:
            continue
        items = (resp.json().get("data") or [])
        if len(items) < 10:
            continue

        songs: list[dict] = []
        for idx, item in enumerate(items, start=1):
            attrs = item.get("attributes", {}) or {}
            if not is_taylor_swift_song(item, attrs):
                continue
            songs.append(
                {
                    "country": storefront,
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
        if songs:
            return songs
    return []



def main() -> None:
    args = parse_args()
    today = args.run_date

    previous = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "song_name"],
        today=today,
    )

    songs = fetch_global_chart()
    rows: list[dict] = []
    for song in songs:
        key = (song["country"], rank_key(song["song_name"]))
        rows.append(
            {
                "date": today,
                "country": song["country"],
                "chart_type": "global",
                "song_name": song["song_name"],
                "apple_music_id": song["apple_music_id"],
                "rank": song["rank"],
                "previous_rank": previous.get(key, ""),
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
        prev = previous.get(key)
        if prev is None:
            marker = "NEW"
        elif prev > song["rank"]:
            marker = f"+{prev - song['rank']}"
        elif prev < song["rank"]:
            marker = f"-{song['rank'] - prev}"
        else:
            marker = "="
        print(f"#{song['rank']:>3} [{marker}] {song['song_name']}")

    rewrite_for_date(CSV_PATH, FIELDNAMES, today, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
