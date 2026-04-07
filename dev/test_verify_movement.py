#!/usr/bin/env python3
"""Vérifier la logique NEW/RE pour les dates 23, 24, 25 mars 2026"""
import csv
import sys
from pathlib import Path

# Add collectors/spotify/core to path
sys.path.insert(0, str(Path(__file__).parent / "collectors" / "spotify"))

from core.fmt import fmt_delta

CSV_PATH = Path(__file__).parent / "db" / "charts_history_global.csv"

# Lire le CSV
data = {}
with open(CSV_PATH, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        date = row['date']
        if date not in data:
            data[date] = []
        data[date].append(row)

# Dates à vérifier
dates_to_check = ['2026-03-23', '2026-03-24', '2026-03-25']

print("=" * 100)
print("VÉRIFICATION NEW/RE POUR 23, 24, 25 MARS 2026")
print("=" * 100)

for date in dates_to_check:
    if date not in data:
        print(f"\n❌ Pas de données pour {date}")
        continue
    
    print(f"\n{'=' * 100}")
    print(f"DATE: {date}")
    print(f"{'=' * 100}")
    print(f"{'Song':<40} {'Rank':<6} {'Prev':<6} {'Total Days':<12} {'Mouvement':<15}")
    print(f"{'-' * 100}")
    
    for row in sorted(data[date], key=lambda r: int(r['rank'])):
        song = row['song_name'][:38]
        rank = row['rank']
        prev_rank = row['previous_rank']
        total_days = row['total_days']
        
        # Appliquer fmt_delta avec total_days
        movement = fmt_delta(
            rank=int(rank),
            previous_rank=prev_rank if prev_rank else None,
            peak_rank=row['peak_rank'],
            total_days=int(float(total_days)) if total_days and total_days != '' else None
        )
        
        print(f"{song:<40} {rank:<6} {prev_rank:<6} {total_days:<12} {movement:<15}")

print("\n" + "=" * 100)
