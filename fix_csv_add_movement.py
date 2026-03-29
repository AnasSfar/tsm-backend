#!/usr/bin/env python3
"""Ajouter la colonne 'movement' au CSV historique avec la logique corrigée"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "collectors" / "spotify"))

from core.fmt import fmt_delta

CSV_PATH = Path(__file__).parent / "db" / "charts_history_global.csv"
OUTPUT_PATH = CSV_PATH

print(f"Lecture du CSV: {CSV_PATH}")
print()

# Lire le CSV
rows = []
with open(CSV_PATH, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"✓ {len(rows)} lignes lues")
print()

# Ajouter le mouvement calculé correctement
print("Calcul des mouvements...")
for i, row in enumerate(rows):
    rank = int(row['rank'])
    
    # Convertir prev_rank correctement (c'est un string type "23.0" ou "")
    prev_rank_val = None
    if row['previous_rank']:
        try:
            prev_rank_val = int(float(row['previous_rank']))
        except (ValueError, TypeError):
            prev_rank_val = None
    
    # Convertir peak_rank correctement
    peak_rank_val = None
    if row['peak_rank']:
        try:
            peak_rank_val = int(float(row['peak_rank']))
        except (ValueError, TypeError):
            peak_rank_val = None
    
    # Convertir total_days correctement
    total_days_val = None
    if row['total_days']:
        try:
            total_days_val = int(float(row['total_days']))
        except (ValueError, TypeError):
            total_days_val = None
    
    movement = fmt_delta(
        rank=rank,
        previous_rank=prev_rank_val,
        peak_rank=peak_rank_val,
        total_days=total_days_val
    )
    
    row['movement'] = movement
    
    if (i + 1) % 1000 == 0:
        print(f"  {i + 1}/{len(rows)} lignes traitées...")

print(f"✓ Mouvements calculés pour toutes les lignes")
print()

# Écrire le CSV avec la nouvelle colonne
print(f"Écriture du CSV corrigé: {OUTPUT_PATH}")

fieldnames = list(rows[0].keys()) if rows else []
# Placer 'movement' juste après 'total_days'
if 'movement' in fieldnames:
    fieldnames.remove('movement')
if 'total_days' in fieldnames:
    idx = fieldnames.index('total_days')
    fieldnames.insert(idx + 1, 'movement')

with open(OUTPUT_PATH, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"✓ {len(rows)} lignes écrites")
print()

# Montrer un aperçu
print("=" * 100)
print("APERÇU DU CSV CORRIGÉ (10 premières lignes)")
print("=" * 100)
with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        if i >= 10:
            break
        print(f"{row['date']} | {row['song_name'][:30]:<30} | rank={row['rank']:<3} | prev={row['previous_rank']:<5} | total_days={row['total_days']:<4} | movement={row['movement']:<4}")

print()
print("✅ CSV corrigé avec la colonne 'movement'!")
