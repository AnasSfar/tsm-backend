from __future__ import annotations

import os

from .config import COUNTRIES


STOREFRONTS_URL = "https://amp-api-edge.music.apple.com/v1/storefronts?limit=200"


def has_country_override() -> bool:
    return bool(os.getenv("APPLE_MUSIC_COUNTRIES", "").strip())


def resolve_storefronts(session) -> list[str]:
    """Return all Apple Music storefront IDs, unless the env override is set."""
    if has_country_override():
        return COUNTRIES

    try:
        resp = session.get(STOREFRONTS_URL)
        if resp.status_code == 401:
            raise RuntimeError("Unauthorized while calling Apple Music storefronts API")
        resp.raise_for_status()
        storefronts = [
            str(item.get("id", "")).strip().lower()
            for item in resp.json().get("data", []) or []
        ]
        storefronts = [storefront for storefront in storefronts if storefront]
        return storefronts or COUNTRIES
    except RuntimeError:
        raise
    except Exception as exc:
        print(f"[Apple Music] Storefront discovery failed, using configured fallback countries: {exc}")
        return COUNTRIES
