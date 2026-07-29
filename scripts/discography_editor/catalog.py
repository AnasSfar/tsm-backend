#!/usr/bin/env python3
"""Load, flatten, edit and safely save db/discography/* for the discography editor GUI.

No web framework dependency here on purpose — this module is plain data logic
so it can be unit-tested without spinning up the HTTP server.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DISCOGRAPHY_DIR = ROOT / "db" / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
FLAT_FILES = ("songs.json", "misc.json", "features.json")

# Pure editor bookkeeping ("j'ai déjà vérifié cette chanson") — deliberately
# NOT under db/discography: it's not music metadata, no collector should ever
# read it, and it must survive independently of DB saves/backups/reloads.
REVIEW_STATE_PATH = Path(__file__).resolve().parent / "review_state.json"

# history_store.py lives in collectors/spotify/streams/tools/scripts/ and itself
# imports `core.data_paths`, so collectors/spotify must be on sys.path too.
SPOTIFY_ROOT = ROOT / "collectors" / "spotify"
SPOTIFY_SCRIPT_DIR = SPOTIFY_ROOT / "streams" / "tools" / "scripts"
for _p in (str(SPOTIFY_ROOT), str(SPOTIFY_SCRIPT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from history_store import get_all_last_history_totals  # noqa: E402

TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")
SPOTIFY_URL_RE = re.compile(r"^https://open\.spotify\.com/(intl-[a-z]{2}/)?track/[A-Za-z0-9]{10,30}(\?.*)?$")

# New schema (scripts/migrate_discography_schema.py) — `type`/`edition`/`filter_tags`
# are superseded by on_album/role/extra_type/category/release_edition/tags. The old
# fields still exist on disk (Phase 2 will retire them from collectors one script at
# a time) but this editor now reads/writes the new ones exclusively.
ROLE_VALUES = ("single", "album_track", "extra")
EXTRA_TYPE_VALUES = ("live", "remix", "commentary", "karaoke", "voice_memo", "acoustic", "demo", "instrumental", "other")
CATEGORY_VALUES = ("soundtrack", "feature", "collab", "other")

# Small, thematic-only vocabulary — everything structural (single/soundtrack/live/
# karaoke/collab/...) moved to the fields above; `tags` only for what's left over.
TAGS_VOCAB = ("christmas",)

EDITABLE_TEXT_FIELDS = (
    "title",
    "url",
    "display_section",
    "primary_artist",
    "song_family",
    "version_tag",
    "role",
    "extra_type",
    "category",
    "release_edition",
    "display_album",
)

FLAT_LABELS = {
    "songs.json": "Standalone & Extras — songs.json",
    "misc.json": "Standalone & Extras — misc.json",
    "features.json": "Standalone & Extras — features.json",
}


class CatalogError(Exception):
    pass


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return path.with_suffix(path.suffix + f".discoedit-{stamp}.bak")


def _as_list(value: Any) -> list:
    """Defensive coercion: some DB entries have e.g. featured_artists as a bare
    string instead of a one-item list (real data bug, not a hypothetical)."""
    if isinstance(value, list):
        return value
    if value:
        return [value]
    return []


def extract_track_id(url: str | None) -> str:
    match = TRACK_ID_RE.search(url or "")
    return match.group(1) if match else ""


def review_key(track_id: str, rel_path: str, section_name: str, title: str) -> str:
    """Stable identity for the 'déjà vérifié' checkbox — track_id when we have
    one (the vast majority of rows); a positional fallback for the handful of
    tracks with no Spotify URL (loses tracking only if that track is later
    renamed AND moved on the same save, an acceptable edge case)."""
    return track_id or f"{rel_path}::{section_name}::{title}"


def load_review_state() -> dict[str, bool]:
    if not REVIEW_STATE_PATH.exists():
        return {}
    try:
        return json.loads(REVIEW_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_review_state(state: dict[str, bool]) -> None:
    REVIEW_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sections_of(payload: Any) -> list[dict]:
    """Return the list of section dicts for either payload shape."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("sections") or []
    return []


