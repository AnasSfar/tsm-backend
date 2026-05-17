from __future__ import annotations

import concurrent.futures
import csv
import json
import os
import re
import threading
from datetime import date, timedelta
from pathlib import Path

from core.data_paths import archived_db_file, update_streams_dir

STREAMS_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = STREAMS_DIR.parents[2]
DB_ROOT = REPO_ROOT / "db"
HISTORY_PATH = (
    DB_ROOT / "streams_history.csv"
    if (DB_ROOT / "streams_history.csv").exists()
    else archived_db_file("streams_history.csv")
)
DISCOGRAPHY_DIR = DB_ROOT / "discography"
DB_ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
DB_SONGS_JSON = DISCOGRAPHY_DIR / "songs.json"
MAX_DAILY_INCREASE = 50_000_000
LOG_MODE = "normal"
_R2_TRACK_PREFIX = os.getenv("SPOTIFY_R2_TRACK_PREFIX", "history-by-track")


def get_previous_stats_date_str(stats_date: str) -> str:
    return (date.fromisoformat(stats_date) - timedelta(days=1)).isoformat()


def extract_track_id(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"track/([A-Za-z0-9]+)", url)
    return match.group(1) if match else None


def load_album_sections_flat() -> list[dict]:
    if not DB_ALBUMS_DIR.exists():
        return []
    sections: list[dict] = []
    for album_file in sorted(DB_ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
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

def _r2_config() -> dict | None:
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no", "off"):
        return None

    r2_account = os.getenv("R2_ACCOUNT_ID", "").strip()
    r2_key_id = os.getenv("R2_ACCESS_KEY_ID", "").strip()
    r2_secret = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
    r2_bucket = os.getenv("R2_BUCKET", "").strip()
    if not all([r2_account, r2_key_id, r2_secret, r2_bucket]):
        return None

    try:
        import boto3 as _boto3
    except ImportError:
        return None

    return {
        "boto3": _boto3,
        "account": r2_account,
        "key_id": r2_key_id,
        "secret": r2_secret,
        "bucket": r2_bucket,
    }

def _upload_track_history_points_to_r2(track_id: str, points: list[dict], cfg: dict) -> bool:
    if not points:
        return False

    points.sort(key=lambda x: x["date"])
    payload = json.dumps(
        {"track_id": track_id, "points": points},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")

    try:
        s3 = cfg["boto3"].client(
            "s3",
            endpoint_url=f"https://{cfg['account']}.r2.cloudflarestorage.com",
            aws_access_key_id=cfg["key_id"],
            aws_secret_access_key=cfg["secret"],
        )
        s3.put_object(
            Bucket=cfg["bucket"],
            Key=f"{_R2_TRACK_PREFIX}/{track_id}.json",
            Body=payload,
            ContentType="application/json; charset=utf-8",
        )
        if LOG_MODE == "verbose":
            print(f"  [r2] {track_id} -> {len(points)} points uploaded")
        return True
    except Exception as e:
        if LOG_MODE == "verbose":
            print(f"  [r2] upload failed for {track_id}: {e}")
        return False

def load_album_track_ids() -> set[str]:
    """Returns track IDs from album files only (excludes songs.json extras)."""
    sections = load_album_sections_flat()
    ids = set()
    for section in sections:
        for track in section.get("tracks", []):
            url = (track.get("url") or track.get("spotify_url") or "").strip()
            tid = extract_track_id(url)
            if tid:
                ids.add(tid)
    return ids

def _daily_for_spotlight(history_index: HistoryIndex, track_id: str, stats_date: str) -> int | None:
    daily = history_index.get_daily_for_date(track_id, stats_date)
    if daily is not None:
        return daily

    total = history_index.get_total_for_date(track_id, stats_date)
    previous_total = history_index.get_total_for_date(
        track_id,
        str(date.fromisoformat(stats_date) - timedelta(days=1)),
    )
    if total is None or previous_total is None:
        return None
    return compute_daily(previous_total, total)

def find_biggest_album_gainer_for_spotlight(
    stats_date: str,
    history_index: HistoryIndex,
    *,
    compare_days: int,
) -> dict | None:
    """Find biggest album-track daily gain vs yesterday or last week.

    Uses only tracks present in db/discography/albums/*.json, so songs.json extras
    and other extras are excluded from spotlight automation.
    """
    album_ids = load_album_track_ids()
    if not album_ids:
        return None

    album_tracks = [
        track for track in load_tracks_from_discography(album_ids)
        if track["track_id"] in album_ids
    ]
    baseline_date = str(date.fromisoformat(stats_date) - timedelta(days=compare_days))

    best: dict | None = None
    for track in album_tracks:
        track_id = track["track_id"]
        daily_today = _daily_for_spotlight(history_index, track_id, stats_date)
        daily_baseline = _daily_for_spotlight(history_index, track_id, baseline_date)
        if daily_today is None or daily_baseline is None:
            continue

        gain = daily_today - daily_baseline
        if gain <= 0:
            continue

        candidate = {
            "track": track,
            "gain": gain,
            "daily_today": daily_today,
            "daily_baseline": daily_baseline,
            "baseline_date": baseline_date,
        }
        if best is None or gain > best["gain"]:
            best = candidate

    return best

def all_album_tracks_done(stats_date: str) -> bool:
    """Returns True when every album-file track has a history row for stats_date."""
    album_ids = load_album_track_ids()
    if not album_ids:
        return True
    done_ids = load_history_track_ids_for_date(stats_date)
    return album_ids.issubset(done_ids)

def album_tracks_done_for(album_name: str, stats_date: str) -> bool:
    """Returns True when every track from the given album has a history row for stats_date."""
    sections = load_album_sections_flat()
    album_ids = set()
    for section in sections:
        if section.get("album") != album_name:
            continue
        for track in section.get("tracks", []):
            tid = extract_track_id(track.get("url") or track.get("spotify_url") or "")
            if tid:
                album_ids.add(tid)
    if not album_ids:
        return False
    done_ids = load_history_track_ids_for_date(stats_date)
    return album_ids.issubset(done_ids)

def load_active_track_ids_from_discography() -> set[str]:
    active_track_ids = set()

    for data in load_album_sections_flat():
        for track in data.get("tracks", []):
            url = (track.get("url") or track.get("spotify_url") or "").strip()
            track_id = extract_track_id(url)
            if track_id:
                active_track_ids.add(track_id)

    if DB_SONGS_JSON.exists():
        try:
            sections = json.loads(DB_SONGS_JSON.read_text(encoding="utf-8-sig"))
        except Exception:
            sections = []
        for data in sections:
            for track in data.get("tracks", []):
                url = (track.get("url") or track.get("spotify_url") or "").strip()
                track_id = extract_track_id(url)
                if track_id:
                    active_track_ids.add(track_id)

    return active_track_ids

def ensure_history_file() -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not HISTORY_PATH.exists():
        with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "track_id", "streams", "daily_streams"])

