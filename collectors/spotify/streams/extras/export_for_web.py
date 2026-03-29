from __future__ import annotations

import csv
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path


_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parents[3]
ROOT        = _REPO_ROOT / "website"
_DB_ROOT    = _REPO_ROOT / "db"

HISTORY_CSV_PATH = _DB_ROOT / "streams_history.csv"

DISCOGRAPHY_DIR  = _DB_ROOT / "discography"
ALBUMS_DIR_SRC   = DISCOGRAPHY_DIR / "albums"
MISC_JSON_SRC    = DISCOGRAPHY_DIR / "songs.json"
COVERS_JSON_PATH = DISCOGRAPHY_DIR / "covers.json"

SITE_DATA_DIR    = ROOT / "site" / "data"
SITE_HISTORY_DIR = ROOT / "site" / "history"
SONGS_JSON_PATH  = SITE_DATA_DIR / "songs.json"
ALBUMS_JSON_PATH = SITE_DATA_DIR / "albums.json"

LAST_RUN_STATE_SRC   = ROOT / "data" / "last_run_state.json"
NOT_FOUND_STREAK_SRC = ROOT / "data" / "not_found_streak.json"
BILLBOARD_CSV_PATH   = _DB_ROOT / "billboard_history.csv"
BILLBOARD_JSON_PATH  = SITE_DATA_DIR / "billboard.json"

TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")

MILESTONES = [
    100_000_000,
    200_000_000,
    300_000_000,
    400_000_000,
    500_000_000,
    600_000_000,
    700_000_000,
    800_000_000,
    900_000_000,
    1_000_000_000,
    1_100_000_000,
    1_200_000_000,
    1_300_000_000,
    1_400_000_000,
    1_500_000_000,
    1_600_000_000,
    1_700_000_000,
    1_800_000_000,
    1_900_000_000,
    2_000_000_000,
    2_100_000_000,
    2_200_000_000,
    2_300_000_000,
    2_400_000_000,
    2_500_000_000,
    2_600_000_000,
    2_700_000_000,
    2_800_000_000,
    2_900_000_000,
    3_000_000_000,
    3_100_000_000,
    3_200_000_000,
    3_300_000_000,
    3_400_000_000,
    3_500_000_000,
]


def load_album_covers() -> dict:
    if not COVERS_JSON_PATH.exists():
        return {}
    with COVERS_JSON_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def extract_track_id(url: str | None) -> str | None:
    if not url:
        return None
    match = TRACK_ID_RE.search(url)
    return match.group(1) if match else None


def normalize_title_for_site(title: str) -> str:
    return (title or "").strip().casefold()


def format_milestone_label(value: int | None) -> str | None:
    if value is None:
        return None

    if value >= 1_000_000_000:
        b = value / 1_000_000_000
        return f"{int(b)}B" if b.is_integer() else f"{b:.1f}B"

    m = value / 1_000_000
    return f"{int(m)}M" if m.is_integer() else f"{m:.1f}M"


def current_milestone(streams: int | None) -> int | None:
    if streams is None:
        return None

    current = None
    for milestone in MILESTONES:
        if streams >= milestone:
            current = milestone
        else:
            break
    return current


def next_milestone(streams: int | None) -> int | None:
    if streams is None:
        return None

    for milestone in MILESTONES:
        if streams < milestone:
            return milestone

    x = MILESTONES[-1]
    while streams >= x:
        x += 100_000_000
    return x


