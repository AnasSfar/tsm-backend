"""TayBoard Albums — weekly album chart.

Source des données : db/swift_top_100_history.csv (weekly_streams déjà agrégés
par chanson). Les streams sont groupés par album via la discographie.

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
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
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

SWIFT_TOP_100_HISTORY_CSV = _DB_DIR / "swift_top_100_history.csv"
SWIFT_TOP_ALBUMS_HISTORY_CSV = _DB_DIR / "swift_top_albums_history.csv"

DISCOGRAPHY_DIR = _DB_DIR / "discography"
ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
COVERS_JSON = DISCOGRAPHY_DIR / "covers.json"

OUTPUT_JSON = _SITE_DATA_DIR / "swift_top_albums.json"

_TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")
_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


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
                tid = _extract_track_id(
                    (track.get("url") or track.get("spotify_url") or "").strip()
                )
                if tid:
                    albums[album_id]["track_ids"].add(tid)

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


# ---------------------------------------------------------------------------
# Song history source
# ---------------------------------------------------------------------------

def _load_song_history() -> list[dict]:
    if not SWIFT_TOP_100_HISTORY_CSV.exists():
        return []
    with SWIFT_TOP_100_HISTORY_CSV.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _all_chart_dates(song_rows: list[dict]) -> list[date]:
    dates: set[date] = set()
    for row in song_rows:
        d = _parse_iso_date(row.get("date") or "")
        if d:
            dates.add(d)
    return sorted(dates)


def _latest_chart_date(song_rows: list[dict]) -> date | None:
    dates = _all_chart_dates(song_rows)
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
    logger.log(f"  source         : {len(week_rows)} songs for {chart_date}")

    album_streams: dict[str, int] = {}
    album_track_count: dict[str, int] = {}

    def _to_int(v: str | None) -> int:
        try:
            return int((v or "").strip())
        except Exception:
            return 0

    unmatched = 0
    for row in week_rows:
        tid = (row.get("track_id") or "").strip()
        streams = _to_int(row.get("weekly_streams"))
        if not tid or streams <= 0:
            continue
        album = track_to_album.get(tid)
        if not album:
            unmatched += 1
            continue
        album_streams[album.album_id] = album_streams.get(album.album_id, 0) + streams
        album_track_count[album.album_id] = album_track_count.get(album.album_id, 0) + 1

    if unmatched:
        logger.log(f"  unmatched      : {unmatched} tracks not linked to any album")

    scored = sorted(album_streams.items(), key=lambda kv: kv[1], reverse=True)
    points_by_album = {
        aid: {"album_id": aid, "weekly_streams": streams, "track_count": album_track_count.get(aid, 0)}
        for aid, streams in scored
    }
    rank_by_album = {aid: i for i, (aid, _) in enumerate(scored, 1)}

    logger.log(f"  top            : {len(scored)} albums ranked")
    return points_by_album, rank_by_album


# ---------------------------------------------------------------------------
# Album history CSV
# ---------------------------------------------------------------------------

def _load_existing_history_before_date(chart_date: str, logger: Logger) -> list[dict]:
    if not SWIFT_TOP_ALBUMS_HISTORY_CSV.exists():
        return []
    try:
        with SWIFT_TOP_ALBUMS_HISTORY_CSV.open("r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:
        logger.log(f"⚠ history        : failed to read CSV — {exc}")
        return []
    return [r for r in rows if (r.get("date") or "").strip() < chart_date]


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
    dates = [p.stem[len("swift_top_albums_"):] for p in _SITE_DATA_DIR.glob("swift_top_albums_????-??-??.json")]
    dates.sort(reverse=True)
    index_path = _SITE_DATA_DIR / "swift_top_albums_index.json"
    index_path.write_text(json.dumps(dates, ensure_ascii=False), encoding="utf-8")
    logger.log(f"✔ IDX  → {index_path.name} ({len(dates)} dates)")


def _write_snapshot_json(payload: dict, logger: Logger) -> None:
    _SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    chart_date = payload.get("chart_date")
    if chart_date:
        dated = _SITE_DATA_DIR / f"swift_top_albums_{chart_date}.json"
        dated.write_text(content, encoding="utf-8")
        logger.log(f"✔ JSON → {dated.name}")
    OUTPUT_JSON.write_text(content, encoding="utf-8")
    logger.log(f"✔ JSON → {OUTPUT_JSON.name} (latest)")
    _rebuild_snapshot_index(logger)


# ---------------------------------------------------------------------------
# R2 upload
# ---------------------------------------------------------------------------

def _maybe_upload_to_r2(*, logger: Logger) -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_REPO_ROOT / ".env", override=True)
    except Exception:
        pass

    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        logger.log("  r2             : skipped (UPLOAD_TO_R2=0)")
        return

    required_env = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    missing = [n for n in required_env if not os.getenv(n, "").strip()]
    if missing:
        logger.log("  r2             : skipped — missing env: " + ", ".join(missing))
        return

    r2_script = _REPO_ROOT / "scripts" / "r2.py"
    if not r2_script.exists():
        logger.log("⚠ r2             : script not found")
        return

    logger.log("  r2             : uploading...")
    try:
        completed = subprocess.run([sys.executable, str(r2_script)], cwd=str(_REPO_ROOT), check=False)
        if completed.returncode == 0:
            logger.log("✔ r2             : upload complete")
        else:
            logger.log(f"⚠ r2             : upload failed (exit code {completed.returncode})")
    except Exception as exc:
        logger.log(f"⚠ r2             : upload failed — {exc}")


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(*, chart_date: date | None, song_rows: list[dict], dry_run: bool) -> int:
    logger = Logger()

    if chart_date is None:
        chart_date = _latest_chart_date(song_rows)

    if chart_date is None:
        logger.log(f"⚠ no dates found in {SWIFT_TOP_100_HISTORY_CSV.name}")
        return 2

    chart_date_str = _format_date(chart_date)
    prev_date_str = _format_date(chart_date - timedelta(days=7))

    # week_start from song history (first row for this date)
    week_start_str = next(
        (r.get("week_start") or "" for r in song_rows if (r.get("date") or "").strip() == chart_date_str),
        "",
    )
    if not week_start_str:
        week_start_str = _format_date(chart_date - timedelta(days=6))

    logger.log(f"▶ TayBoard Albums · {chart_date_str}  week={week_start_str}→{chart_date_str}  prev={prev_date_str}")

    albums = _iter_discography_albums()
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

    existing_rows = _load_existing_history_before_date(chart_date_str, logger)
    weeks_on_chart_by_album, peak_by_album, times_at_peak_by_album = _history_stats(existing_rows)

    is_first_run = len(existing_rows) == 0
    if is_first_run:
        logger.log("  history        : first run — all entries NEW")
        prev_scored: dict = {}
        prev_ranks: dict = {}
    else:
        prior_weeks = len({(r.get("date") or "").strip() for r in existing_rows if r.get("date")})
        logger.log(f"  history        : {prior_weeks} prior week{'s' if prior_weeks != 1 else ''} loaded")
        prev_scored, prev_ranks = _build_album_week(
            chart_date=prev_date_str,
            song_rows=song_rows,
            track_to_album=track_to_album,
            logger=logger,
        )

    out_entries: list[dict] = []
    snapshot_entries: list[dict] = []

    for aid, rank in sorted(curr_ranks.items(), key=lambda kv: kv[1]):
        row = curr_scored[aid]
        meta = albums_by_id.get(aid)

        pr = prev_ranks.get(aid)
        prev_streams_val = (prev_scored.get(aid) or {}).get("weekly_streams")

        if pr is None:
            change = "RE" if weeks_on_chart_by_album.get(aid, 0) > 0 else "NEW"
        else:
            change = None

        weekly_streams = row["weekly_streams"]
        track_count = row["track_count"]

        pct_change = None
        if pr is not None and prev_streams_val and prev_streams_val > 0:
            pct_change = round(((weekly_streams - prev_streams_val) / prev_streams_val) * 100, 1)

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
            "track_count": track_count,
            "prev_rank": pr,
            "change": change,
            "rank_change": pr - rank if pr is not None else None,
            "percentage_change": pct_change,
            "weeks_on_chart": weeks_on_chart,
            "peak_position": peak_position,
            "times_at_peak": times_at_peak,
        })

        points = round(weekly_streams / 30_000, 2)
        snapshot_entries.append({
            "rank": rank,
            "album_id": aid,
            "title": meta.title if meta else aid,
            # image_url: champ attendu par le renderer (cover de l'album)
            "image_url": meta.cover_url if meta else None,
            "spotify_url": meta.spotify_url if meta else None,
            "weekly_streams": weekly_streams,
            "track_count": track_count,
            # Champs attendus par swift_top_100_image.py
            "points": points,
            "points_display": _format_number(points),
            "units": _format_number(weekly_streams),
            "units_surplus_display": _format_number(weekly_streams),  # colonne STREAMS
            "am_ts_units_display": "—",
            "am_global_units_display": "—",
            "units_charts_display": "—",
            "prev_rank": pr,
            "change": change,
            "rank_change": pr - rank if pr is not None else None,
            "percentage_change": pct_change,
            "weeks_on_chart": weeks_on_chart,
            "peak_position": peak_position,
            "times_at_peak": times_at_peak,
        })

    # Final sort + reassign ranks
    snapshot_entries.sort(key=lambda e: e.get("weekly_streams") or 0, reverse=True)
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
        combined_rows = existing_rows + out_entries
        combined_rows.sort(key=lambda r: (
            (r.get("date") or ""),
            int(r.get("rank") or 9999),
            r.get("album_id") or "",
        ))
        _write_history_csv(combined_rows, logger)

        payload = {
            "title": "TayBoard Albums",
            "chart_date": chart_date_str,
            "week_start": week_start_str,
            "week_end": chart_date_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": snapshot_entries,
        }
        _write_snapshot_json(payload, logger)

        try:
            from swift_top_100_image import render_png
            out_path = _SITE_DATA_DIR / "swift_top_albums.png"
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
            shutil.copy2(out_path, history_dir / "swift_top_albums.png")
        except Exception as exc:
            logger.log(f"⚠ image          : generation failed — {exc}")

        _maybe_upload_to_r2(logger=logger)

    logs_dir = _SCRIPT_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger.save(str(logs_dir / f"swift_top_albums_{chart_date_str}.log"))

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate TayBoard Albums weekly chart")
    p.add_argument("--date", dest="date", default=None, help="Week ending date (YYYY-MM-DD)")
    p.add_argument("--backfill", dest="backfill", action="store_true",
                   help="Generate all weeks available in swift_top_100_history.csv")
    p.add_argument("--force", dest="force", action="store_true",
                   help="With --backfill: regenerate weeks that already have a snapshot")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Compute without writing any files")
    p.add_argument("--rebuild-index", dest="rebuild_index", action="store_true",
                   help="Rebuild swift_top_albums_index.json from existing snapshot files")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.rebuild_index:
        logger = Logger()
        _rebuild_snapshot_index(logger)
        raise SystemExit(0)

    song_rows = _load_song_history()
    if not song_rows:
        print(f"[swift_top_albums] No data found in {SWIFT_TOP_100_HISTORY_CSV}")
        raise SystemExit(1)

    if args.backfill:
        all_dates = _all_chart_dates(song_rows)
        if not all_dates:
            print("[swift_top_albums] No dates found in song history.")
            raise SystemExit(1)
        print(f"[swift_top_albums] Backfill: {len(all_dates)} weeks found "
              f"({_format_date(all_dates[0])} → {_format_date(all_dates[-1])})")
        for chart_date in all_dates:
            snapshot_path = _SITE_DATA_DIR / f"swift_top_albums_{_format_date(chart_date)}.json"
            if snapshot_path.exists() and not args.force:
                print(f"[swift_top_albums] Skip {_format_date(chart_date)} (already exists, use --force)")
                continue
            print(f"[swift_top_albums] Generating {_format_date(chart_date)} ...")
            run(chart_date=chart_date, song_rows=song_rows, dry_run=bool(args.dry_run))
        print("[swift_top_albums] Backfill complete.")
        raise SystemExit(0)

    chart_date = _parse_iso_date(args.date) if args.date else None
    code = run(chart_date=chart_date, song_rows=song_rows, dry_run=bool(args.dry_run))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
