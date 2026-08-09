"""
Deezer — Taylor Swift artist stats collector (fan count).

Appends one row/day to a plain (non-charted) CSV: nb_fan and nb_album from
Deezer's public /artist/{id} endpoint (no auth). Not a ranking, so it does
not use the chart snapshot machinery in core/csv_utils.py.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
from pathlib import Path

from core.config import ARTIST_ID, BASE_URL, DB_DIR, SCRIPTS_DIR
from core.export import maybe_run_export
from core.filters import clean_text
from core.http import build_session

CSV_PATH = DB_DIR / "deezer_artist_stats.csv"
EXPORT_SCRIPT = SCRIPTS_DIR / "export_deezer.py"
FIELDNAMES = ["date", "scraped_at", "artist_id", "artist_name", "nb_fan", "nb_album"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Taylor Swift's Deezer fan/album counts.")
    parser.add_argument("--date", dest="run_date", default=date.today().isoformat())
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    return parser.parse_args()


def fetch_artist_stats() -> dict:
    session = build_session()
    url = f"{BASE_URL}/artist/{ARTIST_ID}"
    resp = session.get(url)
    resp.raise_for_status()
    data = resp.json()
    return {
        "artist_name": clean_text(data.get("name", "")),
        "nb_fan": data.get("nb_fan", ""),
        "nb_album": data.get("nb_album", ""),
    }


def append_row(csv_path: Path, fieldnames: list[str], row: dict) -> None:
    """Append-only, one row per day (overwrites a same-day rerun in place)."""
    existing: list[dict] = []
    if csv_path.exists() and csv_path.stat().st_size > 0:
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            existing = [r for r in csv.DictReader(handle) if r.get("date") != row["date"]]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([*existing, row])


def main() -> None:
    args = parse_args()
    today = args.run_date
    scraped_at = args.scraped_at or f"{today}T{datetime.now().strftime('%H:%M:%S')}"

    stats = fetch_artist_stats()
    row = {
        "date": today,
        "scraped_at": scraped_at,
        "artist_id": ARTIST_ID,
        "artist_name": stats["artist_name"],
        "nb_fan": stats["nb_fan"],
        "nb_album": stats["nb_album"],
    }
    append_row(CSV_PATH, FIELDNAMES, row)
    print(f"nb_fan={stats['nb_fan']} nb_album={stats['nb_album']} -> {CSV_PATH}")
    maybe_run_export(EXPORT_SCRIPT)


if __name__ == "__main__":
    main()
