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
    return counts
