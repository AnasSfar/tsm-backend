#!/usr/bin/env python3
"""
Backfill total_days.json for worldwide charts.

Priority order per (track_id, country):
  1. FR/Global/US/UK CSV files — these have accurate accumulated totals.
  2. Worldwide history snapshots — for all other countries, count appearances.

The result seeds tools/json/total_days.json so daily.py accumulates correctly
from a known-good baseline instead of starting from the wrong streak value.

Usage:
    python backfill_total_days.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT         = Path(__file__).resolve().parents[4]
DB_DIR       = ROOT / "db"
OUTPUT_PATH  = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "total_days.json"
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.data_paths import (  # noqa: E402
    LEGACY_WEBSITE_DATA_DIR,
    WEB_EXPORT_DATA_DIR,
    first_existing,
    spotify_chart_snapshot_files,
)

_TRACK_ID_RE = re.compile(r"[A-Za-z0-9]{22}")

REGIONAL_CSVS = {
    "global": DB_DIR / "charts_history_global.csv",
    "fr":     DB_DIR / "charts_history_fr.csv",
    "us":     DB_DIR / "charts_history_us.csv",
    "uk":     DB_DIR / "charts_history_uk.csv",
}

DISCO_SONGS_PATH = ROOT / "db" / "discography" / "songs.json"
WEBSITE_SONGS_PATH = first_existing(WEB_EXPORT_DATA_DIR / "songs.json", LEGACY_WEBSITE_DATA_DIR / "songs.json")


def _track_id_from_url(value: str) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if _TRACK_ID_RE.fullmatch(text):
        return text
    try:
        path = urlparse(text).path.strip("/")
        parts = path.split("/")
        if len(parts) >= 2 and parts[-2] == "track":
            return parts[-1]
    except Exception:
        pass
    return None


def _normalize_song_name(value: str) -> str:
    text = str(value or "").lower().strip()
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _build_song_indexes() -> tuple[dict[str, str], dict[str, str]]:
    """Map normalized song names and historical IDs to canonical track IDs."""
    name_to_tid: dict[str, str] = {}
    tid_to_canonical: dict[str, str] = {}
    for path in (DISCO_SONGS_PATH, WEBSITE_SONGS_PATH):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        songs = data if isinstance(data, list) else data.get("songs", [])
        for song in songs:
            if not isinstance(song, dict):
                continue
            tid = _track_id_from_url(song.get("track_id") or song.get("url") or "")
            if not tid:
                continue
            tid_to_canonical.setdefault(tid, tid)
            for historical_id in song.get("historical_track_ids") or []:
                hist_tid = _track_id_from_url(str(historical_id or ""))
                if hist_tid:
                    tid_to_canonical.setdefault(hist_tid, tid)
            for field in ("title", "base_title", "title_clean"):
                name = _normalize_song_name(str(song.get(field) or ""))
                if name:
                    name_to_tid.setdefault(name, tid)
    return name_to_tid, tid_to_canonical


def main() -> None:
    counts: dict[str, int] = {}

    # ── 1. Seed from regional CSVs (most reliable) ────────────────────────────
    name_to_tid, tid_to_canonical = _build_song_indexes()
    for region, csv_path in REGIONAL_CSVS.items():
        if not csv_path.exists():
            print(f"[SKIP] CSV not found: {csv_path}")
            continue
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # Find the highest verified total_days per track. Prefer exact
            # track_id from the regional archive, falling back to an explicit
            # songs.json name match for older rows that predate track IDs.
            best: dict[str, int] = {}
            for row in reader:
                raw_tid = _track_id_from_url(row.get("track_id") or "")
                tid = tid_to_canonical.get(raw_tid or "", raw_tid)
                if not tid:
                    song_name = _normalize_song_name(row.get("song_name") or "")
                    tid = name_to_tid.get(song_name)
                if not tid:
                    continue
                td = row.get("total_days") or ""
                try:
                    td_int = int(float(td))
                except (ValueError, TypeError):
                    continue
                key = f"{tid}|{region}"
                if td_int > best.get(key, 0):
                    best[key] = td_int
        counts.update(best)
        print(f"[CSV] {region}: {len(best)} entries seeded")

    # ── 2. Fill gaps from worldwide history (other countries) ─────────────────
    snapshot_files = spotify_chart_snapshot_files("worldwide", "ts_worldwide_*.json")
    print(f"[INFO] Scanning {len(snapshot_files)} worldwide snapshots for non-CSV countries…")

    history_counts: dict[str, int] = {}
    for path in snapshot_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            print(f"[WARN] Skipping {path.name}: {exc}")
            continue
        by_track = data.get("by_track")
        if not isinstance(by_track, dict):
            continue
        for track_id, entries in by_track.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                country = entry.get("country")
                if not country or country in REGIONAL_CSVS:
                    continue  # CSV already handled these
                key = f"{track_id}|{country}"
                history_counts[key] = history_counts.get(key, 0) + 1

    # CSV values win; history fills only what's missing.
    for key, val in history_counts.items():
        if key not in counts:
            counts[key] = val

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(counts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] {len(counts)} entries -> {OUTPUT_PATH}")
    top = sorted(counts.items(), key=lambda x: -x[1])[:10]
    print("[INFO] Top 10:")
    for key, days in top:
        print(f"  {key}: {days}d")


if __name__ == "__main__":
    main()
