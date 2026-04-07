"""
Apple Music Global Top Albums collector — Taylor Swift albums only.

Uses the MusicKit API charts endpoint for global album charts.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from core.config import DB_DIR, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, is_taylor_swift_song, rank_key
from core.http import build_session
from core.token import build_auth_headers, fetch_musickit_token

CSV_PATH = DB_DIR / "apple_music_global_albums.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
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
    parser = argparse.ArgumentParser(description="Collect Apple Music global top album chart for Taylor Swift.")
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def fetch_global_albums(session) -> list[dict]:
    """Aggregate TS albums across all storefronts, keeping best rank per album ID."""
    from core.config import COUNTRIES

    best: dict[str, dict] = {}  # apple_music_id → best entry

    for storefront in COUNTRIES:
        url = f"https://amp-api-edge.music.apple.com/v1/catalog/{storefront}/charts?types=albums&limit=100"
        try:
            resp = session.get(url)
        except Exception:
            continue
        if resp.status_code == 401:
            raise RuntimeError("Unauthorized while calling Apple Music charts API")
        if resp.status_code != 200:
            continue

        albums_block = ((resp.json().get("results") or {}).get("albums") or [])
        if not albums_block:
            continue
        items = (albums_block[0] or {}).get("data", [])
        if not items:
            continue

        for idx, item in enumerate(items, start=1):
            attrs = item.get("attributes", {}) or {}
            if not is_taylor_swift_song(item, attrs):
                continue
            album_id = str(item.get("id", ""))
            if not album_id:
                continue
            # Keep entry with best (lowest) rank across all storefronts
            existing = best.get(album_id)
            if existing is None or idx < existing["rank"]:
                genre_names = " | ".join(attrs.get("genreNames") or [])
                best[album_id] = {
                    "country": storefront,
                    "album_name": clean_text(attrs.get("name", "")),
                    "apple_music_id": album_id,
                    "rank": idx,
                    "image_url": build_artwork_url(attrs.get("artwork"), size=500),
                    "url": attrs.get("url", ""),
                    "artist_name": clean_text(attrs.get("artistName", "")),
                    "release_date": attrs.get("releaseDate", ""),
                    "genre_names": genre_names,
                }

    return sorted(best.values(), key=lambda x: x["rank"])


def main() -> None:
    args = parse_args()
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
        key_fields=["country", "album_name"],
        today=scraped_at,
        song_field="album_name",
    )

    albums = fetch_global_albums(session)
    rows: list[dict] = []
    for album in albums:
        key_by_id = (album["country"], album["apple_music_id"])
        key_by_name = (album["country"], rank_key(album["album_name"]))
        prev_rank = previous_by_id.get(key_by_id)
        if prev_rank is None:
            prev_rank = previous_by_name.get(key_by_name)
        rows.append(
            {
                "date": today,
                "scraped_at": scraped_at,
                "country": album["country"],
                "chart_type": "global_albums",
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
        prev = prev_rank
        marker = "NEW" if prev is None else f"+{prev-album['rank']}" if prev > album["rank"] else f"-{album['rank']-prev}" if prev < album["rank"] else "="
        print(f"#{album['rank']:>3} [{marker}] {album['album_name']}")

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
