from __future__ import annotations

from datetime import date


def is_recent_release_date(release_date: str | None, target_date: str, *, window_days: int = 3) -> bool:
    if not release_date:
        return False
    try:
        release_dt = date.fromisoformat(str(release_date)[:10])
        target_dt = date.fromisoformat(target_date)
    except ValueError:
        return False
    delta_days = (target_dt - release_dt).days
    return 0 <= delta_days <= window_days


def recent_release_album_names(
    sections: list[dict],
    target_date: str,
    *,
    window_days: int = 3,
    min_tracks: int = 2,
) -> list[str]:
    albums: dict[str, set[str]] = {}
    for section in sections:
        album = str(section.get("album") or "").strip()
        if not album:
            continue
        for track in section.get("tracks") or []:
            if not is_recent_release_date(track.get("release_date"), target_date, window_days=window_days):
                continue
            track_id = str(track.get("track_id") or track.get("id") or "").strip()
            url = str(track.get("url") or track.get("spotify_url") or "").strip()
            albums.setdefault(album, set()).add(track_id or url or track.get("title") or album)

    return [
        album
        for album, track_keys in sorted(albums.items(), key=lambda item: (-len(item[1]), item[0].casefold()))
        if len(track_keys) >= min_tracks
    ]
