"""
Apple Music — Taylor Swift "TS Top Songs" GLOBAL composite collector.

Aggregates the artist-page top-songs ranking across every discovered Apple
Music storefront into one composite ranking, using the same rank -> points
power-law curve TayBoard already uses for Apple Music scoring.

Writes a SEPARATE csv from ts_page.py's apple_music_ts_top_songs.csv on
purpose: that file is a direct TayBoard scoring input
(collectors/billboard/swift_top_100.py::_weekly_apple_music_ts_points) and
must keep reflecting a single storefront so TayBoard's calibrated weekly
score stays untouched. This composite only feeds the site's "TS Top Songs"
display (see scripts/export_apple_music.py::TOP_SONGS_CSV).
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from threading import local

from requests import RequestException

from core.config import ARTIST_ID, DB_DIR, SCRIPTS_DIR, WORKERS
from core.csv_utils import load_previous_ranks, rewrite_for_snapshot
from core.export import maybe_run_export
from core.filters import build_artwork_url, clean_text, rank_key
from core.http import build_session
from core.storefronts import resolve_storefronts
from core.token import TokenManager, build_auth_headers

CSV_PATH = DB_DIR / "apple_music_ts_top_songs_global.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_apple_music.py"
_THREAD_LOCAL = local()

GLOBAL_STOREFRONT_TAG = "global"
MAX_FAILURE_PCT = 5.0
PAGE_LIMIT = 100

# Tail ranks contribute negligible composite score under the power-law curve
# below (rank 200 ~= 9 pts vs ~500 pts for rank 1), so capping pagination
# depth per storefront keeps request volume bounded across 100+ storefronts.
DEPTH_CAP = max(1, int(os.getenv("APPLE_MUSIC_TS_GLOBAL_DEPTH", "200")))
MAX_PAGES = max(1, -(-DEPTH_CAP // PAGE_LIMIT))

# The site only ever displays the latest snapshot, so this (expensive,
# 100+ storefront) composite only needs to refresh once/day even though
# run_apple_music.py itself runs every 4h. Other invocations skip.
RUN_HOUR = os.getenv("APPLE_MUSIC_TS_GLOBAL_HOUR", "02").strip()

# Same market-weight table as TayBoard's Apple Music scoring
# (collectors/billboard/swift_top_100.py::AM_MARKET_WEIGHTS), duplicated
# locally to avoid a cross-collector import — keep in sync if that table
# ever changes. Without this, a storefront where TS is a niche act would
# count the same as the US in the composite.
MARKET_WEIGHT_DEFAULT = 0.08
MARKET_WEIGHTS: dict[str, float] = {
    # Tier 1
    "us": 1.00,
    "gb": 0.70,
    # Tier 2
    "jp": 0.55,
    "de": 0.50,
    "fr": 0.50,
    "ca": 0.50,
    "au": 0.45,
    # Tier 3
    "br": 0.35,
    "mx": 0.35,
    "it": 0.35,
    "es": 0.35,
    "nl": 0.30,
    "se": 0.30,
    "no": 0.30,
    "dk": 0.25,
    "ie": 0.25,
    "nz": 0.25,
    "in": 0.25,
    "kr": 0.25,
    # Tier 4
    "za": 0.20,
    "ph": 0.20,
    "id": 0.20,
    "th": 0.20,
    "my": 0.20,
    "sg": 0.20,
    "tw": 0.20,
    "hk": 0.20,
    "pl": 0.20,
    "be": 0.18,
    "ch": 0.18,
    "at": 0.18,
    "pt": 0.18,
    "tr": 0.18,
    "ar": 0.18,
    "cl": 0.18,
    "co": 0.18,
}


def _market_weight(storefront: str) -> float:
    return MARKET_WEIGHTS.get(storefront, MARKET_WEIGHT_DEFAULT)

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
    parser = argparse.ArgumentParser(
        description="Aggregate Taylor Swift Apple Music top songs across all storefronts into one composite ranking."
    )
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    parser.add_argument("--force", action="store_true", help="Bypass the once-a-day run gate.")
    return parser.parse_args()


def _rank_to_score(rank: int) -> float:
    """Same power-law curve as TayBoard's Apple Music scoring
    (collectors/billboard/swift_top_100.py::_rank_to_am_units_score),
    duplicated locally to avoid a cross-collector import — keep in sync if
    that curve ever changes."""
    if rank < 1:
        return 0.0
    return 500.0 / (rank ** 0.75)


def worker_session():
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = build_session()
        _THREAD_LOCAL.session = session
    return session


def _get_with_auth(session, manager: TokenManager, url: str):
    token = manager.get()
    resp = session.get(url, headers=build_auth_headers(token))
    if resp.status_code == 401:
        token = manager.refresh(token)
        resp = session.get(url, headers=build_auth_headers(token))
    return resp


def fetch_storefront_top_songs(session, manager: TokenManager, storefront: str) -> list[dict]:
    songs: list[dict] = []
    offset = 0
    page = 0

    while True:
        url = (
            f"https://amp-api-edge.music.apple.com/v1/catalog/{storefront}/artists/{ARTIST_ID}"
            f"/view/top-songs?limit={PAGE_LIMIT}&offset={offset}"
        )
        resp = _get_with_auth(session, manager, url)
        if resp.status_code == 401:
            raise RuntimeError(f"Unauthorized after token refresh ({storefront})")
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

        page += 1
        if len(items) < PAGE_LIMIT or not data.get("next") or page >= MAX_PAGES:
            break
        offset += PAGE_LIMIT

    return songs


def fetch_storefront_task(manager: TokenManager, storefront: str):
    session = worker_session()
    return storefront, fetch_storefront_top_songs(session, manager, storefront)


def main() -> None:
    args = parse_args()
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    run_hour = scraped_at[11:13] if len(scraped_at) >= 13 else ""
    if not args.force and run_hour != RUN_HOUR:
        print(
            f"[Apple Music TS Global] Skipping — runs once/day at hour {RUN_HOUR} "
            f"(this run: {run_hour or 'unknown'}). Use --force to override."
        )
        return

    base_session = build_session()
    manager = TokenManager(base_session)
    base_session.headers.update(build_auth_headers(manager.get()))

    storefronts = [s.lower() for s in resolve_storefronts(base_session)]
    print(f"[Apple Music TS Global] Storefronts: {len(storefronts)} (depth cap {DEPTH_CAP}, {MAX_PAGES} page(s) each)")
    print(f"[Apple Music TS Global] Workers: {WORKERS}")

    results: dict[str, list[dict]] = {}
    failures: list[tuple[str, str]] = []

    if WORKERS == 1:
        for storefront in storefronts:
            try:
                results[storefront] = fetch_storefront_top_songs(base_session, manager, storefront)
            except (RequestException, RuntimeError) as exc:
                failures.append((storefront, str(exc)))
                continue
            print(f"{storefront}: {len(results[storefront])} song(s)")
    else:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_storefront_task, manager, s): s for s in storefronts}
            for future in as_completed(futures):
                storefront = futures[future]
                try:
                    _storefront, songs = future.result()
                except (RequestException, RuntimeError) as exc:
                    failures.append((storefront, str(exc)))
                    continue
                results[storefront] = songs
                print(f"{storefront}: {len(songs)} song(s)")

    if failures:
        for storefront, error in failures:
            print(f"[Apple Music TS Global] Warning: storefront {storefront} skipped: {error}")
        failure_pct = len(failures) * 100.0 / max(len(storefronts), 1)
        if failure_pct > MAX_FAILURE_PCT:
            print(
                f"[Apple Music TS Global] ERROR: {len(failures)}/{len(storefronts)} storefronts failed "
                f"({failure_pct:.1f}% > {MAX_FAILURE_PCT}%), aborting to avoid publishing a partial composite"
            )
            sys.exit(1)

    # Aggregate: apple_music_id -> {score, best_rank, song}
    composite: dict[str, dict] = {}
    for storefront, songs in results.items():
        weight = _market_weight(storefront)
        for idx, song in enumerate(songs, start=1):
            am_id = song["apple_music_id"]
            if not am_id:
                continue
            score = _rank_to_score(idx) * weight
            entry = composite.get(am_id)
            if entry is None:
                composite[am_id] = {"score": score, "best_rank": idx, "song": song}
            else:
                entry["score"] += score
                if idx < entry["best_rank"]:
                    entry["best_rank"] = idx
                    entry["song"] = song

    # Sort by composite score desc; tie-break on apple_music_id for determinism.
    ranked = sorted(composite.items(), key=lambda kv: (-kv[1]["score"], kv[0]))

    previous_by_id = load_previous_ranks(
        CSV_PATH,
        key_fields=["storefront", "apple_music_id"],
        today=scraped_at,
    )
    previous_by_name = load_previous_ranks(
        CSV_PATH,
        key_fields=["storefront", "song_name"],
        today=scraped_at,
    )

    rows: list[dict] = []
    for idx, (am_id, entry) in enumerate(ranked, start=1):
        song = entry["song"]
        key_by_id = (GLOBAL_STOREFRONT_TAG, am_id)
        key_by_name = (GLOBAL_STOREFRONT_TAG, rank_key(song["song_name"]))
        prev_rank = previous_by_id.get(key_by_id)
        if prev_rank is None:
            prev_rank = previous_by_name.get(key_by_name)
        rows.append(
            {
                "date": today,
                "scraped_at": scraped_at,
                "storefront": GLOBAL_STOREFRONT_TAG,
                "song_name": song["song_name"],
                "apple_music_id": am_id,
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
        if idx <= 20:
            prev = prev_rank
            if prev is None:
                marker = "NEW"
            elif prev > idx:
                marker = f"+{prev - idx}"
            elif prev < idx:
                marker = f"-{idx - prev}"
            else:
                marker = "="
            # Composite pulls titles from 100+ storefronts, some outside the
            # console's codepage (e.g. Windows cp1252) — never let a print
            # crash the run over a display-only line.
            line = f"#{idx:>3} [{marker}] {song['song_name']}"
            print(line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))

    if len(rows) > 20:
        print(f"... {len(rows) - 20} more row(s)")

    rewrite_for_snapshot(CSV_PATH, FIELDNAMES, scraped_at, rows)
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