def get_last_stats_date_in_history() -> str | None:
    if not HISTORY_PATH.exists():
        return None

    last_date = None
    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = (row.get("date") or "").strip()
            if d:
                last_date = d
    return last_date

def delete_history_rows_for_date(target_date: str) -> int:
    if not HISTORY_PATH.exists():
        return 0

    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or ["date", "track_id", "streams", "daily_streams"]

    kept_rows = [r for r in rows if (r.get("date") or "").strip() != target_date]
    removed = len(rows) - len(kept_rows)

    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    return removed

def dedupe_history_rows_by_date_track() -> int:
    if not HISTORY_PATH.exists():
        return 0

    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or ["date", "track_id", "streams", "daily_streams"]

    deduped: dict[tuple[str, str], dict] = {}
    for row in rows:
        date_value = (row.get("date") or "").strip()
        track_id = (row.get("track_id") or "").strip()
        if not date_value or not track_id:
            continue
        deduped[(date_value, track_id)] = row

    removed = len(rows) - len(deduped)
    if removed <= 0:
        return 0

    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(deduped.values())

    return removed

def append_history_row(row: list) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)
    if row:
        day = str(row[0])
        daily_path = update_streams_dir(day) / "streams_history.csv"
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not daily_path.exists() or daily_path.stat().st_size == 0
        with daily_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(["date", "track_id", "streams", "daily_streams"])
            writer.writerow(row)

def load_history_rows() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []

    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def save_history_rows(rows: list[dict]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "track_id", "streams", "daily_streams"]
    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "date": row.get("date", ""),
                    "track_id": row.get("track_id", ""),
                    "streams": row.get("streams", ""),
                    "daily_streams": row.get("daily_streams", ""),
                }
            )

