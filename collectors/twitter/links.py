from __future__ import annotations

BASE_SITE_URL = "https://thetsmuseum.app"


def site_url(path: str = "") -> str:
    clean = str(path or "").strip()
    if not clean:
        return BASE_SITE_URL
    return f"{BASE_SITE_URL}/{clean.lstrip('/')}"


def song_url(track_id: str) -> str:
    return site_url(f"songs/{str(track_id).strip()}")


def chart_song_url(track_id: str, *, region: str = "global") -> str:
    """Spotify Charts song detail page (not the streams one `song_url` points
    to) — decision 2026-09-19: chart-rank record tweets must link here, since
    `song_url` sends readers to the streams page instead."""
    return site_url(f"spotifycharts/charts/songs/{str(track_id).strip()}?region={str(region).strip().lower()}")


def streams_latest_url() -> str:
    return site_url("streams/latest")


def charts_url(*, region: str = "global", view: str = "today") -> str:
    return site_url(f"charts?region={region}&view={view}")

def albums_latest_url() -> str:
    return site_url('albums/date/latest')
