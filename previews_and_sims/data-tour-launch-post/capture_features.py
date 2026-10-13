"""Real phone-width screenshots of the live site for the thread cards.

    python previews_and_sims/data-tour-launch-post/capture_features.py
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "shots"
SITE = "https://thetsmuseum.app"
# Hero strip portrait index -> era (album order): 4 = 1989, 3 = Red, 9 = Midnights.
SHOTS = {
    "collections": (9, [
        ("collections_list", "/spotifystreams/top-collections", None),
        ("collections_detail", "/spotifystreams/top-collections/eras-tour-2023", None),
    ]),
    "allartists": (4, [
        ("allartists_everyone", "/spotifycharts/charts/all-artists", "everyone"),
        ("allartists_taylor", "/spotifycharts/charts/all-artists?filter=taylor", None),
    ]),
    "youtube": (3, [
        ("youtube_songs", "/youtube", None),
        ("youtube_new", "/youtube", "new"),
    ]),
}


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for group, (era_idx, shots) in SHOTS.items():
            ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                                is_mobile=True, has_touch=True)
            pg = ctx.new_page()
            pg.goto(SITE + "/", wait_until="networkidle", timeout=60000)
            pg.wait_for_timeout(1000)
            pg.locator(".erashome-strip-photo").nth(era_idx).tap()
            pg.wait_for_timeout(1200)
            for name, route, action in shots:
                pg.goto(SITE + route, wait_until="networkidle", timeout=60000)
                pg.wait_for_timeout(2500)
                if action == "everyone":
                    btn = pg.get_by_role("button", name="Everyone")
                    if btn.count():
                        btn.first.click()
                        pg.wait_for_timeout(1500)
                elif action == "new":
                    btn = pg.locator(".youtube-new-btn")
                    if btn.count():
                        btn.first.click()
                        pg.wait_for_timeout(1500)
                pg.screenshot(path=str(OUT / f"{name}.png"))
                print(name, pg.evaluate("document.body.dataset.theme"))
            ctx.close()
        b.close()


if __name__ == "__main__":
    main()
