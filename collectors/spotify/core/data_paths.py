from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
DB_ROOT = REPO_ROOT / "db"
ARCHIVE_ROOT = DATA_ROOT / "_archive" / "original"
ARCHIVE_DB_ROOT = ARCHIVE_ROOT / "db"
SNAPSHOTS_ROOT = REPO_ROOT / "snapshots"
RUNTIME_ROOT = REPO_ROOT / "runtime"
EXPORTS_ROOT = RUNTIME_ROOT / "exports"
WEB_EXPORT_ROOT = EXPORTS_ROOT / "web"
WEB_EXPORT_DATA_DIR = WEB_EXPORT_ROOT / "site" / "data"
WEB_EXPORT_HISTORY_DIR = WEB_EXPORT_ROOT / "site" / "history"
RUNTIME_SOCIAL_ROOT = RUNTIME_ROOT / "social"
RUNTIME_LOGS_ROOT = RUNTIME_ROOT / "logs"
RUNTIME_CACHE_ROOT = RUNTIME_ROOT / "cache"
LEGACY_WEBSITE_ROOT = REPO_ROOT / "website"
LEGACY_WEBSITE_DATA_DIR = LEGACY_WEBSITE_ROOT / "site" / "data"
LEGACY_WEBSITE_HISTORY_DIR = LEGACY_WEBSITE_ROOT / "site" / "history"
LEGACY_WEBSITE_RUNTIME_DIR = LEGACY_WEBSITE_ROOT / "data"
MANUAL_BACKUPS_ROOT = DATA_ROOT / "_archive" / "manual-backups"
LEGACY_WEBSITE_ARCHIVE_ROOT = DATA_ROOT / "_archive" / "legacy-website"
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


def update_streams_social_dir(value: date | datetime | str) -> Path:
    return RUNTIME_SOCIAL_ROOT / "spotify_streams" / date_key(value)


def collector_data_dir(collector: str, value: date | datetime | str) -> Path:
    return snapshot_day_root(collector, value)


def apple_music_charts_dir(value: date | datetime | str) -> Path:
    return snapshot_day_root("apple_music_charts", value)


def tayboard_dir(value: date | datetime | str) -> Path:
    return snapshot_day_root("tayboard", value)


def archived_db_file(filename: str) -> Path:
    return ARCHIVE_DB_ROOT / filename


def db_file(filename: str) -> Path:
    return DB_ROOT / filename


def db_history_candidates(filename: str) -> list[Path]:
    return [DB_ROOT / filename, ARCHIVE_DB_ROOT / filename]


def first_existing_db_history(filename: str) -> Path:
    return first_existing(*db_history_candidates(filename))


def apple_music_daily_csv(value: date | datetime | str, filename: str) -> Path:
    return apple_music_charts_dir(value) / filename


def legacy_apple_music_daily_csv(value: date | datetime | str, filename: str) -> Path:
    key = date_key(value)
    return DATA_ROOT / key[:4] / key[5:7] / key / "apple_music" / filename


def apple_music_daily_csv_paths(filename: str) -> list[Path]:
    paths = [
        p for p in sorted(APPLE_MUSIC_CHARTS_SNAPSHOT_ROOT.glob(f"20??/??/????-??-??/{filename}"))
        if p.is_file()
    ]
    paths.extend(
        p for p in sorted(DATA_ROOT.glob(f"20??/??/????-??-??/apple_music/{filename}"))
        if p.is_file()
    )
    return paths


def spotify_chart_snapshot_candidates(chart_name: str, value: date | datetime | str, filename: str) -> list[Path]:
    return [
        spotify_chart_dir(chart_name, value) / filename,
        legacy_spotify_chart_dir(chart_name, value) / filename,
        legacy_run_all_charts_dir(chart_name, value) / filename,
    ]


def spotify_chart_snapshot_files(chart_name: str, pattern: str) -> list[Path]:
    paths: list[Path] = []
    snapshot_root = SPOTIFY_CHARTS_SNAPSHOT_ROOT
    if snapshot_root.exists():
        paths.extend(sorted(snapshot_root.glob(f"20??/??/????-??-??/{chart_name}/{pattern}")))
    legacy_history_root = REPO_ROOT / "collectors" / "spotify" / "charts" / chart_name / "history"
    if legacy_history_root.exists():
        paths.extend(sorted(legacy_history_root.glob(f"20??/??/????-??-??/{pattern}")))
    paths.extend(sorted(DATA_ROOT.glob(f"20??/??/????-??-??/run_all_charts/spotify/{chart_name}/{pattern}")))
    return [p for p in paths if p.is_file()]


def billboard_snapshot_dir(value: date | datetime | str) -> Path:
    return snapshot_day_root("billboard", value)


def legacy_billboard_snapshot_dir(value: date | datetime | str) -> Path:
    key = date_key(value)
    return DATA_ROOT / key[:4] / key[5:7] / key / "billboard"


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


def legacy_run_all_charts_dir(chart_name: str, value: date | datetime | str) -> Path:
    key = date_key(value)
    return DATA_ROOT / key[:4] / key[5:7] / key / "run_all_charts" / "spotify" / chart_name


def legacy_update_streams_dir(value: date | datetime | str) -> Path:
    key = date_key(value)
    return DATA_ROOT / key[:4] / key[5:7] / key / "update_streams"


def first_existing(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]
