#!/usr/bin/env python3
"""Quick test for calculate_total_days and calculate_streak"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "collectors" / "spotify" / "core"))
from history import calculate_total_days, calculate_streak

# Create a test history
test_history = {
    "Song A": {
        # Consecutive from 2026-01-01 to 2026-01-10
        "2026-01-01": {"rank": 5, "streams": 1000},
        "2026-01-02": {"rank": 4, "streams": 1100},
        "2026-01-03": {"rank": 3, "streams": 1200},
        "2026-01-04": {"rank": 2, "streams": 1300},
        "2026-01-05": {"rank": 1, "streams": 1400},
        # Missing 2026-01-06 and 2026-01-07 (dropped out)
        "2026-01-08": {"rank": 10, "streams": 900},  # RE-ENTRY
        "2026-01-09": {"rank": 9, "streams": 950},
        "2026-01-10": {"rank": 8, "streams": 1000},
    },
    "Song B": {
        # Always on, every day
        "2025-12-28": {"rank": 20, "streams": 500},
        "2025-12-29": {"rank": 19, "streams": 550},
        "2025-12-30": {"rank": 18, "streams": 600},
        "2025-12-31": {"rank": 17, "streams": 650},
        "2026-01-01": {"rank": 16, "streams": 700},
        "2026-01-02": {"rank": 15, "streams": 750},
        "2026-01-03": {"rank": 14, "streams": 800},
        "2026-01-04": {"rank": 13, "streams": 850},
        "2026-01-05": {"rank": 12, "streams": 900},
        "2026-01-06": {"rank": 11, "streams": 950},
        "2026-01-07": {"rank": 10, "streams": 1000},
        "2026-01-08": {"rank": 9, "streams": 1050},
        "2026-01-09": {"rank": 8, "streams": 1100},
        "2026-01-10": {"rank": 7, "streams": 1150},
    }
}

print("Testing calculate_total_days and calculate_streak\n")
print("=" * 60)

# Test Song A
print("\nSong A (has gaps):")
print("  total_days as of 2026-01-10 should be 8 (all unique days)")
print("  streak as of 2026-01-10 should be 3 (2026-01-08, 09, 10)")

td_a_end = calculate_total_days(test_history, "Song A", "2026-01-10")
st_a_end = calculate_streak(test_history, "Song A", "2026-01-10")
print(f"\n  Calculated total_days: {td_a_end} {'✓' if td_a_end == 8 else '✗ ERROR'}")
print(f"  Calculated streak: {st_a_end} {'✓' if st_a_end == 3 else '✗ ERROR'}")

# Test Song A on a different date (before the gap)
print("\nSong A as of 2026-01-05 (before gap):")
print("  total_days should be 5 (01-01 to 01-05)")
print("  streak should be 5 (01-01 to 01-05)")

td_a_mid = calculate_total_days(test_history, "Song A", "2026-01-05")
st_a_mid = calculate_streak(test_history, "Song A", "2026-01-05")
print(f"\n  Calculated total_days: {td_a_mid} {'✓' if td_a_mid == 5 else '✗ ERROR'}")
print(f"  Calculated streak: {st_a_mid} {'✓' if st_a_mid == 5 else '✗ ERROR'}")

# Test Song B (no gaps)
print("\nSong B (no gaps, continuous):")
print("  total_days as of 2026-01-10 should be 14 (2025-12-28 to 2026-01-10)")
print("  streak as of 2026-01-10 should be 14 (all consecutive)")

td_b = calculate_total_days(test_history, "Song B", "2026-01-10")
st_b = calculate_streak(test_history, "Song B", "2026-01-10")
print(f"\n  Calculated total_days: {td_b} {'✓' if td_b == 14 else '✗ ERROR'}")
print(f"  Calculated streak: {st_b} {'✓' if st_b == 14 else '✗ ERROR'}")

print("\n" + "=" * 60)
