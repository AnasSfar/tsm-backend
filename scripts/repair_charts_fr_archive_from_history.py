#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Allow importing collectors/spotify/core utilities
sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))

from core.fmt import fmt_delta  # noqa: E402

ARCHIVE_COLUMNS = [
    "date",
    "song_name",
    "rank",
    "streams",
    "previous_rank",
    "peak_rank",
    "total_days",
    "streak",
    "movement",
]


def _to_int(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (ValueError, OverflowError):
        return None


def _history_dir_for_date(history_root: Path, chart_date: str) -> Path:
    return history_root / chart_date[:4] / chart_date[5:7] / chart_date


def _load_fr_history_rows(history_root: Path, chart_date: str) -> list[dict]:
    day_dir = _history_dir_for_date(history_root, chart_date)
    json_path = day_dir / f"ts_chart_{chart_date}.json"
    csv_path = day_dir / "ts_all_songs.csv"

    if json_path.exists():
        payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            raise RuntimeError(f"Invalid JSON payload in {json_path}")
        rows = payload
    elif csv_path.exists():
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        raise FileNotFoundError(f"No FR history file found for {chart_date} in {day_dir}")

    cleaned: list[dict] = []
    for row in rows:
        rank = _to_int(row.get("rank"))
        if rank is None:
            continue

        previous_rank = _to_int(row.get("previous_rank"))
        peak_rank = _to_int(row.get("peak_rank"))
        total_days = _to_int(row.get("total_days"))
        streak = _to_int(row.get("streak"))
        streams = _to_int(row.get("streams"))
        song_name = (row.get("track_name") or row.get("song_name") or "").strip()
        if not song_name:
            continue

        movement = fmt_delta(
            rank=rank,
            previous_rank=previous_rank,
            peak_rank=peak_rank,
            total_days=total_days,
        )

        cleaned.append(
            {
                "date": chart_date,
                "song_name": song_name,
                "rank": rank,
                "streams": streams or "",
                "previous_rank": previous_rank if previous_rank is not None else "",
                "peak_rank": peak_rank if peak_rank is not None else "",
                "total_days": total_days if total_days is not None else "",
                "streak": streak if streak is not None else "",
                "movement": movement or "",
            }
        )

    cleaned.sort(key=lambda r: (r.get("rank") or 9999, r.get("song_name") or ""))
    return cleaned


def _rewrite_archive_for_dates(
    archive_path: Path,
    replacements: dict[str, list[dict]],
    *,
    dry_run: bool,
) -> None:
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive CSV not found: {archive_path}")

    dates_to_replace = set(replacements.keys())
    tmp_path = archive_path.with_suffix(archive_path.suffix + ".tmp")

    kept = 0
    removed = 0

    with archive_path.open("r", encoding="utf-8", newline="") as src, tmp_path.open(
        "w", encoding="utf-8", newline=""
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=ARCHIVE_COLUMNS)
        writer.writeheader()

        for row in reader:
            row_date = (row.get("date") or "").strip()
            if row_date in dates_to_replace:
                removed += 1
                continue
            # Normalize to our canonical columns
            writer.writerow({col: (row.get(col) or "") for col in ARCHIVE_COLUMNS})
            kept += 1

        # Append replacements at the end
        for chart_date in sorted(dates_to_replace):
            for row in replacements[chart_date]:
                writer.writerow({col: (row.get(col) or "") for col in ARCHIVE_COLUMNS})

    if dry_run:
        tmp_path.unlink(missing_ok=True)
        print(
            f"[DRY-RUN] Would rewrite {archive_path}: kept={kept}, removed={removed}, appended={sum(len(v) for v in replacements.values())}"
        )
        return

    tmp_path.replace(archive_path)
    print(
        f"[OK] Rewrote {archive_path}: kept={kept}, removed={removed}, appended={sum(len(v) for v in replacements.values())}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Repair db/charts_history_fr.csv for one or more dates by rebuilding rows from the FR history files "
            "(collectors/spotify/charts/fr/history/YYYY/MM/YYYY-MM-DD/ts_chart_*.json or ts_all_songs.csv)."
        )
    )
    parser.add_argument("dates", nargs="+", help="One or more chart dates (YYYY-MM-DD)")
    parser.add_argument(
        "--archive",
        default=str(REPO_ROOT / "db" / "charts_history_fr.csv"),
        help="Path to the FR archive CSV (default: repo db/charts_history_fr.csv)",
    )
    parser.add_argument(
        "--history-root",
        default=str(REPO_ROOT / "collectors" / "spotify" / "charts" / "fr" / "history"),
        help="Root history directory for FR (default: collectors/spotify/charts/fr/history)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not modify files; print what would change")

    args = parser.parse_args()

    archive_path = Path(args.archive).resolve()
    history_root = Path(args.history_root).resolve()

    replacements: dict[str, list[dict]] = {}
    for chart_date in args.dates:
        rows = _load_fr_history_rows(history_root, chart_date)
        if not rows:
            raise RuntimeError(f"No rows found in history for date: {chart_date}")
        replacements[chart_date] = rows

    _rewrite_archive_for_dates(archive_path, replacements, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
