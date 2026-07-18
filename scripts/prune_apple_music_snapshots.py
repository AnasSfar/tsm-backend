#!/usr/bin/env python3
"""
Prune intraday Apple Music snapshots: for every day BEFORE today, keep only the
last snapshot (max scraped_at) of each daily CSV. Reruns within a day are only
useful the day itself; after that they inflate disk, export time and
applemusic_history.json for no analytical value (the published chart day is the
last snapshot).

Dry-run by default — prints what would be removed. Use --apply to write.

    python scripts/prune_apple_music_snapshots.py                 # report only
    python scripts/prune_apple_music_snapshots.py --apply         # rewrite files
    python scripts/prune_apple_music_snapshots.py --since 2026-01-01
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = ROOT / "snapshots" / "apple_music_charts"
ARCHIVE_ROOT = SNAPSHOT_ROOT / "_pruned_archive"

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keep only the last snapshot per day in Apple Music daily CSVs.")
    parser.add_argument("--apply", action="store_true", help="Rewrite the files (default: dry-run report).")
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD", help="Only scan days >= this date.")
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip writing removed rows to snapshots/apple_music_charts/_pruned_archive/*.csv.gz.",
    )
    return parser.parse_args()


def snapshot_key(row: dict) -> str:
    return row.get("scraped_at") or row.get("date", "")


def serialize(fieldnames: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def archive_removed(path: Path, day: str, fieldnames: list[str], removed_rows: list[dict]) -> Path:
    """Write removed intraday rows to a gzip CSV so nothing is lost locally."""
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_ROOT / f"{day}_{path.stem}.csv.gz"
    with gzip.open(archive_path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(removed_rows)
    return archive_path


def prune_file(path: Path, day: str, apply: bool, archive: bool) -> tuple[int, int, int]:
    """Returns (rows_removed, bytes_before, bytes_after); (0, size, size) if untouched."""
    size_before = path.stat().st_size
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if not fieldnames or not rows:
        return 0, size_before, size_before

    keys = {snapshot_key(r) for r in rows if snapshot_key(r)}
    if len(keys) <= 1:
        return 0, size_before, size_before

    keep = max(keys)
    kept_rows = [r for r in rows if snapshot_key(r) == keep]
    removed_rows = [r for r in rows if snapshot_key(r) != keep]
    content = serialize(fieldnames, kept_rows)
    size_after = len(content.encode("utf-8"))

    if apply:
        if archive:
            archive_removed(path, day, fieldnames, removed_rows)
        path.write_text(content, encoding="utf-8", newline="")

    return len(removed_rows), size_before, size_after


def main() -> None:
    args = parse_args()
    today = date.today().isoformat()

    if not SNAPSHOT_ROOT.exists():
        print(f"[prune] snapshot root not found: {SNAPSHOT_ROOT}")
        sys.exit(1)

    day_dirs = sorted(
        d for d in SNAPSHOT_ROOT.glob("20??/??/????-??-??")
        if d.is_dir() and _DAY_RE.match(d.name) and d.name < today
        and (args.since is None or d.name >= args.since)
    )

    total_removed = 0
    total_before = 0
    total_after = 0
    touched_files = 0

    for day_dir in day_dirs:
        for path in sorted(day_dir.glob("*.csv")):
            removed, before, after = prune_file(path, day_dir.name, apply=args.apply, archive=not args.no_archive)
            total_before += before
            total_after += after
            if removed:
                touched_files += 1
                total_removed += removed
                print(
                    f"[{'pruned' if args.apply else 'would prune'}] "
                    f"{path.relative_to(SNAPSHOT_ROOT)}: -{removed} rows "
                    f"({before / 1024:.0f} Ko -> {after / 1024:.0f} Ko)"
                )

    saved_mb = (total_before - total_after) / (1024 * 1024)
    print()
    print(f"[prune] days scanned: {len(day_dirs)} (days before {today})")
    print(f"[prune] files with intraday snapshots: {touched_files}")
    print(f"[prune] rows removed: {total_removed}")
    print(f"[prune] space saved: {saved_mb:.1f} Mo")
    if args.apply and not args.no_archive and touched_files:
        archive_size = sum(f.stat().st_size for f in ARCHIVE_ROOT.glob("*.csv.gz"))
        print(f"[prune] removed rows archived in {ARCHIVE_ROOT} ({archive_size / (1024 * 1024):.1f} Mo gzip)")
    if not args.apply:
        print("[prune] DRY-RUN — nothing written. Re-run with --apply to prune (removed rows are archived unless --no-archive).")


if __name__ == "__main__":
    main()
