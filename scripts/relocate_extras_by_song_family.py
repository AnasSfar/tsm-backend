#!/usr/bin/env python3
"""Move `on_album=True` tracks that are still sitting in the flat catalogs
(songs.json/misc.json/features.json) into the real album file their
`song_family` belongs to — fixes the case where the schema migration correctly
marked something like "Red - Commentary" as on_album=True/role=extra, but it's
still physically filed in the generic "Standalone & Extras" bucket instead of
living alongside "Red" itself in albums/red.json.

The target album file is found via an ANCHOR: an existing on_album=True,
role="album_track" track elsewhere that shares the same song_family. When a
song_family is ambiguous between an original album and its "Taylor's Version"
(most songs — e.g. "welcome_to_new_york" exists in both 1989.json and
1989_taylor_s_version.json), the track's own title decides: mentions "Taylor's
Version" -> the TV file, otherwise -> the original. Anything that can't be
resolved this way (no anchor found, or still ambiguous) is left untouched and
listed in data/relocate_extras_review.csv for manual handling via the editor.

Reuses catalog.py's helpers (section resolution, count guard, atomic write,
backup) — this is deliberately not a reimplementation of that safety logic.

Usage:
    python scripts/relocate_extras_by_song_family.py             # dry-run
    python scripts/relocate_extras_by_song_family.py --apply
    python scripts/relocate_extras_by_song_family.py --apply --no-backup
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discography_editor"))
import catalog  # noqa: E402

FLAT_FILES = ("songs.json", "misc.json", "features.json")
REVIEW_CSV = ROOT / "data" / "relocate_extras_review.csv"
RE_TAYLORS_VERSION = re.compile(r"taylor.?s version", re.IGNORECASE)


def norm(text) -> str:
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.casefold()


def build_family_index(payloads: dict) -> dict[str, list[str]]:
    """song_family -> album files it's confirmed to belong to (anchors only:
    on_album=True, role=album_track — never inferred from other extras)."""
    index: dict[str, list[str]] = {}
    for rel, payload in payloads.items():
        if not rel.startswith("albums/"):
            continue
        for section in catalog._sections_of(payload):
            if not isinstance(section, dict):
                continue
            for track in section.get("tracks") or []:
                if track.get("on_album") and track.get("role") == "album_track" and track.get("song_family"):
                    index.setdefault(track["song_family"], []).append(rel)
    return index


def pick_target(candidates: list[str], title: str) -> str | None:
    candidates = sorted(set(candidates))
    if len(candidates) == 1:
        return candidates[0]
    is_tv_title = bool(RE_TAYLORS_VERSION.search(norm(title)))
    tv = [c for c in candidates if "taylor_s_version" in c]
    non_tv = [c for c in candidates if "taylor_s_version" not in c]
    if is_tv_title and len(tv) == 1:
        return tv[0]
    if not is_tv_title and len(non_tv) == 1:
        return non_tv[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    paths = catalog._discover_files()
    payloads = {catalog._rel(p): catalog.read_json(p) for p in paths}
    family_index = build_family_index(payloads)
    before_count = catalog._total_track_count(payloads)

    moved: list[tuple[str, str, str]] = []
    review_rows: list[dict] = []
    touched: set[str] = set()

    for rel in FLAT_FILES:
        if rel not in payloads:
            continue
        payload = payloads[rel]
        for section in catalog._sections_of(payload):
            if not isinstance(section, dict):
                continue
            tracks = section.get("tracks") or []
            for track in list(tracks):
                if not track.get("on_album") or not track.get("song_family"):
                    continue
                candidates = family_index.get(track["song_family"])
                if not candidates:
                    review_rows.append({
                        "title": track.get("title", ""), "song_family": track.get("song_family", ""),
                        "reason": "aucun album trouvé avec ce song_family",
                    })
                    continue
                target = pick_target(candidates, track.get("title") or "")
                if target is None:
                    review_rows.append({
                        "title": track.get("title", ""), "song_family": track.get("song_family", ""),
                        "reason": f"ambigu entre {sorted(set(candidates))}",
                    })
                    continue

                dest_payload = payloads[target]
                dest_section = catalog._resolve_target_section(dest_payload, section.get("section"), section.get("section", ""))
                idx = next(i for i, t in enumerate(tracks) if t is track)
                tracks.pop(idx)
                track["album"] = catalog._group_album_name(target, dest_payload)
                dest_section.setdefault("tracks", []).append(track)
                touched.add(rel)
                touched.add(target)
                moved.append((track.get("title", ""), rel, target))

    after_count = catalog._total_track_count(payloads)
    print(f"tracks avant: {before_count}, après: {after_count}")
    if after_count != before_count:
        print("GARDE-FOU: le nombre de tracks a changé — rien n'est écrit.")
        return

    print(f"déplacés automatiquement: {len(moved)}")
    for title, src, dst in moved[:30]:
        print(f"  {title!r}: {src} -> {dst}")
    if len(moved) > 30:
        print(f"  ... et {len(moved) - 30} de plus")

    REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "song_family", "reason"])
        writer.writeheader()
        writer.writerows(review_rows)
    print(f"non résolus (revue manuelle): {len(review_rows)} -> {REVIEW_CSV.relative_to(ROOT)}")

    if not args.apply:
        print("\n[dry-run] aucune écriture — relance avec --apply pour écrire les fichiers.")
        return

    for rel in touched:
        catalog._refresh_counts(payloads[rel])

    rendered = {}
    for rel in touched:
        text = catalog.write_json_text(payloads[rel])
        json.loads(text)  # re-validate before writing anything
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
