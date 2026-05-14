"""
Apple Music — Taylor Swift top music videos collector.

Collects the artist page "top music videos" ranking from Apple Music for one storefront.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from core.config import ARTIST_ID, CHART_LIMIT, DB_DIR, DEFAULT_STOREFRONT, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, rank_key
from core.http import build_session
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_ts_top_videos.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
FIELDNAMES = [
    "date",
    "scraped_at",
    "storefront",
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
    parser = argparse.ArgumentParser(description="Collect Taylor Swift Apple Music top music videos.")
    parser.add_argument("storefront", nargs="?", default=DEFAULT_STOREFRONT)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def fetch_top_videos(session, storefront: str) -> list[dict]:
    url = (
        f"https://amp-api-edge.music.apple.com/v1/catalog/{storefront}/artists/{ARTIST_ID}"
        f"/view/top-music-videos?limit={CHART_LIMIT}"
    )
    resp = session.get(url)
    if resp.status_code == 401:
        raise RuntimeError("Unauthorized while calling Apple Music API")
    resp.raise_for_status()

    items = resp.json().get("data", [])
    videos = []
    for idx, item in enumerate(items, start=1):
        attrs = item.get("attributes", {}) or {}
        video_name = clean_text(attrs.get("name", ""))
        if not video_name:
            continue
        genre_names = " | ".join(attrs.get("genreNames") or [])
        videos.append(
            {
                "video_name": video_name,
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
    storefront = args.storefront.lower().strip()
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    session = build_session()
    token = fetch_musickit_token(session) or fetch_musickit_token(session, refresh=True)
    if not token:
        raise RuntimeError("Could not extract Apple Music developer token")
    session.headers.update(build_auth_headers(token))

    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["storefront", "apple_music_id"],
        today=scraped_at,
    )
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["storefront", "video_name"],
        today=scraped_at,
        song_field="video_name",
    )

    videos = fetch_top_videos(session, storefront)
    rows: list[dict] = []
    for video in videos:
        key_by_id = (storefront, video["apple_music_id"])
        key_by_name = (storefront, rank_key(video["video_name"]))
        prev_rank = previous_by_id.get(key_by_id)
        if prev_rank is None:
            prev_rank = previous_by_name.get(key_by_name)
        rows.append(
            {
                "date": today,
                "scraped_at": scraped_at,
                "storefront": storefront,
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
        prev = prev_rank
        marker = "NEW" if prev is None else f"+{prev-video['rank']}" if prev > video["rank"] else f"-{video['rank']-prev}" if prev < video["rank"] else "="
        print(f"#{video['rank']:>3} [{marker}] {video['video_name']}")

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
