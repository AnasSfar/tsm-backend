"""Preview of the era symbols in the real frontend (tsm-frontend worktree
feature/era-symbols): background motif, theme picker badges, era loader,
404, winner-reveal confetti and the ranking card watermark.

Needs the preview Vite server: npx vite --config vite.preview.config.mjs
(port 3030, /api -> prod, read-only; POSTs aborted here). The Track 1 game is
played by a bot (alphabetical picks) so that ranking is fake; nothing is sent.

Usage: python shoot.py [step ...]   steps: decor picker loader notfound reveal desktop
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import os

# 3030 = feature/era-symbols (main), 3031 = redesign/eras-theme (ERA_BASE env).
BASE = os.environ.get("ERA_BASE", "http://localhost:3030")
TAG = "redesign-" if BASE.endswith("3031") else ""
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)
THEMES = ["red", "lover", "1989", "reputation", "midnights", "showgirl", "evermore", "folklore"]
PAGE = "/spotifystreams/streams"


def init_theme(theme, scheme="light"):
    state = {"state": {"userTheme": f"theme-{theme}", "colorScheme": scheme, "siteUnlocked": True}, "version": 0}
    return f"localStorage.setItem('ts-museum-store', {json.dumps(json.dumps(state))});"


LABELS = {"red": "Red", "lover": "Lover", "1989": "1989", "reputation": "reputation", "midnights": "Midnights",
          "showgirl": "Showgirl", "evermore": "evermore", "folklore": "folklore", "debut": "Taylor Swift",
          "fearless": "Fearless", "speak-now": "Speak Now", "ttpd": "TTPD"}


def pick_theme(page, theme, scheme="light"):
    """Choose the theme like a visitor: theme picker button, then the mode."""
    page.locator(".tpick-btn").first.click()
    page.locator(".tpick-album", has_text=LABELS[theme]).first.click()
    if scheme == "dark":
        page.locator(".tpick-btn").first.click()
        page.get_by_role("button", name="☾ Dark").click()
        page.mouse.click(5, 400)
    page.wait_for_timeout(600)


def block_posts(route):
    return route.abort() if route.request.method != "GET" else route.continue_()


def phone(browser, theme, scheme="light"):
    ctx = browser.new_context(viewport={"width": 375, "height": 812}, device_scale_factor=2,
                              is_mobile=True, has_touch=True)
    ctx.route("**/*", block_posts)
    ctx._era_theme = (theme, scheme)
    return ctx


def open_page(ctx, path):
    page = ctx.new_page()
    page.goto(BASE + path, wait_until="networkidle")
    pick_theme(page, *ctx._era_theme)
    return page


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - window.innerWidth")


def step_decor(browser):
    for theme in THEMES:
        scheme = "dark" if theme in ("reputation", "midnights") else "light"
        ctx = phone(browser, theme, scheme)
        page = open_page(ctx, PAGE)
        page.wait_for_timeout(800)
        page.screenshot(path=OUT / f"{TAG}decor-{theme}-375.png")
        print(f"decor {theme}: overflow={overflow(page)} decor={page.locator('.era-decor').count()}")
        ctx.close()


def step_picker(browser):
    for theme in ("red", "showgirl"):
        ctx = phone(browser, theme)
        page = open_page(ctx, PAGE)
        page.locator(".tpick-btn").first.click()
        page.wait_for_timeout(1500)
        page.locator(".tpick-panel").screenshot(path=OUT / f"{TAG}picker-{theme}-375.png")
        print(f"picker {theme}: symbols={page.locator('.tpick-symbol').count()}")
        ctx.close()


def step_loader(browser):
    # The route fallback only shows on a page's first load (in-app navigations
    # are transitions that keep the old page): open /about directly with its
    # lazy chunk held, after picking the theme on another page.
    for theme in ("lover", "1989", "midnights", "red"):
        ctx = phone(browser, theme)
        page = open_page(ctx, PAGE)
        page.route("**/src/pages/About.jsx*", lambda r: None)
        page.goto(BASE + "/about", wait_until="commit")
        page.wait_for_selector(".era-loader", timeout=15000)
        page.wait_for_timeout(700)
        page.screenshot(path=OUT / f"{TAG}loader-{theme}-375.png")
        print(f"loader {theme}: {page.locator('.era-loader').count()}")
        ctx.close()


def step_notfound(browser):
    for theme in ("red", "reputation"):
        ctx = phone(browser, theme, "dark" if theme == "reputation" else "light")
        page = open_page(ctx, PAGE)
        page.goto(BASE + "/zz-missing", wait_until="networkidle")
        page.wait_for_timeout(800)
        page.screenshot(path=OUT / f"{TAG}404-{theme}-375.png")
        ctx.close()


def step_reveal(browser):
    ctx = phone(browser, "lover")
    page = ctx.new_page()
    page.goto(BASE + "/track-1-ranking", wait_until="networkidle")
    for _ in range(800):
        if page.locator(".track1-confetti span").count():
            page.wait_for_timeout(500)
            page.screenshot(path=OUT / f"{TAG}reveal-confetti-375.png")
            break
        no_stream = page.get_by_role("button", name="No, start ranking")
        if no_stream.count():
            no_stream.click()
            continue
        choices = page.locator(".rk-choice:not([disabled])")
        titles = choices.evaluate_all("els => els.map(e => e.getAttribute('aria-label'))")
        if len(titles) != 2:
            page.wait_for_timeout(50)
            continue
        choices.nth(0 if titles[0].lower() <= titles[1].lower() else 1).click()
        page.wait_for_timeout(20)
    era = page.evaluate("document.querySelector('.track1-confetti svg path')?.getAttribute('d')?.slice(0,20)")
    print("confetti shape path:", era)
    for _ in range(60):
        reveal = page.get_by_role("button", name="Reveal full ranking")
        if reveal.count():
            page.wait_for_timeout(2500)
            reveal.click()
        if page.locator(".rkcard").count():
            break
        page.wait_for_timeout(200)
    page.wait_for_timeout(800)
    page.locator(".rkcard").first.screenshot(path=OUT / f"{TAG}rkcard-375.png")
    print("rkcard watermark:", page.locator(".rkcard-watermark").count(), "overflow", overflow(page))
    ctx.close()


def step_desktop(browser):
    for theme in ("red", "1989"):
        ctx = browser.new_context(viewport={"width": 1280, "height": 860})
        ctx.route("**/*", block_posts)
        ctx._era_theme = (theme, "light")
        page = open_page(ctx, PAGE)
        page.wait_for_timeout(800)
        page.screenshot(path=OUT / f"{TAG}decor-{theme}-1280.png")
        ctx.close()


def step_home(browser):
    """Redesign only: ErasHome era picker badges + section title symbol."""
    for theme in (None, "red"):
        for width in (375, 1280):
            ctx = (phone(browser, theme or "red") if width == 375
                   else browser.new_context(viewport={"width": 1280, "height": 900}))
            ctx.route("**/*", block_posts)
            page = ctx.new_page()
            page.goto(BASE + "/", wait_until="networkidle")
            if theme:
                page.locator(".erashome-era", has_text="Red").first.click()
                page.wait_for_timeout(900)
            name = theme or "eras"
            page.locator("#erashome-eras").scroll_into_view_if_needed()
            page.wait_for_timeout(600)
            page.screenshot(path=OUT / f"{TAG}home-{name}-{width}.png")
            print(f"home {name} {width}: overflow={overflow(page)} symbols={page.locator('.erashome-era-symbol').count()}")
            ctx.close()


STEPS = {"home": step_home, "decor": step_decor, "picker": step_picker, "loader": step_loader,
         "notfound": step_notfound, "reveal": step_reveal, "desktop": step_desktop}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(STEPS)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name in wanted:
            STEPS[name](browser)
        browser.close()
