"""
Apple Music music video charts collector — Taylor Swift only.

Uses the MusicKit API to fetch music video charts per country.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from core.config import COUNTRIES, DB_DIR, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_music_video_charts.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
FIELDNAMES = [
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
    parser = argparse.ArgumentParser(description="Collect Apple Music music video charts for Taylor Swift.")
    parser.add_argument("--countries", nargs="*", default=COUNTRIES)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def fetch_country(session, country: str) -> list[dict]:
    url = f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts?types=music-videos&limit=100"
    resp = session.get(url)
    if resp.status_code == 400:
        return []
    if resp.status_code == 401:
        raise RuntimeError("Unauthorized while calling Apple Music charts API")
    resp.raise_for_status()

    videos_block = ((resp.json().get("results") or {}).get("music-videos") or [])
    if not videos_block:
        return []
    items = (videos_block[0] or {}).get("data", [])

    videos = []
    for idx, item in enumerate(items, start=1):
        attrs = item.get("attributes", {}) or {}
        if not is_taylor_swift_song(item, attrs):
            continue
        genre_names = " | ".join(attrs.get("genreNames") or [])
        videos.append(
            {
                "video_name": clean_text(attrs.get("name", "")),
                "apple_music_id": str(item.get("id", "")),
                "rank": idx,
                "image_url": build_artwork_url(attrs.get("artwork")),
                "url": attrs.get("url", ""),
                "artist_name": clean_text(attrs.get("artistName", "")),
                "album_name": clean_text(attrs.get("albumName", "")),
                "duration_ms": attrs.get("durationInMillis", ""),
                "release_date": attrs.get("releaseDate", ""),
                "genre_names": genre_names,
            }
        )
    return videos


def main() -> None:
    args = parse_args()
    countries = [c.lower() for c in args.countries]
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    session = build_session()
    token = fetch_musickit_token(session) or fetch_musickit_token(session, refresh=True)
    if not token:
        raise RuntimeError("Could not extract Apple Music developer token")
    session.headers.update(build_auth_headers(token))

    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "apple_music_id"],
        today=scraped_at,
    )
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "video_name"],
        today=scraped_at,
        song_field="video_name",
    )

    rows: list[dict] = []
    for country in countries:
        try:
            videos = fetch_country(session, country)
        except RuntimeError:
            token = fetch_musickit_token(session, refresh=True)
            if not token:
                raise
            session.headers.update(build_auth_headers(token))
            videos = fetch_country(session, country)
        print(f"{country}: {len(videos)} Taylor Swift video(s)")
        for video in videos:
            key_by_id = (country, video["apple_music_id"])
            key_by_name = (country, rank_key(video["video_name"]))
            prev_rank = previous_by_id.get(key_by_id)
            if prev_rank is None:
                prev_rank = previous_by_name.get(key_by_name)
            rows.append(
                {
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
            )

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
