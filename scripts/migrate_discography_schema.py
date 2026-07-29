#!/usr/bin/env python3
"""Phase 1 of the discography schema redesign (see .claude/plans or REPO_CONTEXT.md § 9).

Adds NEW fields to every track in db/discography (`on_album`, `role`, `extra_type`,
`category`, `release_edition`, `display_album`, `tags`) computed from the existing
messy fields (`type`, `edition`, `section`, title text). Old fields are left 100%
untouched — nothing currently reading `type`/`edition`/`section`/`filter_tags`/
`display_era` should notice any difference after this script runs. The old fields
only get removed later, one consumer script at a time (Phase 2), once each has been
migrated to read the new fields instead. Running this script twice re-derives
everything from scratch (idempotent), it does not layer on top of a previous run.

The one EXCEPTION is `song_family`: for tracks classified as `role=extra`, this
script tries to re-link it to its base song (fixing a real bug found in the DB,
e.g. "Blank Space - Karaoke Version" had its own disconnected `song_family` instead
of pointing back to `blank_space`) — `song_family` is safe to correct in place
because every real consumer of it (spotlight.py, best_day_since.py, chart matching)
wants the correct link and nothing depends on the old broken value.

Usage:
    python scripts/migrate_discography_schema.py              # dry-run, writes the review CSV
    python scripts/migrate_discography_schema.py --apply       # writes the new fields for real
    python scripts/migrate_discography_schema.py --no-backup   # (with --apply) skip the .bak copies
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DISCOGRAPHY_DIR = ROOT / "db" / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
FLAT_FILES = ("songs.json", "misc.json", "features.json")
REVIEW_CSV = ROOT / "data" / "schema_migration_review.csv"

DEFAULT_ARTIST = "Taylor Swift"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def backup(path: Path) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_suffix(path.suffix + f".schema-migration-{stamp}.bak")
    shutil.copy2(path, target)
    print(f"[backup] {target.relative_to(ROOT)}")


def discover_files() -> list[Path]:
    files = [DISCOGRAPHY_DIR / name for name in FLAT_FILES if (DISCOGRAPHY_DIR / name).exists()]
    files.extend(sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()))
    return files


def sections_of(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("sections") or []
    return []


# ---------------------------------------------------------------------------
# title-text detection helpers
# ---------------------------------------------------------------------------

def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


RE_KARAOKE = re.compile(r"karaoke")
RE_VOICE_MEMO = re.compile(r"voice memo")
RE_COMMENTARY = re.compile(r"commentary")
RE_TRACK_BY_TRACK = re.compile(r"track by track")
RE_DEMO = re.compile(r"\bdemo\b")
RE_ACOUSTIC = re.compile(r"acoustic")
RE_INSTRUMENTAL = re.compile(r"instrumental")
RE_REMIX = re.compile(r"remix|\bmix\b|extended version|international mix")
RE_LIVE = re.compile(r"\blive\b|studio sessions|awards performance")
RE_FROM_VAULT = re.compile(r"from the vault")
RE_TAYLORS_VERSION = re.compile(r"taylor.?s version")
RE_SOUNDTRACK = re.compile(r"soundtrack|motion picture|from \"|featured in")

# Order matters: first match wins (karaoke/voice_memo/commentary are unambiguous
# and must be checked before the much broader "remix"/"live" patterns).
EXTRA_TYPE_PATTERNS = (
    ("karaoke", RE_KARAOKE),
    ("voice_memo", RE_VOICE_MEMO),
    ("commentary", RE_COMMENTARY),
    ("commentary", RE_TRACK_BY_TRACK),
    ("demo", RE_DEMO),
    ("instrumental", RE_INSTRUMENTAL),
    ("acoustic", RE_ACOUSTIC),
    ("remix", RE_REMIX),
    ("live", RE_LIVE),
)


def detect_extra_type(text: str, default: str) -> str:
    t = norm(text)
    for extra_type, pattern in EXTRA_TYPE_PATTERNS:
        if pattern.search(t):
            return extra_type
    return default


def detect_release_edition_override(text: str, current: str) -> str:
    t = norm(text)
    if RE_FROM_VAULT.search(t):
        return "from_the_vault"
    if RE_TAYLORS_VERSION.search(t) and current in ("standard", "taylors_version"):
        return "taylors_version"
    return current


# ---------------------------------------------------------------------------
# section -> base classification
# ---------------------------------------------------------------------------

class Classification:
    __slots__ = ("on_album", "role", "extra_type", "category", "release_edition", "low_confidence", "reason")

    def __init__(self, on_album, role=None, extra_type=None, category=None, release_edition=None,
                 low_confidence=False, reason=""):
        self.on_album = on_album
        self.role = role
        self.extra_type = extra_type
        self.category = category
        self.release_edition = release_edition
        self.low_confidence = low_confidence
        self.reason = reason


# Fixed, high-confidence rules keyed by section name.
SECTION_RULES: dict[str, dict] = {
    "standard_edition": dict(on_album=True, role="album_track", release_edition="standard"),
    "standard": dict(on_album=True, role="album_track", release_edition="standard"),
    "standard_edition_tv": dict(on_album=True, role="album_track", release_edition="taylors_version"),
    "deluxe_edition": dict(on_album=True, role="album_track", release_edition="deluxe"),
    "platinum_edition": dict(on_album=True, role="album_track", release_edition="platinum"),
    "anthology_edition": dict(on_album=True, role="album_track", release_edition="anthology"),
    "vault_tracks": dict(on_album=True, role="album_track", release_edition="from_the_vault"),
    "from_the_vault": dict(on_album=True, role="album_track", release_edition="from_the_vault"),
    "til_dawn_edition": dict(on_album=True, role="album_track", release_edition="til_dawn"),
    "3am_edition": dict(on_album=True, role="album_track", release_edition="3am"),
    "acoustic_edition": dict(on_album=True, role="extra", extra_type="acoustic", release_edition="deluxe"),
    "karaoke": dict(on_album=True, role="extra", extra_type="karaoke"),
    "track_by_track": dict(on_album=True, role="extra", extra_type="commentary"),
    "long_pond_studio_sessions": dict(on_album=True, role="extra", extra_type="live", release_edition="deluxe"),
    "voice_memos": dict(on_album=True, role="extra", extra_type="voice_memo"),
    "soundtracks": dict(on_album=False, category="soundtrack"),
    "standalone": dict(on_album=False, category="other"),
    "live": dict(on_album=False, category="other"),
    # title-dependent below: base gives the *default* when no pattern matches
    "remixes": dict(on_album=True, role="extra", extra_type="remix", _title_dependent=True),
    "remixes_and_live": dict(on_album=True, role="extra", extra_type="live", _title_dependent=True),
    "extras_and_live": dict(on_album=True, role="extra", extra_type="acoustic", _title_dependent=True),
    "demos_and_acoustic": dict(on_album=True, role="extra", extra_type="acoustic", release_edition="deluxe", _title_dependent=True),
    "extras": dict(on_album=True, role="album_track", release_edition="deluxe", _title_dependent=True, _extra_fallback=True),
    "cruel_summer_single": dict(on_album=True, role="extra", extra_type="remix", _title_dependent=True),
    "christmas_tree_farm_single": dict(on_album=False, category="other", _title_dependent=True),
    # these two are handled by classify_streaming_extras_fallback() in a second
    # pass instead of the generic branch below — see main()
    "streaming_extras": dict(on_album=True, role="extra", extra_type="other", _needs_context=True),
    "misc_standalone": dict(on_album=True, role="extra", extra_type="other", _needs_context=True),
    "collabs_and_features": dict(on_album=False, category="feature", _title_dependent=True, _review="feature/collab avec parfois karaoké/commentaire — role extra non modélisable ici"),
    "taylor_versions_standalone": dict(on_album=True, role="album_track", release_edition="taylors_version", _review="mélange track officiel (All Too Well 10 Min) et versions alternatives (short film, long pond)"),
    "original_edition": dict(on_album=True, role="album_track", release_edition="standard", _review="chansons originales 2008 rangées dans le fichier (Taylor's Version) — l'album réel est probablement l'édition originale, pas la TV"),
}


def classify_streaming_extras_fallback(track: dict, text: str, known_album_titles: set[str]) -> Classification:
    """Second-pass classifier for `streaming_extras`/`misc_standalone` — the two
    catch-all buckets where `type`/`edition`/`section` carry almost no signal.
    Only reached when no karaoke/live/remix/etc. title pattern matched. Tries,
    in order: soundtrack placement, feature/collab on someone else's song, "this
    is just a real album track re-listed under the same title" (confirmed
    pattern: showgirl tracks like "Opalite"/"Wood" appear a second time here for
    international-chart tracking) — else gives up and flags it for review."""
    norm_text = norm(text)
    primary = norm(track.get("primary_artist"))

    if RE_SOUNDTRACK.search(norm_text):
        return Classification(on_album=False, category="soundtrack")

    if primary and primary != norm(DEFAULT_ARTIST):
        return Classification(on_album=False, category="collab")

    base = norm(strip_extra_suffix(track.get("title") or ""))
    if base and base in known_album_titles:
        return Classification(on_album=True, role="album_track", release_edition="standard")

    return Classification(on_album=True, role="extra", extra_type="other", low_confidence=True,
                           reason="aucun signe (titre/artiste) ne permet de classer ce track automatiquement")


def classify_track(track: dict, section_name: str, group_rel: str,
                    known_album_titles: set[str] | None = None) -> Classification:
    text = " ".join(str(track.get(k) or "") for k in ("title", "base_title"))
    rule = SECTION_RULES.get(section_name)

    if rule is None:
        return Classification(on_album=True, role="album_track", low_confidence=True,
                               reason=f"section inconnue du référentiel de migration: {section_name!r}")

    if rule.get("_needs_context"):
        detected = detect_extra_type(text, default="")
        if detected:
            return Classification(on_album=True, role="extra", extra_type=detected)
        return classify_streaming_extras_fallback(track, text, known_album_titles or set())

    on_album = rule["on_album"]
    role = rule.get("role")
    extra_type = rule.get("extra_type")
    category = rule.get("category")
    release_edition = rule.get("release_edition")
    low_confidence = bool(rule.get("_review"))
    reason = rule.get("_review", "")

    if rule.get("_title_dependent"):
        if rule.get("_extra_fallback"):
            # "extras" section: title match -> real extra; no match -> assume it's
            # a deluxe bonus track filed oddly (confirmed pattern: "Crazier",
            # "September - Recorded at The Tracking Room Nashville", "the lakes").
            detected = detect_extra_type(text, default="")
            if detected:
                role, extra_type, on_album = "extra", detected, True
            else:
                role, extra_type = "album_track", None
        elif on_album and role == "extra":
            extra_type = detect_extra_type(text, default=extra_type)
        elif category:
            # collabs_and_features etc: karaoke/commentary variants of a feature
            # track don't fit the tree cleanly -> always flagged for review.
            if RE_KARAOKE.search(norm(text)) or RE_COMMENTARY.search(norm(text)):
                low_confidence = True
                reason = reason or "variante karaoké/commentaire d'un feature — vérifier manuellement"

    if on_album and release_edition:
        release_edition = detect_release_edition_override(text, release_edition)

    if on_album:
        primary = norm(track.get("primary_artist"))
        featured = track.get("featured_artists") or []
        if not category and (primary and primary != norm(DEFAULT_ARTIST)):
            pass  # on_album tracks are always Taylor's own; ignore stray data here
    else:
        if category in (None, "feature"):
            primary = norm(track.get("primary_artist"))
            if primary and primary != norm(DEFAULT_ARTIST):
                category = "collab"
            elif not category:
                category = "feature" if track.get("featured_artists") else "other"

    return Classification(on_album, role, extra_type, category, release_edition, low_confidence, reason)


# ---------------------------------------------------------------------------
# song_family relinking for extras
# ---------------------------------------------------------------------------

STRIP_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"\s*[-–]\s*karaoke version\s*$", r"\s*\[karaoke version\]\s*$",
    r"\s*[-–]\s*voice memo\s*$", r"\s*\[voice memo\]\s*$",
    r"\s*[-–]\s*live.*$", r"\s*\(live.*?\)\s*$", r"\s*\[live\]\s*$",
    r"\s*[-–]\s*commentary\s*$", r"\s*\[commentary\]\s*$", r"\s*[-–]\s*track by track\s*$",
    r"\s*[-–]\s*[\w\s]*remix\s*$", r"\s*\[[\w\s]*remix\]\s*$",
    r"\s*[-–]\s*acoustic.*$", r"\s*\(acoustic.*?\)\s*$",
    r"\s*[-–]\s*instrumental\s*$",
    r"\s*[-–]\s*original demo recording\s*$",
    r"\s*[-–]\s*extended version.*$",
    r"\s*[-–]\s*international mix\s*$",
]]


def strip_extra_suffix(title: str) -> str:
    result = title
    for pattern in STRIP_PATTERNS:
        result = pattern.sub("", result)
    return result.strip()


def build_family_index(all_tracks: list[tuple[str, dict]]) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """Only tracks whose title is UNCHANGED by strip_extra_suffix (i.e. they
    are not themselves a karaoke/live/remix/... variant) count as an "anchor"
    for a base title — that's the one authoritative song_family to relink
    siblings to. This avoids the ambiguity of picking among several *extras*
    that might disagree with each other (the DB itself has pre-existing
    spelling drift, e.g. "forever_always" vs "forever_and_always" for
    "Forever & Always" showing up on different bonus-track entries) — the
    anchor track itself is not in question.
    Returns (per-group index, global fallback index for cross-file extras like
    a karaoke version living in songs.json while its base song lives in the
    album file)."""
    by_group: dict[tuple[str, str], str] = {}
    global_index: dict[str, str] = {}
    for group_rel, track in all_tracks:
        title = track.get("title") or ""
        base = norm(strip_extra_suffix(title))
        family = track.get("song_family")
        if not base or not family:
            continue
        if norm(title) == base:  # title had no extra-suffix to strip -> this IS the base song
            by_group.setdefault((group_rel, base), family)
            global_index.setdefault(base, family)
    return by_group, global_index


def find_base_family(group_rel: str, title: str, indexes: tuple[dict, dict], own_family: str) -> str | None:
    by_group, global_index = indexes
    base = norm(strip_extra_suffix(title))
    candidate = by_group.get((group_rel, base)) or global_index.get(base)
    if candidate and candidate != own_family:
        return candidate
    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write changes for real (default: dry-run)")
    parser.add_argument("--no-backup", action="store_true", help="skip .bak copies when --apply is used")
    args = parser.parse_args()

    paths = discover_files()
    payloads = {p: read_json(p) for p in paths}

    all_tracks: list[tuple[str, dict]] = []
    for path, payload in payloads.items():
        rel = path.relative_to(DISCOGRAPHY_DIR).as_posix()
        for section in sections_of(payload):
            if not isinstance(section, dict):
                continue
            for track in section.get("tracks") or []:
                if isinstance(track, dict):
                    all_tracks.append((rel, track))

    family_index = build_family_index(all_tracks)

    # Pre-pass: classify every track EXCEPT the two low-signal catch-all buckets,
    # so their fallback classifier can check "is this title actually a real,
    # already-confirmed album track re-listed here?" (see classify_streaming_extras_fallback).
    known_album_titles: set[str] = set()
    for path, payload in payloads.items():
        for section in sections_of(payload):
            if not isinstance(section, dict):
                continue
            section_name = section.get("section", "")
            if SECTION_RULES.get(section_name, {}).get("_needs_context"):
                continue
            for track in section.get("tracks") or []:
                if not isinstance(track, dict):
                    continue
                cls = classify_track(track, section_name, "")
                if cls.on_album and cls.role == "album_track":
                    known_album_titles.add(norm(strip_extra_suffix(track.get("title") or "")))

    review_rows = []
    confident = 0
    family_fixes = 0
    counts_by_role = {}

    for path, payload in payloads.items():
        rel = path.relative_to(DISCOGRAPHY_DIR).as_posix()
        for section in sections_of(payload):
            if not isinstance(section, dict):
                continue
            section_name = section.get("section", "")
            for track in section.get("tracks") or []:
                if not isinstance(track, dict):
                    continue

                cls = classify_track(track, section_name, rel, known_album_titles)
                track["on_album"] = cls.on_album
                if cls.role:
                    track["role"] = cls.role
                if cls.extra_type:
                    track["extra_type"] = cls.extra_type
                if cls.category:
                    track["category"] = cls.category
                if cls.release_edition:
                    track["release_edition"] = cls.release_edition
                if "display_era" in track:
                    track["display_album"] = track["display_era"]
                track.setdefault("tags", [t for t in ("christmas",) if t in (track.get("filter_tags") or [])])

                key = cls.role or cls.category or "?"
                counts_by_role[key] = counts_by_role.get(key, 0) + 1

                old_family = track.get("song_family", "")
                new_family = None
                if cls.role == "extra":
                    new_family = find_base_family(rel, track.get("title") or "", family_index, old_family)
                    if new_family:
                        track["song_family"] = new_family
                        family_fixes += 1

                if cls.low_confidence or new_family:
                    review_rows.append({
                        "group": rel, "section": section_name, "title": track.get("title", ""),
                        "url": track.get("url", ""),
                        "reason": cls.reason or ("song_family relié automatiquement, à confirmer" if new_family else ""),
                        "old_type": track.get("type", ""), "old_edition": track.get("edition", ""),
                        "on_album": cls.on_album, "role": cls.role or "", "extra_type": cls.extra_type or "",
                        "category": cls.category or "", "release_edition": cls.release_edition or "",
                        "old_song_family": old_family, "new_song_family": new_family or "",
                    })
                else:
                    confident += 1

    REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_CSV.open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["group", "section", "title", "url", "reason", "old_type", "old_edition",
                      "on_album", "role", "extra_type", "category", "release_edition",
                      "old_song_family", "new_song_family"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_rows)

    total = confident + len(review_rows)
    print(f"total tracks: {total}")
    print(f"classified with high confidence: {confident} ({100 * confident / total:.1f}%)")
    print(f"flagged for manual review: {len(review_rows)} ({100 * len(review_rows) / total:.1f}%) -> {REVIEW_CSV.relative_to(ROOT)}")
    print(f"song_family auto-relinked: {family_fixes}")
    print("breakdown by role/category:", counts_by_role)

    if not args.apply:
        print("\n[dry-run] aucune écriture — relance avec --apply pour écrire les fichiers.")
        return

    for path, payload in payloads.items():
        if not args.no_backup:
            backup(path)
        write_json(path, payload)
    print(f"\n[apply] {len(payloads)} fichiers réécrits avec les nouveaux champs.")


if __name__ == "__main__":
    main()
