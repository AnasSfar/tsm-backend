from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from core.data_paths import DATA_ROOT


UPDATE_STREAMS_LOG_FILES = {
    "last_successful_updates.json",
    "last_unfinished_updates.json",
    "not_found_today.csv",
    "not_found_streak.json",
}


def _coerce_date(value: date | datetime | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _iter_day_dirs() -> list[tuple[date, Path]]:
    days: list[tuple[date, Path]] = []
    if not DATA_ROOT.exists():
        return days

    for year_dir in DATA_ROOT.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            for day_dir in month_dir.iterdir():
                if not day_dir.is_dir():
                    continue
                try:
                    day = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
                except ValueError:
                    continue
                days.append((day, day_dir))
    return days


SNAPSHOTS_ROOT = DATA_ROOT.parent / "snapshots"

# Generated PNGs (cards/tables already posted, also on X / R2) per snapshot tree,
# with how many dated folders back are kept (decision 2026-10-02: "on day J
# delete the J-2 images"). Folder date = data date, not posting date: streams
# for D and Apple Music for D are posted by D+1, so J and J-1 are kept. Spotify
# Charts publishes D around midnight D+1 and its posts can run into D+2, so one
# more day is kept there. Only *.png — json/csv/locks are never touched.
# (Before 2026-10-02 retention only looked at the legacy data/YYYY/... tree,
# so nothing under snapshots/ was ever pruned: ~4 GB of PNGs piled up.)
SNAPSHOT_IMAGE_KEEP_DAYS = {
    "spotify_streams": 1,
    "apple_music_charts": 1,
    "spotify_charts": 2,
}


def _iter_snapshot_day_dirs(root: Path) -> list[tuple[date, Path]]:
    days: list[tuple[date, Path]] = []
    if not root.exists():
        return days
    for year_dir in root.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            for day_dir in month_dir.iterdir():
                if not day_dir.is_dir():
                    continue
                try:
                    days.append((datetime.strptime(day_dir.name, "%Y-%m-%d").date(), day_dir))
                except ValueError:
                    continue
    return days


def cleanup_snapshot_images(
    *,
    today: date | datetime | str | None = None,
    keep_days: dict[str, int] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Delete generated PNGs under snapshots/<tree>/YYYY/MM/YYYY-MM-DD/ once the
    folder date is older than today - keep_days[tree]."""
    current_day = _coerce_date(today)
    counts: dict[str, int] = {}
    freed = 0
    for tree, keep in (keep_days or SNAPSHOT_IMAGE_KEEP_DAYS).items():
        cutoff = current_day - timedelta(days=keep)
        n = 0
        for day, day_dir in _iter_snapshot_day_dirs(SNAPSHOTS_ROOT / tree):
            if day >= cutoff:
                continue
            for png_path in day_dir.rglob("*.png"):
                size = png_path.stat().st_size if png_path.is_file() else 0
                if _delete_file(png_path, dry_run=dry_run):
                    n += 1
                    freed += size
        counts[tree] = n
    mode = "would delete" if dry_run else "deleted"
    detail = ", ".join(f"{tree} {n}" for tree, n in counts.items())
    print(f"[retention] snapshots PNG {mode}: {detail} ({freed / 1e6:.0f} MB)")
    return counts


# Transparent Windows (WOF/LZX) compression of past snapshot days (2026-10-02).
# Files stay plain .csv for every reader (no code change anywhere), bytes read
# back are identical, read speed unchanged; Apple Music intraday CSVs shrink
# ~7.7x (1.7 GB -> ~220 MB). Writing a file silently stores it uncompressed
# again, so today/yesterday (still rewritten every 2 h) are skipped and a
# rolling window is re-compressed each run (already-compressed files cost ~0).
SNAPSHOT_COMPRESS_TREES = ("apple_music_charts",)
SNAPSHOT_COMPRESS_MIN_AGE_DAYS = 2
SNAPSHOT_COMPRESS_WINDOW_DAYS = 14


def compress_snapshot_days(
    *,
    today: date | datetime | str | None = None,
    all_days: bool = False,
    dry_run: bool = False,
) -> int:
    """`compact /c /exe:lzx` on snapshots/<tree>/YYYY/MM/YYYY-MM-DD/ folders aged
    MIN_AGE..WINDOW days (or every past folder with all_days). Windows only;
    no-op elsewhere. Returns the number of day folders processed."""
    import os
    import subprocess

    if os.name != "nt":
        return 0
    current_day = _coerce_date(today)
    newest = current_day - timedelta(days=SNAPSHOT_COMPRESS_MIN_AGE_DAYS)
    oldest = current_day - timedelta(days=SNAPSHOT_COMPRESS_WINDOW_DAYS)
    done = 0
    for tree in SNAPSHOT_COMPRESS_TREES:
        for day, day_dir in _iter_snapshot_day_dirs(SNAPSHOTS_ROOT / tree):
            if day > newest or (not all_days and day < oldest):
                continue
            done += 1
            if dry_run:
                continue
            subprocess.run(
                ["compact", "/c", "/exe:lzx", f"/s:{day_dir}", "/q"],
                capture_output=True,
                check=False,
            )
    mode = "would compress" if dry_run else "compressed"
    print(f"[retention] snapshots LZX {mode}: {done} day folder(s) ({', '.join(SNAPSHOT_COMPRESS_TREES)})")
    return done


def _delete_file(path: Path, *, dry_run: bool) -> bool:
    if not path.is_file():
        return False
    if not dry_run:
        path.unlink()
    return True


def cleanup_generated_artifacts(
    *,
    today: date | datetime | str | None = None,
    image_days: int = 3,
    update_log_days: int = 7,
    dry_run: bool = False,
) -> dict[str, int]:
    """Delete generated daily artifacts after their retention window.

    Only dated output folders under data/YYYY/MM/YYYY-MM-DD are touched, so
    static headers, logos, and shared assets outside the daily data tree remain.
    """

    current_day = _coerce_date(today)
    image_cutoff = current_day - timedelta(days=image_days)
    update_log_cutoff = current_day - timedelta(days=update_log_days)
    counts = {"chart_images": 0, "stream_images": 0, "update_logs": 0}

    for day, day_dir in _iter_day_dirs():
        if day < image_cutoff:
            charts_dir = day_dir / "run_all_charts"
            if charts_dir.exists():
                for png_path in charts_dir.rglob("*.png"):
                    if _delete_file(png_path, dry_run=dry_run):
                        counts["chart_images"] += 1

            streams_dir = day_dir / "update_streams"
            if streams_dir.exists():
                for png_path in streams_dir.rglob("*.png"):
                    if _delete_file(png_path, dry_run=dry_run):
                        counts["stream_images"] += 1

        if day < update_log_cutoff:
            streams_dir = day_dir / "update_streams"
            if streams_dir.exists():
                for name in UPDATE_STREAMS_LOG_FILES:
                    if _delete_file(streams_dir / name, dry_run=dry_run):
                        counts["update_logs"] += 1

    mode = "would delete" if dry_run else "deleted"
    print(
        "[retention] "
        f"{mode}: {counts['chart_images']} chart image(s), "
        f"{counts['stream_images']} stream image(s), "
        f"{counts['update_logs']} update log file(s)"
    )
    try:
        counts.update(cleanup_snapshot_images(today=current_day, dry_run=dry_run))
    except Exception as exc:  # retention is best-effort, never fails a run
        print(f"[retention] snapshots PNG cleanup failed: {exc!r}")
    try:
        counts["compressed_days"] = compress_snapshot_days(today=current_day, dry_run=dry_run)
    except Exception as exc:
        print(f"[retention] snapshots LZX compression failed: {exc!r}")
    return counts
