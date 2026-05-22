from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # collectors/spotify/ for core.*

from core.data_paths import update_streams_dir
from history_store import (
    get_all_last_history_totals,
    load_active_track_ids_from_discography,
    load_tracks_from_discography,
)
from spotify_api import TokenManager


ARTIST_ID = "06HL4z0CvFAxyc27GXpf02"
ARTIST_URI = f"spotify:artist:{ARTIST_ID}"
PARTNER_QUERY_URL = "https://api-partner.spotify.com/pathfinder/v2/query"
ARTIST_DISCOGRAPHY_HASH = "5e07d323febb57b4a56a42abbf781490e58764aa45feb6e3dc0591564fc56599"
ALBUM_TRACKS_HASH = "b9bfabef66ed756e5e13f68a942deb60bd4125ec1f1be8cc42769dc0259b4b10"
APP_VERSION = "1.2.87.30.gc764ebf1"
PAGE_LIMIT = 50


def _partner_headers(tokens: dict) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {tokens['bearer']}",
        "client-token": tokens["client_token"],
        "spotify-app-version": APP_VERSION,
        "app-platform": "WebPlayer",
        "Accept": "application/json",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://open.spotify.com",
        "Referer": "https://open.spotify.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
        ),
    }


def _request_partner_json(
    session: requests.Session,
    *,
    tokens: dict,
    operation_name: str,
    variables: dict,
    query_hash: str,
) -> dict:
    body = {
        "variables": variables,
        "operationName": operation_name,
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": query_hash}},
    }
    backoff = 1.0
    for _attempt in range(5):
        response = session.post(
            PARTNER_QUERY_URL,
            headers=_partner_headers(tokens),
            json=body,
            timeout=(5, 20),
        )
        if response.status_code == 200:
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(f"Spotify partner query errors: {json.dumps(payload['errors'])[:300]}")
            return payload
        if response.status_code == 429:
            raw_retry_after = (response.headers.get("Retry-After") or "").strip()
            try:
                wait_s = float(raw_retry_after)
            except ValueError:
                wait_s = backoff
            time.sleep(min(15.0, max(0.5, wait_s)))
            backoff = min(15.0, backoff * 1.5)
            continue
        if response.status_code in {408, 500, 502, 503, 504}:
            time.sleep(min(5.0, backoff))
            backoff *= 1.5
            continue
        raise RuntimeError(f"Spotify partner query {response.status_code}: {response.text[:200]}")
    raise RuntimeError(f"Spotify partner retries exhausted for {operation_name}")


def _extract_spotify_id(uri: str, kind: str) -> str:
    prefix = f"spotify:{kind}:"
    return uri[len(prefix):] if uri.startswith(prefix) else ""


