"""Showgirl ranking -> The Encore (16 tracks) simulation.

Part A (always): runs the REAL tsm-frontend API route
(api/routes/showgirl_ranking.py) with fake submissions kept in memory — the
R2 read/write helpers are monkeypatched, nothing touches any bucket. Checks
16-track linear-v2 payloads are accepted, legacy 12-track / linear-v1 payloads
are rejected, and writes the resulting leaderboard to fixtures/leaderboard.json.

Part B (--browser URL): drives the REAL page (/showgirl/ranking on a running
Vite dev server) in headless Chromium, clicks through every battle, and
screenshots battle / results / leaderboard (desktop + mobile). Every
/api/showgirl-ranking-leaderboard call is intercepted: GET is served from
fixtures/leaderboard.json, POST is validated by the real API code (in memory)
and answered locally — nothing is submitted anywhere. Note: client.js
(shouldSkipLocalLeaderboardWrite) never POSTs from localhost/dev anyway, so
the payload the page WOULD send is rebuilt from the result table (rank / title /
points, the exact fields of the submit payload) and validated by the real API.

Usage:
  python simulate.py
  python simulate.py --browser http://localhost:5173
"""

import argparse
import asyncio
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRONTEND_ROOT = Path(r"C:\Users\sfara\Documents\GitHub\tsm-frontend")
FIXTURES = HERE / "fixtures"
CARDS = HERE / "cards"

sys.path.insert(0, str(FRONTEND_ROOT))
from fastapi import HTTPException, Response  # noqa: E402

from api.routes import showgirl_ranking as sr  # noqa: E402

_store = {"submissions": [], "snapshot": {}}
sr._read_submissions = lambda *a, **k: list(_store["submissions"])
sr._write_submissions = lambda entries, *a, **k: _store.update(submissions=list(entries))
sr._read_snapshot = lambda *a, **k: dict(_store["snapshot"])
sr._write_snapshot = lambda ranks, *a, **k: _store.update(snapshot={"ranks": ranks})

TRACKS = list(sr._SHOWGIRL_TRACKS)
PAGE_SRC = (FRONTEND_ROOT / "frontend/src/pages/ShowgirlRanking.jsx").read_text(encoding="utf-8")


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _submit(body):
    try:
        return asyncio.run(sr.submit_showgirl_ranking(FakeRequest(body)))
    except HTTPException as exc:
        return {"status": exc.status_code, "detail": exc.detail}


def submit(body):
    # Own thread: Playwright's sync API keeps an event loop running on the main one.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_submit, body).result()


def fake_ranking(rng, titles, scheme="linear-v2"):
    order = titles[:]
    rng.shuffle(order)
    n = len(order)
    return {
        "ranking": [{"title": t, "rank": i + 1, "points": n - i} for i, t in enumerate(order)],
        "participant_id": f"sim-{rng.random():.12f}",
        "point_scheme": scheme,
    }


def part_a():
    rng = random.Random(1325)
    assert len(TRACKS) == 16, TRACKS
    ok = [submit(fake_ranking(rng, TRACKS)) for _ in range(40)]
    assert all(r == {"ok": True} for r in ok), ok[:3]

    legacy_titles = TRACKS[:12]
    legacy = submit(fake_ranking(rng, legacy_titles, scheme="linear-v1"))
    old_scheme = submit(fake_ranking(rng, TRACKS, scheme="linear-v1"))
    print("legacy 12-track linear-v1 ->", legacy)
    print("16-track but linear-v1   ->", old_scheme)
    assert legacy.get("status") == 422 and old_scheme.get("status") == 422

    board = asyncio.run(sr.get_showgirl_ranking_leaderboard(Response()))
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "leaderboard.json").write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"leaderboard: {len(board)} tracks, {board[0]['entries']} participants")
    for i, row in enumerate(board, 1):
        print(f"  {i:>2}. {row['title']:<24} {row['total_points']:>6} pts  cover={row['cover']}")
    assert len(board) == 16 and all(r["entries"] == 40 for r in board)
    assert {r["cover"] for r in board} == {"/covers/the life of a showgirl the encore.webp"}
    return board