def _group_album_name(rel_path: str, payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("album") or rel_path)
    return "Standalone & Extras"


def _group_label(rel_path: str) -> str:
    if rel_path in FLAT_LABELS:
        return FLAT_LABELS[rel_path]
    if isinstance(rel_path, str) and rel_path.startswith("albums/"):
        return rel_path  # replaced with real album name once payload is known
    return rel_path


@dataclass
class RowRef:
    rel_path: str
    section_index: int
    track_index: int


@dataclass
class Catalog:
    payloads: dict[str, Any] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    rows: dict[int, RowRef] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)


def _discover_files() -> list[Path]:
    files = [DISCOGRAPHY_DIR / name for name in FLAT_FILES if (DISCOGRAPHY_DIR / name).exists()]
    files.extend(sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()))
    return files


def _rel(path: Path) -> str:
    return path.relative_to(DISCOGRAPHY_DIR).as_posix()


def load_catalog() -> Catalog:
    catalog = Catalog()
    for path in _discover_files():
        rel = _rel(path)
        catalog.payloads[rel] = read_json(path)
        catalog.order.append(rel)

    row_id = 0
    for rel in catalog.order:
        payload = catalog.payloads[rel]
        for s_idx, section in enumerate(_sections_of(payload)):
            if not isinstance(section, dict):
                continue
            tracks = section.get("tracks") or []
            for t_idx, track in enumerate(tracks):
                if not isinstance(track, dict):
                    continue
                catalog.rows[row_id] = RowRef(rel, s_idx, t_idx)
                row_id += 1

    catalog.totals = get_all_last_history_totals()
    return catalog


def _track_at(catalog: Catalog, ref: RowRef) -> dict:
    payload = catalog.payloads[ref.rel_path]
    section = _sections_of(payload)[ref.section_index]
    return section["tracks"][ref.track_index]


def group_registry(catalog: Catalog) -> list[dict]:
    """One entry per source file: key (rel path), friendly label, sections present."""
    groups = []
    for rel in catalog.order:
        payload = catalog.payloads[rel]
        album_name = _group_album_name(rel, payload)
        label = FLAT_LABELS.get(rel, album_name)
        section_names = sorted(
            {s.get("section") for s in _sections_of(payload) if isinstance(s, dict) and s.get("section")}
        )
        groups.append({"key": rel, "label": label, "album": album_name, "sections": section_names})
    return groups


def _distinct(catalog: Catalog, getter) -> list[str]:
    values = set()
    for ref in catalog.rows.values():
        track = _track_at(catalog, ref)
        val = getter(track)
        if val:
            values.add(val)
    return sorted(values)


