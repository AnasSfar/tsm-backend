"""iTunes Store purchase charts collector — Taylor Swift only.

Fetches the legacy iTunes RSS Top Songs + Top Albums feeds for every storefront
(same country set as the Apple Music country charts), filters to Taylor Swift,
and writes two idempotent-by-snapshot CSVs:

    db/itunes_top_songs.csv
    db/itunes_top_albums.csv

- A storefront with no feed for a chart (HTTP 404) is a legitimately empty
  chart, not a failure.
- Network / 5xx errors skip the storefront but abort the run if they exceed
  MAX_FAILURE_PCT (never publish a partial day).
- This pipeline never posts to X and never commits git — only the R2 upload
  distributes the data (same model as Apple Music / Deezer).
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from threading import local

from core.config import (
    CHART_LIMIT,
    CRITICAL_STOREFRONTS,
    DB_DIR,
    MAX_FAILURE_PCT,
    SCRIPTS_DIR,
    WORKERS,
)
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.http import build_session
from core.rss import ITunesFeedError, fetch_storefront
from core.storefronts import resolve_storefronts

from collectors.apple_music.core.filters import rank_key
from collectors.spotify.core.data_paths import itunes_daily_csv

SONGS_CSV = DB_DIR / "itunes_top_songs.csv"
ALBUMS_CSV = DB_DIR / "itunes_top_albums.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_itunes.py"
_THREAD_LOCAL = local()

SONG_FIELDNAMES = [
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
    "album_name",
    "genre_names",
    "release_date",
]
ALBUM_FIELDNAMES = [
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
    "genre_names",
    "release_date",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect iTunes Store Top Songs / Top Albums purchase charts for Taylor Swift."
    )
    parser.add_argument("--countries", nargs="*", default=None)
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def worker_session():
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = build_session()
        _THREAD_LOCAL.session = session
    return session


def fetch_task(storefront: str):
    return storefront, fetch_storefront(worker_session(), storefront)


def build_song_row(*, today, scraped_at, country, song, previous_by_id, previous_by_name) -> dict:
    prev_rank = previous_by_id.get((country, song["apple_music_id"]))
    if prev_rank is None:
        prev_rank = previous_by_name.get((country, rank_key(song["song_name"])))
    return {
        "date": today,
        "scraped_at": scraped_at,
        "country": country,
        "chart_type": "itunes_country",
        "song_name": song["song_name"],
        "apple_music_id": song["apple_music_id"],
        "rank": song["rank"],
        "previous_rank": prev_rank if prev_rank is not None else "",
        "image_url": song["image_url"],
        "url": song["url"],
        "artist_name": song["artist_name"],
        "album_name": song["album_name"],
        "genre_names": song["genre_names"],
        "release_date": song["release_date"],
    }


def build_album_row(*, today, scraped_at, country, album, previous_by_id, previous_by_name) -> dict:
    prev_rank = previous_by_id.get((country, album["apple_music_id"]))
    if prev_rank is None:
        prev_rank = previous_by_name.get((country, rank_key(album["album_name"])))
    return {
        "date": today,
        "scraped_at": scraped_at,
        "country": country,
        "chart_type": "itunes_country_albums",
        "album_name": album["album_name"],
        "apple_music_id": album["apple_music_id"],
        "rank": album["rank"],
        "previous_rank": prev_rank if prev_rank is not None else "",
        "image_url": album["image_url"],
        "url": album["url"],
        "artist_name": album["artist_name"],
        "genre_names": album["genre_names"],
        "release_date": album["release_date"],
    }


def main() -> None:
    args = parse_args()
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    storefronts = [c.lower() for c in (args.countries if args.countries is not None else resolve_storefronts())]
    print(f"[iTunes] Storefronts: {len(storefronts)} | limit={CHART_LIMIT} | workers={WORKERS}")

    previous = {
        "songs_by_id": load_previous_ranks(SONGS_CSV, key_fields=["country", "apple_music_id"], today=scraped_at),
        "songs_by_name": load_previous_ranks(SONGS_CSV, key_fields=["country", "song_name"], today=scraped_at),
        "albums_by_id": load_previous_ranks(ALBUMS_CSV, key_fields=["country", "apple_music_id"], today=scraped_at),
        "albums_by_name": load_previous_ranks(
            ALBUMS_CSV, key_fields=["country", "album_name"], today=scraped_at, song_field="album_name"
        ),
    }

    results: dict[str, tuple[list[dict], list[dict]]] = {}
    failures: list[tuple[str, str]] = []

    if WORKERS == 1:
        for storefront in storefronts:
            try:
                results[storefront] = fetch_storefront(build_session(), storefront)
            except ITunesFeedError as exc:
                failures.append((storefront, str(exc)))
                continue
            songs, albums = results[storefront]
            print(f"{storefront}: {len(songs)} song(s), {len(albums)} album(s)")
    else:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_task, storefront): storefront for storefront in storefronts}
            for future in as_completed(futures):
                storefront = futures[future]
                try:
                    _storefront, result = future.result()
                except ITunesFeedError as exc:
                    failures.append((storefront, str(exc)))
                    continue
                results[storefront] = result
                songs, albums = result
                print(f"{storefront}: {len(songs)} song(s), {len(albums)} album(s)")

    # Throttled storefronts (esp. us/gb — the ones that matter most) get one
    # more try, sequentially, with a fresh session and generous spacing.
    # If more than half the run failed, the whole IP is throttled — a slow
    # sequential sweep won't help, so only rescue the critical storefronts.
    if failures:
        all_failed = {sf: err for sf, err in failures}
        if len(failures) > len(storefronts) / 2:
            retry_targets = [sf for sf in all_failed if sf in CRITICAL_STOREFRONTS]
            print(
                f"[iTunes] {len(failures)}/{len(storefronts)} failed — IP likely throttled; "
                f"sequential retry limited to critical storefront(s): {', '.join(retry_targets) or 'none'}"
            )
        else:
            retry_targets = list(all_failed)
            print(f"[iTunes] Sequential retry for {len(retry_targets)} throttled storefront(s): {', '.join(retry_targets)}")

        retry_session = build_session()
        for storefront in retry_targets:
            time.sleep(5.0)
            try:
                results[storefront] = fetch_storefront(retry_session, storefront)
                songs, albums = results[storefront]
                all_failed.pop(storefront, None)
                print(f"{storefront} (retry): {len(songs)} song(s), {len(albums)} album(s)")
            except ITunesFeedError as exc:
                all_failed[storefront] = str(exc)
        failures = list(all_failed.items())

    if failures:
        for storefront, error in failures:
            print(f"[iTunes] Warning: storefront {storefront} skipped: {error}")
        failed_names = {sf for sf, _err in failures}
        lost_critical = sorted(failed_names & CRITICAL_STOREFRONTS)
        failure_pct = len(failures) * 100.0 / max(len(storefronts), 1)
        if lost_critical:
            print(
                f"[iTunes] ERROR: critical storefront(s) {', '.join(lost_critical)} failed "
                f"after retry — aborting (set ITUNES_CRITICAL_STOREFRONTS='' to override)"
            )
            sys.exit(1)
        if failure_pct > MAX_FAILURE_PCT:
            print(
                f"[iTunes] ERROR: {len(failures)}/{len(storefronts)} storefronts failed "
                f"({failure_pct:.1f}% > {MAX_FAILURE_PCT}%), aborting to avoid publishing a partial day"
            )
            sys.exit(1)
        print(
            f"[iTunes] Proceeding with {len(storefronts) - len(failures)}/{len(storefronts)} storefronts "
            f"({failure_pct:.1f}% missing — next snapshot will backfill)"
        )

    song_rows: list[dict] = []
    album_rows: list[dict] = []
    for storefront in storefronts:
        if storefront not in results:
            continue
        songs, albums = results[storefront]
        for song in songs:
            song_rows.append(
                build_song_row(
                    today=today,
                    scraped_at=scraped_at,
                    country=storefront,
                    song=song,
                    previous_by_id=previous["songs_by_id"],
                    previous_by_name=previous["songs_by_name"],
                )
            )
        for album in albums:
            album_rows.append(
                build_album_row(
                    today=today,
                    scraped_at=scraped_at,
                    country=storefront,
                    album=album,
                    previous_by_id=previous["albums_by_id"],
                    previous_by_name=previous["albums_by_name"],
                )
            )

    day = scraped_at[:10]
    rewrite_for_snapshot(SONGS_CSV, SONG_FIELDNAMES, scraped_at, song_rows)
    print(f"Wrote {len(song_rows)} rows -> {itunes_daily_csv(day, SONGS_CSV.name)}")
    rewrite_for_snapshot(ALBUMS_CSV, ALBUM_FIELDNAMES, scraped_at, album_rows)
    print(f"Wrote {len(album_rows)} rows -> {itunes_daily_csv(day, ALBUMS_CSV.name)}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