def part_b(base_url):
    from playwright.sync_api import sync_playwright

    CARDS.mkdir(parents=True, exist_ok=True)
    board_json = (FIXTURES / "leaderboard.json").read_text(encoding="utf-8")
    posted = []

    def handle_leaderboard(route):
        req = route.request
        if req.method == "POST":
            body = json.loads(req.post_data or "{}")
            posted.append(body)
            result = submit(body)
            route.fulfill(status=200 if result == {"ok": True} else 422,
                          content_type="application/json", body=json.dumps(result))
        else:
            route.fulfill(status=200, content_type="application/json", body=board_json)

    def run(page, tag):
        page.route("**/api/showgirl-ranking-leaderboard**", handle_leaderboard)
        page.goto(f"{base_url}/showgirl/ranking", wait_until="networkidle")
        page.wait_for_selector(".track13-choice img")
        page.wait_for_timeout(800)
        page.screenshot(path=str(CARDS / f"{tag}_battle.png"), full_page=False)
        seen = set()
        clicks = 0
        rng = random.Random(7)
        while page.locator(".track13-choice").count() and clicks < 80:
            for label in page.locator(".track13-choice span").all_inner_texts():
                seen.add(label)
            roll = rng.random()
            if roll < 0.12:
                page.get_by_role("button", name="Both").click()
            elif roll < 0.18:
                page.get_by_role("button", name="Skip").click()
            else:
                page.locator(".track13-choice").nth(rng.randint(0, 1)).click()
            clicks += 1
        page.wait_for_selector(".folklore-result-card")
        page.wait_for_timeout(1500)
        page.locator(".folklore-result-card").screenshot(path=str(CARDS / f"{tag}_result_card.png"))
        page.screenshot(path=str(CARDS / f"{tag}_full.png"), full_page=True)
        rows = page.locator(".folklore-result-card .track13-results-row").count()
        payload = page.evaluate(
            """() => [...document.querySelectorAll('.folklore-result-card .track13-results-row')].map(r => ({
                 rank: Number(r.children[0].textContent), title: r.querySelector('.track13-results-song span').textContent,
                 points: Number(r.children[2].textContent) }))"""
        )
        scheme = next(line.split('"')[1] for line in PAGE_SRC.splitlines() if line.startswith("const POINT_SCHEME"))
        verdict = submit({"ranking": payload, "participant_id": f"sim-{tag}", "point_scheme": scheme})
        print(f"[{tag}] page payload ({scheme}) -> API {verdict}; ranks={[i['rank'] for i in payload]}")
        assert verdict == {"ok": True}, verdict
        broken = page.evaluate(
            "() => [...document.images].filter(i => i.complete && i.naturalWidth === 0).map(i => i.src)"
        )
        print(f"[{tag}] battles={clicks} distinct songs seen in battles={len(seen)} result rows={rows} broken imgs={broken}")
        return clicks, seen, rows, broken

    with sync_playwright() as p:
        browser = p.chromium.launch()
        desktop = browser.new_page(viewport={"width": 1280, "height": 900})
        d = run(desktop, "desktop")
        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        m = run(mobile, "mobile")
        browser.close()

    print(f"POSTs intercepted: {len(posted)}")
    for body in posted:
        titles = [item["title"] for item in body["ranking"]]
        print(f"  scheme={body['point_scheme']} items={len(titles)} top3={titles[:3]}")
        assert body["point_scheme"] == "linear-v2" and len(titles) == 16 and set(titles) == set(TRACKS)
    for clicks, seen, rows, broken in (d, m):
        assert rows == 16 and not broken and seen <= set(TRACKS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", help="base URL of a running Vite dev server")
    args = parser.parse_args()
    part_a()
    if args.browser:
        part_b(args.browser.rstrip("/"))
    print("OK")


if __name__ == "__main__":
    main()
