from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = SCRIPT_DIR.parents[2]
DB_ROOT = REPO_ROOT / "db"
DATA_DIR = REPO_ROOT / "website" / "data"
DISCOGRAPHY_DIR = DB_ROOT / "discography"
ARTIST_PATH = DISCOGRAPHY_DIR / "artist.json"
ARTIST_MONTHLY_HISTORY_PATH = (
    DB_ROOT / "artist_monthly_listeners_history.csv"
    if (DB_ROOT / "artist_monthly_listeners_history.csv").exists()
    else REPO_ROOT / "data" / "_archive" / "original" / "db" / "artist_monthly_listeners_history.csv"
)
ARTIST_URL = "https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02"
PAGE_GOTO_TIMEOUT_MS = 20_000


def get_scrape_date_str() -> str:
    return date.today().isoformat()


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


def is_duration_line(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", text.strip()))


def is_large_number_line(text: str) -> bool:
    cleaned = text.strip().replace("\u202f", " ").replace("\xa0", " ")
    if not re.fullmatch(r"[\d\s,.\']+", cleaned):
        return False
    value = parse_int_from_text(cleaned)
    return value is not None and value >= 1000


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = value.replace("â€™", "'").replace("â€˜", "'").replace("â€œ", '"').replace("â€", '"')
    value = re.sub(r"\s+", " ", value)
    return value


def extract_monthly_listeners_and_rank_from_text(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    monthly_listeners = None
    monthly_rank = None
    text_compact = re.sub(r"\s+", " ", text).strip()
    monthly_patterns = [
        r"([\d\s.,]+)\s+monthly listeners",
        r"monthly listeners\s*[:\-]?\s*([\d\s.,]+)",
        r"([\d\s.,]+)\s+auditeurs mensuels",
        r"auditeurs mensuels\s*[:\-]?\s*([\d\s.,]+)",
    ]
    for pattern in monthly_patterns:
        match = re.search(pattern, text_compact, re.IGNORECASE)
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
        match = re.search(pattern, text_compact, re.IGNORECASE)
        if match:
            monthly_rank = parse_int_from_text(match.group(1))
            if monthly_rank is not None:
                break
    return monthly_listeners, monthly_rank


def block_unneeded(route):
    request = route.request
    url = request.url.lower()
    resource_type = request.resource_type
    blocked_resource_types = {"media", "font", "image"}
    blocked_keywords = (
        "doubleclick", "googletagmanager", "google-analytics", "analytics",
        "facebook", "pixel", "ads", ".mp4", ".webm", ".mp3", ".wav",
        ".ogg", ".woff", ".woff2", ".ttf",
    )
    if resource_type in blocked_resource_types or any(x in url for x in blocked_keywords):
        route.abort()
    else:
        route.continue_()


def maybe_accept_cookies(page) -> None:
    for pattern in [r"Accept", r"Accept all", r"Accepter", r"Autoriser"]:
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=1500)
            page.wait_for_timeout(1000)
            return
        except Exception:
            pass


def launch_browser(playwright):
    return playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--disable-features=IsolateOrigins,site-per-process"],
    )

def extract_artist_image(page) -> str | None:
    selectors = [
        'img[alt="Taylor Swift"]',
        'img[src*="i.scdn.co"]',
        "img",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = min(loc.count(), 12)
        except Exception:
            continue

        for i in range(count):
            try:
                src = (loc.nth(i).get_attribute("src") or "").strip()
                alt = (loc.nth(i).get_attribute("alt") or "").strip()
            except Exception:
                continue

            if not src:
                continue

            if "i.scdn.co" in src:
                if alt.lower() == "taylor swift" or selector != 'img[alt="Taylor Swift"]':
                    return src

    return None

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
    browser = launch_browser(p)
    context = browser.new_context(locale="fr-FR")
    page = context.new_page()
    page.route("**/*", block_unneeded)

    try:
        success = False

        for attempt in range(2):
            try:
                page.goto(ARTIST_URL, wait_until="commit", timeout=PAGE_GOTO_TIMEOUT_MS)
                page.wait_for_timeout(4000)
                maybe_accept_cookies(page)
                success = True
                break
            except PlaywrightTimeoutError:
                print(f"Artist page timeout ({attempt + 1}/2)")
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"Artist page error ({attempt + 1}/2): {e}")
                page.wait_for_timeout(2000)

        if not success:
            return result

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

