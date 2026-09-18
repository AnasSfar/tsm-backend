from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Iterable


class IncompleteDailyChart(RuntimeError):
    """Raised when an exact daily chart cannot be built from the raw snapshots."""


def _snapshot_key(row: dict) -> str:
    return str(row.get("scraped_at") or row.get("date") or "").strip()


def _identity(row: dict) -> str:
    isrc = str(row.get("isrc") or "").strip().upper()
    if isrc:
        return f"isrc:{isrc}"
    apple_music_id = str(row.get("apple_music_id") or "").strip()
    if apple_music_id:
        return f"id:{apple_music_id}"
    raise IncompleteDailyChart("TS Top Songs row has neither ISRC nor apple_music_id")


def _number(value, *, field: str, context: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise IncompleteDailyChart(f"Missing or invalid {field} for {context}") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise IncompleteDailyChart(f"Missing or invalid {field} for {context}")
    return parsed


def _storefront_ranks(row: dict) -> dict[str, int]:
    raw = row.get("storefront_ranks") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IncompleteDailyChart("Invalid storefront_ranks JSON") from exc
    if not isinstance(raw, dict):
        raise IncompleteDailyChart("Invalid storefront_ranks value")

    result: dict[str, int] = {}
    for storefront, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            rank = int(value.get("rank") or 0)
        except (TypeError, ValueError):
            continue
        if rank > 0:
            result[str(storefront).lower()] = rank
    return result


def _previous_rows_by_identity(rows: Iterable[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        result[_identity(row)] = row
    return result


def aggregate_daily_chart(
    snapshot_rows: list[dict],
    previous_daily_rows: list[dict],
    *,
    target_day: str,
    expected_hours: Iterable[int],
    important_storefronts: Iterable[str],
) -> list[dict]:
    """Build one exact daily chart from all expected two-hour snapshots.

    The overall daily order is the mean of the already-computed composite
    scores. Missing songs contribute zero for that snapshot. Storefront
    columns use the same method with the existing rank-to-score curve, then
    are ranked against one another for a coherent daily placement.
    """
    expected = sorted(set(int(hour) for hour in expected_hours))
    if not expected:
        raise IncompleteDailyChart("No expected snapshot hours configured")
    important_storefronts = list(important_storefronts)

    snapshots_by_hour: dict[int, list[dict]] = defaultdict(list)
    keys_by_hour: dict[int, set[str]] = defaultdict(set)
    for row in snapshot_rows:
        key = _snapshot_key(row)
        if not key.startswith(target_day) or len(key) < 13:
            continue
        try:
            hour = int(key[11:13])
        except ValueError:
            continue
        if hour not in expected:
            continue
        snapshots_by_hour[hour].append(row)
        keys_by_hour[hour].add(key)

    missing = [hour for hour in expected if hour not in snapshots_by_hour]
    if missing:
        rendered = ", ".join(f"{hour:02d}:00" for hour in missing)
        raise IncompleteDailyChart(f"Missing TS Top Songs snapshots for {target_day}: {rendered}")
    ambiguous = {hour: sorted(keys) for hour, keys in keys_by_hour.items() if len(keys) != 1}
    if ambiguous:
        raise IncompleteDailyChart(f"Multiple snapshot keys for the same daily slot: {ambiguous}")

    snapshot_count = len(expected)
    score_sums: dict[str, float] = defaultdict(float)
    storefront_score_sums: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    metadata: dict[str, dict] = {}

    for hour in expected:
        seen: dict[str, tuple[float, dict[str, int]]] = {}
        for row in snapshots_by_hour[hour]:
            identity = _identity(row)
            score = _number(
                row.get("composite_score"),
                field="composite_score",
                context=f"{identity} at {target_day}T{hour:02d}:00",
            )
            stores = _storefront_ranks(row)
            signature = (score, stores)
            if identity in seen:
                if seen[identity] != signature:
                    raise IncompleteDailyChart(
                        f"Conflicting duplicate {identity} at {target_day}T{hour:02d}:00"
                    )
                continue
            seen[identity] = signature
            score_sums[identity] += score
            for storefront in important_storefronts:
                store = str(storefront).lower()
                rank = stores.get(store)
                if rank:
                    storefront_score_sums[store][identity] += 500.0 / (rank ** 0.75)
            metadata[identity] = row

    if not score_sums:
        raise IncompleteDailyChart(f"No TS Top Songs rows found for {target_day}")

    daily_scores = {identity: total / snapshot_count for identity, total in score_sums.items()}
    ranked_identities = sorted(daily_scores, key=lambda identity: (-daily_scores[identity], identity))
    previous = _previous_rows_by_identity(previous_daily_rows)

    daily_storefront_ranks: dict[str, dict[str, int]] = {}
    for storefront in important_storefronts:
        store = str(storefront).lower()
        sums = storefront_score_sums.get(store, {})
        ordered = sorted(
            (identity for identity, total in sums.items() if total > 0),
            key=lambda identity: (-(sums[identity] / snapshot_count), identity),
        )
        daily_storefront_ranks[store] = {
            identity: rank for rank, identity in enumerate(ordered, start=1)
        }

    output: list[dict] = []
    for rank, identity in enumerate(ranked_identities, start=1):
        source = metadata[identity]
        previous_row = previous.get(identity)
        previous_rank = previous_row.get("rank", "") if previous_row else ""
        previous_stores = _storefront_ranks(previous_row) if previous_row else {}
        storefront_ranks = {}
        for storefront in important_storefronts:
            store = str(storefront).lower()
            store_rank = daily_storefront_ranks.get(store, {}).get(identity)
            if store_rank is None:
                continue
            storefront_ranks[store] = {
                "rank": store_rank,
                "previous_rank": previous_stores.get(store),
            }

        output.append(
            {
                "date": target_day,
                "scraped_at": target_day,
                "storefront": "global",
                "song_name": source.get("song_name", ""),
                "apple_music_id": source.get("apple_music_id", ""),
                "rank": rank,
                "previous_rank": previous_rank,
                "composite_score": f"{daily_scores[identity]:.6f}",
                "snapshot_count": snapshot_count,
                "image_url": source.get("image_url", ""),
                "url": source.get("url", ""),
                "artist_name": source.get("artist_name", ""),
                "album_name": source.get("album_name", ""),
                "duration_ms": source.get("duration_ms", ""),
                "release_date": source.get("release_date", ""),
                "isrc": source.get("isrc", ""),
                "content_rating": source.get("content_rating", ""),
                "genre_names": source.get("genre_names", ""),
                "storefront_ranks": json.dumps(
                    storefront_ranks, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )
    return output
