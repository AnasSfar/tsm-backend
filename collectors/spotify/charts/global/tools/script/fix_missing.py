#!/usr/bin/env python3
"""
Scrape et reconstruit les jours manquants sans poster sur Twitter.

Usage :
python fix_missing.py 2024-01-01 2024-03-01
python fix_missing.py 2023-01-01

Le script :
- scrape chaque date
- génère ts_all_songs.csv
- met à jour ts_history.json
- ne poste rien
"""

import subprocess
import sys
from datetime import date, timedelta, datetime
from pathlib import Path

ROOT = Path(__file__).parent
FILTER_SCRIPT = ROOT / "filter.py"
REBUILD_SCRIPT = ROOT / "rebuild.py"


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def date_range(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def ts_csv_exists(d):
    p = ROOT / str(d.year) / f"{d.month:02d}" / str(d) / "ts_all_songs.csv"
    return p.exists()


def main():

    if len(sys.argv) < 2:
        print("Usage : python fix_missing.py DATE_DEBUT [DATE_FIN]")
        sys.exit(1)

    start = parse_date(sys.argv[1])

    if len(sys.argv) >= 3:
        end = parse_date(sys.argv[2])
    else:
        end = date.today() - timedelta(days=1)

    if start > end:
        print("Erreur : start > end")
        sys.exit(1)

    print(f"\nScan {start} -> {end}\n")

    for d in date_range(start, end):

        if ts_csv_exists(d):
            print(f"SKIP {d} deja present")
            continue

        print(f"Scrape {d}...")

        r = subprocess.run(
            [sys.executable, str(FILTER_SCRIPT), str(d)],
            cwd=str(ROOT)
        )

        if r.returncode != 0:
            print(f"ERREUR {d}")
            continue

    print("\nReconstruction ts_history...\n")

    subprocess.run(
        [sys.executable, str(REBUILD_SCRIPT)],
        cwd=str(ROOT)
    )

    print("\nTermine.\n")


if __name__ == "__main__":
    main()