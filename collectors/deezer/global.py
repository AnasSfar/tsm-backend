"""
Deezer global chart collector — Taylor Swift tracks only.

Uses Deezer's public /chart/0/tracks endpoint (no auth). This chart is
geolocated by the request's source IP (Deezer has no explicit country
param) — treat "global" as "as seen from wherever this collector runs",
not a literal worldwide chart. See collectors/deezer/CONTEXTE.md.

CONFIRMED 2026-08-09 (per Anas): this is actually the France chart, not
worldwide. Deliberately left as "global" everywhere (file name, CSV,
DEEZER_GLOBAL_* constants, JSON keys, UI labels) for now — paused mid-fix,
picking this back up later. TODO when resumed: rename to
france.py / deezer_france_chart.csv / DEEZER_FRANCE_* / "france_chart" JSON
key / relabel "Global Chart" -> "France Chart" in the frontend, per the
decision in conversation (full internal rename, not just UI text — cheap
now since only ~1 day of history exists and it isn't deployed to the VPS
yet).
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from core.config import BASE_URL, ARTIST_ID, CHART_LIMIT, DB_DIR, SCRIPTS_DIR
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import clean_text, rank_key
from core.http import build_session

CSV_PATH = DB_DIR / "deezer_global_chart.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_deezer.py"
FIELDNAMES = [
    "date",
    "scraped_at",
    "rank",
    "previous_rank",
    "deezer_track_id",
    "title",
    "artist_name",
    "album_title",
    "link",
    "cover_url",
    "duration",
    "explicit_lyrics",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Deezer global chart entries for Taylor Swift tracks.")
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def fetch_global_chart() -> list[dict]:
    session = build_session()
    url = f"{BASE_URL}/chart/0/tracks?limit={CHART_LIMIT}"
    resp = session.get(url)
    resp.raise_for_status()
    items = resp.json().get("data") or []

    songs: list[dict] = []
    for idx, item in enumerate(items, start=1):
        artist = item.get("artist") or {}
        if str(artist.get("id", "")) != ARTIST_ID:
            continue
        album = item.get("album") or {}
        songs.append(
            {
                "rank": idx,
                "deezer_track_id": str(item.get("id", "")),
                "title": clean_text(item.get("title", "")),
                "artist_name": clean_text(artist.get("name", "")),
                "album_title": clean_text(album.get("title", "")),
                "link": item.get("link", ""),
                "cover_url": album.get("cover_medium", ""),
                "duration": item.get("duration", ""),
                "explicit_lyrics": item.get("explicit_lyrics", ""),
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

    songs = fetch_global_chart()
    rows: list[dict] = []
    for song in songs:
        key_by_id = (song["deezer_track_id"],)
        key_by_name = (rank_key(song["title"]),)
        prev_rank = previous_by_id.get(key_by_id)
        if prev_rank is None:
            prev_rank = previous_by_name.get(key_by_name)
        rows.append(
            {
                "date": today,
                "scraped_at": scraped_at,
                "rank": song["rank"],
                "previous_rank": prev_rank if prev_rank is not None else "",
                "deezer_track_id": song["deezer_track_id"],
                "title": song["title"],
                "artist_name": song["artist_name"],
                "album_title": song["album_title"],
                "link": song["link"],
                "cover_url": song["cover_url"],
                "duration": song["duration"],
                "explicit_lyrics": song["explicit_lyrics"],
            }
        )
        prev = prev_rank
        if prev is None:
            marker = "NEW"
        elif prev > song["rank"]:
            marker = f"+{prev - song['rank']}"
        elif prev < song["rank"]:
            marker = f"-{song['rank'] - prev}"
        else:
            marker = "="
        print(f"#{song['rank']:>3} [{marker}] {song['title']}")

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
