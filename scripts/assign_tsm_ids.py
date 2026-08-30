#!/usr/bin/env python3
"""assign_tsm_ids.py — assign & keep in sync the stable internal TSM ids.

Two identifiers, opposite roles:

  * ``tsm_song_id`` / ``tsm_album_id`` — opaque short base36, **frozen for life**.
    Stored in ``db/discography/tsm_id_registry.json`` (source of truth) AND
    written back onto every track object in ``db/discography/``. This is the join
    key across collectors and the URL key for share links. A superseded id is
    never deleted — it gets ``merged_into`` set instead.

  * ``slug`` / ``catalog_code`` — readable / meaningful, **regenerated every run**
    from the current title and schema fields. They live only in the registry and
    in ``catalog_index.{json,csv}`` — never in the discography files (they would
    re-churn ~20 files on any title/edition edit).

Run this after ANY change to ``db/discography/`` (new song, editor save, manual
edit) — it is idempotent. Dry-run by default; pass ``--apply`` to write.

    python scripts/assign_tsm_ids.py            # dry-run, show the diff
    python scripts/assign_tsm_ids.py --apply    # write registry + files + index
    python scripts/assign_tsm_ids.py --apply --no-backup
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import shutil
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DISCOGRAPHY_DIR = REPO_ROOT / "db" / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
FLAT_FILES = ("songs.json", "misc.json", "features.json")

REGISTRY_PATH = DISCOGRAPHY_DIR / "tsm_id_registry.json"
INDEX_JSON_PATH = DISCOGRAPHY_DIR / "catalog_index.json"
INDEX_CSV_PATH = DISCOGRAPHY_DIR / "catalog_index.csv"

TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")
ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
SONG_ID_LEN = 4
ALBUM_ID_LEN = 3

TODAY = date.today().isoformat()

# --- catalog_code cosmetics (regenerated every run — never a key) -------------

ERA_ABBR = {
    "Taylor Swift": "DEBUT",
    "Fearless": "FEAR",
    "Fearless (Taylor's Version)": "FEARTV",
    "Speak Now": "SPKNOW",
    "Speak Now (Taylor's Version)": "SPKNOWTV",
    "Red": "RED",
    "Red (Taylor's Version)": "REDTV",
    "1989": "1989",
    "1989 (Taylor's Version)": "1989TV",
    "reputation": "REP",
    "Lover": "LOVER",
    "folklore": "FOLK",
    "evermore": "EVER",
    "Midnights": "MID",
    "THE TORTURED POETS DEPARTMENT": "TTPD",
    "The Tortured Poets Department": "TTPD",
    "The Life of a Showgirl": "SHOWGIRL",
    "The Taylor Swift Holiday Collection": "HOLIDAY",
}

ED_ABBR = {
    "standard": "std",
    "standard edition": "std",
    "original_edition": "std",
    "taylors_version": "std",
    "taylors_version_deluxe": "dlx",
    "deluxe": "dlx",
    "platinum": "plat",
    "anthology": "anth",
    "from_the_vault": "vault",
    "From The Vault": "vault",
    "vault": "vault",
    "3am": "3am",
    "til_dawn": "dawn",
    "the long pond studio sessions": "longpond",
    "acoustic": "acou",
    "live": "live",
    "karaoke": "kar",
    "extended": "ext",
    "extras": "ext",
    "other editions": "other",
}

# display_order sentinels used for extras (999 / 9999 / 10008 …)
POSITION_SENTINEL = 900


# --------------------------------------------------------------------------- #
# small pure helpers (kept local on purpose — importing scripts.discography_   #
# editor.catalog would drag in history_store -> core.data_paths)              #
# --------------------------------------------------------------------------- #
def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return path.with_suffix(path.suffix + f".tsmids-{stamp}.bak")


def extract_track_id(url: str | None) -> str:
    match = TRACK_ID_RE.search(url or "")
    return match.group(1) if match else ""


def strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(ch)
    )


def slugify_dash(value: str) -> str:
    """Readable web slug: ``all-too-well``."""
    value = strip_accents(value).lower().strip().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "unknown"


def clean_identity(value: str) -> str:
    """Loose normalisation for the title-fallback identity key."""
    value = strip_accents(value).lower()
    value = re.sub(r"['`]", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def display_title(track: dict) -> str:
    return str(
        track.get("base_title")
        or track.get("title_clean")
        or track.get("title")
        or ""
    ).strip()


def song_identity_key(track: dict) -> str:
    family = str(track.get("song_family") or "").strip()
    if family:
        return f"family:{family}"
    return f"title:{clean_identity(display_title(track))}"


def _sections_of(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [s for s in payload if isinstance(s, dict)]
    if isinstance(payload, dict):
        return [s for s in (payload.get("sections") or []) if isinstance(s, dict)]
    return []


def gen_id(existing: set[str], length: int) -> str:
    while True:
        candidate = "".join(random.choice(ID_ALPHABET) for _ in range(length))
        if candidate not in existing:
            existing.add(candidate)
            return candidate


# --------------------------------------------------------------------------- #
# catalog loading                                                             #
# --------------------------------------------------------------------------- #
class Entry:
    """One track object in one discography file, with enough context to write back."""

    __slots__ = ("rel_path", "album_name", "from_album_file", "section_name", "track")

    def __init__(self, rel_path, album_name, from_album_file, section_name, track):
        self.rel_path = rel_path
        self.album_name = album_name
        self.from_album_file = from_album_file
        self.section_name = section_name
        self.track = track

    @property
    def track_id(self) -> str:
        return extract_track_id(self.track.get("url") or self.track.get("spotify_url"))

    @property
    def historical_ids(self) -> list[str]:
        raw = self.track.get("historical_track_ids") or []
        return [x for x in raw if isinstance(x, str) and x.strip()]

    @property
    def all_spotify_ids(self) -> set[str]:
        ids = set(self.historical_ids)
        tid = self.track_id
        if tid:
            ids.add(tid)
        return ids


def discover_files() -> list[Path]:
    files = [DISCOGRAPHY_DIR / name for name in FLAT_FILES if (DISCOGRAPHY_DIR / name).exists()]
    files.extend(sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()))
    return files


def load_catalog() -> tuple[dict[str, Any], list[Entry]]:
    """Return ({rel_path: payload}, [Entry, ...]) — payloads are live dicts to mutate."""
    payloads: dict[str, Any] = {}
    entries: list[Entry] = []
    for path in discover_files():
        rel = path.relative_to(DISCOGRAPHY_DIR).as_posix()
        payload = read_json(path)
        payloads[rel] = payload
        from_album_file = rel.startswith("albums/")
        album_name = payload.get("album", "") if isinstance(payload, dict) else ""
        for section in _sections_of(payload):
            section_album = section.get("album") or album_name
            for track in section.get("tracks") or []:
                if not isinstance(track, dict):
                    continue
                entries.append(
                    Entry(
                        rel_path=rel,
                        album_name=str(section_album or ""),
                        from_album_file=from_album_file,
                        section_name=str(section.get("section") or ""),
                        track=track,
                    )
                )
    return payloads, entries


# --------------------------------------------------------------------------- #
# registry                                                                    #
# --------------------------------------------------------------------------- #
def empty_registry() -> dict:
    return {"version": 1, "updated_at": TODAY, "songs": {}, "albums": {}}


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return empty_registry()
    data = read_json(REGISTRY_PATH)
    data.setdefault("version", 1)
    data.setdefault("songs", {})
    data.setdefault("albums", {})
    return data


def _active(section: dict) -> dict[str, dict]:
    return {k: v for k, v in section.items() if not v.get("merged_into")}


# --------------------------------------------------------------------------- #
# catalog_code                                                                #
# --------------------------------------------------------------------------- #
def era_abbr(album_name: str) -> str:
    if album_name in ERA_ABBR:
        return ERA_ABBR[album_name]
    compact = re.sub(r"[^a-z0-9]+", "", strip_accents(album_name).lower())
    return (compact[:6] or "misc").upper()


def edition_abbr(track: dict) -> str:
    raw = str(track.get("release_edition") or track.get("edition") or "").strip()
    if not raw:
        return "std"
    return ED_ABBR.get(raw, ED_ABBR.get(raw.lower(), re.sub(r"[^a-z0-9]+", "", raw.lower())[:6] or "std"))


def catalog_code(entry: Entry) -> str:
    track = entry.track
    if entry.from_album_file:
        era = era_abbr(entry.album_name)
        ed = edition_abbr(track)
        pos = track.get("display_order")
        code = f"{era}/{ed}"
        if isinstance(pos, int) and 0 < pos < POSITION_SENTINEL:
            code += f"/{pos:02d}"
        return code
    # flat files (songs.json / misc.json / features.json)
    on_album = track.get("on_album")
    category = str(track.get("category") or "").strip()
    extra_type = str(track.get("extra_type") or "").strip()
    if on_album is False and category:
        return f"NA/{category}"
    if extra_type:
        display_album = str(track.get("display_album") or "").strip()
        head = ERA_ABBR.get(display_album, "X") if display_album else "X"
        return f"{head}/{extra_type}"
    if category:
        return f"NA/{category}"
    return "NA/other"


# --------------------------------------------------------------------------- #
# main sync                                                                   #
# --------------------------------------------------------------------------- #
def choose_canonical_title(group: list[Entry]) -> str:
    def rank(e: Entry) -> tuple:
        t = e.track
        on_album = 0 if (t.get("on_album") or e.from_album_file) else 1
        is_extra = 1 if str(t.get("role") or "") == "extra" else 0
        order = t.get("display_order")
        order = order if isinstance(order, int) and order > 0 else 9999
        return (is_extra, on_album, order, len(display_title(t)))

    best = sorted(group, key=rank)[0]
    return display_title(best.track) or best.track.get("title") or "Unknown"


def sync_songs(entries: list[Entry], registry: dict, warnings: list[str]) -> dict[str, str]:
    """Return {identity_key: tsm_song_id}. Mutates registry['songs']."""
    songs_reg: dict[str, dict] = registry["songs"]
    used_ids = set(songs_reg.keys())

    family_to_id = {
        rec["song_family"]: sid
        for sid, rec in _active(songs_reg).items()
        if rec.get("song_family")
    }
    spotify_to_id: dict[str, str] = {}
    for sid, rec in _active(songs_reg).items():
        for tid in rec.get("spotify_track_ids") or []:
            spotify_to_id.setdefault(tid, sid)

    groups: dict[str, list[Entry]] = {}
    for e in entries:
        groups.setdefault(song_identity_key(e.track), []).append(e)

    key_to_id: dict[str, str] = {}
    stats = {"new": 0, "renamed": 0, "updated": 0, "unchanged": 0}
    id_claims: dict[str, list[str]] = {}

    for key, group in sorted(groups.items()):
        family = key.split(":", 1)[1] if key.startswith("family:") else ""
        spotify_ids = sorted({sid for e in group for sid in e.all_spotify_ids})
        canonical = choose_canonical_title(group)

        sid = None
        renamed_from = None
        if family and family in family_to_id:
            sid = family_to_id[family]
        else:
            for tid in spotify_ids:
                if tid in spotify_to_id:
                    sid = spotify_to_id[tid]
                    renamed_from = songs_reg[sid].get("song_family")
                    stats["renamed"] += 1
                    break
        if sid is None:
            sid = gen_id(used_ids, SONG_ID_LEN)
            songs_reg[sid] = {
                "song_family": family,
                "canonical_title": canonical,
                "slug": slugify_dash(canonical),
                "spotify_track_ids": spotify_ids,
                "created_at": TODAY,
                "updated_at": TODAY,
                "merged_into": None,
            }
            stats["new"] += 1
        else:
            rec = songs_reg[sid]
            changed = (
                rec.get("song_family") != family
                or rec.get("canonical_title") != canonical
                or (rec.get("spotify_track_ids") or []) != spotify_ids
            )
            rec["song_family"] = family
            rec["canonical_title"] = canonical
            rec["slug"] = slugify_dash(canonical)
            rec["spotify_track_ids"] = spotify_ids
            if changed:
                rec["updated_at"] = TODAY
            if renamed_from is None:
                stats["updated" if changed else "unchanged"] += 1

        if renamed_from:
            warnings.append(
                f"song {sid}: renamed song_family {renamed_from!r} -> {family!r} "
                f"(matched on shared Spotify id)"
            )
        key_to_id[key] = sid
        id_claims.setdefault(sid, []).append(key)

    for sid, keys in id_claims.items():
        if len(keys) > 1:
            warnings.append(
                f"song {sid}: {len(keys)} distinct identities resolve to it "
                f"(possible merge to review): {', '.join(keys)}"
            )

    stale = sorted(
        sid for sid, rec in _active(songs_reg).items()
        if sid not in id_claims
    )
    for sid in stale:
        warnings.append(
            f"song {sid} ({songs_reg[sid].get('song_family')!r}) is in the registry "
            f"but no longer in the catalog — kept, not deleted"
        )

    registry["_song_stats"] = stats
    return key_to_id


def sync_albums(entries: list[Entry], registry: dict, warnings: list[str]) -> dict[str, str]:
    albums_reg: dict[str, dict] = registry["albums"]
    used_ids = set(albums_reg.keys())

    name_to_id = {
        rec["album"]: aid for aid, rec in _active(albums_reg).items() if rec.get("album")
    }
    track_to_id: dict[str, str] = {}
    for aid, rec in _active(albums_reg).items():
        for tid in rec.get("spotify_track_ids") or []:
            track_to_id.setdefault(tid, aid)

    groups: dict[str, list[Entry]] = {}
    for e in entries:
        if e.from_album_file and e.album_name:
            groups.setdefault(e.album_name, []).append(e)

    name_to_tsm: dict[str, str] = {}
    stats = {"new": 0, "renamed": 0, "updated": 0, "unchanged": 0}
    for album_name, group in sorted(groups.items()):
        spotify_ids = sorted({sid for e in group for sid in e.all_spotify_ids})
        aid = None
        renamed = False
        if album_name in name_to_id:
            aid = name_to_id[album_name]
        else:
            for tid in spotify_ids:
                if tid in track_to_id:
                    aid = track_to_id[tid]
                    warnings.append(
                        f"album {aid}: renamed {albums_reg[aid].get('album')!r} -> "
                        f"{album_name!r} (matched on shared Spotify id)"
                    )
                    stats["renamed"] += 1
                    renamed = True
                    break
        if aid is None:
            aid = gen_id(used_ids, ALBUM_ID_LEN)
            albums_reg[aid] = {
                "album": album_name,
                "slug": slugify_dash(album_name),
                "spotify_track_ids": spotify_ids,
                "created_at": TODAY,
                "updated_at": TODAY,
                "merged_into": None,
            }
            stats["new"] += 1
        else:
            rec = albums_reg[aid]
            changed = rec.get("album") != album_name or (rec.get("spotify_track_ids") or []) != spotify_ids
            rec["album"] = album_name
            rec["slug"] = slugify_dash(album_name)
            rec["spotify_track_ids"] = spotify_ids
            if changed:
                rec["updated_at"] = TODAY
            if not renamed:
                stats["updated" if changed else "unchanged"] += 1
        name_to_tsm[album_name] = aid

    registry["_album_stats"] = stats
    return name_to_tsm


def detect_dup_track_ids(entries: list[Entry], warnings: list[str]) -> None:
    seen: dict[str, list[str]] = {}
    for e in entries:
        tid = e.track_id
        if not tid:
            warnings.append(f"track with no Spotify id: {e.track.get('title')!r} in {e.rel_path}")
            continue
        seen.setdefault(tid, []).append(f"{e.track.get('title')!r} ({e.rel_path}/{e.section_name})")
    for tid, where in seen.items():
        if len(where) > 1:
            warnings.append(f"Spotify id {tid} on {len(where)} entries: {' | '.join(where)}")


# --------------------------------------------------------------------------- #
# write-back + index                                                          #
# --------------------------------------------------------------------------- #
def apply_ids_to_tracks(
    entries: list[Entry],
    song_key_to_id: dict[str, str],
    album_name_to_id: dict[str, str],
) -> set[str]:
    """Mutate track dicts in place. Return the set of rel_paths actually changed."""
    touched: set[str] = set()
    for e in entries:
        sid = song_key_to_id.get(song_identity_key(e.track))
        aid = album_name_to_id.get(e.album_name) if e.from_album_file else None
        if e.track.get("tsm_song_id") != sid:
            e.track["tsm_song_id"] = sid
            touched.add(e.rel_path)
        if e.track.get("tsm_album_id") != aid:
            e.track["tsm_album_id"] = aid
            touched.add(e.rel_path)
    return touched


def build_index_rows(
    entries: list[Entry],
    registry: dict,
    album_name_to_id: dict[str, str],
    song_key_to_id: dict[str, str],
) -> list[dict]:
    rows = []
    for e in entries:
        sid = song_key_to_id.get(song_identity_key(e.track), "")
        srec = registry["songs"].get(sid, {})
        aid = album_name_to_id.get(e.album_name, "") if e.from_album_file else ""
        t = e.track
        counts_toward = e.album_name if e.from_album_file else str(t.get("display_album") or "")
        rows.append(
            {
                "tsm_song_id": sid,
                "slug": srec.get("slug", ""),
                "catalog_code": catalog_code(e),
                "tsm_album_id": aid,
                "album": e.album_name,
                "spotify_track_id": e.track_id,
                "historical_track_ids": ";".join(e.historical_ids),
                "title": t.get("title") or "",
                "song_family": t.get("song_family") or "",
                "section": e.section_name,
                "source_file": e.rel_path,
                "edition": t.get("edition") or "",
                "release_edition": t.get("release_edition") or "",
                "display_section": t.get("display_section") or "",
                "display_order": t.get("display_order") if isinstance(t.get("display_order"), int) else "",
                "on_album": t.get("on_album"),
                "role": t.get("role") or "",
                "extra_type": t.get("extra_type") or "",
                "category": t.get("category") or "",
                "display_album": t.get("display_album") or "",
                "chart_extra": bool(t.get("chart_extra")),
                "counts_toward_era": counts_toward,
                "tags": ";".join(t.get("tags") or []),
            }
        )
    rows.sort(key=lambda r: (r["album"].casefold(), str(r["display_order"]).zfill(4), r["title"].casefold()))
    return rows


def clean_registry_for_write(registry: dict, updated_at: str) -> dict:
    return {
        "version": registry.get("version", 1),
        "updated_at": updated_at,
        "songs": dict(sorted(registry["songs"].items())),
        "albums": dict(sorted(registry["albums"].items())),
    }


def _registry_updated_at(new_reg: dict) -> str:
    """Keep the previous updated_at when songs/albums are unchanged on disk."""
    if not REGISTRY_PATH.exists():
        return TODAY
    try:
        old = read_json(REGISTRY_PATH)
    except (json.JSONDecodeError, OSError):
        return TODAY
    if old.get("songs") == new_reg["songs"] and old.get("albums") == new_reg["albums"]:
        return str(old.get("updated_at") or TODAY)
    return TODAY


def write_if_changed(path: Path, text: str, do_backup: bool) -> bool:
    """Write only when the content differs. Returns True if it wrote."""
    if path.exists():
        try:
            if path.read_text(encoding="utf-8-sig") == text:
                return False
        except OSError:
            pass
        if do_backup:
            shutil.copy2(path, backup_path(path))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write changes. Defaults to dry-run.")
    parser.add_argument("--no-backup", action="store_true", help="Skip .bak copies when --apply is used.")
    parser.add_argument("--quiet", action="store_true", help="Reduce logging.")
    args = parser.parse_args()

    random.seed()  # fresh entropy for id minting

    payloads, entries = load_catalog()
    total_before = len(entries)
    registry = load_registry()
    warnings: list[str] = []

    detect_dup_track_ids(entries, warnings)
    song_key_to_id = sync_songs(entries, registry, warnings)
    album_name_to_id = sync_albums(entries, registry, warnings)

    touched = apply_ids_to_tracks(entries, song_key_to_id, album_name_to_id)

    # guardrail: we only add keys, never move/drop tracks
    total_after = sum(len(s.get("tracks") or []) for p in payloads.values() for s in _sections_of(p))
    if total_after != total_before:
        raise SystemExit(
            f"GUARDRAIL: track count changed {total_before} -> {total_after}. Nothing written."
        )

    index_rows = build_index_rows(entries, registry, album_name_to_id, song_key_to_id)
    song_stats = registry.pop("_song_stats", {})
    album_stats = registry.pop("_album_stats", {})

    # ---- report -----------------------------------------------------------
    print(f"catalog: {total_before} track entries across {len(payloads)} files")
    print(
        f"songs : {song_stats.get('new', 0)} new, {song_stats.get('renamed', 0)} renamed, "
        f"{song_stats.get('updated', 0)} updated, {song_stats.get('unchanged', 0)} unchanged  "
        f"(registry now {len(registry['songs'])})"
    )
    print(
        f"albums: {album_stats.get('new', 0)} new, {album_stats.get('renamed', 0)} renamed, "
        f"{album_stats.get('updated', 0)} updated, {album_stats.get('unchanged', 0)} unchanged  "
        f"(registry now {len(registry['albums'])})"
    )
    print(f"files with new/changed tsm ids: {len(touched)}")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    if not args.quiet:
        print("\nsample catalog_index rows:")
        for r in index_rows[:12]:
            print(f"  {r['catalog_code']:<18} {r['tsm_song_id']}  {r['slug']:<32} {r['title']}")

    if not args.apply:
        print("\n[dry-run] nothing written. Re-run with --apply.")
        return

    do_backup = not args.no_backup
    wrote_files = 0
    for rel in sorted(touched):
        path = DISCOGRAPHY_DIR / rel
        text = write_json_text(payloads[rel])
        json.loads(text)  # validate before writing
        if write_if_changed(path, text, do_backup):
            wrote_files += 1

    reg_out = clean_registry_for_write(registry, TODAY)
    reg_out["updated_at"] = _registry_updated_at(reg_out)
    wrote_registry = write_if_changed(REGISTRY_PATH, write_json_text(reg_out), do_backup)
    wrote_index = write_if_changed(INDEX_JSON_PATH, write_json_text(index_rows), do_backup)

    fieldnames = list(index_rows[0].keys()) if index_rows else []
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(index_rows)
    write_if_changed(INDEX_CSV_PATH, buf.getvalue(), do_backup)

    print(
        f"\n[apply] {wrote_files} discography file(s) rewritten, "
        f"registry {'updated' if wrote_registry else 'unchanged'}, "
        f"catalog_index {'updated' if wrote_index else 'unchanged'}"
    )


if __name__ == "__main__":
    main()
