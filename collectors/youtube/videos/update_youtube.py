"""Canonical entry point for the YouTube Videos collector.

The implementation stays in collectors.youtube.update_youtube for backward
compatibility with existing scheduled tasks. New automation should prefer:

    python -m collectors.youtube.videos.update_youtube
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from collectors.youtube.update_youtube import main


if __name__ == "__main__":
    sys.exit(main())
