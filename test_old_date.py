#!/usr/bin/env python3
"""Vérifier si une vieille date aurait des mouvements mal calculés"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "collectors" / "spotify"))

from core.fmt import fmt_delta

CSV_PATH = Path(__file__).parent / "db" / "charts_history_global.csv"

print("=" * 100)
print("VÉRIFICATION ANCIENNE DATE: 2021-05-10")
print("=" * 100)
print()

with open(CSV_PATH, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['date'] == '2021-05-10':
            song = row['song_name']
            rank = int(row['rank'])
            prev_rank = row['previous_rank']
            peak_rank = row['peak_rank']
            total_days = row['total_days']
            
            movement = fmt_delta(
                rank=rank,
                previous_rank=prev_rank if prev_rank else None,
                peak_rank=peak_rank if peak_rank else None,
                total_days=int(total_days) if total_days else None
            )
            
            print(f"Song:           {song}")
            print(f"Rank:           {rank}")
            print(f"Previous Rank:  {prev_rank if prev_rank else '(empty)'}")
            print(f"Peak Rank:      {peak_rank if peak_rank else '(empty)'}")
            print(f"Total Days:     {total_days}")
            print()
            print(f"=> Mouvement calculé: {movement}")
            print()
            print("Analyse:")
            print("- previous_rank est vide → pas de données d'hier")
            print(f"- total_days = {total_days} → la chanson a {total_days} jours d'historique")
            print()
            if movement == "RE":
                print("✅ RE (Re-entry) = CORRECT: la chanson était avant au classement, a disparu, puis revient")
            elif movement == "NEW":
                print("❌ NEW = INCORRECT: devrait être RE car total_days > 0")
