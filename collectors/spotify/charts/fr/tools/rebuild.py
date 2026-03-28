#!/usr/bin/env python3
"""Reconstruit ts_history.json pour Fr depuis data/. Usage : python tools/rebuild.py"""
import sys
from pathlib import Path

ROOT     = Path(__file__).parent.parent          # fr/
DATA_DIR = ROOT / "data"

sys.path.insert(0, str(ROOT.parent.parent.parent))  # collectors/spotify/
from core.history import rebuild_from_csvs, save


def main():
    print(f"Reconstruction de ts_history.json depuis {DATA_DIR}")
    history = rebuild_from_csvs(DATA_DIR, "regional-fr-daily")
    save(history, ROOT / "ts_history.json")
    total = sum(len(v) for v in history.values())
    print(f"Termine — {len(history)} chansons, {total} entrees")


if __name__ == "__main__":
    main()
