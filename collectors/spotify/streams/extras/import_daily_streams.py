"""
Import daily stream data from one or more pivot CSVs into a new merged history CSV.

CSV format expected:
  - Row 1: headers -> "Title", "2026/04/18", "2026/04/17", ...  (dates descending)
  - Other rows: song title + daily stream values (non-breaking space as thousands separator)

Strategy:
  - Uses the LATEST anchor per track (most recent = most reliable cumulative)
  - Walks backwards day by day from the anchor using source daily values
  - Source values take priority over existing streams_history.csv for overlapping dates
  - Tracks not found in source are kept unchanged from streams_history.csv
  - Output is written directly to streams_history.csv (in-place merge)

Usage:
  # Single file
  python collectors/spotify/streams/extras/import_daily_streams.py db/my_file.csv

  # Multiple files (later files override earlier for the same date/title)
  python collectors/spotify/streams/extras/import_daily_streams.py db/archive_2025.csv db/archive_2026.csv

  # Dry run (preview only)
  python collectors/spotify/streams/extras/import_daily_streams.py db/archive_2026.csv --dry-run
"""

import csv
import io
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Force UTF-8 output on Windows to handle non-ASCII filenames/titles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[4]
HISTORY_CSV = REPO_ROOT / "db" / "streams_history.csv"
OUTPUT_CSV  = REPO_ROOT / "db" / "streams_history.csv"
ALBUMS_DIR  = REPO_ROOT / "db" / "discography" / "albums"
SONGS_JSON  = REPO_ROOT / "db" / "discography" / "songs.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_track_id(url: str) -> str | None:
    if "/track/" not in url:
        return None
    return url.split("/track/")[1].split("?")[0]


# Abbreviations used in source spreadsheets -> canonical forms in discography
_ABBR_EXPANSIONS = {
    r"\(tv\)":   "(taylor's version)",
    r"\(ftv\)":  "(from the vault)",
    r"\btv\b":   "taylor's version",
    r"\bftv\b":  "from the vault",
    r"\(tlpss\)": "",  # The Long Pond Studio Sessions — drop, title alone should match
    r"\btlpss\b": "",
}


