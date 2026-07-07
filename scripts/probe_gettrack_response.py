#!/usr/bin/env python3
"""
One-off probe: dump the FULL raw getTrack GraphQL response for a few known
track_ids, to find the exact JSON path to cover art (never logged in this
repo before — fetch_playcount_api only reads trackUnion.playcount).

Usage: python scripts/probe_gettrack_response.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
STREAMS_SCRIPTS = REPO_ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts"
sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))  # for core.*
sys.path.insert(0, str(STREAMS_SCRIPTS))

from spotify_api import TokenManager, GRAPHQL_URL, GETTRACK_HASH, APP_VERSION  # noqa: E402

OUT_DIR = REPO_ROOT / "scripts"

TEST_TRACK_IDS = {
    "the_great_war": "2VuqMjgoKaOHNM8HpxtXKx",
    "style_original": "0ug5NqcwcFR2xrfTkc7k8e",
    "style_taylors_version": "1hjRhYpWyqDpPahmSlUTlc",
    "slut_tv_vault": "0CD7DzeCsuPJygddqlUVYa",
}


def fetch_raw(track_id: str, bearer: str, client_token: str) -> dict:
    body = {
        "variables": {"uri": f"spotify:track:{track_id}"},
        "operationName": "getTrack",
        "extensions": {
            "persistedQuery": {"version": 1, "sha256Hash": GETTRACK_HASH},
        },
    }
    headers = {
        "Authorization": f"Bearer {bearer}",
        "client-token": client_token,
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
    resp = requests.post(GRAPHQL_URL, json=body, headers=headers, timeout=(5, 15))
    print(f"  status={resp.status_code}")
    resp.raise_for_status()
    return resp.json()


def find_cover_paths(obj, path="data") -> list[str]:
    """Recursively find any key path containing 'cover', 'image', or 'artwork'."""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_l = k.lower()
            new_path = f"{path}.{k}"
            if "cover" in key_l or "image" in key_l or "artwork" in key_l:
                hits.append(new_path)
            hits.extend(find_cover_paths(v, new_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:2]):  # only peek first 2 to avoid explosion
            hits.extend(find_cover_paths(item, f"{path}[{i}]"))
    return hits


def main() -> None:
    print("Capturing tokens via TokenManager...")
    token_mgr = TokenManager()
    if not token_mgr.capture():
        print("FAILED to capture tokens — aborting.")
        sys.exit(1)
    tokens = token_mgr.get()

    for label, track_id in TEST_TRACK_IDS.items():
        print(f"\nFetching getTrack for {label} ({track_id})...")
        try:
            data = fetch_raw(track_id, tokens["bearer"], tokens["client_token"])
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        out_path = OUT_DIR / f"probe_gettrack_{label}_{track_id}.json"
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved full response -> {out_path}")

        cover_paths = find_cover_paths(data)
        if cover_paths:
            print("  Candidate cover/image key paths found:")
            for p in cover_paths:
                print(f"    {p}")
        else:
            print("  No cover/image-like keys found in response.")


if __name__ == "__main__":
    main()
