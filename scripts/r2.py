#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "website" / "site" / "history"
SITE_DATA_DIR = ROOT / "website" / "site" / "data"
DB_DIR = ROOT / "db"

R2_ACCOUNT_ID = os.environ["R2_ACCOUNT_ID"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ["R2_BUCKET"]

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
)


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def upload_json_bytes(obj: dict, bucket_key: str) -> None:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    s3.upload_fileobj(
        io.BytesIO(raw),
        R2_BUCKET,
        bucket_key,
        ExtraArgs={"ContentType": "application/json"},
    )


def upload_raw_bytes(data: bytes, bucket_key: str, content_type: str) -> None:
    s3.upload_fileobj(
        io.BytesIO(data),
        R2_BUCKET,
        bucket_key,
        ExtraArgs={"ContentType": content_type},
    )


def upload_static_data() -> None:
    """Upload all generated JSON/CSV data files to R2 so the Vercel API can read them."""

    # ── JSON files from website/site/data/ ───────────────────────────────────
    json_mappings = [
        ("songs.json",               "data/songs.json"),
        ("albums.json",              "data/albums.json"),
        ("artist.json",              "data/artist.json"),
        ("expected_milestones.json", "data/milestones.json"),
        ("billboard.json",           "data/billboard.json"),
        ("applemusic.json",          "data/applemusic.json"),
        ("applemusic_history.json",  "data/applemusic_history.json"),
        ("songs-appearances.json",   "data/songs-appearances.json"),
    ]
    for filename, r2_key in json_mappings:
        src = SITE_DATA_DIR / filename
        if not src.exists():
            print(f"[SKIP] absent: {src}")
            continue
        obj = load_json(src)
        upload_json_bytes(obj, r2_key)
        print(f"[OK] {r2_key}")

    # ── CSV charts from db/ ───────────────────────────────────────────────────
    csv_mappings = [
        ("charts_history_global.csv", "data/charts_global.csv"),
        ("charts_history_fr.csv",     "data/charts_fr.csv"),
    ]
    for filename, r2_key in csv_mappings:
        src = DB_DIR / filename
        if not src.exists():
            print(f"[SKIP] absent: {src}")
            continue
        upload_raw_bytes(src.read_bytes(), r2_key, "text/csv; charset=utf-8")
        print(f"[OK] {r2_key}")

    # ── history/index.json ────────────────────────────────────────────────────
    index_path = HISTORY_DIR / "index.json"
    if index_path.exists():
        upload_json_bytes(load_json(index_path), "history/index.json")
        print("[OK] history/index.json")

    # ── daily history snapshots ───────────────────────────────────────────────
    daily_files = sorted(
        p for p in HISTORY_DIR.glob("*.json")
        if p.name != "index.json"
    )
    for path in daily_files:
        m = DATE_RE.search(path.stem)
        if not m:
            continue
        r2_key = f"history/{m.group(1)}.json"
        upload_json_bytes(load_json(path), r2_key)
        print(f"[OK] {r2_key}")

    print(f"[DONE] static data uploaded ({len(json_mappings)} JSON + {len(csv_mappings)} CSV + history)")


def main() -> None:
    if not HISTORY_DIR.exists():
        raise FileNotFoundError(f"History folder not found: {HISTORY_DIR}")

    daily_files = sorted(
        p for p in HISTORY_DIR.glob("*.json")
        if p.name != "index.json"
    )

    if not daily_files:
        print("No history files found.")
        return

    by_track = defaultdict(list)

    for path in daily_files:
        m = DATE_RE.search(path.stem)
        if not m:
            print(f"[SKIP] date introuvable: {path}")
            continue

        date = m.group(1)
        data = load_json(path)

        if not isinstance(data, dict):
            print(f"[SKIP] format invalide: {path}")
            continue

        for track_id, values in data.items():
            if not isinstance(values, dict):
                continue

            point = {
                "date": date,
                "streams": values.get("s"),
                "daily_streams": values.get("d"),
            }

            if "rank" in values:
                point["rank"] = values.get("rank")

            by_track[track_id].append(point)

    uploaded = 0

    for track_id, points in by_track.items():
        points.sort(key=lambda x: x["date"])

        payload = {
            "track_id": track_id,
            "points": points,
        }

        bucket_key = f"history-by-track/{track_id}.json"
        upload_json_bytes(payload, bucket_key)
        uploaded += 1
        print(f"[OK] {bucket_key}")

    print(f"[DONE] {uploaded} fichiers history-by-track envoyés sur R2")

    # Upload all static data files
    upload_static_data()


if __name__ == "__main__":
    main()
