#!/usr/bin/env python3
"""Ajouter la colonne 'movement' à TOUS les CSVs historiques"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "collectors" / "spotify"))

from core.fmt import fmt_delta

REPO_ROOT = Path(__file__).parent
CSV_FILES = [
    REPO_ROOT / "db" / "charts_history_global.csv",
    REPO_ROOT / "db" / "charts_history_fr.csv",
    REPO_ROOT / "db" / "charts_history_uk.csv",
    REPO_ROOT / "db" / "charts_history_us.csv",
]

print("=" * 100)
print("CORRECTION DE TOUS LES CSVs HISTORIQUES")
print("=" * 100)
print()

for csv_path in CSV_FILES:
    if not csv_path.exists():
        print(f"⏭️  {csv_path.name} (n'existe pas)")
        continue
    
    print(f"📝 {csv_path.name}...")
    
    # Lire
    rows = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"   ✓ {len(rows)} lignes lues")
    
    # Ajouter le mouvement
    for row in rows:
        rank = int(row['rank'])
        
        prev_rank_val = None
        if row.get('previous_rank'):
            try:
                prev_rank_val = int(float(row['previous_rank']))
            except (ValueError, TypeError):
                pass
        
        peak_rank_val = None
        if row.get('peak_rank'):
            try:
                peak_rank_val = int(float(row['peak_rank']))
            except (ValueError, TypeError):
                pass
        
        total_days_val = None
        if row.get('total_days'):
            try:
                total_days_val = int(float(row['total_days']))
            except (ValueError, TypeError):
                pass
        
        movement = fmt_delta(
            rank=rank,
            previous_rank=prev_rank_val,
            peak_rank=peak_rank_val,
            total_days=total_days_val
        )
        
        row['movement'] = movement
    
    # Écrire
    fieldnames = list(rows[0].keys()) if rows else []
    if 'movement' in fieldnames:
        fieldnames.remove('movement')
    if 'total_days' in fieldnames:
        idx = fieldnames.index('total_days')
        fieldnames.insert(idx + 1, 'movement')
    
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"   ✓ {len(rows)} lignes écrites")
    print()

print("=" * 100)
print("✅ TOUS LES CSVs CORRIGÉS!")
print("=" * 100)
