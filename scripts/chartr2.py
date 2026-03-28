#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import boto3
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]

# ===== CONFIG =====
CSV_PATH = ROOT / "db" / "charts_history_global.csv"

R2_PREFIX = "chart-history-global-by-track"

WEBSITE_SONGS_PATH = ROOT / "website" / "site" / "data" / "songs.json"
DISCO_SONGS_PATH = ROOT / "db" / "discography" / "songs.json"
DISCO_ALBUMS_PATH = ROOT / "db" / "discography" / "albums.json"
MANUAL_MAP_PATH = ROOT / "scripts" / "chart_title_to_track_id.json"

R2_ACCOUNT_ID = os.environ["R2_ACCOUNT_ID"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ["R2_BUCKET"]

TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")
PARENS_RE = re.compile(r"\s*[\(\[].*?[\)\]]")
FEAT_RE = re.compile(r"\s+(feat\.|featuring|ft\.)\s+.*$", re.IGNORECASE)
MULTISPACE_RE = re.compile(r"\s+")

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = MULTISPACE_RE.sub(" ", s)
    return s


def simplify_title(title: str) -> str:
    s = normalize_text(title)
    s = FEAT_RE.sub("", s)
    s = PARENS_RE.sub("", s)
    s = s.replace("taylor's version", "")
    s = s.replace("taylors version", "")
    s = s.replace("from the vault", "")
    s = s.replace("remix", "")
    s = s.replace("acoustic", "")
    s = s.replace("live", "")
    s = s.replace("version", "")
    s = MULTISPACE_RE.sub(" ", s).strip(" -")
    return s.strip()


def extract_track_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = TRACK_ID_RE.search(url)
    return m.group(1) if m else None


def possible_title_keys(title: str) -> set[str]:
    keys = set()
    if not title:
        return keys

    n = normalize_text(title)
    s = simplify_title(title)

    if n:
        keys.add(n)
    if s:
        keys.add(s)

    s2 = s.replace("'", "").replace("’", "")
    if s2:
        keys.add(s2)

    return {k for k in keys if k}


def iter_discography_tracks() -> Iterable[Dict[str, Any]]:
    if DISCO_SONGS_PATH.exists():
        songs = load_json(DISCO_SONGS_PATH)
        if isinstance(songs, list):
            for item in songs:
                if isinstance(item, dict):
                    yield item

    if DISCO_ALBUMS_PATH.exists():
        albums = load_json(DISCO_ALBUMS_PATH)
        if isinstance(albums, list):
            for section in albums:
                if not isinstance(section, dict):
                    continue
                tracks = section.get("tracks", [])
                if not isinstance(tracks, list):
                    continue
                for track in tracks:
                    if not isinstance(track, dict):
                        continue
                    merged = dict(track)
                    if "album" not in merged and "album" in section:
                        merged["album"] = section["album"]
                    yield merged


def iter_website_song_entries() -> Iterable[Dict[str, Any]]:
    if WEBSITE_SONGS_PATH.exists():
        data = load_json(WEBSITE_SONGS_PATH)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item


def get_track_id(item: Dict[str, Any]) -> Optional[str]:
    for key in ("track_id", "id"):
        value = item.get(key)
        if value:
            return str(value)

    for key in ("spotify_url", "url", "track_url"):
        value = item.get(key)
        if isinstance(value, str):
            tid = extract_track_id_from_url(value)
            if tid:
                return tid

    return None


def title_fields_from_item(item: Dict[str, Any]) -> list[str]:
    vals = []
    for key in ("title", "name", "base_title", "title_clean", "song_family"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            vals.append(v.strip())
    return vals


def build_track_lookup() -> Dict[str, str]:
    lookup = {}

    for item in iter_website_song_entries():
        track_id = get_track_id(item)
        if not track_id:
            continue
        for field in title_fields_from_item(item):
            for key in possible_title_keys(field):
                lookup.setdefault(key, track_id)

    for item in iter_discography_tracks():
        track_id = get_track_id(item)
        if not track_id:
            continue
        for field in title_fields_from_item(item):
            for key in possible_title_keys(field):
                lookup.setdefault(key, track_id)

    return lookup


def build_manual_mapping() -> Dict[str, str]:
    if not MANUAL_MAP_PATH.exists():
        return {}

    data = load_json(MANUAL_MAP_PATH)
    if not isinstance(data, dict):
        return {}

    out = {}
    for k, v in data.items():
        for key in possible_title_keys(k):
            out[key] = str(v)
    return out


def to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except:
        return None


def upload_json_bytes(obj: Dict[str, Any], bucket_key: str) -> None:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    s3.upload_fileobj(
        io.BytesIO(raw),
        R2_BUCKET,
        bucket_key,
        ExtraArgs={"ContentType": "application/json"},
    )


# 🔥 FIX PRINCIPAL ICI
def resolve_track_id(song_name: str, manual_lookup: Dict[str, str], track_lookup: Dict[str, str]) -> Optional[str]:
    keys = possible_title_keys(song_name)

    # 1. manuel
    for key in keys:
        if key in manual_lookup:
            return manual_lookup[key]

    # 2. exact
    for key in keys:
        if key in track_lookup:
            return track_lookup[key]

    # 3. inclusion (très puissant)
    for key in keys:
        for k, track_id in track_lookup.items():
            if key in k or k in key:
                return track_id

    # 4. fuzzy léger
    for key in keys:
        for k, track_id in track_lookup.items():
            if abs(len(key) - len(k)) <= 3:
                if key[:10] == k[:10]:
                    return track_id

    return None


def main() -> None:
    track_lookup = build_track_lookup()
    manual_lookup = build_manual_mapping()

    by_track = defaultdict(list)
    unresolved = []

    with CSV_PATH.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row in reader:
            song_name = (row.get("song_name") or "").strip()
            date = (row.get("date") or "").strip()

            if not song_name or not date:
                continue

            track_id = resolve_track_id(song_name, manual_lookup, track_lookup)

            if not track_id:
                unresolved.append({
                    "date": date,
                    "song_name": song_name,
                })
                continue

            by_track[track_id].append({
                "date": date,
                "rank": to_int(row.get("rank")),
                "streams": to_int(row.get("streams")),
            })

    uploaded = 0

    for track_id, points in by_track.items():
        points.sort(key=lambda x: x["date"])

        upload_json_bytes(
            {"track_id": track_id, "points": points},
            f"{R2_PREFIX}/{track_id}.json"
        )
        uploaded += 1

    if unresolved:
        upload_json_bytes({"unresolved": unresolved}, f"{R2_PREFIX}/_unresolved.json")
        print(f"[WARN] {len(unresolved)} unresolved")

    print(f"[DONE] {uploaded} tracks uploaded")


if __name__ == "__main__":
    main()