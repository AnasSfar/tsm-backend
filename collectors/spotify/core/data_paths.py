from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"


def date_key(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()


def day_root(value: date | datetime | str) -> Path:
    key = date_key(value)
    return DATA_ROOT / key[:4] / key[5:7] / key


def run_all_charts_root(value: date | datetime | str) -> Path:
    return day_root(value) / "run_all_charts"


def spotify_chart_dir(chart_name: str, value: date | datetime | str) -> Path:
    return run_all_charts_root(value) / "spotify" / chart_name


def update_streams_dir(value: date | datetime | str) -> Path:
    return day_root(value) / "update_streams"


def legacy_spotify_chart_dir(chart_name: str, value: date | datetime | str) -> Path:
    key = date_key(value)
    return (
        REPO_ROOT
        / "collectors"
        / "spotify"
        / "charts"
        / chart_name
        / "history"
        / key[:4]
        / key[5:7]
        / key
    )


def first_existing(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]
