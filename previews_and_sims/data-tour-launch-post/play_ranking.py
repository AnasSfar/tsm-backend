"""Play a full album ranking on the LIVE site to capture its result screen,
WITHOUT submitting anything: every non-GET request to /api is aborted in the
browser, so the real leaderboard never receives this run.

    python previews_and_sims/data-tour-launch-post/play_ranking.py
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SITE = "https://thetsmuseum.app"
ROUTE = "/lover-ranking"
# My order of preference (earlier = wins the battle).
PREFERENCE = [
    "The Archer", "Cruel Summer", "Cornelia Street", "Daylight", "Death By A Thousand Cuts",
    "Lover", "Afterglow", "Paper Rings", "False God", "Miss Americana & The Heartbreak Prince",
    "I Forgot That You Existed", "The Man", "Soon You'll Get Better", "London Boy",
    "I Think He Knows", "ME!", "You Need To Calm Down", "It's Nice To Have A Friend",
]


CALLOUTS = [
    (".rkcard-podium", "Your top 3, podium-style"),
    (".rkcard-list", "Every song, in your order"),
    (".rkcard-takes", "My #1, hot take & how close you are to the fandom"),
    ("role=button[name='Share image']", "Share it as an image"),
]


def rank_of(title):
    for i, name in enumerate(PREFERENCE):
        if name.lower() in title.lower():
            return i
    return len(PREFERENCE)


def main():
    blocked = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)

        def guard(route):
            if route.request.method != "GET":
                blocked.append(f"{route.request.method} {route.request.url}")
                return route.abort()
            return route.continue_()

        ctx.route("**/api/**", guard)
        pg = ctx.new_page()
        pg.goto(SITE + ROUTE, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2000)
        for battle in range(300):
            if pg.locator(".rkcard-podium").count():
                break
            choices = pg.locator(".rk-choice")
            if choices.count() < 2:
                pg.wait_for_timeout(400)
                continue
            titles = [choices.nth(i).locator(".rk-choice-title").inner_text() for i in range(2)]
            pick = 0 if rank_of(titles[0]) <= rank_of(titles[1]) else 1
            choices.nth(pick).click()
            pg.wait_for_timeout(1300)
        pg.wait_for_timeout(2500)
        pg.screenshot(path=str(HERE / "ranking_result_full.png"), full_page=True)
        # Card shot: no bottom tab bar over the result, result card framed at the top.
        pg.add_style_tag(content=".tabbar{display:none!important}")
        top = pg.locator(".rkcard-podium").bounding_box()["y"] + pg.evaluate("window.scrollY")
        pg.evaluate(f"window.scrollTo(0, {max(0, top - 150)})")
        pg.wait_for_timeout(800)
        cards = HERE / "cards"
        cards.mkdir(exist_ok=True)
        pg.screenshot(path=str(cards / "album_rankings_shot.png"))
        boxes = []
        for sel, label in CALLOUTS:
            loc = pg.locator(sel)
            box = loc.first.bounding_box() if loc.count() else None
            if box and 0 <= box["y"] < 824:
                boxes.append({"label": label, **box})
            else:
                print("  skip (not visible):", sel)
        (cards / "album_rankings_prebuilt.json").write_text(json.dumps(boxes, indent=1), encoding="utf-8")
        print("battles:", battle, "finished:", bool(pg.locator(".rkcard-podium").count()))
        print("takes:", pg.locator(".rkcard-take").all_inner_texts())
        print("blocked writes:", blocked)
        b.close()


if __name__ == "__main__":
    main()
