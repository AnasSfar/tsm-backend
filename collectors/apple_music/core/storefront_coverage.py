"""Which storefronts each country_all.py / genre_all.py snapshot really fetched.

A storefront (or storefront x genre pair) whose fetch failed (DNS / WARP, 73
big-store skips in the log by 2026-09-27) simply has no rows in that snapshot
— indistinguishable from "Taylor has no song on that chart" (Japan, often).
Compared against such a snapshot, every song of the next one looked like a
re-entry. Readers skip a snapshot where the chart is recorded as not fetched;
snapshots without a record (before 2026-09-27) are trusted as before.

One file per kind, {scraped_at: {"fetched": [countries], "skipped":
["<cc>|<genre lower>"]}} — genre charts record the failed pairs rather than
the ~2,500 fetched ones.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

JSON_DIR = Path(__file__).resolve().parents[1] / "tools" / "json"
KEEP_DAYS = 10

_cache: dict[str, dict[str, dict]] = {}


def _path(kind: str) -> Path:
    return JSON_DIR / f"{kind}_coverage.json"


def _load(kind: str) -> dict[str, dict]:
    try:
        data = json.loads(_path(kind).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def pair_key(country: str, genre: str) -> str:
    return f"{str(country).lower()}|{str(genre).strip().lower()}"


def record(scraped_at: str, countries, skipped=(), kind: str = "country") -> None:
    """Store the storefronts fetched for `scraped_at` (+ the failed
    storefront x genre pairs for kind "genre"). Atomic write, entries older
    than KEEP_DAYS pruned."""
    data = _load(kind)
    data[scraped_at] = {"fetched": sorted({str(c).lower() for c in countries}), "skipped": sorted(set(skipped))}
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    data = {at: v for at, v in data.items() if at[:10] >= cutoff}
    path = _path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    _cache.pop(kind, None)


def not_fetched(scraped_at: str, country: str, genre: str | None = None) -> bool:
    """True only when that snapshot has a record and the chart (country, or
    country x genre) isn't in it."""
    kind = "genre" if genre else "country"
    if kind not in _cache:
        _cache[kind] = {
            at: {"fetched": set(v.get("fetched") or ()), "skipped": set(v.get("skipped") or ())}
            for at, v in _load(kind).items() if isinstance(v, dict)
        }
    entry = _cache[kind].get(scraped_at)
    if entry is None:
        return False
    country = str(country).lower()
    return country not in entry["fetched"] or (bool(genre) and pair_key(country, genre) in entry["skipped"])
