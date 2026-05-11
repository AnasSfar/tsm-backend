#!/usr/bin/env python3
"""Debug: Check what values are being loaded."""

import csv
import re
import json
from pathlib import Path
from datetime import date, timedelta

_REPO_ROOT = Path(__file__).resolve().parent
_DB_DIR = _REPO_ROOT / "db"

CHARTS_GLOBAL_CSV = _DB_DIR / "charts_history_global.csv"
STREAMS_HISTORY_CSV = _DB_DIR / "streams_history.csv"
DISCOGRAPHY_DIR = _DB_DIR / "discography"

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_PAREN_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_DASH_SPLIT_RE = re.compile(r"\s+-\s+")
_TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")

def _normalize_title(value: str) -> str:
    s = (value or "").strip().casefold()
    if not s:
        return ""
    s = s.replace("'", "'").replace("'", "'").replace(""", '"').replace(""", '"')
    s = _PAREN_RE.sub(" ", s)
    s = _DASH_SPLIT_RE.split(s, maxsplit=1)[0]
    s = _NORMALIZE_RE.sub(" ", s)
    s = " ".join(s.split())
    return s

def _extract_track_id(url: str | None) -> str | None:
    if not url:
        return None
    m = _TRACK_ID_RE.search(url)
    return m.group(1) if m else None

# Week of April 3, 2026
week_end = date(2026, 4, 3)
week_start = week_end - timedelta(days=6)
day_list = [str(week_start + timedelta(days=i)) for i in range(7)]
week_set = set(day_list)

print(f"Week: {week_start} to {week_end}\n")

# Load unfiltered from streams_history
print("=" * 60)
print("UNFILTERED STREAMS (from streams_history.csv)")
print("=" * 60)
unfiltered_by_tid = {}
with STREAMS_HISTORY_CSV.open("r") as f:
    reader = csv.DictReader(f)
    for row in reader:
        day = (row.get("date") or "").strip()
        if day not in week_set:
            continue
        tid = (row.get("track_id") or "").strip()
        daily_streams = int((row.get("daily_streams") or "0").strip() or 0)
        if not tid or daily_streams <= 0:
            continue
        unfiltered_by_tid[tid] = unfiltered_by_tid.get(tid, 0) + daily_streams

print(f"Loaded {len(unfiltered_by_tid)} track IDs with unfiltered streams")

# Load filtered from charts
print("\n" + "=" * 60)
print("FILTERED STREAMS (from charts_history_global.csv)")
print("=" * 60)
filtered_by_title = {}
with CHARTS_GLOBAL_CSV.open("r", encoding="utf-8", errors="replace") as f:
    reader = csv.DictReader(f)
    for row in reader:
        day = (row.get("date") or "").strip()
        if day not in week_set:
            continue
        title = (row.get("song_name") or "").strip()
        streams = int((row.get("streams") or "0").strip() or 0)
        if not title or streams <= 0:
            continue
        norm = _normalize_title(title)
        filtered_by_title[norm] = filtered_by_title.get(norm, 0) + streams

print(f"Loaded {len(filtered_by_title)} normalized titles with filtered streams")
for norm in list(filtered_by_title.keys())[:5]:
    print(f"  {norm}: {filtered_by_title[norm]:,}")

# Load track ID to title mapping
print("\n" + "=" * 60)
print("TRACK ID MAPPING")
print("=" * 60)
title_by_tid = {}
albums_dir = DISCOGRAPHY_DIR / "albums"
songs_json = DISCOGRAPHY_DIR / "songs.json"

# From albums
if albums_dir.exists():
    for album_file in sorted(albums_dir.glob("*.json"))[:2]:
        try:
            payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            for section in payload.get("sections", []):
                for track in section.get("tracks", []):
                    url = (track.get("url") or "").strip()
                    tid = _extract_track_id(url)
                    title = (track.get("title") or "").strip()
                    if tid and title:
                        title_by_tid[tid] = title
        except Exception:
            pass

# From songs.json
if songs_json.exists():
    try:
        payload = json.loads(songs_json.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            for section in payload:
                for track in section.get("tracks", []):
                    url = (track.get("url") or "").strip()
                    tid = _extract_track_id(url)
                    title = (track.get("title") or "").strip()
                    if tid and title:
                        title_by_tid[tid] = title
    except Exception:
        pass

print(f"Loaded {len(title_by_tid)} track IDs with titles")

# Test specific track IDs
test_tids = ["6PlBKImSl4AZoxBU7F649D", "0E3Vtmgzk067KASoo0HwzV", "3r9fAceWmaDFvbNiTnFiLr"]
print("\n" + "=" * 60)
print("TEST DATA FOR SPECIFIC TRACKS")
print("=" * 60)

for tid in test_tids:
    title = title_by_tid.get(tid, "??")
    norm_title = _normalize_title(title)
    unfiltered = unfiltered_by_tid.get(tid, 0)
    filtered = filtered_by_title.get(norm_title, 0)
    
    print(f"\n{tid}")
    print(f"  Title: {title}")
    print(f"  Normalized: {norm_title}")
    print(f"  Unfiltered streams: {unfiltered:,}")
    print(f"  Filtered streams: {filtered:,}")
    print(f"  Bonus: {max(0, unfiltered - filtered):,}")
