from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parents[2]
ROOT        = _REPO_ROOT / "website"
DATA_DIR = ROOT / "data"
SITE_DATA_DIR = ROOT / "site" / "data"
ARTIST_PATH = DATA_DIR / "artist.json"
SITE_ARTIST_PATH = SITE_DATA_DIR / "artist.json"
ARTIST_URL = "https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02"

HEADLESS = False


def get_scrape_date_str() -> str:
    return date.today().isoformat()


def block_unneeded(route):
    request = route.request
    url = request.url.lower()
    resource_type = request.resource_type

    blocked_resource_types = {"media", "font"}
    blocked_keywords = (
        "doubleclick",
        "googletagmanager",
        "google-analytics",
        "analytics",
        "facebook",
        "pixel",
        "ads",
        ".mp4",
        ".webm",
        ".mp3",
        ".wav",
        ".ogg",
        ".woff",
        ".woff2",
        ".ttf",
    )

    if resource_type in blocked_resource_types or any(x in url for x in blocked_keywords):
        route.abort()
    else:
        route.continue_()


def format_int(value: int | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,}".replace(",", " ")


def parse_int_from_text(value: str | None) -> int | None:
    if not value:
        return None

    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None

    try:
        return int(digits)
    except ValueError:
        return None


def extract_artist_image(page) -> str | None:
    selectors = [
        'img[alt="Taylor Swift"]',
        'img[src*="i.scdn.co"]',
        "img",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = min(loc.count(), 20)
        except Exception:
            continue

        for i in range(count):
            try:
                elt = loc.nth(i)
                src = (elt.get_attribute("src") or "").strip()
                alt = (elt.get_attribute("alt") or "").strip()
            except Exception:
                continue

            if not src:
                continue

            if "i.scdn.co" not in src:
                continue

            if selector == 'img[alt="Taylor Swift"]':
                return src

            if alt.lower() == "taylor swift":
                return src

            if "ab676161" in src:
                return src

    return None


def extract_monthly_listeners_and_rank_from_text(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None

    monthly_listeners = None
    monthly_rank = None

    compact = re.sub(r"\s+", " ", text).strip()

    monthly_patterns = [
        r"([\d\s.,]+)\s+monthly listeners",
        r"monthly listeners\s*[:\-]?\s*([\d\s.,]+)",
        r"([\d\s.,]+)\s+auditeurs mensuels",
        r"auditeurs mensuels\s*[:\-]?\s*([\d\s.,]+)",
    ]

    for pattern in monthly_patterns:
        match = re.search(pattern, compact, re.IGNORECASE)
        if match:
            monthly_listeners = parse_int_from_text(match.group(1))
            if monthly_listeners is not None:
                break

    rank_patterns = [
        r"#\s*([\d\s.,]+)\s+in the world",
        r"world\s*rank\s*[:\-]?\s*#?\s*([\d\s.,]+)",
        r"ranked\s*#\s*([\d\s.,]+)",
        r"#\s*([\d\s.,]+)\s+dans le monde",
    ]

    for pattern in rank_patterns:
        match = re.search(pattern, compact, re.IGNORECASE)
        if match:
            monthly_rank = parse_int_from_text(match.group(1))
            if monthly_rank is not None:
                break

    return monthly_listeners, monthly_rank


def scrape_artist_metadata() -> dict:
    result = {
        "name": "Taylor Swift",
        "spotify_url": ARTIST_URL,
        "image_url": None,
        "monthly_listeners": None,
        "monthly_rank": None,
        "updated_at": get_scrape_date_str(),
    }

    p = sync_playwright().start()
    browser = p.chromium.launch(headless=HEADLESS)
    context = browser.new_context()
    page = context.new_page()
    page.route("**/*", block_unneeded)

    try:
        success = False

        for attempt in range(2):
            try:
                page.goto(ARTIST_URL, wait_until="domcontentloaded", timeout=60000)
                success = True
                break
            except PlaywrightTimeoutError:
                print(f"Artist page timeout (attempt {attempt + 1}/2)")
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"Artist page error (attempt {attempt + 1}/2): {e}")
                page.wait_for_timeout(2000)

        if not success:
            print("Could not load artist page. Keeping existing artist metadata if available.")
            return result

        try:
            page.get_by_role("button", name=re.compile("Accept|Accept all|Accepter|Autoriser", re.I)).click(timeout=2000)
            page.wait_for_timeout(1000)
        except Exception:
            pass

        for wait_ms in (1000, 2000, 4000, 6000):
            page.wait_for_timeout(wait_ms)

            try:
                body_text = page.locator("body").inner_text(timeout=5000)
            except Exception:
                body_text = ""

            image_url = extract_artist_image(page)
            monthly_listeners, monthly_rank = extract_monthly_listeners_and_rank_from_text(body_text)

            if image_url:
                result["image_url"] = image_url
            if monthly_listeners is not None:
                result["monthly_listeners"] = monthly_listeners
            if monthly_rank is not None:
                result["monthly_rank"] = monthly_rank

            if result["image_url"] and result["monthly_listeners"] is not None:
                break

    finally:
        browser.close()
        p.stop()

    return result


def load_existing_artist_metadata() -> dict:
    if not ARTIST_PATH.exists():
        return {}

    try:
        return json.loads(ARTIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_artist_metadata(data: dict) -> None:
    save_json(ARTIST_PATH, data)
    save_json(SITE_ARTIST_PATH, data)


def update_artist_metadata() -> dict:
    existing = load_existing_artist_metadata()
    scraped = scrape_artist_metadata()

    merged = {
        "name": scraped.get("name") or existing.get("name") or "Taylor Swift",
        "spotify_url": scraped.get("spotify_url") or existing.get("spotify_url") or ARTIST_URL,
        "image_url": scraped.get("image_url") or existing.get("image_url"),
        "monthly_listeners": (
            scraped.get("monthly_listeners")
            if scraped.get("monthly_listeners") is not None
            else existing.get("monthly_listeners")
        ),
        "monthly_rank": (
            scraped.get("monthly_rank")
            if scraped.get("monthly_rank") is not None
            else existing.get("monthly_rank")
        ),
        "updated_at": get_scrape_date_str(),
    }

    save_artist_metadata(merged)

    print(
        "Artist metadata updated | "
        f"monthly_listeners={format_int(merged.get('monthly_listeners'))} | "
        f"rank={merged.get('monthly_rank') if merged.get('monthly_rank') is not None else 'N/A'}"
    )

    return merged


def main():
    data = update_artist_metadata()

    print("Artist metadata updated.")
    print(f"Name: {data.get('name')}")
    print(f"Image: {data.get('image_url') or 'N/A'}")
    print(f"Monthly listeners: {format_int(data.get('monthly_listeners'))}")
    print(
        "Rank: "
        + (f"#{data.get('monthly_rank')}" if data.get("monthly_rank") is not None else "N/A")
    )
    print(f"Saved to: {ARTIST_PATH}")
    print(f"Copied to: {SITE_ARTIST_PATH}")


if __name__ == "__main__":
    main()