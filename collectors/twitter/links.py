from __future__ import annotations

BASE_SITE_URL = "https://thetsmuseum.app"


def site_url(path: str = "") -> str:
    clean = str(path or "").strip()
    if not clean:
        return BASE_SITE_URL
    return f"{BASE_SITE_URL}/{clean.lstrip('/')}"


def song_url(track_id: str) -> str:
    return site_url(f"songs/{str(track_id).strip()}")


def streams_latest_url() -> str:
    return site_url("streams/latest")


def charts_url(*, region: str = "global", view: str = "today") -> str:
    return site_url(f"charts?region={region}&view={view}")

def albums_latest_url() -> str:
    return site_url('albums/date/latest')
