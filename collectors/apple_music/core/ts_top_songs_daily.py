"""
Shared aggregation for the "TS Top Songs Global" composite: turns several
same-day cycles (raw per-run rows, one per song per cycle) into ONE final
per-day ranking.

Used by:
- `finalize_ts_top_songs_daily.py`: the live daily job, aggregating a day's
  `*_raw.csv` cycles into the canonical (published) CSV.
- `backfill_ts_top_songs_daily_final.py`: the one-off historical backfill
  that did the same thing retroactively for days collected before the
  raw/final split existed.

A song's daily score is the sum of `_rank_to_score(rank)` (same power-law
curve as the live composite in `ts_page_all.py`) over every cycle it
appeared in that day — a song holding #1 all day accumulates far more than
one that spiked to #1 once and vanished. Songs are matched across cycles by
ISRC (fallback apple_music_id), same identity rule as the live
storefront-merge step.
"""

from __future__ import annotations

import json

from .filters import rank_key

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
    "storefront_ranks",
]


def rank_to_score(rank: int) -> float:
    """Same power-law curve as ts_page_all.py::_rank_to_score (duplicated to
    keep this module standalone/offline — no live HTTP/token deps)."""
    if rank < 1:
        return 0.0
    return 500.0 / (rank ** 0.75)


def _merge_key(row: dict) -> str:
    isrc = (row.get("isrc") or "").strip()
    if isrc:
        return f"isrc:{isrc}"
    return f"id:{(row.get('apple_music_id') or '').strip()}"


def _identity_keys(row: dict) -> list[str]:
    keys = []
    am_id = (row.get("apple_music_id") or "").strip()
    name = (row.get("song_name") or "").strip()
    if am_id:
        keys.append(f"id:{am_id}")
    if name:
        keys.append(f"name:{rank_key(name)}")
    return keys


def compute_final_rows(day: str, cycle_rows: list[dict], previous_final_rows: list[dict]) -> list[dict]:
    """Aggregate every cycle-row collected for `day` into one final ranking.

    `previous_final_rows` must be the immediately preceding day's already
    finalized rows (or []) — `previous_rank` is only ever computed against
    that, never against other cycles of the same day."""
    if not cycle_rows:
        return []

    # One shared timestamp for every row of this day's final ranking (the
    # last cycle collected that day) — export_apple_music.py groups rows by
    # exact scraped_at, so per-song timestamps (e.g. each song's own
    # best-ranked cycle) would fragment one day's ranking across several
    # partial "snapshots" instead of one complete one.
    day_scraped_at = max(
        (r.get("scraped_at") or "" for r in cycle_rows if r.get("scraped_at")),
        default=f"{day}T23:59:59",
    )

    groups: dict[str, dict] = {}
    for row in cycle_rows:
        key = _merge_key(row)
        try:
            rank = int(row.get("rank") or "")
        except (TypeError, ValueError):
            continue
        entry = groups.get(key)
        score = rank_to_score(rank)
        if entry is None:
            entry = groups[key] = {
                "score": 0.0,
                "cycles": 0,
                "best_rank": rank,
                "row": row,
                "storefront_ranks": {},
            }
        entry["score"] += score
        entry["cycles"] += 1
        if rank < entry["best_rank"]:
            entry["best_rank"] = rank
            entry["row"] = row
        try:
            sf_ranks = json.loads(row.get("storefront_ranks") or "{}")
        except json.JSONDecodeError:
            sf_ranks = {}
        if isinstance(sf_ranks, dict):
            for storefront, value in sf_ranks.items():
                if not isinstance(value, dict):
                    continue
                try:
                    sf_rank = int(value.get("rank"))
                except (TypeError, ValueError):
                    continue
                existing = entry["storefront_ranks"].get(storefront)
                if existing is None or sf_rank < existing:
                    entry["storefront_ranks"][storefront] = sf_rank

    ranked = sorted(groups.items(), key=lambda kv: (-kv[1]["score"], kv[0]))

    prev_by_id: dict[str, int] = {}
    prev_by_name: dict[str, int] = {}
    prev_storefront_ranks: dict[tuple[str, str], int] = {}
    for prow in previous_final_rows:
        try:
            prank = int(prow.get("rank") or "")
        except (TypeError, ValueError):
            continue
        am_id = (prow.get("apple_music_id") or "").strip()
        name = (prow.get("song_name") or "").strip()
        if am_id:
            prev_by_id[am_id] = prank
        if name:
            prev_by_name[rank_key(name)] = prank
        try:
            sf_ranks = json.loads(prow.get("storefront_ranks") or "{}")
        except json.JSONDecodeError:
            sf_ranks = {}
        if isinstance(sf_ranks, dict):
            for identity_key in _identity_keys(prow):
                for storefront, value in sf_ranks.items():
                    if not isinstance(value, dict):
                        continue
                    try:
                        sf_rank = int(value.get("rank"))
                    except (TypeError, ValueError):
                        continue
                    prev_storefront_ranks[(identity_key, storefront)] = sf_rank

    final_rows: list[dict] = []
    for idx, (_key, entry) in enumerate(ranked, start=1):
        row = entry["row"]
        am_id = (row.get("apple_music_id") or "").strip()
        name = (row.get("song_name") or "").strip()
        prev_rank = prev_by_id.get(am_id)
        if prev_rank is None:
            prev_rank = prev_by_name.get(rank_key(name))

        storefront_ranks_out = {}
        identity_keys = [f"id:{am_id}", f"name:{rank_key(name)}"]
        for storefront, sf_rank in sorted(entry["storefront_ranks"].items()):
            prev_sf_rank = None
            for identity_key in identity_keys:
                prev_sf_rank = prev_storefront_ranks.get((identity_key, storefront))
                if prev_sf_rank is not None:
                    break
            storefront_ranks_out[storefront] = {
                "rank": sf_rank,
                "previous_rank": prev_sf_rank,
            }

        final_rows.append(
            {
                "date": day,
                "scraped_at": day_scraped_at,
                "storefront": row.get("storefront", "global"),
                "song_name": name,
                "apple_music_id": am_id,
                "rank": idx,
                "previous_rank": prev_rank if prev_rank is not None else "",
                "image_url": row.get("image_url", ""),
                "url": row.get("url", ""),
                "artist_name": row.get("artist_name", ""),
                "album_name": row.get("album_name", ""),
                "duration_ms": row.get("duration_ms", ""),
                "release_date": row.get("release_date", ""),
                "isrc": row.get("isrc", ""),
                "content_rating": row.get("content_rating", ""),
                "genre_names": row.get("genre_names", ""),
                "storefront_ranks": json.dumps(storefront_ranks_out, ensure_ascii=False, separators=(",", ":")),
            }
        )
    return final_rows
