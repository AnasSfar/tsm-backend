#!/usr/bin/env python3
"""One-off: shift every YouTube history row's ``date`` back by one day.

Why: the collector fires at 06:05 Europe/Paris ≈ 00:05 America/New_York, i.e.
right at NY midnight. The ``viewCount`` delta between two consecutive runs
therefore covers the NY calendar day that just *ended*, but every row was
labelled with the run date (the day that just *started*) — an off-by-one on
the whole history. ``update_youtube.py`` was fixed to write ``run_date - 1``;
this script realigns the existing CSVs to match.

Only the first column (``date``) changes. ``snapshot_at`` (the real
measurement instant), ``daily_views``, ranks, everything else is untouched —
the delta values were always correct, only their label was wrong. Because
``date`` is the first, always-``YYYY-MM-DD`` field, each line is rewritten by
its 11-char prefix only, so the diff stays minimal.

Dry-run by default. Pass --apply to write. --expect-latest guards the starting
state so this cannot be run twice (it would shift -2 days).

    python scripts/shift_youtube_dates_back_one_day.py                       # preview
    python scripts/shift_youtube_dates_back_one_day.py --apply --expect-latest 2026-09-02
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSVS = [
    ROOT / "db" / "youtube_views_history.csv",
    ROOT / "db" / "youtube_title_history.csv",
]

_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2}),")


def _shift_line(line: str) -> str:
    m = _DATE_PREFIX.match(line)
    if not m:
        raise ValueError(f"line does not start with an ISO date + comma: {line[:40]!r}")
    shifted = (date.fromisoformat(m.group(1)) - timedelta(days=1)).isoformat()
    return f"{shifted},{line[m.end():]}"


def process(path: Path, *, apply: bool, expect_latest: str | None) -> bool:
    if not path.exists():
        print(f"[skip] {path.name} — not found")
        return True

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines:
        print(f"[skip] {path.name} — empty")
        return True

    header, body = lines[0], lines[1:]
    dates_before = sorted({_DATE_PREFIX.match(l).group(1) for l in body if _DATE_PREFIX.match(l)})
    non_matching = [i for i, l in enumerate(body, 2) if not _DATE_PREFIX.match(l)]
    if non_matching:
        print(f"[ERROR] {path.name}: {len(non_matching)} line(s) without a leading ISO date "
              f"(first at line {non_matching[0]}) — aborting, no file is safe to shift blindly")
        return False

    if expect_latest and dates_before and dates_before[-1] != expect_latest:
        print(f"[ERROR] {path.name}: latest date is {dates_before[-1]}, expected {expect_latest}. "
              f"Already shifted? Pass the real --expect-latest or bail.")
        return False

    shifted_body = [_shift_line(l) for l in body]
    dates_after = sorted({_DATE_PREFIX.match(l).group(1) for l in shifted_body})

    print(f"\n{path.name}")
    print(f"  rows            : {len(body)}  (unchanged)")
    print(f"  distinct dates  : {len(dates_before)} -> {len(dates_after)}")
    print(f"  range           : {dates_before[0]}…{dates_before[-1]}  ->  {dates_after[0]}…{dates_after[-1]}")
    print(f"  sample line     : {shifted_body[-1][:70]}…")

    if len(shifted_body) != len(body):
        print(f"[ERROR] {path.name}: row-count guard failed ({len(body)} -> {len(shifted_body)})")
        return False

    if not apply:
        print("  [dry-run] not written")
        return True

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_suffix(path.suffix + f".{stamp}.bak")
    backup.write_bytes(path.read_bytes())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(header + "".join(shifted_body), encoding="utf-8")
    tmp.replace(path)
    print(f"  [written] backup: {backup.name}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the shifted files (default: dry-run)")
    ap.add_argument("--expect-latest", metavar="YYYY-MM-DD",
                    help="refuse unless the current latest date equals this (guards against a double run)")
    args = ap.parse_args()

    ok = all(process(p, apply=args.apply, expect_latest=args.expect_latest) for p in CSVS)
    if not ok:
        print("\n[FAILED] one or more files were not processed — nothing partial was applied per file")
        return 1
    if not args.apply:
        print("\n[dry-run] re-run with --apply --expect-latest <current latest> to write")
    else:
        print("\n[done] now: re-upload to R2 (r2.upload_youtube()) and re-run generate_home_highlights.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
