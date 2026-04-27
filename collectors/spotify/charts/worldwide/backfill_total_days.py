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
from pathlib import Path
from urllib.parse import urlparse

ROOT         = Path(__file__).resolve().parents[4]
HISTORY_ROOT = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "history"
DB_DIR       = ROOT / "db"
OUTPUT_PATH  = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "total_days.json"

_TRACK_ID_RE = re.compile(r"[A-Za-z0-9]{22}")

REGIONAL_CSVS = {
    "global": DB_DIR / "charts_history_global.csv",
    "fr":     DB_DIR / "charts_history_fr.csv",
    "us":     DB_DIR / "charts_history_us.csv",
    "uk":     DB_DIR / "charts_history_uk.csv",
}

DISCO_SONGS_PATH = ROOT / "db" / "discography" / "songs.json"
WEBSITE_SONGS_PATH = ROOT / "website" / "site" / "data" / "songs.json"


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


def _build_song_name_to_track_id() -> dict[str, str]:
    """Map normalised song name → track_id from discography / website songs."""
    mapping: dict[str, str] = {}
    for path in (DISCO_SONGS_PATH, WEBSITE_SONGS_PATH):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        songs = data if isinstance(data, list) else data.get("songs", [])
        for song in songs:
            if not isinstance(song, dict):
                continue
            tid = _track_id_from_url(song.get("track_id") or song.get("url") or "")
            if not tid:
                continue
            for field in ("title", "base_title", "title_clean"):
                name = str(song.get(field) or "").lower().strip()
                if name:
                    mapping.setdefault(name, tid)
    return mapping


def main() -> None:
    counts: dict[str, int] = {}

    # ── 1. Seed from regional CSVs (most reliable) ────────────────────────────
    name_to_tid = _build_song_name_to_track_id()
    for region, csv_path in REGIONAL_CSVS.items():
        if not csv_path.exists():
            print(f"[SKIP] CSV not found: {csv_path}")
            continue
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # Find the latest total_days per song (last row for each song_name).
            best: dict[str, int] = {}
            for row in reader:
                song_name = (row.get("song_name") or "").lower().strip()
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
    snapshot_files = sorted(HISTORY_ROOT.rglob("ts_worldwide_*.json"))
    print(f"[INFO] Scanning {len(snapshot_files)} worldwide snapshots for non-CSV countries…")

    history_counts: dict[str, int] = {}
    for path in snapshot_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
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
    print(f"[DONE] {len(counts)} entries → {OUTPUT_PATH}")
    top = sorted(counts.items(), key=lambda x: -x[1])[:10]
    print("[INFO] Top 10:")
    for key, days in top:
        print(f"  {key}: {days}d")


if __name__ == "__main__":
    main()
