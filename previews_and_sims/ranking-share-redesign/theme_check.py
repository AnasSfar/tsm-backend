"""Album ranking pages take their album's colours (routeTheme) but keep the
visitor's title font; leaving the page restores the normal theme.

Usage: python theme_check.py   (preview server of vite.preview.config.mjs on :3020)
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:3020"
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)

STATE = """() => {
  const b = document.body, cs = getComputedStyle(b);
  const title = document.querySelector('.games-title');
  return {
    theme: b.dataset.theme,
    scheme: b.dataset.colorScheme,
    accent: cs.getPropertyValue('--accent').trim(),
    bg: cs.getPropertyValue('--bg').trim(),
    titleFont: title ? getComputedStyle(title).fontFamily : null,
  };
}"""


def spa_go(page, path):
    # Client-side navigation (no reload), like clicking a link in the site.
    page.evaluate("p => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate')); }", path)
    page.wait_for_timeout(900)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 375, "height": 812}, device_scale_factor=2,
                              is_mobile=True, has_touch=True)
    ctx.route("**/api/**", lambda r: r.abort() if r.request.method != "GET" else r.continue_())
    page = ctx.new_page()
    page.goto(BASE + "/games", wait_until="networkidle")
    page.wait_for_timeout(800)
    print("/games          ", page.evaluate(STATE))
    for path in ["/red-ranking", "/reputation-ranking", "/folklore-ranking", "/showgirl/ranking", "/debut-ranking"]:
        spa_go(page, path)
        page.wait_for_selector(".rk-choice")
        print(f"{path:16}", page.evaluate(STATE))
        page.screenshot(path=OUT / f"theme-{path.strip('/').replace('/', '-')}.png")
    spa_go(page, "/games")
    print("/games (back)   ", page.evaluate(STATE))
    page.goto(BASE + "/lover-ranking", wait_until="networkidle")
    page.wait_for_selector(".rk-choice")
    print("/lover (reload) ", page.evaluate(STATE))
    page.screenshot(path=OUT / "theme-lover-ranking.png")
    browser.close()
