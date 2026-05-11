import csv
import re
from datetime import date, timedelta
from pathlib import Path

_DB_DIR = Path("db")
CHARTS_GLOBAL_CSV = _DB_DIR / "charts_history_global.csv"

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_PAREN_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_DASH_SPLIT_RE = re.compile(r"\s+-\s+")

def _normalize_title(value: str) -> str:
    """Best-effort normalization for matching chart CSV titles."""
    s = (value or "").strip().casefold()
    if not s:
        return ""
    s = s.replace("'", "'").replace("'", "'").replace(""", '"').replace(""", '"')
    s = _PAREN_RE.sub(" ", s)
    s = _DASH_SPLIT_RE.split(s, maxsplit=1)[0]
    s = _NORMALIZE_RE.sub(" ", s)
    s = " ".join(s.split())
    return s

# Test data
week_end = date(2026, 4, 3)
week_start = week_end - timedelta(days=6)
week_dates = set()
for i in range(7):
    d = week_start + timedelta(days=i)
    week_dates.add(d.isoformat())

print("Testing _aggregate_weekly_unfiltered_streams_by_title:")
print(f"Week dates: {sorted(week_dates)}")
print()

unfiltered = {}
matched_rows = 0

def _to_int(v: str | None) -> int:
    try:
        return int((v or "").strip())
    except Exception:
        return 0

with CHARTS_GLOBAL_CSV.open("r", newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        day = (row.get("date") or "").strip()
        if day not in week_dates:
            continue
        title = (row.get("song_name") or "").strip()
        streams = _to_int(row.get("streams"))
        if not title or streams <= 0:
            continue
        key = _normalize_title(title)
        if not key:
            continue
        unfiltered[key] = unfiltered.get(key, 0) + streams
        matched_rows += 1
        if matched_rows <= 5:
            print(f"Row {matched_rows}: '{title}' ({day}) -> normalized: '{key}' = {streams} streams")

print()
print(f"Total rows: {matched_rows}")
print(f"Unique titles: {len(unfiltered)}")
print()
print("Top 5 unfiltered:")
for key, total in sorted(unfiltered.items(), key=lambda x: x[1], reverse=True)[:5]:
    print(f"  {key}: {total:,}")