def _normalize_title(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(char for char in ascii_text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).strip()


def _artist_releases(session: requests.Session, *, tokens: dict) -> list[dict]:
    releases_by_id: dict[str, dict] = {}
    offset = 0
    while True:
        payload = _request_partner_json(
            session,
            tokens=tokens,
            operation_name="queryArtistDiscographyAll",
            variables={"uri": ARTIST_URI, "offset": offset, "limit": PAGE_LIMIT, "order": "DATE_DESC"},
            query_hash=ARTIST_DISCOGRAPHY_HASH,
        )
        groups = (
            (((payload.get("data") or {}).get("artistUnion") or {}).get("discography") or {})
            .get("all", {})
            .get("items", [])
        )
        page_releases = []
        for group in groups:
            page_releases.extend(
                release for release in ((group.get("releases") or {}).get("items") or [])
                if isinstance(release, dict)
            )
        for release in page_releases:
            release_id = str(release.get("id") or "").strip()
            if release_id:
                releases_by_id[release_id] = release
        print(f"[catalog-gap] Artist discography page offset={offset}: {len(page_releases)} release(s)")
        if len(groups) < PAGE_LIMIT or not page_releases:
            break
        offset += PAGE_LIMIT
    return list(releases_by_id.values())


def _album_tracks(session: requests.Session, *, tokens: dict, release: dict) -> list[dict]:
    album_uri = str(release.get("uri") or "").strip()
    payload = _request_partner_json(
        session,
        tokens=tokens,
        operation_name="queryAlbumTracks",
        variables={"uri": album_uri, "offset": 0, "limit": 300},
        query_hash=ALBUM_TRACKS_HASH,
    )
    album_union = ((payload.get("data") or {}).get("albumUnion") or {})
    items = ((album_union.get("tracksV2") or {}).get("items") or [])
    return [item.get("track") for item in items if isinstance(item, dict) and isinstance(item.get("track"), dict)]


def _artist_release_tracks(session: requests.Session, *, tokens: dict) -> list[dict]:
    releases = _artist_releases(session, tokens=tokens)

    tracks_by_id: dict[str, dict] = {}
    for index, release in enumerate(releases, 1):
        release_id = str(release.get("id") or "").strip()
        release_uri = str(release.get("uri") or "").strip()
        if not release_id or not release_uri:
            continue
        release_tracks = _album_tracks(session, tokens=tokens, release=release)
        if index == 1 or index % 25 == 0 or index == len(releases):
            print(f"[catalog-gap] Release tracks {index}/{len(releases)}: {release.get('name') or release_id}")
        for track in release_tracks:
            track_uri = str(track.get("uri") or "").strip()
            track_id = _extract_spotify_id(track_uri, "track")
            artists = [
                {
                    "name": ((artist.get("profile") or {}).get("name") or ""),
                    "uri": artist.get("uri") or "",
                }
                for artist in (((track.get("artists") or {}).get("items")) or [])
                if isinstance(artist, dict)
            ]
            if not track_id:
                continue

            track_summary = tracks_by_id.setdefault(
                track_id,
                {
                    "track_id": track_id,
                    "title": track.get("name") or "",
                    "spotify_url": f"https://open.spotify.com/track/{track_id}",
                    "artists": [artist["name"] for artist in artists if artist["name"]],
                    "playcount": int(track["playcount"]) if str(track.get("playcount") or "").isdigit() else None,
                    "releases": [],
                },
            )
            track_summary["releases"].append(
                {
                    "id": release_id,
                    "name": release.get("name") or "",
                    "type": release.get("type") or "",
                    "release_date": ((release.get("date") or {}).get("isoString") or ""),
                }
            )

    return sorted(tracks_by_id.values(), key=lambda item: (item["title"].casefold(), item["track_id"]))


def build_catalog_gap_report(*, tokens: dict) -> dict:
    with requests.Session() as session:
        catalog_tracks = _artist_release_tracks(session, tokens=tokens)

    db_track_ids = load_active_track_ids_from_discography()
    db_tracks = load_tracks_from_discography(db_track_ids)
    db_playcounts = get_all_last_history_totals()
    db_tracks_by_title: dict[str, list[dict]] = {}
    for db_track in db_tracks:
        db_tracks_by_title.setdefault(_normalize_title(db_track["title"]), []).append(db_track)

    missing_tracks = []
    alternate_track_ids = []
    title_matches_with_different_playcount = []
    for track in catalog_tracks:
        if track["track_id"] in db_track_ids:
            continue

        title_matches = db_tracks_by_title.get(_normalize_title(track["title"]), [])
        playcount_matches = [
            match for match in title_matches
            if track.get("playcount") is not None
            and db_playcounts.get(match["track_id"]) == track["playcount"]
        ]
        if playcount_matches:
            alternate_track = dict(track)
            alternate_track["db_matches"] = [
                {
                    "track_id": match["track_id"],
                    "title": match["title"],
                    "spotify_url": match["spotify_url"],
                    "playcount": db_playcounts.get(match["track_id"]),
                }
                for match in playcount_matches
            ]
            alternate_track_ids.append(alternate_track)
            continue

        if title_matches:
            review_track = dict(track)
            review_track["db_title_matches"] = [
                {
                    "track_id": match["track_id"],
                    "title": match["title"],
                    "spotify_url": match["spotify_url"],
                    "playcount": db_playcounts.get(match["track_id"]),
                }
                for match in title_matches
            ]
            title_matches_with_different_playcount.append(review_track)

        missing_tracks.append(track)

    return {
        "artist_id": ARTIST_ID,
        "source": "spotify web player artist discography all",
        "catalog_track_count": len(catalog_tracks),
        "discography_track_count": len(db_track_ids),
        "missing_track_count": len(missing_tracks),
        "missing_tracks": missing_tracks,
        "alternate_track_id_count": len(alternate_track_ids),
        "alternate_track_ids": alternate_track_ids,
        "title_match_different_playcount_count": len(title_matches_with_different_playcount),
        "title_matches_with_different_playcount": title_matches_with_different_playcount,
    }


def write_catalog_gap_report(*, tokens: dict, stats_date: str | None = None) -> Path | None:
    target_date = stats_date or date.today().isoformat()
    try:
        report = build_catalog_gap_report(tokens=tokens)
    except Exception as exc:
        print(f"[catalog-gap] Scan failed: {exc}")
        return None

    report["stats_date"] = target_date
    report_path = update_streams_dir(target_date) / "spotify_catalog_gap_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[catalog-gap] Missing DB tracks: {report['missing_track_count']} "
        f"of {report['catalog_track_count']} catalog track ID(s)."
    )
    print(f"[catalog-gap] Alternate Spotify track IDs covered by title + playcount: {report['alternate_track_id_count']}.")
    print(
        "[catalog-gap] Title matches with different/missing playcount still marked missing: "
        f"{report['title_match_different_playcount_count']}."
    )
    print(f"[catalog-gap] Report written: {report_path}")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Report Spotify artist catalog tracks missing from the stream DB.")
    parser.add_argument("--date", default=None, help="Stats date folder for the JSON report (YYYY-MM-DD).")
    args = parser.parse_args()

    token_mgr = TokenManager()
    if not token_mgr.capture():
        raise SystemExit("Could not capture Spotify tokens.")
    tokens = token_mgr.get()
    if not tokens.get("bearer") or not tokens.get("client_token"):
        raise SystemExit("Spotify partner tokens are missing.")
    if write_catalog_gap_report(tokens=tokens, stats_date=args.date) is None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
