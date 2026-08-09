"""
Deezer — Taylor Swift top tracks collector.

Collects the artist's own "top tracks" ranking from Deezer's public
/artist/{id}/top endpoint (no auth, Taylor-only by construction — no
artist filter needed). Keeps a daily CSV history, rerun-safe like the
global chart collector.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from core.config import ARTIST_ID, ARTIST_TOP_LIMIT, BASE_URL, DB_DIR, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import clean_text, rank_key
from core.http import build_session

CSV_PATH = DB_DIR / "deezer_artist_top_tracks.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_deezer.py"
FIELDNAMES = [
    "date",
    "scraped_at",
    "rank",
    "previous_rank",
    "deezer_track_id",
    "title",
    "album_title",
    "link",
    "cover_url",
    "duration",
    "explicit_lyrics",
    "deezer_popularity",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Taylor Swift's top tracks ranking on Deezer.")
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def fetch_top_tracks() -> list[dict]:
    session = build_session()
    url = f"{BASE_URL}/artist/{ARTIST_ID}/top?limit={ARTIST_TOP_LIMIT}"
    resp = session.get(url)
    resp.raise_for_status()
    items = resp.json().get("data") or []

    songs: list[dict] = []
    for item in items:
        album = item.get("album") or {}
        songs.append(
            {
                "deezer_track_id": str(item.get("id", "")),
                "title": clean_text(item.get("title", "")),
                "album_title": clean_text(album.get("title", "")),
                "link": item.get("link", ""),
                "cover_url": album.get("cover_medium", ""),
                "duration": item.get("duration", ""),
                "explicit_lyrics": item.get("explicit_lyrics", ""),
                # Deezer's own internal popularity score (not a chart position).
                "deezer_popularity": item.get("rank", ""),
            }
        )
    return songs


def main() -> None:
    args = parse_args()
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["deezer_track_id"],
        today=scraped_at,
    )
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["title"],
        today=scraped_at,
    )

    songs = fetch_top_tracks()
    rows: list[dict] = []
    for idx, song in enumerate(songs, start=1):
        key_by_id = (song["deezer_track_id"],)
        key_by_name = (rank_key(song["title"]),)
        prev_rank = previous_by_id.get(key_by_id)
        if prev_rank is None:
            prev_rank = previous_by_name.get(key_by_name)
        rows.append(
            {
                "date": today,
                "scraped_at": scraped_at,
                "rank": idx,
                "previous_rank": prev_rank if prev_rank is not None else "",
                "deezer_track_id": song["deezer_track_id"],
                "title": song["title"],
                "album_title": song["album_title"],
                "link": song["link"],
                "cover_url": song["cover_url"],
                "duration": song["duration"],
                "explicit_lyrics": song["explicit_lyrics"],
                "deezer_popularity": song["deezer_popularity"],
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
        print(f"#{idx:>3} [{marker}] {song['title']}")

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
