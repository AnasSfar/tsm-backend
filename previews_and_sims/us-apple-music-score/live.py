"""Run the real _post_score_card on today's latest US snapshot, never posting:
cards + locks go to this folder, state is an in-memory dict, post function
stubbed to raise. --force = ignore the score threshold (to see the card).

    python previews_and_sims/us-apple-music-score/live.py [--force] [--country us]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO, REPO / "collectors" / "apple_music"):
    sys.path.insert(0, str(p))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import post_new_release_progression as nrp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--force", action="store_true")
ap.add_argument("--country", default="us")
a = ap.parse_args()

nrp.OUT_DIR = HERE / "cards"
nrp.LOCKS_DIR = HERE / "locks"
nrp.OUT_DIR.mkdir(exist_ok=True)


def _no_post(*_a, **_k):
    raise RuntimeError("sim: posting is disabled")


nrp.post_with_retries = _no_post
if a.force:
    nrp.SCORE_CARD_MIN_SCORE = -1e9
now = nrp._now_paris()
args = argparse.Namespace(dry_run=False, no_post=True)
nrp._post_score_card(a.country, args, now.strftime("%Y-%m-%d"), {}, "apple_music", False, now)
