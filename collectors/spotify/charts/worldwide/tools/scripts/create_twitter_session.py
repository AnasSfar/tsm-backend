#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
from core.twitter import setup_session

SESSION_FILE = Path(__file__).resolve().parents[1] / "json" / "twitter_session.json"

if __name__ == "__main__":
    setup_session(SESSION_FILE)
