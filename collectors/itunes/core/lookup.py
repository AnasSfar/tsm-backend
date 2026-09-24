"""Resolve clean vs. explicit album edition for duplicate iTunes chart entries.

The legacy RSS feeds (core/rss.py) carry no explicit-content flag at all, so a
song sold as two separate album editions (e.g. "explicit" and "cleaned"
pressings of the same collection) shows up as two chart rows with the same
song/album name but different `apple_music_id` — indistinguishable without an
extra call. This only matters for the rare case where such a duplicate exists,
so it is resolved with a single batched call to the (also unauthenticated)
iTunes Lookup API, never per-storefront.
"""

from __future__ import annotations

from typing import Iterable

from .config import RSS_BASE

LOOKUP_URL = f"{RSS_BASE}/lookup"
_LOOKUP_CHUNK = 150  # comfortably under the Lookup API's practical id limit


def resolve_explicitness(session, apple_music_ids: Iterable[str], *, country: str = "us") -> dict[str, str]:
    """Map apple_music_id -> "explicit" | "clean" | "" (unknown/not applicable).

    Only worth calling with ids that are known duplicates (see charts.py) —
    resolving every chart entry would multiply requests for no benefit, since
    almost no title has more than one edition charting at once.
    """
    ids = sorted({str(i).strip() for i in apple_music_ids if str(i).strip()})
    if not ids:
        return {}

    resolved: dict[str, str] = {}
    for start in range(0, len(ids), _LOOKUP_CHUNK):
        chunk = ids[start : start + _LOOKUP_CHUNK]
        try:
            resp = session.get(LOOKUP_URL, params={"id": ",".join(chunk), "country": country})
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # network hiccup — leave these ids unresolved, never abort the run
            print(f"[iTunes] explicitness lookup failed for {len(chunk)} id(s): {exc}")
            continue
        for result in payload.get("results", []):
            track_id = str(result.get("trackId", "")).strip()
            if not track_id:
                continue
            raw = str(result.get("collectionExplicitness", "")).strip().lower()
            resolved[track_id] = "explicit" if raw == "explicit" else "clean" if raw == "cleaned" else ""
    return resolved
