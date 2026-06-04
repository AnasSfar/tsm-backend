from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
ARCHIVE_ROOT = DATA_ROOT / "_archive" / "original"
SNAPSHOTS_ROOT = REPO_ROOT / "snapshots"
SPOTIFY_STREAMS_SNAPSHOT_ROOT = SNAPSHOTS_ROOT / "spotify_streams"
SPOTIFY_CHARTS_SNAPSHOT_ROOT = SNAPSHOTS_ROOT / "spotify_charts"
APPLE_MUSIC_CHARTS_SNAPSHOT_ROOT = SNAPSHOTS_ROOT / "apple_music_charts"
TAYBOARD_SNAPSHOT_ROOT = SNAPSHOTS_ROOT / "tayboard"


def date_key(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()


def day_root(value: date | datetime | str) -> Path:
    key = date_key(value)
    return SNAPSHOTS_ROOT / key[:4] / key[5:7] / key


def snapshot_day_root(snapshot_name: str, value: date | datetime | str) -> Path:
    key = date_key(value)
    return SNAPSHOTS_ROOT / snapshot_name / key[:4] / key[5:7] / key


def run_all_charts_root(value: date | datetime | str) -> Path:
    return snapshot_day_root("spotify_charts", value)


def spotify_chart_dir(chart_name: str, value: date | datetime | str) -> Path:
    return run_all_charts_root(value) / chart_name


def update_streams_dir(value: date | datetime | str) -> Path:
    return snapshot_day_root("spotify_streams", value)


def collector_data_dir(collector: str, value: date | datetime | str) -> Path:
    return snapshot_day_root(collector, value)


def apple_music_charts_dir(value: date | datetime | str) -> Path:
    return snapshot_day_root("apple_music_charts", value)


def tayboard_dir(value: date | datetime | str) -> Path:
    return snapshot_day_root("tayboard", value)


def archived_db_file(filename: str) -> Path:
    return ARCHIVE_ROOT / "db" / filename


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
