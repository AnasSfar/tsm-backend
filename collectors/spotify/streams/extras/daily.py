#!/usr/bin/env python3
"""
daily.py — alias for update_streams.py (same interface, charts-style entry point).

Usage:
  python daily.py
  python daily.py 2026-03-15
  python daily.py --debug-daily
  (see update_streams.py --help for full options)
"""
from update_streams import main

if __name__ == "__main__":
    main()
