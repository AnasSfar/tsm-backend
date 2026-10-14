#!/usr/bin/env python3
"""Milestone Watch posts (owner decision 2026-10-13): the next 10 Taylor Swift songs expected to
pass a milestone, ranked by expected date (spotify_app_cards.watch_rows). Card titles: "Billion
Watch" for 1B, "900M Watch", "2B Watch"... Never "Road to 1B" (name used by another account).

Two triggers, each its own standalone post:
  1. a song crossed a milestone today (same events as post_stream_milestones.py, every 100M)
     -> the Watch of that milestone, once per milestone per day;
  2. the LIST_WATCHES (900M, 1B, 2B, 3B) also post when their list changes vs the last posted
     one (a song enters or leaves, or the order changes), state per Watch in
     runtime/social/milestone_watch_state.json. Skipped for a milestone trigger 1 posted today.

  python post_milestone_watch.py [DATE] [--no-post] [--force-lists] [--post-spacing-seconds N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
REPO_ROOT = SCRIPT_DIR.parents[4]
sys.path.insert(0, str(REPO_ROOT / "collectors"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(SCRIPT_DIR))

from twitter.sessions import default_twitter_session  # noqa: E402
from twitter.text import billion_watch_tweet, milestone_watch_tweet  # noqa: E402
from core.twitter import post_with_image  # noqa: E402
from core.data_paths import RUNTIME_SOCIAL_ROOT, update_streams_dir  # noqa: E402
import forecast_milestones  # noqa: E402
import post_stream_milestones  # noqa: E402
import spotify_app_cards as app  # noqa: E402

TWITTER_SESSION = default_twitter_session(REPO_ROOT)
STATE_PATH = RUNTIME_SOCIAL_ROOT / "milestone_watch_state.json"
# Watches that also post when their list changes (owner 2026-10-13)
LIST_WATCHES = (900_000_000, 1_000_000_000, 2_000_000_000, 3_000_000_000)
MIN_ROWS = 3  # a Watch with 1-2 songs projected within 5 years is not worth a card


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _clean_title(row: dict) -> str:
    return app.song_title_parts(str(row.get("title") or ""), row.get("primary_album") or row.get("album") or "")[0].strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Post Milestone Watch cards (milestone crossed / Billion list changed).")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--no-post", action="store_true", help="Render + print only; no state written.")
    parser.add_argument("--force-lists", action="store_true", help="Post the list Watches even if unchanged.")
    parser.add_argument("--post-spacing-seconds", type=int, default=0)
    args = parser.parse_args()
    d = args.date or str(date.today() - timedelta(days=1))

    # The forecast reads the web history export: it must already contain the stats date,
    # otherwise the expected dates would be computed on stale data.
    dates = forecast_milestones.load_history_bundle().get("dates") or []
    if not dates or dates[-1] < d:
        print(f"[milestone_watch] history export ends {dates[-1] if dates else 'nowhere'}, before {d}; skip.")
        return 0

    day_dir = update_streams_dir(d)
    day_lock = day_dir / "milestone_watch_posted.json"
    posted = set(_read_json(day_lock).get("posted") or [])
    state = _read_json(STATE_PATH)

    jobs: list[tuple[str, int, str, list[dict]]] = []  # (key, milestone, tweet, rows)
    crossed: dict[int, list[str]] = {}
    for e in post_stream_milestones.find_milestone_events(d):
        crossed.setdefault(int(e["milestone"]), []).append(_clean_title(e))
    for m in sorted(crossed, reverse=True):  # biggest milestone first
        rows = app.watch_rows(d, m)
        if len(rows) < MIN_ROWS:
            print(f"[milestone_watch] {app.watch_title(m)}: {len(rows)} song(s) projected within 5 years; skip.")
            continue
        tweet = milestone_watch_tweet(
            stats_date=d, watch_title=app.watch_title(m), milestone_words=app.milestone_words(m),
            crossed_titles=crossed[m], next_title=_clean_title(rows[0]),
            next_expected=rows[0]["expected_date"], count=len(rows))
        jobs.append((str(m), m, tweet, rows))

    for m in LIST_WATCHES:
        label = app.milestone_label(m)
        if m in crossed:
            print(f"[milestone_watch] {label} crossed today: its Watch covers the list change.")
            continue
        rows = app.watch_rows(d, m)
        last = state.get(label) or {}
        if not last and len(rows) >= MIN_ROWS and not args.force_lists:
            # first run: the current list becomes the reference, no burst of 4 posts at once
            print(f"[milestone_watch] {app.watch_title(m)}: no reference yet, recorded (no post).")
            if not args.no_post:
                state[label] = {"date": d, "ids": [r["track_id"] for r in rows]}
                _write_json(STATE_PATH, state)
            continue
        if len(rows) < MIN_ROWS:
            print(f"[milestone_watch] {app.watch_title(m)}: {len(rows)} song(s) projected within 5 years; skip.")
            continue
        if [r["track_id"] for r in rows] == last.get("ids") and not args.force_lists:
            print(f"[milestone_watch] {app.watch_title(m)} list unchanged since {last.get('date')}.")
            continue
        tweet = billion_watch_tweet(stats_date=d, next_title=_clean_title(rows[0]),
                                    next_expected=rows[0]["expected_date"], count=len(rows),
                                    watch_title=app.watch_title(m), milestone_words=app.milestone_words(m))
        jobs.append((f"list-{label}", m, tweet, rows))

    jobs = [j for j in jobs if args.no_post or j[0] not in posted]
    if not jobs:
        print(f"[milestone_watch] nothing to post for {d}.")
        return 0
    if not args.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return 1

    for i, (key, m, tweet, rows) in enumerate(jobs):
        label = app.milestone_label(m)
        for n, r in enumerate(rows, 1):
            print(f"[milestone_watch] {label} #{n} {r['title']} {r['streams']:,} -> {r['expected_date']}")
        image = app.render_watch(d, day_dir / f"milestone_watch_{label}_{d}.png", rows, m)
        print(f"[milestone_watch] {app.watch_title(m)} ({key}) tweet ({len(tweet)} chars):\n{tweet}")
        if args.no_post:
            continue
        if i and args.post_spacing_seconds > 0:
            time.sleep(args.post_spacing_seconds)
        if not post_with_image(tweet, image, TWITTER_SESSION):
            print(f"[milestone_watch] post failed: {key}")
            return 1
        posted.add(key)
        _write_json(day_lock, {"posted": sorted(posted)})
        if m in LIST_WATCHES:  # any posted Watch of a list milestone is its new reference
            state[label] = {"date": d, "ids": [r["track_id"] for r in rows]}
            _write_json(STATE_PATH, state)
        print(f"[milestone_watch] posted {app.watch_title(m)} for {d}.")
    if args.no_post:
        print("[milestone_watch] posts skipped (--no-post).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
