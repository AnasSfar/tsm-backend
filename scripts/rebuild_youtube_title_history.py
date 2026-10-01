#!/usr/bin/env python3
"""rebuild_youtube_title_history.py — regenerate db/youtube_title_history.csv
from db/youtube_views_history.csv using the CURRENT title-matching logic.

Why: title_groups.py had matching bugs fixed on 2026-09-05 (album files with
dict-shaped JSON silently ignored, chart_extra noise stealing matches,
curly-quote apostrophes breaking matches) that changed the title_key computed
for many songs. Historical rows in youtube_title_history.csv were written
with the OLD (broken) keys, so "yesterday" and "today" no longer share a
title_key for those songs — the frontend shows them all as "NEW" even though
nothing actually changed. This rebuilds the whole file date-by-date with
today's fixed matching, so title_key is consistent across all of history.

Also regenerates every `source` split (title_groups.video_rows_by_source:
all/main/topic + videos/audios/extras, the YouTube page sections added
2026-09-27, + songs = videos + audios since 2026-09-30) for every historical date, using each row's `channel` field (blank
= "main", true historically since Topic wasn't tracked before 2026-09-05).

--sources videos,audios,... regenerates only those sources and keeps every
other source's rows byte-for-byte (e.g. leave `all`, read by TayBoard, alone
when only the page sections changed).

Dry-run by default: prints a diff summary, writes nothing. --apply to write
(backup created first).
"""
from __future__ import annotations

import argparse
import shutil
import time
from collections import defaultdict
from pathlib import Path

from collectors.youtube.core.config import (
    CSV_PATH,
    DISCOGRAPHY_SONGS_PATH,
    TITLE_CSV_FIELDNAMES,
    TITLE_HISTORY_PATH,
    VIDEO_CATEGORIES_PATH,
    VIDEO_GROUPS_PATH,
)
from collectors.youtube.core.csv_utils import read_csv_rows
from collectors.youtube.update_youtube import enrich_chart_rows
from collectors.youtube.core.title_groups import build_title_rows, video_rows_by_source


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the rebuilt file (default: dry-run)")
    parser.add_argument("--no-backup", action="store_true", help="Skip the .bak copy on --apply")
    parser.add_argument(
        "--sources",
        default="",
        help="Comma-separated sources to regenerate (default: all of them); other sources are kept as-is",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    video_rows = read_csv_rows(CSV_PATH)
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in video_rows:
        d = row.get("date")
        if d:
            by_date[d].append(row)
    dates = sorted(by_date.keys())
    print(f"[INFO] {len(dates)} dates, {len(video_rows)} video rows total")

    rebuilt: list[dict] = []  # accumulates all rebuilt rows so far, across dates
    title_key_changes = 0
    old_rows = read_csv_rows(TITLE_HISTORY_PATH)
    only_sources = {s.strip() for s in args.sources.split(",") if s.strip()}
    old_by_date_source: dict[tuple[str, str], dict[str, str]] = {}
    for row in old_rows:
        key = (row.get("date", ""), row.get("source") or "all")
        old_by_date_source.setdefault(key, {})[row.get("title_key", "")] = row.get("title", "")

    for date in dates:
        day_rows = by_date[date]
        sources = video_rows_by_source(
            day_rows,
            songs_path=DISCOGRAPHY_SONGS_PATH,
            manual_groups_path=VIDEO_GROUPS_PATH,
            categories_path=VIDEO_CATEGORIES_PATH,
        )
        for source_tag, source_video_rows in sources.items():
            if only_sources and source_tag not in only_sources:
                continue
            if not source_video_rows:
                continue
            variant_rows = build_title_rows(
                date=date,
                video_rows=source_video_rows,
                songs_path=DISCOGRAPHY_SONGS_PATH,
                manual_groups_path=VIDEO_GROUPS_PATH,
            )
            for r in variant_rows:
                r["source"] = source_tag
            variant_existing = [r for r in rebuilt if (r.get("source") or "all") == source_tag]
            variant_rows = enrich_chart_rows(
                variant_rows,
                existing_rows=variant_existing,
                target_date=date,
                key_field="title_key",
            )
            new_keys = {r["title_key"] for r in variant_rows}
            old_keys = set(old_by_date_source.get((date, source_tag), {}).keys())
            if old_keys and new_keys != old_keys:
                title_key_changes += 1
            rebuilt.extend(variant_rows)

    if only_sources:
        kept = [r for r in old_rows if (r.get("source") or "all") not in only_sources]
        print(f"[INFO] Keeping {len(kept)} rows of other sources untouched")
        rebuilt = sorted(kept + rebuilt, key=lambda r: r.get("date") or "")  # stable: kept rows first per date
    print(f"[INFO] {len(rebuilt)} rebuilt rows across {len(dates)} dates")
    print(f"[INFO] {title_key_changes} (date, source) pairs had a different title_key set than the old file")

    if not args.apply:
        print("[DRY-RUN] Nothing written. Re-run with --apply.")
        return 0

    if not args.no_backup:
        backup = TITLE_HISTORY_PATH.with_suffix(
            TITLE_HISTORY_PATH.suffix + f".rebuild-{time.strftime('%Y%m%d-%H%M%S')}.bak"
        )
        shutil.copy2(TITLE_HISTORY_PATH, backup)
        print(f"[INFO] Backup: {backup}")

    # Full rebuild, not a merge — write every row in one shot rather than the
    # per-date append write_title_history does (which would mean O(dates^2)
    # re-reads of a file we already hold entirely in memory).
    import csv

    with TITLE_HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TITLE_CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rebuilt)

    print(f"[APPLY] {TITLE_HISTORY_PATH} rebuilt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
