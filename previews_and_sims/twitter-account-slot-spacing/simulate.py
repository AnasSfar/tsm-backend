#!/usr/bin/env python3
"""Simulate X account-slot contention between pipelines (no browser, no post).

Reproduit l'incident du 2026-09-28 : un post Apple Music/iTunes (espacement 150 s)
prenait le verrou compte puis dormait son espacement DEDANS, bloquant le post Global
Spotify Charts (60 s) ~2,5 min. Ici les espacements sont reduits (slow=15 s, fast=6 s,
lead=2 s) et chaque "post" = entrer dans _twitter_account_slot + 1 s de faux navigateur
+ _wait_account_spacing + _mark_account_posted — le vrai code de core/twitter.py, avec
TWITTER_COORD_DIR redirige dans ce dossier (jamais le vrai %TEMP%/tsm_twitter_posts).

Usage: python previews_and_sims/twitter-account-slot-spacing/simulate.py
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
COORD = HERE / "coord"
FAKE_SESSION = HERE / "fake_session" / "twitter_session.json"
BROWSER_OPEN_S = 1.0


def _child(label: str, priority: int, spacing: int, posts: int, start_at: float, lead: int,
           aging: int, out_q) -> None:
    os.environ["TWITTER_ACCOUNT_SPACING_MIN_SECONDS"] = str(spacing)
    os.environ["TWITTER_ACCOUNT_SPACING_MAX_SECONDS"] = str(spacing)
    os.environ["TWITTER_SLOT_SPACING_LEAD_SECONDS"] = str(lead)
    os.environ["TWITTER_WAITER_AGING_SECONDS"] = str(aging)
    sys.path.insert(0, str(REPO))
    from collectors.spotify.core import twitter  # noqa: E402

    twitter.TWITTER_COORD_DIR = COORD
    twitter.TWITTER_COORD_LOCK = COORD / "coordinator.lock"
    time.sleep(max(0.0, start_at - time.time()))
    for _ in range(posts):
        asked = time.time()
        with twitter._twitter_account_slot(FAKE_SESSION, 120, priority=priority) as key:
            got = time.time()
            time.sleep(BROWSER_OPEN_S)
            twitter._wait_account_spacing(key)
            twitter._mark_account_posted(key)
            out_q.put({"label": label, "asked": asked, "slot": got, "posted": time.time()})


def run(name: str, actors: list[dict], *, lead: int = 2, aging: int = 300) -> list[dict]:
    shutil.rmtree(COORD, ignore_errors=True)
    COORD.mkdir(parents=True)
    FAKE_SESSION.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO))
    from collectors.spotify.core import twitter

    t0 = time.time() + 3.0  # laisse les process spawn demarrer
    # Un post vient de partir a t0 (comme le thread Global NEW a 16:00:10).
    (COORD / f"last_post_{twitter._account_key(FAKE_SESSION)}.txt").write_text(str(t0), encoding="ascii")
    q = mp.Queue()
    procs = [
        mp.Process(target=_child, args=(a["label"], a["priority"], a["spacing"], a.get("posts", 1),
                                        t0 + a["start"], lead, aging, q))
        for a in actors
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    events = []
    while not q.empty():
        events.append(q.get())
    events.sort(key=lambda e: e["posted"])
    print(f"\n=== {name} ===")
    for e in events:
        print(f"  {e['label']:<14} asked t={e['asked'] - t0:5.1f}s  slot t={e['slot'] - t0:5.1f}s  "
              f"posted t={e['posted'] - t0:5.1f}s")
    return [{**e, "asked": e["asked"] - t0, "slot": e["slot"] - t0, "posted": e["posted"] - t0} for e in events]


def main() -> int:
    results = {}
    # 1. L'incident : iTunes (lent, 150 s) arrive avant Global (60 s), meme priorite 1.
    results["incident"] = run("incident 2026-09-28 (prio egale)", [
        {"label": "itunes-slow", "priority": 1, "spacing": 15, "start": 0.5},
        {"label": "global-fast", "priority": 1, "spacing": 6, "start": 2.0},
    ])
    # 2. Priorite stricte respectee : un lent prio 0 passe avant un rapide prio 3.
    results["priority"] = run("prio 0 lent vs prio 3 rapide", [
        {"label": "debut-p0-slow", "priority": 0, "spacing": 15, "start": 0.5},
        {"label": "streams-p3", "priority": 3, "spacing": 6, "start": 1.0},
    ])
    # 3. Debit d'un pipeline seul (finalize) : pas de delai ajoute par le lead.
    results["throughput"] = run("debit finalize (3 posts rapides)", [
        {"label": "finalize", "priority": 3, "spacing": 6, "start": 0.5, "posts": 3},
    ])
    # 4. Anti-famine : rafale de posts rapides + un lent de meme priorite (aging 8 s).
    results["aging"] = run("anti-famine (aging 8 s)", [
        {"label": "slow", "priority": 1, "spacing": 10, "start": 0.5},
        {"label": "burst", "priority": 1, "spacing": 4, "start": 0.6, "posts": 6},
    ], aging=8)
    (HERE / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
