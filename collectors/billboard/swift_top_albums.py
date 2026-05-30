"""TayBoard Albums — weekly album chart.

Source des données : db/swift_top_songs_history.csv (Spotify + Apple Music units
calculés pour toutes les chansons par swift_top_100.py). Les units sont groupées
par album via la discographie, hors extras/live/remixes/track-by-track.

Outputs:
- db/swift_top_albums_history.csv
- website/site/data/swift_top_albums.json (latest)
- website/site/data/swift_top_albums_YYYY-MM-DD.json (snapshots)
- website/site/data/swift_top_albums_index.json

Run:
  python collectors/billboard/swift_top_albums.py
  python collectors/billboard/swift_top_albums.py --date 2026-04-22
  python collectors/billboard/swift_top_albums.py --dry-run
  python collectors/billboard/swift_top_albums.py --backfill --force
  python collectors/billboard/swift_top_albums.py --upload
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[1]

sys.path.insert(0, str((_REPO_ROOT / "collectors" / "spotify").resolve()))
from core.logger import Logger  # noqa: E402

_DB_DIR = _REPO_ROOT / "db"
_SITE_DATA_DIR = _REPO_ROOT / "website" / "site" / "data"

SWIFT_TOP_SONGS_HISTORY_CSV = _DB_DIR / "swift_top_songs_history.csv"
SWIFT_TOP_ALBUMS_HISTORY_CSV = _DB_DIR / "swift_top_albums_history.csv"
CHART_SLUG = "swift_top_albums"
CHART_TITLE = "TayBoard Albums"
CHART_KIND = "albums"

DISCOGRAPHY_DIR = _DB_DIR / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
COVERS_JSON = DISCOGRAPHY_DIR / "covers.json"

OUTPUT_JSON = _SITE_DATA_DIR / "swift_top_albums.json"

_TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")
_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_TS_FEATURE_RE = re.compile(r"\bfeat(?:\.|uring)?\s+taylor\s+swift\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _format_date(value: date) -> str:
    return value.isoformat()


def _configure_variant(variant: str) -> None:
    global SWIFT_TOP_ALBUMS_HISTORY_CSV, OUTPUT_JSON, CHART_SLUG, CHART_TITLE, CHART_KIND
    if variant == "albums":
        CHART_SLUG = "swift_top_albums"
        CHART_TITLE = "TayBoard Albums"
        CHART_KIND = "albums"
    elif variant == "eras":
        CHART_SLUG = "swift_top_eras"
        CHART_TITLE = "TayBoard Eras"
        CHART_KIND = "eras"
    else:
        raise ValueError(f"Unknown TayBoard albums variant: {variant}")
    SWIFT_TOP_ALBUMS_HISTORY_CSV = _DB_DIR / f"{CHART_SLUG}_history.csv"
    OUTPUT_JSON = _SITE_DATA_DIR / f"{CHART_SLUG}.json"


def _normalize_album_id(name: str) -> str:
    return _NORMALIZE_RE.sub("_", (name or "").strip().casefold()).strip("_")


def _extract_track_id(url: str | None) -> str | None:
    if not url:
        return None
    m = _TRACK_ID_RE.search(url)
    return m.group(1) if m else None


def _format_number(value: int | float | None, decimals: int = 2) -> str:
    if value is None or value == 0:
        return "0"
    value = float(value)
    if abs(value) < 1_000:
        return str(int(value)) if value == int(value) else f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    elif abs(value) < 1_000_000:
        return f"{value / 1_000:.{decimals}f}".rstrip("0").rstrip(".") + "k"
    elif abs(value) < 1_000_000_000:
        return f"{value / 1_000_000:.{decimals}f}".rstrip("0").rstrip(".") + "M"
    else:
        return f"{value / 1_000_000_000:.{decimals}f}".rstrip("0").rstrip(".") + "B"


def _is_taylor_feature(row: dict) -> bool:
    """Return True for songs where Taylor Swift is credited as the feature."""
    title = row.get("title") or ""
    return bool(_TS_FEATURE_RE.search(title))


def _track_has_taylor_as_primary(track: dict) -> bool:
    primary = (track.get("primary_artist") or "").strip().casefold()
    if primary:
        return primary == "taylor swift"
    artists = track.get("artists") or []
    if isinstance(artists, list) and artists:
        return str(artists[0]).strip().casefold() == "taylor swift"
    return not _is_taylor_feature(track)


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().casefold()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return None


def _track_counts_for_album_chart(track: dict, section: dict) -> bool:
    track_flag = _as_bool(track.get("chart_extra"))
    if track_flag is not None:
        return not track_flag

    section_flag = _as_bool(section.get("chart_extra"))
    if section_flag is not None:
        return not section_flag

    edition = (track.get("edition") or "").strip().casefold()
    display_section = (
        track.get("display_section") or section.get("title") or section.get("name") or ""
    ).strip().casefold()
    title = (track.get("title") or "").strip().casefold()

    blocked_tokens = (
        "extra",
        "live",
        "karaoke",
        "acoustic",
        "remix",
        "track by track",
        "music video",
        "video extended",
        "extended version part",
    )
    haystack = " ".join(part for part in (edition, display_section, title) if part)
    return not any(token in haystack for token in blocked_tokens)


def _era_title(album_title: str) -> str:
    title = (album_title or "").strip()
    tv_map = {
        "Fearless (Taylor's Version)": "Fearless",
        "Speak Now (Taylor's Version)": "Speak Now",
        "Red (Taylor's Version)": "Red",
        "1989 (Taylor's Version)": "1989",
    }
    return tv_map.get(title, title)


# ---------------------------------------------------------------------------
# Discography — albums
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AlbumMeta:
    album_id: str
    title: str
    cover_url: str | None
    spotify_url: str | None
    track_ids: frozenset[str]


def _load_covers() -> dict[str, dict]:
    if not COVERS_JSON.exists():
        return {}
    try:
        return json.loads(COVERS_JSON.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _iter_discography_albums() -> list[AlbumMeta]:
    covers = _load_covers()
    albums: dict[str, dict] = {}

    if not ALBUMS_DIR.exists():
        return []

    for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        album_name = (payload.get("album") or album_file.stem).strip()
        if not album_name:
            continue

        album_id = _normalize_album_id(album_name)
        cover_data = covers.get(album_name, {})

        if album_id not in albums:
            albums[album_id] = {
                "title": album_name,
                "cover_url": cover_data.get("cover_url") or None,
                "spotify_url": cover_data.get("spotify_url") or None,
                "track_ids": set(),
            }

        for section in payload.get("sections", []) or []:
            if not isinstance(section, dict):
                continue
            for track in section.get("tracks", []) or []:
                if not isinstance(track, dict):
                    continue
                if not _track_has_taylor_as_primary(track):
                    continue
                if not _track_counts_for_album_chart(track, section):
                    continue
                tid = _extract_track_id(
                    (track.get("url") or track.get("spotify_url") or "").strip()
                )
                if tid:
                    albums[album_id]["track_ids"].add(tid)
                for hist_id in track.get("historical_track_ids") or []:
                    if isinstance(hist_id, str) and hist_id.strip():
                        albums[album_id]["track_ids"].add(hist_id.strip())

    return [
        AlbumMeta(
            album_id=album_id,
            title=info["title"],
            cover_url=info["cover_url"],
            spotify_url=info["spotify_url"],
            track_ids=frozenset(info["track_ids"]),
        )
        for album_id, info in albums.items()
    ]


def _albums_for_chart_variant(albums: list[AlbumMeta]) -> list[AlbumMeta]:
    if CHART_KIND != "eras":
        return albums

    grouped: dict[str, dict] = {}
    for album in albums:
        title = _era_title(album.title)
        aid = _normalize_album_id(title)
        if aid not in grouped:
            grouped[aid] = {
                "title": title,
                "cover_url": album.cover_url,
                "spotify_url": album.spotify_url,
                "track_ids": set(),
            }
        grouped[aid]["track_ids"].update(album.track_ids)
        # Prefer Taylor's Version artwork for re-recorded eras when available.
        if "Taylor's Version" in album.title:
            grouped[aid]["cover_url"] = album.cover_url
            grouped[aid]["spotify_url"] = album.spotify_url

    return [
        AlbumMeta(
            album_id=aid,
            title=info["title"],
            cover_url=info["cover_url"],
            spotify_url=info["spotify_url"],
            track_ids=frozenset(info["track_ids"]),
        )
        for aid, info in grouped.items()
    ]


# ---------------------------------------------------------------------------
# Song history source
# ---------------------------------------------------------------------------

def _load_song_history() -> list[dict]:
    if not SWIFT_TOP_SONGS_HISTORY_CSV.exists():
        return []
    with SWIFT_TOP_SONGS_HISTORY_CSV.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _week_dates(week_end: date) -> tuple[date, list[str]]:
    week_start = week_end - timedelta(days=6)
    days = [_format_date(week_start + timedelta(days=i)) for i in range(7)]
    return week_start, days


def _all_stream_dates(song_rows: list[dict]) -> list[date]:
    dates: set[date] = set()
    for row in song_rows:
        d = _parse_iso_date(row.get("date") or "")
        if d:
            dates.add(d)
    return sorted(dates)


def _all_chart_dates(song_rows: list[dict]) -> list[date]:
    return _all_stream_dates(song_rows)


def _latest_chart_date(song_rows: list[dict]) -> date | None:
    dates = _all_chart_dates(song_rows)
    return dates[-1] if dates else None


def _previous_chart_date(song_rows: list[dict], chart_date: date) -> date | None:
    dates = [d for d in _all_chart_dates(song_rows) if d < chart_date]
    return dates[-1] if dates else None


def _build_album_week(
    *,
    chart_date: str,
    song_rows: list[dict],
    track_to_album: dict[str, AlbumMeta],
    logger: Logger,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Aggregate song rows for chart_date into album-level streams.

    Returns (scored_by_album_id, rank_by_album_id).
    """
    week_rows = [r for r in song_rows if (r.get("date") or "").strip() == chart_date]
    logger.log(f"  source         : {len(week_rows)} scored songs for {chart_date}")

    album_weekly_streams: dict[str, int] = {}
    album_units_am: dict[str, int] = {}
    album_units_am_ts: dict[str, int] = {}
    album_units_am_overall: dict[str, int] = {}
    album_units_spotify: dict[str, int] = {}
    album_units_charts: dict[str, int] = {}
    album_units_surplus: dict[str, int] = {}
    album_total_units: dict[str, int] = {}
    album_track_ids: dict[str, set[str]] = {}

    def _to_int(v: str | None) -> int:
        try:
            return int((v or "").strip())
        except Exception:
            return 0

    def _score_to_units(v: str | None) -> int:
        try:
            return round(float((v or "").strip()) * 1000)
        except Exception:
            return 0

    unmatched_track_ids: set[str] = set()
    for row in week_rows:
        tid = (row.get("track_id") or "").strip()
        if not tid:
            continue
        album = track_to_album.get(tid)
        if not album:
            unmatched_track_ids.add(tid)
            continue
        album_weekly_streams[album.album_id] = album_weekly_streams.get(album.album_id, 0) + _to_int(row.get("weekly_streams"))
        album_units_am[album.album_id] = album_units_am.get(album.album_id, 0) + _to_int(row.get("units_am"))
        album_units_am_ts[album.album_id] = album_units_am_ts.get(album.album_id, 0) + _score_to_units(row.get("am_ts_score"))
        album_units_am_overall[album.album_id] = (
            album_units_am_overall.get(album.album_id, 0)
            + _score_to_units(row.get("am_overall_score"))
        )
        album_units_spotify[album.album_id] = album_units_spotify.get(album.album_id, 0) + _to_int(row.get("units_spotify"))
        album_units_charts[album.album_id] = album_units_charts.get(album.album_id, 0) + _to_int(row.get("units_charts"))
        album_units_surplus[album.album_id] = album_units_surplus.get(album.album_id, 0) + _to_int(row.get("units_surplus"))
        album_total_units[album.album_id] = album_total_units.get(album.album_id, 0) + _to_int(row.get("total_units"))
        album_track_ids.setdefault(album.album_id, set()).add(tid)

    if unmatched_track_ids:
        logger.log(f"  unmatched      : {len(unmatched_track_ids)} tracks not linked to any album")

    scored = sorted(album_total_units.items(), key=lambda kv: kv[1], reverse=True)
    points_by_album = {
        aid: {
            "album_id": aid,
            "weekly_streams": album_weekly_streams.get(aid, 0),
            "units_am": album_units_am.get(aid, 0),
            "units_am_ts": album_units_am_ts.get(aid, 0),
            "units_am_overall": album_units_am_overall.get(aid, 0),
            "units_spotify": album_units_spotify.get(aid, 0),
            "units_charts": album_units_charts.get(aid, 0),
            "units_surplus": album_units_surplus.get(aid, 0),
            "total_units": total_units,
            "track_count": len(album_track_ids.get(aid, set())),
        }
        for aid, total_units in scored
    }
    rank_by_album = {aid: i for i, (aid, _) in enumerate(scored, 1)}

    logger.log(f"  top            : {len(scored)} albums ranked")
    return points_by_album, rank_by_album


