#!/usr/bin/env python3
"""Delete active tracks whose Spotify track_id is ALREADY listed in another
track's `historical_track_ids` elsewhere in the discography.

This is a real duplication bug, not a judgment call: `historical_track_ids` on
a track means "this ID is a superseded/duplicate ID for THIS song, exclude it
from active collection" (see REPO_CONTEXT.md § 2). When that same ID also
exists as its OWN separate active track entry, the song is being counted
twice. Deleting the duplicate loses nothing — the canonical track already
keeps the ID for historical stream continuity.

Dry-run by default (prints what would be deleted), --apply to write (backup +
atomic write, same conventions as the rest of scripts/).

Usage:
    python scripts/dedupe_historical_track_ids.py
    python scripts/dedupe_historical_track_ids.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discography_editor"))
import catalog  # noqa: E402

TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")


def extract_id(url: str | None) -> str | None:
    m = TRACK_ID_RE.search(url or "")
    return m.group(1) if m else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    paths = catalog._discover_files()
    payloads = {catalog._rel(p): catalog.read_json(p) for p in paths}
    before_count = catalog._total_track_count(payloads)

    all_historical: set[str] = set()
    for payload in payloads.values():
        for section in catalog._sections_of(payload):
            if not isinstance(section, dict):
                continue
            for track in section.get("tracks") or []:
                all_historical.update(track.get("historical_track_ids") or [])

    touched: set[str] = set()
    removed: list[tuple[str, str]] = []

    for rel, payload in payloads.items():
        for section in catalog._sections_of(payload):
            if not isinstance(section, dict):
                continue
            tracks = section.get("tracks") or []
            for track in list(tracks):
                tid = extract_id(track.get("url"))
                if tid and tid in all_historical:
                    idx = next(i for i, t in enumerate(tracks) if t is track)
                    tracks.pop(idx)
                    touched.add(rel)
                    removed.append((track.get("title", ""), rel))

    after_count = catalog._total_track_count(payloads)
    print(f"tracks avant: {before_count}, après: {after_count}, supprimés: {len(removed)}")
    if after_count != before_count - len(removed):
        print("GARDE-FOU: incohérence de compte — rien n'est écrit.")
        return

    for title, rel in removed:
        print(f"  supprimé: {title!r} ({rel})")

    if not args.apply:
        print("\n[dry-run] aucune écriture — relance avec --apply pour écrire les fichiers.")
        return

    for rel in touched:
        catalog._refresh_counts(payloads[rel])

    rendered = {}
    for rel in touched:
        text = catalog.write_json_text(payloads[rel])
        json.loads(text)
        rendered[rel] = text

    for rel, text in rendered.items():
        path = catalog.DISCOGRAPHY_DIR / rel
        if not args.no_backup and path.exists():
            shutil.copy2(path, catalog.backup_path(path))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    print(f"\n[apply] {len(touched)} fichiers réécrits.")


if __name__ == "__main__":
    main()
