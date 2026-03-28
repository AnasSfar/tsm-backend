#!/usr/bin/env python3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.twitter import setup_session

ROOT         = Path(__file__).parent
PROJECT_ROOT = ROOT.parent
SESSION_FILE = PROJECT_ROOT / "twitter_session.json"

if __name__ == "__main__":
    setup_session(SESSION_FILE)