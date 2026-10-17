"""Isolated browser check using the real build and verified local data."""
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PUBLIC = ROOT.parent / "tsm-frontend/public"
DATA = ROOT / "runtime/exports/web/site/data"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (PUBLIC / urlparse(self.path).path.lstrip("/")).is_file():
            print("SPA_FALLBACK", self.path, flush=True)
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(PUBLIC)))
threading.Thread(target=server.serve_forever, daemon=True).start()
payloads = {name: json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("songs", "albums", "artist", "expected_milestones")}


def intercept(route):
    path = urlparse(route.request.url).path
    if path.startswith("/_vercel/"):
        return route.fulfill(body="", content_type="application/javascript")
    if "/api/" not in path and "/data/" not in path:
        return route.continue_()
    name = path.rsplit("/", 1)[-1].removesuffix(".json")
    if name == "milestones":
        name = "expected_milestones"
    value = payloads.get(name, {"dates": [payloads["expected_milestones"]["latest_history_date"]]} if name == "dates" else {})
    if name == "init":
        value = {"songs": payloads["songs"], "albums": payloads["albums"], "dates": {"dates": [payloads["expected_milestones"]["latest_history_date"]]}}
    if name == "news":
        value = []
    route.fulfill(json=value, headers={"Access-Control-Allow-Origin": "*"})


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width in (375, 1440):
            page = browser.new_page(viewport={"width": width, "height": 1000})
            page.on("pageerror", lambda error: print("BROWSER_ERROR", str(error)))
            page.on("console", lambda message: print("CONSOLE", message.text[:1200], flush=True) if message.type == "error" else None)
            page.route("**/*", intercept)
            page.add_init_script("localStorage.setItem('ts-museum-store', JSON.stringify({state:{siteUnlocked:true,language:'en'},version:0}));")
            page.goto(f"http://127.0.0.1:{server.server_port}/milestones", wait_until="domcontentloaded")
            picker = page.get_by_placeholder("Search a song or album").first
            try:
                picker.fill("The Fate of Ophelia", timeout=8000)
            except Exception:
                print("PAGE", page.locator("body").inner_text()[:2200])
                page.screenshot(path=str(HERE / "frontend_debug.png"))
                raise
            page.locator(".milestone-search-track").filter(has_text="The Fate of Ophelia").first.click()
            inputs = page.locator(".milestone-unit-inputs input")
            inputs.nth(0).fill("2")
            inputs.nth(1).fill("100")
            result = page.locator(".milestone-calculator-copy")
            result.get_by_text("The Fate of Ophelia", exact=True).wait_for()
            text = result.inner_text()
            assert "2027" in text and "18" in text and "Mar" in text, text
            dimensions = page.evaluate("({view:innerWidth,body:document.documentElement.scrollWidth})")
            assert dimensions["body"] <= dimensions["view"], dimensions
            page.locator(".milestone-calculator").screenshot(path=str(HERE / f"frontend_calculator_{width}.png"))
            print(width, dimensions, text.replace("\n", " | "))
            page.close()
        browser.close()
finally:
    server.shutdown()
    server.server_close()
