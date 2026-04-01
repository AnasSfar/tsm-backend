"""
Collect Taylor Swift appearances in Apple Music country charts.

Uses the MusicKit API (same as genre_charts.py) for real-time data.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from core.config import ARTIST_ID, COUNTRIES, DB_DIR, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_country_charts.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
FIELDNAMES = [
    "date",
    "scraped_at",
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
    url = f"https://amp-api-edge.music.apple.com/v1/catalog/{country}/charts?types=songs&limit=100"
    resp = session.get(url)
    if resp.status_code == 400:
        return []
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
    session = base_session

    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "apple_music_id"],
        today=scraped_at,
    )
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["country", "song_name"],
        today=scraped_at,
    )

    rows: list[dict] = []
    for country in countries:
        try:
            songs = fetch_country(session, country)
        except RuntimeError:
            token = fetch_musickit_token(session, refresh=True)
            if not token:
                raise
            session.headers.update(build_auth_headers(token))
            songs = fetch_country(session, country)
        print(f"{country}: {len(songs)} Taylor Swift song(s)")
        for song in songs:
            key_by_id = (country, song["apple_music_id"])
            key_by_name = (country, rank_key(song["song_name"]))
            prev_rank = previous_by_id.get(key_by_id)
            if prev_rank is None:
                prev_rank = previous_by_name.get(key_by_name)
            rows.append(
                {
                    "date": today,
                    "scraped_at": scraped_at,
                    "country": country,
                    "chart_type": "country",
                    "song_name": song["song_name"],
                    "apple_music_id": song["apple_music_id"],
                    "rank": song["rank"],
                    "previous_rank": prev_rank if prev_rank is not None else "",
                    "image_url": song["image_url"],
                    "url": song["url"],
                    "artist_name": song["artist_name"],
                }
            )

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