def serialize_state(catalog: Catalog) -> dict:
    review_state = load_review_state()
    rows = []
    for row_id, ref in catalog.rows.items():
        track = _track_at(catalog, ref)
        payload = catalog.payloads[ref.rel_path]
        section = _sections_of(payload)[ref.section_index]
        track_id = extract_track_id(track.get("url"))
        rkey = review_key(track_id, ref.rel_path, section.get("section", ""), track.get("title", ""))
        rows.append(
            {
                "id": row_id,
                "group": ref.rel_path,
                "section": section.get("section"),
                "display_order": track.get("display_order", 0) or 0,
                "title": track.get("title", ""),
                "url": track.get("url", ""),
                "track_id": track_id,
                "total_streams": catalog.totals.get(track_id),
                "on_album": bool(track.get("on_album", False)),
                "role": track.get("role") or "",
                "extra_type": track.get("extra_type") or "",
                "category": track.get("category") or "",
                "release_edition": track.get("release_edition") or "",
                "display_album": track.get("display_album") or "",
                "display_section": track.get("display_section", ""),
                "primary_artist": track.get("primary_artist", ""),
                "featured_artists": _as_list(track.get("featured_artists")),
                "song_family": track.get("song_family", ""),
                "version_tag": track.get("version_tag"),
                "chart_extra": bool(track.get("chart_extra", section.get("chart_extra", False))),
                "tags": [t for t in _as_list(track.get("tags")) if t in TAGS_VOCAB],
                "review_key": rkey,
                "done": bool(review_state.get(rkey)),
            }
        )

    options = {
        "groups": group_registry(catalog),
        "tags_vocab": list(TAGS_VOCAB),
        "roles": sorted(set(ROLE_VALUES) | set(_distinct(catalog, lambda t: t.get("role")))),
        "extra_types": sorted(set(EXTRA_TYPE_VALUES) | set(_distinct(catalog, lambda t: t.get("extra_type")))),
        "categories": sorted(set(CATEGORY_VALUES) | set(_distinct(catalog, lambda t: t.get("category")))),
        "release_editions": _distinct(catalog, lambda t: t.get("release_edition")),
        "primary_artists": _distinct(catalog, lambda t: t.get("primary_artist")),
        "version_tags": _distinct(catalog, lambda t: t.get("version_tag")),
        "album_names": sorted({g["album"] for g in group_registry(catalog) if g["album"] != "Standalone & Extras"}),
    }
    return {"rows": rows, "options": options, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def _resolve_target_section(payload: Any, requested_section: str | None, fallback_section_name: str) -> dict:
    sections = _sections_of(payload)
    name = (requested_section or fallback_section_name or "").strip()
    if not name:
        raise CatalogError("Nom de section vide.")
    for section in sections:
        if isinstance(section, dict) and section.get("section") == name:
            return section
    album_name = _group_album_name("", payload) if isinstance(payload, dict) else None
    new_section = {"section": name, "track_count": 0, "chart_extra": False, "tracks": []}
    if isinstance(payload, list):
        new_section["album"] = "Standalone & Extras"
        payload.append(new_section)
    else:
        if album_name:
            new_section["album"] = album_name
        payload.setdefault("sections", []).append(new_section)
    return new_section


def _refresh_counts(payload: Any) -> None:
    sections = _sections_of(payload)
    for section in sections:
        if isinstance(section, dict):
            section["track_count"] = len(section.get("tracks") or [])
    if isinstance(payload, dict):
        payload["section_count"] = len(sections)
        payload["track_count"] = sum(s.get("track_count", 0) for s in sections if isinstance(s, dict))


def _total_track_count(payloads: dict[str, Any]) -> int:
    return sum(len(section.get("tracks") or []) for payload in payloads.values() for section in _sections_of(payload) if isinstance(section, dict))


def _apply_field_changes(track: dict, changes: dict) -> None:
    for key in EDITABLE_TEXT_FIELDS:
        if key in changes:
            value = changes[key]
            if key == "url" and value:
                if not SPOTIFY_URL_RE.match(value.strip()):
                    raise CatalogError(f"URL Spotify invalide : {value!r}")
                track[key] = value.strip()
            elif key == "version_tag":
                track[key] = value.strip() if isinstance(value, str) and value.strip() else None
            else:
                track[key] = value.strip() if isinstance(value, str) else value

    if "chart_extra" in changes:
        track["chart_extra"] = bool(changes["chart_extra"])

    if "on_album" in changes:
        track["on_album"] = bool(changes["on_album"])

    if "tags" in changes:
        tags = changes["tags"] or []
        unknown = sorted(set(tags) - set(TAGS_VOCAB))
        if unknown:
            raise CatalogError(f"tags inconnus : {unknown}")
        track["tags"] = list(tags)

    artists_changed = "featured_artists" in changes or "primary_artist" in changes
    if "featured_artists" in changes:
        featured = [a.strip() for a in (changes["featured_artists"] or []) if a and a.strip()]
        track["featured_artists"] = featured
    if artists_changed:
        primary = track.get("primary_artist") or ""
        featured = _as_list(track.get("featured_artists"))
        merged = [primary] if primary else []
        for a in featured:
            if a not in merged:
                merged.append(a)
        track["artists"] = merged


def save_changes(catalog: Catalog, changes: dict[str, dict]) -> tuple[bool, list[str]]:
    """Apply `changes` ({row_id: {field: value}}) to a deep copy of the catalog's
    payloads, validate everything, and only then back up + atomically write the
    touched files to disk. Returns (ok, errors). On failure nothing is written.
    """
    errors: list[str] = []
    # `ref.section_index`/`ref.track_index` are positions captured at LOAD time.
    # If a batch moves/edits two tracks that started in the same section, popping
    # the first shifts the second's real position — indexing by the stale
    # `ref.track_index` would then silently grab the WRONG track. Passing our own
    # memo to deepcopy lets us map id(original_track) -> copied_track directly,
    # so every track is found by identity regardless of what else in its section
    # has already been removed/reordered during this same save.
    memo: dict[int, Any] = {}
    payloads = copy.deepcopy(catalog.payloads, memo)
    before_count = _total_track_count(payloads)
    touched: set[str] = set()
    deleted_count = 0

    def _find_current_section(payload: Any, track: dict) -> dict | None:
        for s in _sections_of(payload):
            if isinstance(s, dict) and any(t is track for t in (s.get("tracks") or [])):
                return s
        return None

    for row_id_str, field_changes in changes.items():
        try:
            row_id = int(row_id_str)
        except (TypeError, ValueError):
            errors.append(f"row id invalide: {row_id_str!r}")
            continue
        ref = catalog.rows.get(row_id)
        if ref is None:
            errors.append(f"row id inconnu: {row_id}")
            continue

        try:
            original_track = _track_at(catalog, ref)
            track = memo.get(id(original_track))
            if track is None:
                raise CatalogError("piste introuvable après copie interne")

            payload = payloads[ref.rel_path]
            section = _find_current_section(payload, track)
            if section is None:
                raise CatalogError("section introuvable pour cette piste")

            if field_changes.get("_delete"):
                idx = next(i for i, t in enumerate(section["tracks"]) if t is track)
                section["tracks"].pop(idx)
                touched.add(ref.rel_path)
                deleted_count += 1
                continue

            move_group = field_changes.get("group")
            move_section = field_changes.get("section")
            plain_changes = {k: v for k, v in field_changes.items() if k not in ("group", "section")}

            _apply_field_changes(track, plain_changes)
            touched.add(ref.rel_path)

            if move_group and move_group != ref.rel_path:
                if move_group not in payloads:
                    raise CatalogError(f"groupe cible inconnu : {move_group!r}")
                dest_payload = payloads[move_group]
                dest_section = _resolve_target_section(dest_payload, move_section, section.get("section", ""))
                idx = next(i for i, t in enumerate(section["tracks"]) if t is track)
                section["tracks"].pop(idx)
                track["album"] = _group_album_name(move_group, dest_payload)
                dest_section.setdefault("tracks", []).append(track)
                touched.add(move_group)
            elif move_section and move_section != section.get("section"):
                dest_section = _resolve_target_section(payload, move_section, section.get("section", ""))
                if dest_section is not section:
                    idx = next(i for i, t in enumerate(section["tracks"]) if t is track)
                    section["tracks"].pop(idx)
                    dest_section.setdefault("tracks", []).append(track)
        except CatalogError as exc:
            errors.append(f"row {row_id}: {exc}")
        except (KeyError, IndexError) as exc:
            errors.append(f"row {row_id}: position invalide ({exc})")

    if errors:
        return False, errors

    for rel in touched:
        _refresh_counts(payloads[rel])

    after_count = _total_track_count(payloads)
    expected_count = before_count - deleted_count
    if after_count != expected_count:
        return False, [
            f"Garde-fou : {before_count} tracks avant, {deleted_count} suppression(s) demandée(s), "
            f"{after_count} après (attendu {expected_count}). Annulé, rien n'a été écrit."
        ]

    # Re-serialize + re-parse every touched file before writing anything to disk.
    rendered: dict[str, str] = {}
    for rel in touched:
        text = write_json_text(payloads[rel])
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return False, [f"Échec de validation JSON pour {rel}: {exc}"]
        rendered[rel] = text

    for rel, text in rendered.items():
        path = DISCOGRAPHY_DIR / rel
        if path.exists():
            shutil.copy2(path, backup_path(path))
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)

    return True, []
