#!/usr/bin/env python3
"""
Script pour corriger les CSVs charts_history avec les bonnes valeurs de DAYS et STREAK.

DAYS = nombre unique de jours où la chanson a charté
STREAK = jours consécutifs depuis aujourd'hui
"""
import csv
import json
from datetime import timedelta
from pathlib import Path

# Importer les véritables fonctions
import sys
sys.path.insert(0, str(Path(__file__).parent / "collectors" / "spotify" / "core"))
from history import calculate_total_days, calculate_streak, parse_date

DB_DIR = Path(__file__).parent / "db"
CHARTS_CSVS = [
    DB_DIR / "charts_history_fr.csv",
    DB_DIR / "charts_history_global.csv",
    DB_DIR / "charts_history_uk.csv",
    DB_DIR / "charts_history_us.csv",
]


def load_history_from_csvs():
    """Charge l'historique complet depuis les CSVs."""
    history = {}
    
    for csv_path in CHARTS_CSVS:
        if not csv_path.exists():
            continue
            
        print(f"Chargement {csv_path.name}...")
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date = row["date"]
                track = row["song_name"]
                
                if track not in history:
                    history[track] = {}
                
                # Stocker pour chaque date et track
                history[track][date] = {
                    "rank": row.get("rank"),
                    "streams": row.get("streams"),
                }
    
    return history


def fix_csv(csv_path, history):
    """Fix un CSV en ajoutant les colonnes correctes."""
    print(f"\nTraitement {csv_path.name}...")
    
    rows = []
    fieldnames = None
    
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        
        # Lecture et correction
        for row in reader:
            date = row["date"]
            track = row["song_name"]
            
            # Calculer les vraies valeurs
            total_days = calculate_total_days(history, track, date)
            streak = calculate_streak(history, track, date)
            
            # Mettre à jour avec les vraies valeurs
            row["total_days"] = str(total_days)
            row["streak"] = str(streak)
            
            rows.append(row)
    
    # Construire les fieldnames: garder l'ordre, ajouter streak après total_days si absent
    fieldnames = list(fieldnames) if fieldnames else []
    if "streak" not in fieldnames:
        if "total_days" in fieldnames:
            idx = fieldnames.index("total_days")
            fieldnames.insert(idx + 1, "streak")
        else:
            fieldnames.append("total_days")
            fieldnames.append("streak")
    
    # Écrire le CSV corrigé
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            # Assurer que les champs existent
            for field in fieldnames:
                if field not in row:
                    row[field] = ""
            writer.writerow(row)
    
    print(f"  ✓ {csv_path.name} corrigé ({len(rows)} lignes)")


if __name__ == "__main__":
    print("Correction des CSVs charts_history...")
    
    # Charger tout l'historique
    history = load_history_from_csvs()
    print(f"Total: {len(history)} chansons chargées")
    
    # Corriger chaque CSV
    for csv_path in CHARTS_CSVS:
        if csv_path.exists():
            fix_csv(csv_path, history)
    
    print("\n✓ Correction terminée!")