class HistoryIndex:
    """In-memory view of streams_history.csv for fast worker lookups."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self._lock = threading.Lock()
        self.rows: list[dict] = []
        self.last_total_by_track: dict[str, int] = {}
        self.total_by_date_track: dict[tuple[str, str], int] = {}
        self.ids_by_date: dict[str, set[str]] = {}
        self.points_by_track: dict[str, list[dict]] = {}
        for row in rows or []:
            self._consume_row(row)

    @classmethod
    def load(cls) -> "HistoryIndex":
        return cls(load_history_rows())

    def _consume_row(self, row: dict) -> None:
        date_value = (row.get("date") or "").strip()
        track_id = (row.get("track_id") or "").strip()
        streams_raw = (row.get("streams") or "").strip()
        if not date_value or not track_id or not streams_raw:
            return
        try:
            streams = int(streams_raw)
        except Exception:
            return

        daily_raw = (row.get("daily_streams") or "").strip()
        clean_row = {
            "date": date_value,
            "track_id": track_id,
            "streams": str(streams),
            "daily_streams": daily_raw,
        }
        self.rows.append(clean_row)
        self.last_total_by_track[track_id] = streams
        self.total_by_date_track[(date_value, track_id)] = streams
        self.ids_by_date.setdefault(date_value, set()).add(track_id)

        point: dict = {"date": date_value, "streams": streams}
        if daily_raw:
            try:
                point["daily_streams"] = int(daily_raw)
            except Exception:
                pass
        self.points_by_track.setdefault(track_id, []).append(point)

    def get_last_total(self, track_id: str) -> int | None:
        with self._lock:
            return self.last_total_by_track.get(track_id)

    def get_total_for_date(self, track_id: str, stats_date: str) -> int | None:
        with self._lock:
            return self.total_by_date_track.get((stats_date, track_id))

    def get_daily_for_date(self, track_id: str, stats_date: str) -> int | None:
        with self._lock:
            for point in self.points_by_track.get(track_id, []):
                if point.get("date") == stats_date and "daily_streams" in point:
                    return point["daily_streams"]
        return None

    def get_previous_total_before_date(self, track_id: str, stats_date: str) -> int | None:
        target = date.fromisoformat(stats_date)
        best_date = None
        best_total = None
        with self._lock:
            for point in self.points_by_track.get(track_id, []):
                try:
                    point_date = date.fromisoformat(point["date"])
                    point_total = int(point["streams"])
                except Exception:
                    continue
                if point_date >= target:
                    continue
                if best_date is None or point_date > best_date:
                    best_date = point_date
                    best_total = point_total
        return best_total

    def done_ids_for_date(self, stats_date: str) -> set[str]:
        with self._lock:
            return set(self.ids_by_date.get(stats_date, set()))

    def append(self, stats_date: str, track_id: str, total: int, daily: int | None) -> None:
        row = {
            "date": stats_date,
            "track_id": track_id,
            "streams": str(total),
            "daily_streams": "" if daily is None else str(daily),
        }
        with self._lock:
            append_history_row([stats_date, track_id, total, "" if daily is None else daily])
            self._consume_row(row)

    def points_for_track(self, track_id: str) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self.points_by_track.get(track_id, [])]

def push_updated_track_histories_to_r2(track_ids: set[str], history_index: HistoryIndex) -> None:
    if not track_ids:
        return
    cfg = _r2_config()
    if cfg is None:
        return

    ids = sorted(track_ids)
    print(f"[r2] Uploading {len(ids)} updated track history file(s)...")

    uploaded = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(ids))) as ex:
        futures = {
            ex.submit(
                _upload_track_history_points_to_r2,
                track_id,
                history_index.points_for_track(track_id),
                cfg,
            ): track_id
            for track_id in ids
        }
        for fut in concurrent.futures.as_completed(futures):
            try:
                if fut.result():
                    uploaded += 1
            except Exception:
                pass

    print(f"[r2] Uploaded {uploaded}/{len(ids)} track history file(s).")

def load_history_track_ids_for_date(stats_date: str) -> set[str]:
    if not HISTORY_PATH.exists():
        return set()

    done = set()
    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("date") or "").strip() == stats_date:
                track_id = (row.get("track_id") or "").strip()
                if track_id:
                    done.add(track_id)
    return done

def get_last_history_total(track_id: str) -> int | None:
    if not HISTORY_PATH.exists():
        return None

    last = None
    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("track_id") == track_id:
                try:
                    last = int(row["streams"])
                except Exception:
                    pass
    return last

def get_all_last_history_totals() -> dict[str, int]:
    """Lit le CSV une seule fois et retourne {track_id: last_streams}."""
    result: dict[str, int] = {}
    if not HISTORY_PATH.exists():
        return result
    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            track_id = (row.get("track_id") or "").strip()
            try:
                result[track_id] = int(row["streams"])
            except Exception:
                pass
    return result

def get_history_total_for_date(track_id: str, stats_date: str) -> int | None:
    if not HISTORY_PATH.exists():
        return None

    value = None
    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("track_id") or "").strip() != track_id:
                continue
            if (row.get("date") or "").strip() != stats_date:
                continue
            raw = (row.get("streams") or "").strip()
            if not raw:
                continue
            try:
                value = int(raw)
            except Exception:
                continue
    return value

def get_previous_total_before_date(track_id: str, stats_date: str) -> int | None:
    if not HISTORY_PATH.exists():
        return None

    target = date.fromisoformat(stats_date)
    best_date = None
    best_total = None

    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("track_id") or "").strip() != track_id:
                continue

            d_raw = (row.get("date") or "").strip()
            s_raw = (row.get("streams") or "").strip()
            if not d_raw or not s_raw:
                continue

            try:
                d = date.fromisoformat(d_raw)
                total = int(s_raw)
            except Exception:
                continue

            if d >= target:
                continue

            if best_date is None or d > best_date:
                best_date = d
                best_total = total

    return best_total

def has_real_update(previous_streams: int | None, new_streams: int) -> bool:
    if previous_streams is None:
        return True
    if new_streams == previous_streams:
        return False
    if new_streams - previous_streams > MAX_DAILY_INCREASE:
        if LOG_MODE != "quiet":
            print(
                f"  [ANOMALY REJECTED] {new_streams:,} "
                f"(prev={previous_streams:,}, delta=+{new_streams - previous_streams:,}) "
                f"— exceeds {MAX_DAILY_INCREASE:,}/day cap, skipping"
            )
        return False
    return True

def compute_daily(previous_streams: int | None, new_streams: int) -> int | None:
    if previous_streams is None:
        return None
    diff = new_streams - previous_streams
    if diff < 0:
        return None
    return diff

def load_tracks_from_discography(active_track_ids: set[str] | None = None) -> list[dict]:
    seen: dict[str, dict] = {}

    all_sections = load_album_sections_flat()
    if DB_SONGS_JSON.exists():
        try:
            all_sections.extend(json.loads(DB_SONGS_JSON.read_text(encoding="utf-8-sig")))
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

            if active_track_ids is not None and track_id not in active_track_ids:
                continue

            spotify_url = f"https://open.spotify.com/track/{track_id}"
            image_url = track.get("image_url") or None
            artists = track.get("artists") or []
            primary_artist = track.get("primary_artist") or (artists[0] if artists else None)

            seen[track_id] = {
                "track_id": track_id,
                "title": title,
                "spotify_url": spotify_url,
                "streams": None,
                "daily_streams": None,
                "last_updated": None,
                "image_url": image_url,
                "primary_artist": primary_artist,
                "artists_json": json.dumps(artists),
            }

    tracks = list(seen.values())
    tracks.sort(key=lambda t: t["title"].casefold())
    return tracks

def build_track_lookup(tracks: list[dict]) -> dict[str, list[dict]]:
    lookup: dict[str, list[dict]] = {}
    for track in tracks:
        key = normalize_title(track["title"])
        lookup.setdefault(key, []).append(track)
    return lookup

def load_track_priorities_from_specific_date(target_date: str) -> dict[str, int]:
    result: dict[str, int] = {}

    if not HISTORY_PATH.exists():
        return result

    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("date") or "").strip() != target_date:
                continue

            track_id = (row.get("track_id") or "").strip()
            daily_raw = (row.get("daily_streams") or "").strip()

            if not track_id or not daily_raw:
                continue

            try:
                daily = int(daily_raw)
            except ValueError:
                continue

            result[track_id] = daily

    return result

def get_priority_top_50_track_ids_from_previous_day(tracks: list[dict], stats_date: str) -> set[str]:
    previous_date = get_previous_stats_date_str(stats_date)
    priorities = load_track_priorities_from_specific_date(previous_date)

    ordered = sorted(
        tracks,
        key=lambda t: (-priorities.get(t["track_id"], 0), t["title"].casefold())
    )
    return {t["track_id"] for t in ordered[:50]}