def extract_populaires_from_lines(lines: list[str]) -> dict[str, int]:
    """Extract {normalized_title: stream_count} from the 'Populaires' section of the artist page."""
    result: dict[str, int] = {}

    start_idx = None
    for i, line in enumerate(lines):
        if normalize_title(line) in ("populaires", "popular"):
            start_idx = i
            break

    if start_idx is None:
        return result

    end_markers = {
        "selection de l artiste", "selection de l artiste",
        "artist pick", "sur la route", "on tour", "sélection de l artiste",
    }

    block: list[str] = []
    for line in lines[start_idx + 1:]:
        if normalize_title(line) in end_markers:
            break
        block.append(line.strip())

    i = 0
    while i < len(block):
        line = block[i]

        # Skip rank numbers, durations, bullets, empty lines
        if not line or line in {"•", "-", "..."} or line.isdigit() or is_duration_line(line) or is_large_number_line(line):
            i += 1
            continue

        # Candidate title — look ahead for a stream count
        norm_title = normalize_title(line)
        for j in range(i + 1, min(i + 6, len(block))):
            if is_large_number_line(block[j]):
                count = parse_int_from_text(block[j])
                if count is not None:
                    result[norm_title] = count
                break

        i += 1

    return result

def scrape_artist_top_tracks() -> dict[str, int]:
    """Scrape the artist page and return {normalized_title: stream_count} for the top 10 tracks."""
    print("Pre-scraping artist page for top tracks…")
    result: dict[str, int] = {}

    p = sync_playwright().start()
    browser = launch_browser(p)
    context = browser.new_context(locale="fr-FR")
    page = context.new_page()
    page.route("**/*", block_unneeded)

    try:
        page.goto(ARTIST_URL, wait_until="commit", timeout=PAGE_GOTO_TIMEOUT_MS)
        page.wait_for_timeout(3000)
        maybe_accept_cookies(page)

        # Click "Afficher plus" / "Show more" to expand from 5 to 10 tracks
        for label in (r"afficher plus", r"show more"):
            try:
                page.get_by_text(re.compile(label, re.I)).first.click(timeout=3000)
                page.wait_for_timeout(1500)
                break
            except Exception:
                pass

        try:
            body_text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            return result

        lines = [
            line.replace("\u202f", " ").replace("\xa0", " ").strip()
            for line in body_text.splitlines() if line.strip()
        ]
        result = extract_populaires_from_lines(lines)

    except Exception as e:
        print(f"  Artist pre-scrape error: {e}")
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    print(f"  Artist pre-scrape: {len(result)} track(s) found")
    return result

def load_existing_artist_metadata() -> dict:
    if not ARTIST_PATH.exists():
        return {}

    try:
        return json.loads(ARTIST_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}

def save_artist_metadata(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARTIST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def upsert_artist_monthly_history(*, day: str, monthly_listeners: int | None, monthly_rank: int | None) -> None:
    """Upsert a single day of artist monthly listeners/rank into a small CSV history.

    This allows the frontend (or any other consumer) to compute deltas reliably,
    even when the API only returns the current snapshot.
    """

    if not day:
        return
    if monthly_listeners is None and monthly_rank is None:
        return

    ARTIST_MONTHLY_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "monthly_listeners", "monthly_rank"]

    rows: list[dict] = []
    if ARTIST_MONTHLY_HISTORY_PATH.exists():
        try:
            with ARTIST_MONTHLY_HISTORY_PATH.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            rows = []

    updated = False
    for row in rows:
        if (row.get("date") or "").strip() == day:
            row["monthly_listeners"] = "" if monthly_listeners is None else str(int(monthly_listeners))
            row["monthly_rank"] = "" if monthly_rank is None else str(int(monthly_rank))
            updated = True
            break

    if not updated:
        rows.append(
            {
                "date": day,
                "monthly_listeners": "" if monthly_listeners is None else str(int(monthly_listeners)),
                "monthly_rank": "" if monthly_rank is None else str(int(monthly_rank)),
            }
        )

    with ARTIST_MONTHLY_HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def update_artist_metadata(pre_scraped: dict | None = None) -> dict:
    existing = load_existing_artist_metadata()
    scraped = pre_scraped if pre_scraped is not None else scrape_artist_metadata()

    today = get_scrape_date_str()
    existing_updated_at = (existing.get("updated_at") or "").strip()
    prev_monthly_listeners = None
    prev_monthly_rank = None

    if existing_updated_at and existing_updated_at != today:
        prev_monthly_listeners = existing.get("monthly_listeners")
        prev_monthly_rank = existing.get("monthly_rank")
    else:
        prev_monthly_listeners = existing.get("previous_monthly_listeners")
        prev_monthly_rank = existing.get("previous_monthly_rank")

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
        "previous_monthly_listeners": prev_monthly_listeners,
        "previous_monthly_rank": prev_monthly_rank,
        "updated_at": today,
    }

    save_artist_metadata(merged)

    upsert_artist_monthly_history(
        day=today,
        monthly_listeners=merged.get("monthly_listeners"),
        monthly_rank=merged.get("monthly_rank"),
    )

    print(
        "Artist metadata updated | "
        f"monthly_listeners={format_int(merged.get('monthly_listeners'))} | "
        f"rank={merged.get('monthly_rank') if merged.get('monthly_rank') is not None else 'N/A'}"
    )

    return merged
