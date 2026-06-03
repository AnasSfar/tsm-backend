from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import post_debut_releases
from release_targets import recent_release_album_names

REPO_ROOT = Path(__file__).resolve().parents[5]
REAL_SONGS_PATH = REPO_ROOT / "db" / "discography" / "songs.json"
REAL_ALBUMS_DIR = REPO_ROOT / "db" / "discography" / "albums"
REAL_HISTORY_PATH = REPO_ROOT / "db" / "streams_history.csv"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _tracks_from_sections(sections) -> list[dict]:
    return [
        track
        for section in sections if isinstance(section, dict)
        for track in section.get("tracks") or []
        if track.get("url") and track.get("image_url")
    ]


def _clean_song_key(track: dict) -> str:
    return post_debut_releases._clean_base_title(
        str(track.get("title_clean") or track.get("base_title") or track.get("title") or "")
    ).casefold()


def _real_song_versions(preferred_title: str = "Blank Space") -> list[dict]:
    sections = json.loads(REAL_SONGS_PATH.read_text(encoding="utf-8-sig"))
    candidates = _tracks_from_sections(sections)
    if REAL_ALBUMS_DIR.exists():
        for album_path in sorted(REAL_ALBUMS_DIR.glob("*.json"), key=lambda path: path.name.casefold()):
            try:
                payload = json.loads(album_path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            album_sections = payload.get("sections") if isinstance(payload, dict) else []
            candidates.extend(_tracks_from_sections(album_sections or []))

    candidates = [
        track for track in candidates
        if post_debut_releases._track_id(track.get("url"))
    ]
    versions_by_id: dict[str, dict] = {}
    for track in candidates:
        if _clean_song_key(track) != preferred_title.casefold():
            continue
        track_id = post_debut_releases._track_id(track.get("url"))
        if track_id and track_id not in versions_by_id:
            versions_by_id[track_id] = dict(track)

    versions = sorted(
        versions_by_id.values(),
        key=lambda track: (
            0 if str(track.get("title") or "").casefold() == preferred_title.casefold() else 1,
            str(track.get("title") or "").casefold(),
        ),
    )
    if not versions:
        raise RuntimeError(f"No existing discography versions found for {preferred_title!r}.")
    return versions


def _latest_streams_by_track(track_ids: set[str]) -> tuple[dict[str, int], str]:
    latest: dict[str, tuple[str, int]] = {}
    if not REAL_HISTORY_PATH.exists():
        return {}, ""

    with REAL_HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            track_id = (row.get("track_id") or "").strip()
            if track_id not in track_ids:
                continue
            day = (row.get("date") or "").strip()
            try:
                streams = int((row.get("streams") or "0").strip() or "0")
            except ValueError:
                streams = 0
            if not day or streams <= 0:
                continue
            if track_id not in latest or day > latest[track_id][0]:
                latest[track_id] = (day, streams)

    source_date = max((day for day, _streams in latest.values()), default="")
    return {track_id: streams for track_id, (_day, streams) in latest.items()}, source_date


def run_scenario() -> int:
    target_date = "2026-06-03"
    real_versions = _real_song_versions()
    real_title = "Blank Space"
    track_ids = {
        str(post_debut_releases._track_id(track.get("url")) or "")
        for track in real_versions
    }
    track_ids.discard("")
    streams_by_track, source_streams_date = _latest_streams_by_track(track_ids)
    album_name = f"Scenario Release - {real_title}"
    tracks = []
    for track in real_versions:
        scenario_track = dict(track)
        scenario_track["release_date"] = target_date
        scenario_track["base_title"] = real_title
        scenario_track["song_family"] = track.get("song_family") or real_title
        tracks.append(scenario_track)

    expected_total = sum(streams_by_track.get(post_debut_releases._track_id(track.get("url")) or "", 0) for track in tracks)
    if expected_total <= 0:
        raise RuntimeError("No real stream totals found for the selected Blank Space versions.")

    tmp_root = Path(__file__).resolve().parents[5] / "data" / "_tmp"
    root = tmp_root / "scenario_new_song_versions"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    albums_dir = root / "albums"
    history_path = root / "streams_history.csv"
    songs_path = root / "songs.json"

    scenario_sections = [{"album": album_name, "name": "Standard", "tracks": tracks}]
    album_payload = {
        "album": album_name,
        "sections": scenario_sections,
    }
    _write_json(albums_dir / "ts12_scenario_album.json", album_payload)
    _write_json(songs_path, [{"album": "TS12 Scenario Album", "tracks": tracks}])

    with history_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "track_id", "streams", "daily_streams"])
        for track in tracks:
            track_id = post_debut_releases._track_id(track.get("url"))
            writer.writerow([target_date, track_id, str(streams_by_track.get(track_id or "", 0)), ""])

    post_debut_releases.ALBUMS_DIR = albums_dir
    post_debut_releases.SONGS_PATH = songs_path
    post_debut_releases.HISTORY_PATH = history_path
    post_debut_releases.update_streams_dir = lambda _target_date: root / "update_streams"

    posts = post_debut_releases._build_posts(target_date)
    recent_albums = recent_release_album_names(scenario_sections, target_date)

    print("[scenario] Taylor releases one existing song with real versions")
    print(f"[scenario] target_date={target_date}")
    print(f"[scenario] source_streams_date={source_streams_date}")
    print(f"[scenario] real_song={real_title}")
    print(f"[scenario] versions={len(tracks)}")
    for track in tracks:
        track_id = post_debut_releases._track_id(track.get("url"))
        print(
            "[scenario] version="
            f"{track.get('title')} | track_id={track_id} | streams={streams_by_track.get(track_id or '', 0)}"
        )
    print(f"[scenario] recent_album_targets={recent_albums}")
    print(f"[scenario] debut_posts={len(posts)}")
    for slug, text, image_path in posts:
        print(f"[scenario] slug={slug}")
        print(f"[scenario] image={image_path}")
        print(f"[scenario] text={text}")

    if recent_albums != [album_name]:
        print("[scenario] FAIL: recent album was not detected.")
        return 1
    if len(posts) != 2:
        print("[scenario] FAIL: expected a two-post debut thread.")
        return 1
    expected_total_text = f"{expected_total:,}"
    if not posts[0][0].endswith(":total") or expected_total_text not in posts[0][1] or f"across {len(tracks)} versions" not in posts[0][1]:
        print("[scenario] FAIL: expected aggregated real streams across real versions.")
        return 1
    if not posts[1][0].endswith(":details") or "version breakdown" not in posts[1][1]:
        print("[scenario] FAIL: expected a version breakdown post.")
        return 1
    if any(image_path is None or not image_path.exists() for _slug, _text, image_path in posts):
        print("[scenario] FAIL: expected a rendered debut image.")
        return 1

    print("[scenario] PASS")
    print(f"[scenario] preview_dir={root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_scenario())
