#!/usr/bin/env python3
"""Test rapide pour vérifier que fmt_delta() calcule correctement RE/NEW avec total_days"""
import sys
from pathlib import Path

# Add collectors/spotify/core to path
sys.path.insert(0, str(Path(__file__).parent / "collectors" / "spotify"))

from core.fmt import fmt_delta

# Cas de test
test_cases = [
    # (rank, previous_rank, peak_rank, total_days, expected_result, description)
    (5, None, 1, None, "NEW", "Première apparition, pas d'historique"),
    (5, None, 1, 0, "NEW", "Première apparition, total_days=0"),
    (5, None, 1, 25, "RE", "Re-entry: était au top 1 avant, réapparaît (total_days > 0)"),
    (5, 8, 1, None, "+3", "Mouvement normal: +3 places"),
    (5, 2, 1, None, "-3", "Mouvement normal: -3 places"),
    (5, 5, 1, None, "0", "Pas de mouvement"),
    (5, 0, 5, 15, "RE", "Re-entry: position égale au peak mais total_days > 0"),
    (5, 0, 3, 10, "RE", "Re-entry: peak != rank, total_days > 0"),
]

print("=" * 80)
print("TEST fmt_delta() avec total_days")
print("=" * 80)

passed = 0
failed = 0

for rank, prev_rank, peak_rank, total_days, expected, description in test_cases:
    result = fmt_delta(rank, prev_rank, peak_rank, total_days)
    status = "✅ PASS" if result == expected else "❌ FAIL"
    
    if result == expected:
        passed += 1
    else:
        failed += 1
    
    print(f"\n{status}")
    print(f"  Description: {description}")
    print(f"  Input: rank={rank}, previous_rank={prev_rank}, peak_rank={peak_rank}, total_days={total_days}")
    print(f"  Expected: {expected!r}")
    print(f"  Got:      {result!r}")

print("\n" + "=" * 80)
print(f"Résultat: {passed} PASS, {failed} FAIL")
print("=" * 80)

sys.exit(0 if failed == 0 else 1)