# ---------------------------------------------------------------------------
# Album history CSV
# ---------------------------------------------------------------------------

def _load_existing_history(logger: Logger) -> list[dict]:
    if not SWIFT_TOP_ALBUMS_HISTORY_CSV.exists():
        return []
    try:
        with SWIFT_TOP_ALBUMS_HISTORY_CSV.open("r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:
        logger.log(f"⚠ history        : failed to read CSV — {exc}")
        return []
    return rows


def _history_rows_before_date(rows: list[dict], chart_date: str) -> list[dict]:
    return [r for r in rows if (r.get("date") or "").strip() < chart_date]


def _history_rows_without_date(rows: list[dict], chart_date: str) -> list[dict]:
    return [r for r in rows if (r.get("date") or "").strip() != chart_date]


def _history_stats(rows: list[dict]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    seen_weeks: dict[str, set[str]] = {}
    all_ranks: dict[str, list[int]] = {}

    def _to_int(v: str | None) -> int | None:
        try:
            return int((v or "").strip())
        except Exception:
            return None

    for row in rows:
        aid = (row.get("album_id") or "").strip()
        d = (row.get("date") or "").strip()
        rk = _to_int(row.get("rank"))
        if not aid or not d:
            continue
        seen_weeks.setdefault(aid, set()).add(d)
        if rk:
            all_ranks.setdefault(aid, []).append(rk)

    weeks_on_chart = {aid: len(ds) for aid, ds in seen_weeks.items()}
    peaks = {aid: min(ranks) for aid, ranks in all_ranks.items()}
    times_at_peak = {aid: ranks.count(peaks[aid]) for aid, ranks in all_ranks.items()}
    return weeks_on_chart, peaks, times_at_peak


def _write_history_csv(rows: list[dict], logger: Logger) -> None:
    SWIFT_TOP_ALBUMS_HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date",
        "week_start",
        "rank",
        "album_id",
        "title",
        "weekly_streams",
        "units_am",
        "units_am_ts",
        "units_am_overall",
        "units_spotify",
        "units_charts",
        "units_surplus",
        "total_units",
        "track_count",
        "prev_rank",
        "change",
        "rank_change",
        "percentage_change",
        "weeks_on_chart",
        "peak_position",
        "times_at_peak",
    ]
    with SWIFT_TOP_ALBUMS_HISTORY_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.log(f"✔ CSV  → {SWIFT_TOP_ALBUMS_HISTORY_CSV.name} ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# Snapshot JSON
# ---------------------------------------------------------------------------

def _rebuild_snapshot_index(logger: Logger) -> None:
    dates = [p.stem[len(CHART_SLUG) + 1:] for p in _SITE_DATA_DIR.glob(f"{CHART_SLUG}_????-??-??.json")]
    dates.sort(reverse=True)
    index_path = _SITE_DATA_DIR / f"{CHART_SLUG}_index.json"
    index_path.write_text(json.dumps(dates, ensure_ascii=False), encoding="utf-8")
    logger.log(f"✔ IDX  → {index_path.name} ({len(dates)} dates)")


def _write_snapshot_json(payload: dict, logger: Logger) -> None:
    _SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    chart_date = payload.get("chart_date")
    if chart_date:
        dated = _SITE_DATA_DIR / f"{CHART_SLUG}_{chart_date}.json"
        dated.write_text(content, encoding="utf-8")
        logger.log(f"✔ JSON → {dated.name}")
    OUTPUT_JSON.write_text(content, encoding="utf-8")
    logger.log(f"✔ JSON → {OUTPUT_JSON.name} (latest)")
    _rebuild_snapshot_index(logger)


# ---------------------------------------------------------------------------
# R2 upload
# ---------------------------------------------------------------------------

def _maybe_upload_to_r2(*, logger: Logger, skip_r2: bool = False) -> None:
    if skip_r2:
        logger.log("  r2             : skipped for this chart run (--skip-r2)")
        return
    logger.log("  r2             : uploading...")
    try:
        _scripts_dir = str(_REPO_ROOT / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import r2 as _r2
        ok = _r2.upload_slugs(["swift_top_albums", "swift_top_eras"])
        if ok:
            logger.log("✔ r2             : upload complete")
        else:
            logger.log("  r2             : skipped (credentials / config)")
    except Exception as exc:
        logger.log(f"⚠ r2             : upload failed — {exc}")


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(*, chart_date: date | None, song_rows: list[dict], dry_run: bool, skip_r2: bool = False) -> int:
    logger = Logger()

    if chart_date is None:
        chart_date = _latest_chart_date(song_rows)

    if chart_date is None:
        logger.log(f"⚠ no dates found in {SWIFT_TOP_SONGS_HISTORY_CSV.name}")
        return 2

    if chart_date.weekday() != 2:
        logger.log(
            f"⚠ invalid date    : {_format_date(chart_date)} is not a Wednesday "
            "(tracking week must end on Wednesday)"
        )
        return 2

    chart_date_str = _format_date(chart_date)
    available_dates = {_format_date(d) for d in _all_chart_dates(song_rows)}
    if chart_date_str not in available_dates:
        logger.log(
            f"⚠ missing source  : no full song history for {chart_date_str} "
            f"in {SWIFT_TOP_SONGS_HISTORY_CSV.name}"
        )
        return 2

    prev_chart_date = _previous_chart_date(song_rows, chart_date)
    prev_date_str = _format_date(prev_chart_date) if prev_chart_date else ""

    week_start, _ = _week_dates(chart_date)
    week_start_str = _format_date(week_start)

    logger.log(f"▶ {CHART_TITLE} · {chart_date_str}  week={week_start_str}→{chart_date_str}  prev={prev_date_str or 'none'}")

    albums = _albums_for_chart_variant(_iter_discography_albums())
    albums_by_id = {a.album_id: a for a in albums}
    logger.log(f"  discography    : {len(albums)} albums, {sum(len(a.track_ids) for a in albums)} tracks indexed")

    # Build reverse mapping: track_id → AlbumMeta (first album wins for shared IDs)
    track_to_album: dict[str, AlbumMeta] = {}
    for album in albums:
        for tid in album.track_ids:
            if tid not in track_to_album:
                track_to_album[tid] = album

    curr_scored, curr_ranks = _build_album_week(
        chart_date=chart_date_str,
        song_rows=song_rows,
        track_to_album=track_to_album,
        logger=logger,
    )

    existing_rows = _load_existing_history(logger)
    prior_rows = _history_rows_before_date(existing_rows, chart_date_str)
    weeks_on_chart_by_album, peak_by_album, times_at_peak_by_album = _history_stats(prior_rows)

    is_first_run = len(prior_rows) == 0
    if is_first_run:
        logger.log("  history        : first run — all entries NEW")
        prev_scored: dict = {}
        prev_ranks: dict = {}
    else:
        prior_weeks = len({(r.get("date") or "").strip() for r in prior_rows if r.get("date")})
        logger.log(f"  history        : {prior_weeks} prior week{'s' if prior_weeks != 1 else ''} loaded")
        if prev_date_str:
            prev_scored, prev_ranks = _build_album_week(
                chart_date=prev_date_str,
                song_rows=song_rows,
                track_to_album=track_to_album,
                logger=logger,
            )
        else:
            prev_scored = {}
            prev_ranks = {}

    out_entries: list[dict] = []
    snapshot_entries: list[dict] = []

    for aid, rank in sorted(curr_ranks.items(), key=lambda kv: kv[1]):
        row = curr_scored[aid]
        meta = albums_by_id.get(aid)

        pr = prev_ranks.get(aid)
        prev_units_val = (prev_scored.get(aid) or {}).get("total_units")

        if pr is None:
            change = "RE" if weeks_on_chart_by_album.get(aid, 0) > 0 else "NEW"
        else:
            change = None

        weekly_streams = row["weekly_streams"]
        units_am = row["units_am"]
        units_am_ts = row.get("units_am_ts", 0)
        units_am_overall = row.get("units_am_overall", 0)
        units_spotify = row["units_spotify"]
        units_charts = row["units_charts"]
        units_surplus = row["units_surplus"]
        total_units = row["total_units"]
        track_count = row["track_count"]

        pct_change = None
        if pr is not None and prev_units_val and prev_units_val > 0:
            pct_change = round(((total_units - prev_units_val) / prev_units_val) * 100, 1)

        weeks_on_chart = weeks_on_chart_by_album.get(aid, 0) + 1
        hist_peak = peak_by_album.get(aid, 9999)
        peak_position = min(hist_peak, rank)
        hist_times = times_at_peak_by_album.get(aid, 0)
        if rank < hist_peak:
            times_at_peak = 1
        elif rank == hist_peak:
            times_at_peak = hist_times + 1
        else:
            times_at_peak = hist_times

        out_entries.append({
            "date": chart_date_str,
            "week_start": week_start_str,
            "rank": rank,
            "album_id": aid,
            "title": meta.title if meta else aid,
            "weekly_streams": weekly_streams,
            "units_am": units_am,
            "units_am_ts": units_am_ts,
            "units_am_overall": units_am_overall,
            "units_spotify": units_spotify,
            "units_charts": units_charts,
            "units_surplus": units_surplus,
            "total_units": total_units,
            "track_count": track_count,
            "prev_rank": pr,
            "change": change,
            "rank_change": pr - rank if pr is not None else None,
            "percentage_change": pct_change,
            "weeks_on_chart": weeks_on_chart,
            "peak_position": peak_position,
            "times_at_peak": times_at_peak,
        })

        points = round(total_units / 100_000, 1)
        snapshot_entries.append({
            "rank": rank,
            "album_id": aid,
            "title": meta.title if meta else aid,
            # image_url: champ attendu par le renderer (cover de l'album)
            "image_url": meta.cover_url if meta else None,
            "spotify_url": meta.spotify_url if meta else None,
            "weekly_streams": weekly_streams,
            "units_am": units_am,
            "units_am_ts": units_am_ts,
            "units_am_overall": units_am_overall,
            "units_spotify": units_spotify,
            "units_charts": units_charts,
            "units_surplus": units_surplus,
            "total_units": total_units,
            "track_count": track_count,
            # Champs attendus par swift_top_100_image.py
            "points": points,
            "points_display": _format_number(points),
            "units": _format_number(total_units),
            "units_surplus_display": _format_number(units_surplus),
            "am_ts_units_display": _format_number(units_am_ts),
            "am_global_units_display": _format_number(units_am_overall),
            "units_charts_display": _format_number(units_charts),
            "prev_rank": pr,
            "change": change,
            "rank_change": pr - rank if pr is not None else None,
            "percentage_change": pct_change,
            "weeks_on_chart": weeks_on_chart,
            "peak_position": peak_position,
            "times_at_peak": times_at_peak,
        })

    # Final sort + reassign ranks
    snapshot_entries.sort(key=lambda e: e.get("total_units") or 0, reverse=True)
    for i, e in enumerate(snapshot_entries, 1):
        e["rank"] = i

    # Recalculate rank_change after final sort
    for e in snapshot_entries:
        pr = e.get("prev_rank")
        curr_rank = e.get("rank")
        e["rank_change"] = pr - curr_rank if pr is not None and curr_rank is not None else None

    # Sync final rank + peak back to out_entries
    final_rank_by_album = {e["album_id"]: e["rank"] for e in snapshot_entries}
    for out in out_entries:
        aid = out["album_id"]
        final_rank = final_rank_by_album.get(aid)
        if final_rank is None:
            continue
        out["rank"] = final_rank
        prev_r = out.get("prev_rank")
        out["rank_change"] = (int(prev_r) - final_rank) if prev_r is not None else None
        hist_peak = peak_by_album.get(aid, 9999)
        hist_times = times_at_peak_by_album.get(aid, 0)
        out["peak_position"] = min(hist_peak, final_rank)
        if final_rank < hist_peak:
            out["times_at_peak"] = 1
        elif final_rank == hist_peak:
            out["times_at_peak"] = hist_times + 1
        else:
            out["times_at_peak"] = hist_times

    # Sync corrected peak/times back to snapshot
    out_by_album = {e["album_id"]: e for e in out_entries}
    for snap in snapshot_entries:
        out = out_by_album.get(snap["album_id"])
        if out:
            snap["peak_position"] = out["peak_position"]
            snap["times_at_peak"] = out["times_at_peak"]

    if dry_run:
        logger.log("⚠ DRY-RUN — no files written")
    else:
        combined_rows = _history_rows_without_date(existing_rows, chart_date_str) + out_entries
        combined_rows.sort(key=lambda r: (
            (r.get("date") or ""),
            int(r.get("rank") or 9999),
            r.get("album_id") or "",
        ))
        _write_history_csv(combined_rows, logger)

        payload = {
            "title": CHART_TITLE,
            "chart_date": chart_date_str,
            "week_start": week_start_str,
            "week_end": chart_date_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": snapshot_entries,
        }
        _write_snapshot_json(payload, logger)

        try:
            from swift_top_100_image import render_png
            out_path = _SITE_DATA_DIR / f"{CHART_SLUG}.png"
            render_png(
                payload=payload,
                output_path=out_path,
                columns=1,
                limit=len(snapshot_entries),
                offset=0,
                width=1400,
                scale=2,
            )
            logger.log(f"✔ PNG  → {out_path.name}")
            history_dir = _SCRIPT_DIR / "history" / chart_date_str
            history_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_path, history_dir / f"{CHART_SLUG}.png")
        except Exception as exc:
            logger.log(f"⚠ image          : generation failed — {exc}")

        _maybe_upload_to_r2(logger=logger, skip_r2=skip_r2)

        logs_dir = _SCRIPT_DIR / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        logger.save(str(logs_dir / f"{CHART_SLUG}_{chart_date_str}.log"))

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate TayBoard Albums weekly chart")
    p.add_argument("--date", dest="date", default=None, help="Week ending date (YYYY-MM-DD)")
    p.add_argument("--backfill", dest="backfill", action="store_true",
                   help="Generate all weeks available in swift_top_songs_history.csv")
    p.add_argument("--force", dest="force", action="store_true",
                   help="With --backfill: regenerate weeks that already have a snapshot")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Compute without writing any files")
    p.add_argument("--rebuild-index", dest="rebuild_index", action="store_true",
                   help="Rebuild swift_top_albums_index.json from existing snapshot files")
    p.add_argument("--skip-r2", dest="skip_r2", action="store_true",
                   help="Do not upload generated files to R2")
    p.add_argument("--variant", dest="variant", choices=["albums", "eras", "all"], default="albums",
                   help="Generate album chart, eras chart, or both")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.variant == "all":
        for variant in ("albums", "eras"):
            _configure_variant(variant)
            next_args = argparse.Namespace(**vars(args))
            next_args.variant = variant
            try:
                main_from_args(next_args)
            except SystemExit as exc:
                if exc.code not in (0, None):
                    raise
        raise SystemExit(0)

    _configure_variant(args.variant)
    main_from_args(args)


def main_from_args(args: argparse.Namespace) -> None:

    if args.rebuild_index:
        logger = Logger()
        _rebuild_snapshot_index(logger)
        raise SystemExit(0)

    song_rows = _load_song_history()
    if not song_rows:
        print(f"[swift_top_albums] No data found in {SWIFT_TOP_SONGS_HISTORY_CSV}")
        raise SystemExit(1)

    if args.backfill:
        all_dates = _all_chart_dates(song_rows)
        if not all_dates:
            print("[swift_top_albums] No dates found in song history.")
            raise SystemExit(1)
        print(f"[swift_top_albums] Backfill: {len(all_dates)} weeks found "
              f"({_format_date(all_dates[0])} -> {_format_date(all_dates[-1])})")
        for chart_date in all_dates:
            snapshot_path = _SITE_DATA_DIR / f"{CHART_SLUG}_{_format_date(chart_date)}.json"
            if snapshot_path.exists() and not args.force:
                print(f"[swift_top_albums] Skip {_format_date(chart_date)} (already exists, use --force)")
                continue
            print(f"[swift_top_albums] Generating {_format_date(chart_date)} ...")
            run(chart_date=chart_date, song_rows=song_rows, dry_run=bool(args.dry_run), skip_r2=True)
        print("[swift_top_albums] Backfill complete.")
        if not args.dry_run:
            _maybe_upload_to_r2(logger=Logger(), skip_r2=bool(args.skip_r2))
        raise SystemExit(0)

    chart_date = _parse_iso_date(args.date) if args.date else None
    code = run(chart_date=chart_date, song_rows=song_rows, dry_run=bool(args.dry_run), skip_r2=bool(args.skip_r2))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
