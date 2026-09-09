"""Storefront list for the iTunes collector.

Reuses the Apple Music collector's live storefront discovery (~167 countries) so
the iTunes country coverage matches the Apple Music country charts. Falls back to
the Apple Music static country list if discovery fails (401 / offline).

Override with ITUNES_COUNTRIES="us,gb,jp,..." (comma-separated, 2-letter codes).
"""

from __future__ import annotations

import os


def _override() -> list[str] | None:
    raw = os.getenv("ITUNES_COUNTRIES", "").strip()
    if not raw:
        return None
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def resolve_storefronts() -> list[str]:
    override = _override()
    if override:
        print(f"[iTunes] Storefronts from ITUNES_COUNTRIES override: {len(override)}")
        return override

    from collectors.apple_music.core.config import COUNTRIES as AM_FALLBACK_COUNTRIES

    try:
        from collectors.apple_music.core.http import build_session as build_am_session
        from collectors.apple_music.core.storefronts import resolve_storefronts as am_resolve
        from collectors.apple_music.core.token import TokenManager, build_auth_headers

        session = build_am_session()
        manager = TokenManager(session)
        session.headers.update(build_auth_headers(manager.get()))
        storefronts = [s for s in am_resolve(session) if s]
        if storefronts:
            print(f"[iTunes] Storefronts from Apple Music discovery: {len(storefronts)}")
            return storefronts
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        print(f"[iTunes] Storefront discovery failed, using Apple Music fallback list: {exc}")

    print(f"[iTunes] Storefronts (fallback): {len(AM_FALLBACK_COUNTRIES)}")
    return list(AM_FALLBACK_COUNTRIES)
