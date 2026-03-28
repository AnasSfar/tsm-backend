#!/usr/bin/env python3
"""
Compat wrapper.
Au lieu de telecharger des CSV, on scrape directement la date demandee.
Usage : python download.py 2026-03-11
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
FILTER_SCRIPT = ROOT / "filter.py"

def main():
    if len(sys.argv) < 2:
        print("Usage : python download.py YYYY-MM-DD")
        sys.exit(1)

    result = subprocess.run([sys.executable, str(FILTER_SCRIPT), sys.argv[1]], cwd=str(ROOT))
    sys.exit(result.returncode)

if __name__ == "__main__":
    main()