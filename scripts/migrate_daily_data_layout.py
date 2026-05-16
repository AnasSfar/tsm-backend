#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
ARCHIVE_ROOT = DATA_ROOT / "_archive" / "original"


def day_root(day: str) -> Path:
    return DATA_ROOT / day[:4] / day[5:7] / day


def is_day(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def archive_path(src: Path) -> Path:
    return ARCHIVE_ROOT / src.relative_to(REPO_ROOT)


def remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    stop = stop.resolve()
    while current.resolve() != stop and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def transfer_file(src: Path, dst: Path, *, apply: bool, move: bool) -> None:
    action = "MOVE" if move else "COPY"
    print(f"{action if apply else 'WOULD ' + action} {src} -> {dst}")
    if not apply:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if src.stat().st_size == dst.stat().st_size:
            if move:
                src.unlink()
            return
        raise FileExistsError(f"Target exists with different size: {dst}")
    if move:
        shutil.move(str(src), str(dst))
    else:
        shutil.copy2(src, dst)


def copytree_contents(src: Path, dst: Path, *, apply: bool, move: bool = False) -> int:
    if not src.exists():
        return 0
    files = [p for p in src.rglob("*") if p.is_file()]
    for file in files:
        rel = file.relative_to(src)
        transfer_file(file, dst / rel, apply=apply, move=move)
    if apply and move:
        remove_empty_parents(src, src.parents[2] if len(src.parents) > 2 else src.parent)
    return len(files)


def migrate_spotify_chart_history(chart_name: str, *, apply: bool, move: bool) -> int:
    src_root = REPO_ROOT / "collectors" / "spotify" / "charts" / chart_name / "history"
    if not src_root.exists():
        return 0
    moved = 0
    for day_dir in sorted(src_root.glob("*/*/????-??-??")):
        if not day_dir.is_dir():
            continue
        dst = day_root(day_dir.name) / "run_all_charts" / "spotify" / chart_name
        moved += copytree_contents(day_dir, dst, apply=apply, move=move)
    if apply and move:
        remove_empty_parents(src_root, src_root.parent)
    return moved


def migrate_flat_data_days(*, apply: bool, move: bool) -> int:
    if not DATA_ROOT.exists():
        return 0
    moved = 0
    flat_day_dirs = sorted(p for p in DATA_ROOT.iterdir() if p.is_dir() and is_day(p.name))
    for src in flat_day_dirs:
        dst = day_root(src.name)
        if src.resolve() == dst.resolve():
            continue
        print(f"{'MOVE' if apply else 'WOULD MOVE'} {src} -> {dst}")
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                for file in src.rglob("*"):
                    if file.is_file():
                        rel = file.relative_to(src)
                        transfer_file(file, dst / rel, apply=True, move=True)
                remove_empty_parents(src, DATA_ROOT)
            else:
                shutil.move(str(src), str(dst))
        moved += 1
    return moved


def migrate_dated_history_dirs(src_root: Path, dst_group: str, *, apply: bool, move: bool) -> int:
    if not src_root.exists():
        return 0
    moved = 0
    for day_dir in sorted(p for p in src_root.iterdir() if p.is_dir() and is_day(p.name)):
        moved += copytree_contents(day_dir, day_root(day_dir.name) / dst_group, apply=apply, move=move)
    if apply and move:
        remove_empty_parents(src_root, src_root.parent)
    return moved


def migrate_site_history(*, apply: bool, move: bool) -> int:
    src_root = REPO_ROOT / "website" / "site" / "history"
    if not src_root.exists():
        return 0
    count = 0
    for src in sorted(src_root.glob("????-??-??.json")):
        day = src.stem
        if not is_day(day):
            continue
        transfer_file(src, day_root(day) / "update_streams" / "site_history.json", apply=apply, move=move)
        count += 1
    if apply and move:
        remove_empty_parents(src_root, src_root.parent)
    return count


def split_csv_by_date(src: Path, dst_group: str, *, apply: bool, move: bool) -> int:
    if not src.exists():
        return 0
    with src.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "date" not in reader.fieldnames:
            print(f"SKIP {src} (no date column)")
            return 0
        rows_by_date: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in reader:
            day = (row.get("date") or "").strip()
            if day:
                rows_by_date[day].append(row)

    written = 0
    for day, rows in sorted(rows_by_date.items()):
        dst = day_root(day) / dst_group / src.name
        print(f"{'WRITE' if apply else 'WOULD WRITE'} {dst} ({len(rows)} row(s))")
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            with dst.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=reader.fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        written += 1
    if apply and move and written:
        dst_archive = archive_path(src)
        print(f"ARCHIVE {src} -> {dst_archive}")
        dst_archive.parent.mkdir(parents=True, exist_ok=True)
        if dst_archive.exists():
            raise FileExistsError(f"Archive target exists: {dst_archive}")
        shutil.move(str(src), str(dst_archive))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the new day-first data/ layout.")
    parser.add_argument("--apply", action="store_true", help="Actually write/copy files. Default is dry-run.")
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move source files after writing the new layout. CSV originals are archived under data/_archive/original/.",
    )
    args = parser.parse_args()
    if args.move and not args.apply:
        print("[INFO] --move without --apply is a dry-run move preview.")

    total = 0
    total += migrate_flat_data_days(apply=args.apply, move=True)

    for chart_name in ("artists_global", "global", "fr", "worldwide", "us", "uk"):
        total += migrate_spotify_chart_history(chart_name, apply=args.apply, move=args.move)

    total += migrate_dated_history_dirs(
        REPO_ROOT / "collectors" / "billboard" / "history",
        "billboard",
        apply=args.apply,
        move=args.move,
    )
    total += migrate_site_history(apply=args.apply, move=args.move)

    total += split_csv_by_date(REPO_ROOT / "db" / "streams_history.csv", "update_streams", apply=args.apply, move=args.move)
    total += split_csv_by_date(
        REPO_ROOT / "db" / "artist_monthly_listeners_history.csv",
        "update_streams",
        apply=args.apply,
        move=args.move,
    )

    for apple_csv in sorted((REPO_ROOT / "db").glob("apple_music*.csv")):
        total += split_csv_by_date(apple_csv, "apple_music", apply=args.apply, move=args.move)

    total += split_csv_by_date(REPO_ROOT / "db" / "billboard_history.csv", "billboard", apply=args.apply, move=args.move)

    mode = "moved" if args.apply and args.move else "applied" if args.apply else "dry-run"
    print(f"[OK] {mode}: {total} item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