def expand_abbrs(t: str) -> str:
    """Expand spreadsheet abbreviations to canonical discography forms."""
    t = t.lower().strip()
    for pattern, replacement in _ABBR_EXPANSIONS.items():
        t = re.sub(pattern, replacement, t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_title(t: str) -> str:
    t = t.lower().strip()
    t = t.replace("(", " ").replace(")", " ")
    t = re.sub(r"\s*-\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_aggressive(t: str) -> str:
    """Strip all punctuation/symbols, keep only letters, digits and spaces."""
    t = t.lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)   # remove all non-word chars (keeps letters/digits/_)
    t = t.replace("_", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_no_apostrophe(t: str) -> str:
    """Like normalize_title but removes apostrophes instead of keeping them."""
    t = t.lower().strip()
    t = t.replace("'", "").replace("’", "").replace("‘", "")
    t = t.replace("(", " ").replace(")", " ")
    t = re.sub(r"\s*-\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_int(val: str) -> int | None:
    """Parse a stream value that may use non-breaking space or comma as thousands sep."""
    val = val.strip().replace("\xa0", "").replace("\u202f", "").replace(" ", "").replace(",", "")
    if not val or val == "-":
        return None
    try:
        return int(val)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def strip_trailing_parens(t: str) -> str:
    """Strip all trailing parenthetical suffixes: 'Foo (Bar) (Baz)' -> 'Foo'."""
    result = re.sub(r"(\s*\([^)]*\))+\s*$", "", t).strip()
    return result if result != t else ""


def load_title_map() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return (exact_map, fuzzy_map, base_map): {normalized_title -> track_id}.

    base_map uses titles with trailing parentheticals stripped, as a fallback
    for source files that omit subtitles like '(Fifty Shades Darker)'.
    """
    exact_map: dict[str, str] = {}
    fuzzy_map: dict[str, str] = {}
    base_map:  dict[str, str] = {}

    def index_tracks(tracks):
        for track in tracks:
            tid = extract_track_id(track.get("url", ""))
            if not tid:
                continue
            for key in [track.get("title", ""), track.get("title_clean", "")]:
                if not key:
                    continue
                exact = key.lower().strip()
                fuzzy = normalize_title(key)
                if exact and exact not in exact_map:
                    exact_map[exact] = tid
                if fuzzy and fuzzy not in fuzzy_map:
                    fuzzy_map[fuzzy] = tid
                # Also index with apostrophes removed (for source files with "dont" etc.)
                no_apos = normalize_no_apostrophe(key)
                if no_apos and no_apos not in fuzzy_map and no_apos not in base_map:
                    base_map[no_apos] = tid

                base = strip_trailing_parens(key)
                if base:
                    base_exact = base.lower().strip()
                    base_fuzzy = normalize_title(base)
                    base_no_apos = normalize_no_apostrophe(base)
                    if base_exact and base_exact not in exact_map and base_exact not in base_map:
                        base_map[base_exact] = tid
                    if base_fuzzy and base_fuzzy not in fuzzy_map and base_fuzzy not in base_map:
                        base_map[base_fuzzy] = tid
                    if base_no_apos and base_no_apos not in fuzzy_map and base_no_apos not in base_map:
                        base_map[base_no_apos] = tid

    for album_file in sorted(ALBUMS_DIR.glob("*.json")):
        with open(album_file, encoding="utf-8-sig") as f:
            data = json.load(f)
        for section in data.get("sections", []):
            index_tracks(section.get("tracks", []))

    with open(SONGS_JSON, encoding="utf-8-sig") as f:
        songs_data = json.load(f)
    for section in songs_data:
        index_tracks(section.get("tracks", []))

    return exact_map, fuzzy_map, base_map


def load_history() -> dict[str, dict[str, dict]]:
    """
    Load existing streams_history.csv.

    Returns:
      existing: {track_id: {date: {"streams": int|None, "daily": int|None}}}
    """
    existing: dict[str, dict[str, dict]] = {}

    with open(HISTORY_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            date = row["date"].strip()
            tid  = row["track_id"].strip()
            streams = parse_int(row.get("streams", ""))
            daily   = parse_int(row.get("daily_streams", ""))
            existing.setdefault(tid, {})[date] = {"streams": streams, "daily": daily}

    return existing


def find_best_anchor(
    existing_for_tid: dict[str, dict],
    source_dailies: dict[str, int | None],
) -> tuple[str, int] | tuple[None, None]:
    """
    Find the most recent date that is present in BOTH the existing CSV
    (with a valid cumulative) AND the source data (with a valid daily).
    This ensures we can backfill correctly from that point.
    """
    for date in sorted(existing_for_tid.keys(), reverse=True):
        streams = existing_for_tid[date]["streams"]
        if streams is None:
            continue
        if date in source_dailies and source_dailies[date] is not None:
            return date, streams
    return None, None


def load_source_csvs(paths: list[str]) -> dict[str, dict[str, int | None]]:
    """
    Parse and merge one or more pivot CSV files.
    Later files in the list override earlier files for the same (title, date).

    Returns: {title_lower: {date_YYYY-MM-DD: daily_streams_or_None}}
    """
    merged: dict[str, dict[str, int | None]] = {}

    for path in paths:
        print(f"  Reading {path} ...")
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            raw_headers = next(reader)
            rows = list(reader)

        # Parse date headers (skip column 0 = Title)
        date_cols: list[str | None] = []
        for h in raw_headers[1:]:
            h = h.strip()
            parsed = None
            for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    parsed = datetime.strptime(h, fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    pass
            if parsed is None and h:
                print(f"    [WARN] Cannot parse date header: {h!r}")
            date_cols.append(parsed)

        for row in rows:
            if not row or not row[0].strip():
                continue
            title = row[0].strip().lower()
            entry = merged.setdefault(title, {})
            for i, val in enumerate(row[1:]):
                if i >= len(date_cols):
                    break
                date = date_cols[i]
                if date is None:
                    continue
                v = parse_int(val)
                # Only override if we have a real value (don't overwrite with None)
                if v is not None or date not in entry:
                    entry[date] = v

        print(f"    -> {len(rows)} songs, dates {min(d for d in date_cols if d)} to {max(d for d in date_cols if d)}")

    return merged


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def build_full_history(
    anchor_date: str,
    anchor_streams: int,
    source_dailies: dict[str, int | None],
) -> dict[str, tuple[int, int | None]]:
    """
    Build complete history backwards from anchor_date using source daily values.

    anchor_date / anchor_streams: a date present in BOTH the existing CSV and source.
    source_dailies: {date: daily_streams} from the merged source files.

    Computes:
      - streams[anchor_date - 1] = anchor_streams - daily[anchor_date]
      - streams[anchor_date - 2] = streams[anchor_date - 1] - daily[anchor_date - 1]
      - etc.

    Also includes the anchor date itself (with source daily, which may update existing).
    Stops at the first date not present in source_dailies.
    """
    result: dict[str, tuple[int, int | None]] = {}

    anchor_daily = source_dailies[anchor_date]  # guaranteed non-None by find_best_anchor
    result[anchor_date] = (anchor_streams, anchor_daily)

    running = anchor_streams - anchor_daily
    current_dt = datetime.strptime(anchor_date, "%Y-%m-%d") - timedelta(days=1)

    while True:
        date_str = current_dt.strftime("%Y-%m-%d")
        if date_str not in source_dailies:
            break
        daily = source_dailies[date_str]
        result[date_str] = (running, daily)
        if daily is None:
            break
        running -= daily
        current_dt -= timedelta(days=1)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Merge daily stream CSVs into a new history file")
    parser.add_argument("csv_files", nargs="+", help="One or more pivot CSV files to import")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--output", default=str(OUTPUT_CSV), help="Output CSV path (default: streams_history.csv)")
    args = parser.parse_args()

    for p in args.csv_files:
        if not Path(p).exists():
            print(f"[ERROR] File not found: {p}")
            sys.exit(1)

    print("Loading title -> track_id mapping...")
    exact_map, fuzzy_map, base_map = load_title_map()
    print(f"  {len(exact_map)} exact titles indexed, {len(base_map)} base-title fallbacks")

    print("Loading existing streams_history.csv ...")
    existing = load_history()
    print(f"  {len(existing)} tracks loaded")

    print("Parsing source CSV(s) ...")
    source_data = load_source_csvs(args.csv_files)
    print(f"  {len(source_data)} songs total after merge")

    # Build reconstructed history per track
    # {track_id: {date: (cumulative, daily)}}
    reconstructed: dict[str, dict[str, tuple[int, int | None]]] = {}
    unmatched: list[str] = []
    no_anchor: list[str] = []

    # First pass: resolve all titles to track_ids and merge dailies per track_id.
    # This handles cases where the same song appears under slightly different titles
    # across files (e.g. "I Dont Wanna Live Forever" vs "I Don't Wanna Live Forever"),
    # ensuring the 2025 and 2026 data for the same track are combined before anchor lookup.
    resolved: dict[str, dict[str, int | None]] = {}  # track_id -> merged dailies

    for title, dailies in source_data.items():
        expanded = expand_abbrs(title)
        track_id = (
            exact_map.get(title)
            or exact_map.get(expanded)
            or fuzzy_map.get(normalize_title(title))
            or fuzzy_map.get(normalize_title(expanded))
            or base_map.get(title)
            or base_map.get(expanded)
            or base_map.get(normalize_title(title))
            or base_map.get(normalize_title(expanded))
            or base_map.get(normalize_no_apostrophe(title))
            or base_map.get(normalize_no_apostrophe(expanded))
        )
        if track_id is None:
            unmatched.append(title)
            continue
        merged = resolved.setdefault(track_id, {})
        for date, val in dailies.items():
            if val is not None or date not in merged:
                merged[date] = val

    for track_id, dailies in resolved.items():
        existing_for_tid = existing.get(track_id)
        if not existing_for_tid:
            no_anchor.append(track_id)
            continue

        anchor_date, anchor_streams = find_best_anchor(existing_for_tid, dailies)
        if anchor_date is None:
            no_anchor.append(track_id)
            continue

        history = build_full_history(anchor_date, anchor_streams, dailies)
        reconstructed[track_id] = history

    # --- Stats ---
    total_new = sum(
        sum(1 for d in hist if d not in existing.get(tid, {}))
        for tid, hist in reconstructed.items()
    )
    total_updated = sum(
        sum(
            1 for d, (cum, daily) in hist.items()
            if d in existing.get(tid, {}) and (
                existing[tid][d]["streams"] != cum or
                existing[tid][d]["daily"] != daily
            )
        )
        for tid, hist in reconstructed.items()
    )

    print(f"\n--- Summary ---")
    print(f"  Tracks reconstructed:  {len(reconstructed)}")
    print(f"  New rows (pre-2026-03-09 or gaps):  {total_new}")
    print(f"  Updated rows (source overrides existing): {total_updated}")
    if unmatched:
        print(f"  Unmatched titles ({len(unmatched)}):")
        for t in sorted(unmatched):
            print(f"    - {t!r}")
    if no_anchor:
        print(f"  No anchor in history ({len(no_anchor)}) — skipped:")
        for t in sorted(no_anchor):
            print(f"    - {t!r}")

    if args.dry_run:
        # Show sample of what would be written for one track
        if reconstructed:
            sample_tid = next(iter(reconstructed))
            hist = reconstructed[sample_tid]
            dates = sorted(hist)
            print(f"\n[DRY RUN] Sample track {sample_tid}:")
            print(f"  Date range: {dates[0]} -> {dates[-1]}  ({len(dates)} rows)")
            for d in dates[:3]:
                print(f"  {d}: cumulative={hist[d][0]:,}  daily={hist[d][1]}")
            print("  ...")
        print("\n[DRY RUN] No files written.")
        return

    # --- Assemble output ---
    # Start with all existing rows
    output: dict[str, dict[str, dict]] = {}
    for tid, dates in existing.items():
        output.setdefault(tid, {}).update({d: {"streams": v["streams"], "daily": v["daily"]} for d, v in dates.items()})

    # Apply reconstructed history (source overrides existing for same date)
    for tid, hist in reconstructed.items():
        for date, (cum, daily) in hist.items():
            output.setdefault(tid, {})[date] = {"streams": cum, "daily": daily}

    # Flatten and sort
    all_rows = []
    for tid, dates in output.items():
        for date, vals in dates.items():
            all_rows.append({
                "date": date,
                "track_id": tid,
                "streams": str(vals["streams"]) if vals["streams"] is not None else "",
                "daily_streams": str(vals["daily"]) if vals["daily"] is not None else "",
            })
    all_rows.sort(key=lambda r: (r["date"], r["track_id"]))

    out_path = Path(args.output)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "track_id", "streams", "daily_streams"])
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n[OK] Written {len(all_rows)} rows to {out_path}")
    print(f"Then regenerate the site:")
    print(f"  python scripts/export_for_web.py")


if __name__ == "__main__":
    main()
