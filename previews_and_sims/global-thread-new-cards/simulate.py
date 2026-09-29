#!/usr/bin/env python3
"""Dry check: routine Global post + pending NEW/RE Global cards as replies in ITS thread.

Runs the real global/daily.py::post_global_with_new_cards on a real chart date with
X, Discord and the posted-lock write stubbed (nothing posted, nothing written).
--force-pending ignores global_new_releases_posted.json (to see the thread for a day
whose cards already went out). Existing card PNGs are reused, never re-rendered.

Usage: python previews_and_sims/global-thread-new-cards/simulate.py 2026-09-27 --force-pending
"""
import argparse
import functools
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "collectors" / "spotify" / "charts" / "global"))
import daily  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--force-pending", action="store_true")
    args = ap.parse_args()
    target = date.fromisoformat(args.date)

    gnr = daily._global_new_releases_module()
    real_pending = gnr.pending_priority_posts
    gnr.pending_priority_posts = functools.partial(real_pending, force=args.force_pending)
    gnr.send_priority_discord = lambda *a, **k: print("[sim] discord skipped")
    gnr.mark_priority_posted = lambda d, slugs: print(f"[sim] would mark posted: {slugs}")

    def fake_thread(posts, session, **kw):
        print(f"[sim] X thread of {len(posts)} post(s), skip_if={'yes' if kw.get('skip_if') else 'no'}:")
        for i, (text, img) in enumerate(posts, 1):
            print(f"  #{i} [{Path(img).name} exists={Path(img).exists()}] {text.splitlines()[0]}")
        return True

    daily.post_image_thread = fake_thread
    daily.post_with_image = lambda text, img, session, **kw: print(f"[sim] single post {Path(img).name}") or True

    chart_dir = daily.spotify_chart_dir("global", target)
    tweet = daily.build_tweet_content([target])
    image = chart_dir / "chart_image.png"
    ok = daily.post_global_with_new_cards(tweet, image, target, skip_if=lambda: False)
    print(f"[sim] result={ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