def write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def load_album_sections_flat() -> list[dict]:
    """Load db/discography/albums/*.json and flatten to section entries."""
    if not ALBUMS_DIR_SRC.exists():
        return []

    sections: list[dict] = []
    for album_file in sorted(ALBUMS_DIR_SRC.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            payload = json.loads(album_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        album_name = payload.get("album", "") if isinstance(payload, dict) else ""
        raw_sections = payload.get("sections", []) if isinstance(payload, dict) else []
        if not isinstance(raw_sections, list):
            continue

        for section in raw_sections:
            if not isinstance(section, dict):
                continue
            item = dict(section)
            if not item.get("album"):
                item["album"] = album_name
            sections.append(item)

    return sections


def load_tracks_from_discography() -> list[dict]:
    seen: dict[str, dict] = {}

    all_sections = load_album_sections_flat()
    if MISC_JSON_SRC.exists():
        try:
            all_sections.extend(json.loads(MISC_JSON_SRC.read_text(encoding="utf-8")))
        except Exception:
            pass

    for section in all_sections:
        for track in section.get("tracks", []):
            url = (track.get("url") or track.get("spotify_url") or "").strip()
            track_id = extract_track_id(url)
            if not track_id or track_id in seen:
                continue
            title = (track.get("title") or "").strip()
            if not title:
                continue
            spotify_url = f"https://open.spotify.com/track/{track_id}"
            image_url = track.get("image_url") or None
            artists = track.get("artists") or []
            primary_artist = track.get("primary_artist") or (artists[0] if artists else None)

            seen[track_id] = {
                "track_id": track_id,
                "title": title,
                "title_key": normalize_title_for_site(title),
                "spotify_url": spotify_url,
                "image_url": image_url,
                "streams": None,
                "daily_streams": None,
                "last_updated": None,
                "primary_artist": primary_artist,
                "artists": artists,
                "appearances": [],
            }

    return list(seen.values())


def load_raw_history() -> tuple[list[str], dict[str, dict[str, dict]]]:
    if not HISTORY_CSV_PATH.exists():
        return [], {}

    by_date: dict[str, dict[str, dict]] = defaultdict(dict)

    with HISTORY_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            date_value = (row.get("date") or "").strip()
            track_id = (row.get("track_id") or "").strip()

            if not date_value or not track_id:
                continue

            streams_raw = (row.get("streams") or "").strip()
            daily_raw = (row.get("daily_streams") or "").strip()

            try:
                streams = int(streams_raw) if streams_raw else None
            except ValueError:
                streams = None

            try:
                daily_streams = int(daily_raw) if daily_raw else None
            except ValueError:
                daily_streams = None

            by_date[date_value][track_id] = {
                "streams": streams,
                "daily_streams": daily_streams,
            }

    return sorted(by_date.keys()), dict(by_date)


def history_count_by_track(raw_history_by_date: dict[str, dict[str, dict]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)

    for _, day_data in raw_history_by_date.items():
        for track_id in day_data.keys():
            counts[track_id] += 1

    return dict(counts)


def choose_best_song(group: list[dict], counts: dict[str, int]) -> dict:
    def score(song: dict):
        main_count = sum(1 for app in song.get("appearances", []) if app.get("source_type") == "album")
        misc_count = sum(1 for app in song.get("appearances", []) if app.get("source_type") == "misc")
        return (
            main_count,
            counts.get(song["track_id"], 0),
            song["streams"] or 0,
            song["daily_streams"] or 0,
            1 if song.get("image_url") else 0,
            misc_count,
            song["track_id"],
        )

    return max(group, key=score)


def dedupe_songs_for_site(
    songs: list[dict],
    raw_history_by_date: dict[str, dict[str, dict]],
) -> tuple[list[dict], dict[str, str]]:
    counts = history_count_by_track(raw_history_by_date)

    groups: dict[str, list[dict]] = defaultdict(list)
    for song in songs:
        groups[song["title_key"]].append(song)

    deduped = []
    old_to_kept: dict[str, str] = {}

    for _, group in groups.items():
        if len(group) == 1:
            kept = dict(group[0])
            kept["merged_track_ids"] = [kept["track_id"]]
            deduped.append(kept)
            old_to_kept[kept["track_id"]] = kept["track_id"]
            continue

        kept = dict(choose_best_song(group, counts))
        merged_track_ids = [song["track_id"] for song in group]

        merged_appearances = []
        seen = set()
        for song in group:
            for app in song.get("appearances", []):
                key = (
                    app.get("source_type"),
                    app.get("album"),
                    app.get("section"),
                    app.get("group"),
                    app.get("edition"),
                    app.get("display_section"),
                    app.get("type"),
                )
                if key not in seen:
                    seen.add(key)
                    merged_appearances.append(app)

        kept["appearances"] = merged_appearances
        kept["merged_track_ids"] = merged_track_ids

        album_apps = [a for a in merged_appearances if a.get("source_type") == "album"]
        primary = album_apps[0] if album_apps else (merged_appearances[0] if merged_appearances else None)

        kept["primary_album"] = primary.get("album") if primary else None
        kept["primary_section"] = primary.get("section") if primary else None
        kept["type"] = primary.get("type") if primary else kept.get("type")
        kept["edition"] = primary.get("edition") if primary else kept.get("edition")
        kept["display_section"] = primary.get("display_section") if primary else kept.get("display_section")
        kept["display_order"] = primary.get("display_order") if primary else kept.get("display_order")
        kept["base_title"] = primary.get("base_title") if primary else kept.get("base_title")

        deduped.append(kept)

        for song in group:
            old_to_kept[song["track_id"]] = kept["track_id"]

    deduped.sort(key=lambda s: (s["title"].casefold(), s["track_id"]))
    return deduped, old_to_kept


def merge_history_by_kept_track(
    dates: list[str],
    raw_history_by_date: dict[str, dict[str, dict]],
    old_to_kept: dict[str, str],
) -> dict[str, dict[str, dict]]:
    merged: dict[str, dict[str, dict]] = {}

    for date_value in dates:
        merged[date_value] = {}
        buckets: dict[str, list[dict]] = defaultdict(list)

        for old_track_id, values in raw_history_by_date.get(date_value, {}).items():
            kept_track_id = old_to_kept.get(old_track_id, old_track_id)
            buckets[kept_track_id].append(values)

        for kept_track_id, entries in buckets.items():
            best = max(
                entries,
                key=lambda v: (
                    v.get("streams") is not None,
                    v.get("streams") or 0,
                    v.get("daily_streams") is not None,
                    v.get("daily_streams") or 0,
                ),
            )
            merged[date_value][kept_track_id] = {
                "streams": best.get("streams"),
                "daily_streams": best.get("daily_streams"),
            }

    return merged


def enrich_history_with_milestones(
    dates: list[str],
    by_date: dict[str, dict[str, dict]],
) -> dict[str, dict[str, dict]]:
    previous_streams_by_track: dict[str, int | None] = {}
    enriched: dict[str, dict[str, dict]] = {}

    for date_value in dates:
        enriched[date_value] = {}
        day_data = by_date.get(date_value, {})

        for track_id, values in day_data.items():
            streams = values.get("streams")
            daily_streams = values.get("daily_streams")
            prev_streams = previous_streams_by_track.get(track_id)

            curr_ms = current_milestone(streams)
            nxt_ms = next_milestone(streams)
            remaining = None if streams is None or nxt_ms is None else max(nxt_ms - streams, 0)

            crossed = None
            if streams is not None:
                prev_ms = current_milestone(prev_streams) if prev_streams is not None else None
                if curr_ms is not None:
                    if prev_ms is None and streams >= curr_ms:
                        crossed = curr_ms
                    elif prev_ms is not None and curr_ms > prev_ms:
                        crossed = curr_ms

            enriched[date_value][track_id] = {
                "streams": streams,
                "daily_streams": daily_streams,
                "current_milestone": curr_ms,
                "current_milestone_label": format_milestone_label(curr_ms),
                "next_milestone": nxt_ms,
                "next_milestone_label": format_milestone_label(nxt_ms),
                "remaining_to_next_milestone": remaining,
                "crossed_milestone_today": crossed,
                "crossed_milestone_today_label": format_milestone_label(crossed),
            }

            previous_streams_by_track[track_id] = streams

    return enriched


def add_ranks(songs: list[dict]) -> list[dict]:
    songs_copy = [dict(song) for song in songs]

    total_sorted = sorted(
        songs_copy,
        key=lambda s: (s.get("streams") is not None, s.get("streams") or 0, s["title"].casefold()),
        reverse=True,
    )
    daily_sorted = sorted(
        songs_copy,
        key=lambda s: (s.get("daily_streams") is not None, s.get("daily_streams") or 0, s["title"].casefold()),
        reverse=True,
    )

    rank_total = {song["track_id"]: i for i, song in enumerate(total_sorted, 1)}
    rank_daily = {song["track_id"]: i for i, song in enumerate(daily_sorted, 1)}

    for song in songs_copy:
        song["rank_total"] = rank_total.get(song["track_id"])
        song["rank_daily"] = rank_daily.get(song["track_id"])

    return songs_copy


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_discography_index() -> tuple[dict, list[dict]]:
    track_appearances_by_id: dict[str, list[dict]] = defaultdict(list)
    albums_payload: list[dict] = []
    album_map: dict[str, dict] = {}

    # ── Albums ────────────────────────────────────────────────────────────────
    raw_editions: list[dict] = load_album_sections_flat()
    if raw_editions:

        # Group sections by album name while preserving insertion order
        albums_order: list[str] = []
        albums_data: dict[str, list[dict]] = {}
        for data in raw_editions:
            aname = data.get("album", "")
            if aname not in albums_data:
                albums_order.append(aname)
                albums_data[aname] = []
            albums_data[aname].append(data)

        _SECTION_PRIORITY: dict[str, int] = {
            "standard_edition": 0, "standard_edition_tv": 0, "original_edition": 0,
            "deluxe_edition": 10, "platinum_edition": 10,
            "3am_edition": 20, "til_dawn_edition": 20, "anthology_edition": 20,
            "from_the_vault": 30, "from_the_vault_tv": 30,
            "long_pond_studio_sessions": 40, "acoustic_edition": 40,
        }

        for album_name in sorted(albums_order, key=str.casefold):
            album_track_ids_ordered: list[str] = []
            album_sections: list[dict] = []

            for data in sorted(albums_data[album_name],
                               key=lambda d: (_SECTION_PRIORITY.get(d.get("section", ""), 50),
                                              d.get("section", "").casefold())):
                section_name = data.get("section") or ""
                file_name    = section_name + ".json"
                source_path  = f"discography/albums/{album_name}/{file_name}"

                file_tracks: list[dict] = []
                for track in data.get("tracks", []):
                    track_id = extract_track_id(
                        track.get("url") or track.get("spotify_url")
                    )
                    if not track_id:
                        continue

                    track_type    = track.get("type")
                    edition       = track.get("edition")
                    display_section = track.get("display_section")
                    display_order = track.get("display_order")
                    base_title    = track.get("base_title")

                    file_tracks.append({
                        "track_id":       track_id,
                        "title":          track.get("title"),
                        "type":           track_type,
                        "edition":        edition,
                        "display_section": display_section,
                        "display_order":  display_order,
                        "base_title":     base_title,
                        "section":        section_name,
                        "source_file":    file_name,
                    })
                    album_track_ids_ordered.append(track_id)

                    track_appearances_by_id[track_id].append({
                        "source_type":    "album",
                        "album":          album_name,
                        "section":        section_name,
                        "group":          None,
                        "source_path":    source_path,
                        "type":           track_type,
                        "edition":        edition,
                        "display_section": display_section,
                        "display_order":  display_order,
                        "base_title":     base_title,
                    })

                album_sections.append({
                    "name":        section_name,
                    "file":        file_name,
                    "tracks":      file_tracks,
                    "track_ids":   [t["track_id"] for t in file_tracks],
                    "track_count": len(file_tracks),
                })

            unique_ids = list(dict.fromkeys(album_track_ids_ordered))
            album_payload = {
                "album":       album_name,
                "kind":        "album",
                "sections":    album_sections,
                "track_ids":   unique_ids,
                "track_count": len(unique_ids),
            }
            albums_payload.append(album_payload)
            album_map[album_name] = album_payload

    # ── Misc ──────────────────────────────────────────────────────────────────
    misc_groups: list[dict] = []
    misc_all_track_ids: list[str] = []

    if MISC_JSON_SRC.exists():
        raw_misc: list[dict] = json.loads(
            MISC_JSON_SRC.read_text(encoding="utf-8")
        )

        groups_order: list[str] = []
        groups_data: dict[str, list[dict]] = {}
        for data in raw_misc:
            gname = data.get("album", "")
            if gname not in groups_data:
                groups_order.append(gname)
                groups_data[gname] = []
            groups_data[gname].append(data)

        for group_name in sorted(groups_order, key=str.casefold):
            group_sections: list[dict] = []
            group_track_ids: list[str] = []

            for data in sorted(groups_data[group_name],
                               key=lambda d: d.get("section", "").casefold()):
                section_name = data.get("section") or ""
                file_name    = section_name + ".json"
                source_path  = f"discography/misc/{group_name}/{file_name}"

                section_tracks: list[dict] = []
                for track in data.get("tracks", []):
                    track_id = extract_track_id(
                        track.get("url") or track.get("spotify_url")
                    )
                    if not track_id:
                        continue

                    track_type    = track.get("type")
                    edition       = track.get("edition")
                    display_section = track.get("display_section")
                    display_order = track.get("display_order")
                    base_title    = track.get("base_title")

                    track_entry = {
                        "track_id":       track_id,
                        "title":          track.get("title"),
                        "type":           track_type,
                        "edition":        edition,
                        "display_section": display_section,
                        "display_order":  display_order,
                        "base_title":     base_title,
                        "section":        section_name,
                        "source_file":    file_name,
                    }

                    section_tracks.append(track_entry)
                    group_track_ids.append(track_id)
                    misc_all_track_ids.append(track_id)

                    track_appearances_by_id[track_id].append({
                        "source_type":    "misc",
                        "album":          "Misc",
                        "section":        section_name,
                        "group":          group_name,
                        "source_path":    source_path,
                        "type":           track_type,
                        "edition":        edition,
                        "display_section": display_section,
                        "display_order":  display_order,
                        "base_title":     base_title,
                    })

                    if group_name in album_map:
                        alb_sections = album_map[group_name]["sections"]
                        existing_section = next(
                            (s for s in alb_sections
                             if s.get("name") == section_name
                             and s.get("file") == file_name),
                            None,
                        )
                        if existing_section is None:
                            existing_section = {
                                "name":        section_name,
                                "file":        file_name,
                                "tracks":      [],
                                "track_ids":   [],
                                "track_count": 0,
                            }
                            alb_sections.append(existing_section)

                        if track_id not in existing_section["track_ids"]:
                            existing_section["tracks"].append(track_entry)
                            existing_section["track_ids"].append(track_id)
                            existing_section["track_count"] = len(
                                existing_section["track_ids"]
                            )

                        album_map[group_name]["track_ids"] = list(
                            dict.fromkeys(
                                album_map[group_name]["track_ids"] + [track_id]
                            )
                        )
                        album_map[group_name]["track_count"] = len(
                            album_map[group_name]["track_ids"]
                        )

                        track_appearances_by_id[track_id].append({
                            "source_type":    "album",
                            "album":          group_name,
                            "section":        section_name,
                            "group":          "misc",
                            "source_path":    source_path,
                            "type":           track_type,
                            "edition":        edition,
                            "display_section": display_section,
                            "display_order":  display_order,
                            "base_title":     base_title,
                        })

                group_sections.append({
                    "name":        section_name,
                    "file":        file_name,
                    "tracks":      section_tracks,
                    "track_ids":   [t["track_id"] for t in section_tracks],
                    "track_count": len(section_tracks),
                })

            misc_groups.append({
                "name":        group_name,
                "sections":    group_sections,
                "track_ids":   list(dict.fromkeys(group_track_ids)),
                "track_count": len(list(dict.fromkeys(group_track_ids))),
            })

    if misc_groups:
        albums_payload.append({
            "album":       "Misc",
            "kind":        "misc",
            "groups":      misc_groups,
            "track_ids":   list(dict.fromkeys(misc_all_track_ids)),
            "track_count": len(list(dict.fromkeys(misc_all_track_ids))),
        })

    return dict(track_appearances_by_id), albums_payload


def group_album_tracks_for_display(album: dict, songs_by_id: dict[str, dict]) -> dict:
    if album.get("kind") != "album":
        return album

    all_entries = []

    for section in album.get("sections", []):
        for track in section.get("tracks", []):
            kept_id = track["track_id"]
            if kept_id not in songs_by_id:
                continue

            block_name = track.get("display_section") or track.get("edition") or "Other Editions"

            all_entries.append(
                {
                    "track_id": kept_id,
                    "title": songs_by_id[kept_id]["title"],
                    "display_section": block_name,
                    "display_order": track.get("display_order") if track.get("display_order") is not None else 999999,
                }
            )

    grouped = {}
    seen = set()

    for entry in sorted(
        all_entries,
        key=lambda x: (
            x["display_order"],
            (x["title"] or "").casefold(),
            x["track_id"],
        ),
    ):
        track_id = entry["track_id"]
        if track_id in seen:
            continue
        seen.add(track_id)

        block_name = entry["display_section"]
        grouped.setdefault(block_name, []).append(track_id)

    album["display_blocks"] = [
        {
            "key": name,
            "name": name,
            "track_ids": track_ids,
            "track_count": len(track_ids),
        }
        for name, track_ids in grouped.items()
    ]

    return album


def enrich_albums_payload(albums_payload: list[dict], songs_by_id: dict[str, dict]) -> list[dict]:
    out = []
    album_covers = load_album_covers()

    for album in albums_payload:
        track_ids = album.get("track_ids", [])
        tracks = [songs_by_id[tid] for tid in track_ids if tid in songs_by_id]

        album_name = album.get("album")
        cover_entry = album_covers.get(album_name, {})
        cover_url = cover_entry.get("cover_url")

        if not cover_url:
            cover_url = next((t.get("image_url") for t in tracks if t.get("image_url")), None)

        total_streams_sum = sum((t.get("streams") or 0) for t in tracks)
        daily_streams_sum = sum((t.get("daily_streams") or 0) for t in tracks)

        top_song_total = max(tracks, key=lambda t: t.get("streams") or 0)["track_id"] if tracks else None
        top_song_daily = max(tracks, key=lambda t: t.get("daily_streams") or 0)["track_id"] if tracks else None

        enriched = dict(album)
        enriched["image_url"] = cover_url
        enriched["total_streams_sum"] = total_streams_sum
        enriched["daily_streams_sum"] = daily_streams_sum
        enriched["top_song_total"] = top_song_total
        enriched["top_song_daily"] = top_song_daily

        if album.get("album") == "Misc":
            for group in enriched.get("groups", []):
                group_tracks = [songs_by_id[tid] for tid in group.get("track_ids", []) if tid in songs_by_id]
                group["image_url"] = next((t.get("image_url") for t in group_tracks if t.get("image_url")), None)
                group["total_streams_sum"] = sum((t.get("streams") or 0) for t in group_tracks)
                group["daily_streams_sum"] = sum((t.get("daily_streams") or 0) for t in group_tracks)
        else:
            enriched = group_album_tracks_for_display(enriched, songs_by_id)

        out.append(enriched)

    return out


def build_summary(
    songs: list[dict],
    albums: list[dict],
    dates: list[str],
    history_by_date: dict[str, dict[str, dict]],
) -> dict:
    latest_date = dates[-1] if dates else None
    latest_day = history_by_date.get(latest_date, {}) if latest_date else {}

    return {
        "total_songs": len(songs),
        "total_albums": len(albums),
        "songs_with_images": sum(1 for s in songs if s.get("image_url")),
        "songs_with_streams": sum(1 for s in songs if s.get("streams") is not None),
        "songs_with_daily_streams": sum(1 for s in songs if s.get("daily_streams") is not None),
        "history_dates_count": len(dates),
        "latest_date": latest_date,
        "songs_updated_on_latest_date": len(latest_day),
        "total_combined_streams": sum((s.get("streams") or 0) for s in songs),
        "milestones_crossed_on_latest_date": 0,
    }


def _load_billboard_from_csv() -> dict | None:
    """Read the latest date from billboard_history.csv and return a billboard.json structure."""
    if not BILLBOARD_CSV_PATH.exists():
        return None

    with open(BILLBOARD_CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return None

    latest_date = max(r["date"] for r in rows if r.get("date"))
    today = [r for r in rows if r["date"] == latest_date]

    def _int(v):
        try:
            return int(v) if v not in (None, "", "None") else None
        except (ValueError, TypeError):
            return None

    scraped_at = next((r["scraped_at"] for r in today if r.get("scraped_at")), latest_date)
    result = {
        "scraped_at": scraped_at,
        "hot_100": [],
        "billboard_200": [],
        "ts_chart_history": [],
        "greatest_artists": None,
    }

    for r in today:
        ct = r["chart_type"]
        if ct in ("hot_100", "billboard_200"):
            result[ct].append({
                "rank": _int(r["rank"]), "title": r["title"],
                "artist": r["artist"], "weeks_on_chart": _int(r["weeks_on_chart"]),
                "peak_rank": _int(r["peak_rank"]),
            })
        elif ct == "ts_chart_history":
            result["ts_chart_history"].append({
                "rank": _int(r["rank"]), "title": r["title"],
                "chart": r.get("chart_label", ""),
                "weeks_on_chart": _int(r["weeks_on_chart"]),
                "peak_rank": _int(r["peak_rank"]),
            })
        elif ct == "greatest_artists":
            result["greatest_artists"] = {"rank": _int(r["rank"]), "name": r["title"]}

    return result


def export_billboard_from_csv() -> None:
    data = _load_billboard_from_csv()
    if not data:
        return
    BILLBOARD_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    BILLBOARD_JSON_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total = sum(len(data[k]) for k in ("hot_100", "billboard_200", "ts_chart_history"))
    print(f"  Billboard JSON written ({total} entries) -> {BILLBOARD_JSON_PATH}")


def export_for_web() -> None:
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"ROOT     = {ROOT}")
    print(f"HISTORY  = {HISTORY_CSV_PATH}")
    raw_songs = load_tracks_from_discography()
    dates, raw_history_by_date = load_raw_history()
    print(f"Last 10 dates found: {dates[-10:]}")
    track_appearances_by_id, albums_payload_raw = build_discography_index()

    for song in raw_songs:
        song["appearances"] = track_appearances_by_id.get(song["track_id"], [])

        album_apps = [a for a in song["appearances"] if a.get("source_type") == "album"]
        primary = album_apps[0] if album_apps else (song["appearances"][0] if song["appearances"] else None)

        song["primary_album"] = primary.get("album") if primary else None
        song["primary_section"] = primary.get("section") if primary else None
        song["type"] = primary.get("type") if primary else None
        song["edition"] = primary.get("edition") if primary else None
        song["display_section"] = primary.get("display_section") if primary else None
        song["display_order"] = primary.get("display_order") if primary else None
        song["base_title"] = primary.get("base_title") if primary else None

    deduped_songs, old_to_kept = dedupe_songs_for_site(raw_songs, raw_history_by_date)
    merged_history = merge_history_by_kept_track(dates, raw_history_by_date, old_to_kept)

    latest_date = dates[-1] if dates else None
    latest_values = merged_history.get(latest_date, {})

    for song in deduped_songs:
        day = latest_values.get(song["track_id"])
        if day:
            song["streams"] = day.get("streams")
            song["daily_streams"] = day.get("daily_streams")

    deduped_songs = add_ranks(deduped_songs)
    songs_by_id = {song["track_id"]: song for song in deduped_songs}

    albums_payload_filtered = []
    for album in albums_payload_raw:
        filtered = dict(album)

        filtered["track_ids"] = list(
            dict.fromkeys(
                old_to_kept.get(tid, tid)
                for tid in album.get("track_ids", [])
                if old_to_kept.get(tid, tid) in songs_by_id
            )
        )
        filtered["track_count"] = len(filtered["track_ids"])

        if filtered.get("album") == "Misc":
            new_groups = []
            for group in filtered.get("groups", []):
                new_group = dict(group)
                new_group["track_ids"] = list(
                    dict.fromkeys(
                        old_to_kept.get(tid, tid)
                        for tid in group.get("track_ids", [])
                        if old_to_kept.get(tid, tid) in songs_by_id
                    )
                )
                new_group["track_count"] = len(new_group["track_ids"])

                new_sections = []
                for section in group.get("sections", []):
                    new_section = dict(section)
                    section_tracks = []

                    seen_ids = set()
                    for track in section.get("tracks", []):
                        kept_id = old_to_kept.get(track["track_id"], track["track_id"])
                        if kept_id not in songs_by_id or kept_id in seen_ids:
                            continue
                        seen_ids.add(kept_id)

                        new_track = dict(track)
                        new_track["track_id"] = kept_id
                        section_tracks.append(new_track)

                    new_section["tracks"] = section_tracks
                    new_section["track_ids"] = [t["track_id"] for t in section_tracks]
                    new_section["track_count"] = len(section_tracks)
                    new_sections.append(new_section)

                new_group["sections"] = new_sections
                new_groups.append(new_group)

            filtered["groups"] = new_groups

        else:
            new_sections = []
            for section in filtered.get("sections", []):
                new_section = dict(section)
                section_tracks = []

                seen_ids = set()
                for track in section.get("tracks", []):
                    kept_id = old_to_kept.get(track["track_id"], track["track_id"])
                    if kept_id not in songs_by_id or kept_id in seen_ids:
                        continue
                    seen_ids.add(kept_id)

                    new_track = dict(track)
                    new_track["track_id"] = kept_id
                    section_tracks.append(new_track)

                new_section["tracks"] = section_tracks
                new_section["track_ids"] = [t["track_id"] for t in section_tracks]
                new_section["track_count"] = len(section_tracks)
                new_sections.append(new_section)

            filtered["sections"] = new_sections

        albums_payload_filtered.append(filtered)

    albums_payload = enrich_albums_payload(albums_payload_filtered, songs_by_id)
    summary = build_summary(deduped_songs, albums_payload, dates, merged_history)

    # Split heavy fields out of songs.json into a separate lazy-loaded file
    _DEFERRED_FIELDS = {"appearances", "artists_json", "merged_track_ids"}
    appearances_map = {
        song["track_id"]: song.get("appearances", [])
        for song in deduped_songs
    }
    songs_stripped = [
        {k: v for k, v in song.items() if k not in _DEFERRED_FIELDS}
        for song in deduped_songs
    ]

    songs_payload = {
        "summary": {**summary, "dates": dates},
        "songs": songs_stripped,
    }

    appearances_payload_out = {"appearances": appearances_map}
    write_json(SITE_DATA_DIR / "songs-appearances.json", appearances_payload_out)

    albums_payload_out = {
        "summary": {
            "total_albums": len(albums_payload),
            "latest_date": latest_date,
        },
        "albums": albums_payload,
    }

    write_json(SONGS_JSON_PATH, songs_payload)
    write_json(ALBUMS_JSON_PATH, albums_payload_out)

    for src, dst in [
        (LAST_RUN_STATE_SRC,          SITE_DATA_DIR / "last_run_state.json"),
        (NOT_FOUND_STREAK_SRC,        SITE_DATA_DIR / "not_found_streak.json"),
        (DISCOGRAPHY_DIR / "artist.json", SITE_DATA_DIR / "artist.json"),
    ]:
        if src.exists():
            shutil.copy2(src, dst)

    SITE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for date_str, day_data in merged_history.items():
        compact = {
            tid: {"s": v["streams"], "d": v["daily_streams"]}
            for tid, v in day_data.items()
        }
        (SITE_HISTORY_DIR / f"{date_str}.json").write_text(
            json.dumps(compact, ensure_ascii=False), encoding="utf-8"
        )
    existing_dates = sorted(p.stem for p in SITE_HISTORY_DIR.glob("*.json") if p.stem != "index")
    (SITE_HISTORY_DIR / "index.json").write_text(
        json.dumps({"dates": existing_dates}, ensure_ascii=False), encoding="utf-8"
    )

    export_billboard_from_csv()

    # copy album header images to website
    headers_src = DISCOGRAPHY_DIR / "headers"
    headers_dst = SITE_DATA_DIR / "headers"
    if headers_src.exists():
        headers_dst.mkdir(parents=True, exist_ok=True)
        for f in headers_src.iterdir():
            if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                shutil.copy2(f, headers_dst / f.name)
        print(f"Exported headers: {headers_dst}")

    print(f"Exported songs:   {SONGS_JSON_PATH}")
    print(f"Exported albums:  {ALBUMS_JSON_PATH}")
    print(f"Exported history: {len(existing_dates)} per-date files in {SITE_HISTORY_DIR}")
    print(f"Songs exported:   {len(deduped_songs)}")
    print(f"Albums exported:  {len(albums_payload)}")
    print(f"Dates exported:   {len(dates)}")

    # R2 upload — opt-in via UPLOAD_TO_R2=1 env var
    import os as _os, subprocess as _subprocess, sys as _sys
    if _os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("1", "true", "yes"):
        print("Uploading per-track history to R2...")
        _r2_script = Path(__file__).resolve().parents[4] / "scripts" / "r2.py"
        _subprocess.run([_sys.executable, str(_r2_script)], check=True)


def main() -> None:
    export_for_web()


if __name__ == "__main__":
    main()