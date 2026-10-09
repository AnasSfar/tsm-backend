"""Preview of the album ranking pages (tsm-frontend pages/AlbumTracksRanking.jsx).

Plays the real game in a phone-sized browser against the preview Vite server
(vite.preview.config.mjs, port 3020, /api -> prod, read-only) and saves:
  shots/<slug>-*.png           page screenshots (375px, touch)
  shots/<slug>-card-*.png      the exported share image (1080px wide)
  shots/<slug>-card-*-310.png  same image at X mobile timeline width
The picks are made by the bot (always the alphabetically-first title), so the
resulting rankings are fake; nothing is submitted (POSTs aborted).

Usage: python shoot.py [slug ...]   (default: every ranking page)
"""
import base64
import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

BASE = "http://localhost:3020"
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)
ROUTES = {
    "showgirl": "/showgirl/ranking",
    "album": "/album-ranking",
    "track-1": "/track-1-ranking",
    "number-ones": "/number-ones-ranking",
    "soundtrack": "/soundtrack-ranking",
    "track-13": "/swift-day/track-13-ranking",
}


def route_for(slug):
    return ROUTES.get(slug, f"/{slug}-ranking")


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - window.innerWidth")


def save_data_url(data_url, name):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    img = Image.open(io.BytesIO(raw))
    img.save(OUT / f"{name}.png")
    small = img.resize((310, round(img.height * 310 / img.width)), Image.LANCZOS)
    small.save(OUT / f"{name}-310.png")
    return img.size


def block_posts(route):
    if route.request.method != "GET":
        return route.abort()
    return route.continue_()


def dismiss_prompts(page):
    no_stream = page.get_by_role("button", name="No, start ranking")
    if no_stream.count():
        no_stream.click()


def play_to_end(page, limit=600):
    for _ in range(limit):
        if page.locator(".rkcard").count():
            return True
        reveal = page.get_by_role("button", name="Reveal full ranking")
        if reveal.count():
            page.wait_for_timeout(2500)  # let the theme wash / confetti play
            page.screenshot(path=OUT / f"_reveal.png")
            reveal.click()
            continue
        choices = page.locator(".rk-choice:not([disabled])")
        titles = choices.evaluate_all("els => els.map(e => e.getAttribute('aria-label'))")
        if len(titles) != 2:
            page.wait_for_timeout(60)
            continue
        pick = 0 if titles[0].lower() <= titles[1].lower() else 1
        choices.nth(pick).click()
        page.wait_for_timeout(20)
    return False


def export_card(page, name):
    page.get_by_role("button", name="Save image").click()
    img = page.locator(".folklore-image-modal-inner img")
    img.wait_for(timeout=20000)
    size = save_data_url(img.get_attribute("src"), name)
    page.get_by_role("button", name="Close").click()
    return size


def run(slug, browser):
    ctx = browser.new_context(
        viewport={"width": 375, "height": 812},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    )
    ctx.route("**/api/**", block_posts)
    page = ctx.new_page()
    url = BASE + route_for(slug)
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector(".rk-choice")
    page.wait_for_timeout(800)
    page.screenshot(path=OUT / f"{slug}-0-landing.png")
    dismiss_prompts(page)
    page.screenshot(path=OUT / f"{slug}-1-start.png")
    print(slug, "start overflow", overflow(page))

    page.locator(".rk-choice").first.click()
    page.wait_for_timeout(400)
    page.locator(".rk-choice").first.click()
    page.wait_for_timeout(400)
    page.screenshot(path=OUT / f"{slug}-2-playing.png")
    count_before = page.locator(".rk-battle-count").inner_text()
    page.get_by_role("button", name="Undo last pick").click()
    print(slug, "undo:", count_before, "->", page.locator(".rk-battle-count").inner_text())

    page.reload(wait_until="networkidle")
    page.wait_for_selector(".rk-choice")
    print(slug, "after reload:", page.locator(".rk-battle-count").inner_text(),
          "| restored note:", page.locator(".rk-restored").count())

    assert play_to_end(page), "game did not finish"
    page.wait_for_timeout(1500)
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=OUT / f"{slug}-3-result.png")
    page.screenshot(path=OUT / f"{slug}-3-result-full.png", full_page=True)
    print(slug, "result overflow", overflow(page))
    print(slug, "takes:", page.locator(".rkcard-take").all_inner_texts())

    print(slug, "card", export_card(page, f"{slug}-card"))

    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    page.get_by_role("button", name="Copy link").click()
    page.wait_for_timeout(300)
    note = page.locator(".rk-share-note").inner_text()
    link = page.evaluate("navigator.clipboard.readText().catch(() => '')") or note
    print(slug, "share link:", link)

    page.reload(wait_until="networkidle")
    page.wait_for_timeout(800)
    print(slug, "finished restored after reload:", page.locator(".rkcard").count() == 1)

    # Friend's view of the shared link, in a fresh context (no saved game).
    ctx2 = browser.new_context(viewport={"width": 375, "height": 812}, device_scale_factor=2,
                               is_mobile=True, has_touch=True)
    ctx2.route("**/api/**", block_posts)
    p2 = ctx2.new_page()
    p2.goto(link.replace("https://thetsmuseum.app", BASE), wait_until="networkidle")
    p2.wait_for_selector(".rk-shared .rkcard")
    p2.wait_for_timeout(800)
    p2.screenshot(path=OUT / f"{slug}-4-shared.png")
    print(slug, "shared overflow", overflow(p2))
    p2.get_by_role("button", name="Rank yours").click()
    p2.wait_for_timeout(300)
    print(slug, "shared dismissed:", p2.locator(".rk-shared").count() == 0, p2.url)
    ctx2.close()

    # Desktop result
    ctx3 = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx3.route("**/api/**", block_posts)
    p3 = ctx3.new_page()
    p3.goto(url, wait_until="networkidle")
    p3.wait_for_selector(".rk-choice")
    dismiss_prompts(p3)
    p3.keyboard.press("ArrowLeft")
    p3.wait_for_timeout(400)
    print(slug, "desktop keyboard:", p3.locator(".rk-battle-count").inner_text())
    play_to_end(p3)
    p3.wait_for_timeout(1200)
    p3.locator(".rk-game").screenshot(path=OUT / f"{slug}-5-desktop-result.png")
    ctx3.close()
    ctx.close()


if __name__ == "__main__":
    slugs = sys.argv[1:] or ["folklore", "red", "album", "track-1", "number-ones", "soundtrack", "track-13"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for slug in slugs:
            run(slug, browser)
        browser.close()
