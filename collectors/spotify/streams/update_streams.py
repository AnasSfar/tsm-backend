from __future__ import annotations

import csv
import json
import re
import subprocess
import threading
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from queue import Empty, Queue
import sys

import requests as _requests

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[2]

sys.path.insert(0, str(_SCRIPT_DIR / "tools" / "scripts"))
sys.path.insert(0, str(_SCRIPT_DIR / "extras"))
sys.path.insert(0, str(_SCRIPT_DIR.parents[0]))  # collectors/spotify/ for core.*

import export_for_web
from git_ops import git_commit_and_push
from config import NTFY_TOPIC
from core.notify import send as notify

ROOT = _REPO_ROOT / "website"
DATA_DIR = ROOT / "data"
_DB_ROOT = _REPO_ROOT / "db"

HISTORY_PATH = _DB_ROOT / "streams_history.csv"
FAILED_PATH = DATA_DIR / "not_found_today.csv"
PENDING_LOG_PATH = DATA_DIR / "pending_debug_today.csv"
LAST_SUCCESSFUL_UPDATE_JSON = DATA_DIR / "last_successful_updates.json"
LAST_UNFINISHED_UPDATE_JSON = DATA_DIR / "last_unfinished_updates.json"

DISCOGRAPHY_DIR = _DB_ROOT / "discography"
DB_ALBUMS_DIR = DISCOGRAPHY_DIR / "albums"
DB_SONGS_JSON = DISCOGRAPHY_DIR / "songs.json"
ARTIST_PATH = DISCOGRAPHY_DIR / "artist.json"
ARTIST_URL = "https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02"

HEADLESS = True
MAX_PARALLEL_PAGES = 10
PAGE_GOTO_TIMEOUT_MS = 20_000
DEBUG_PAGE_PREVIEW = False
BROWSER_CACHE_DIR = _SCRIPT_DIR / "tools" / "browser_cache"

# Hill climbing
HILL_WINDOW        = 20     # completions par fenêtre d'évaluation
HILL_429_THRESHOLD = 0.15   # taux de 429 au-delà duquel on retire 1 worker
HILL_MIN_WORKERS   = 2
HILL_INITIAL       = 6      # point de départ (MAX_PARALLEL_PAGES comme plafond)

PROBE_TITLES = [
    "Cruel Summer",
    "Anti-Hero",
    "Blank Space",
    "Style",
    "cardigan",
]
MIN_SUCCESSFUL_PROBES = 2
MIN_UPDATED_PROBES_TO_START = 1

PENDING_RETRY_SLEEP_SECONDS = 1 * 60
MAX_PENDING_RETRY_ROUNDS = 6
INCREMENTAL_PUBLISH_ON_UPDATE = False

NOT_FOUND_STREAK_PATH = DATA_DIR / "not_found_streak.json"
MAX_NOT_FOUND_DAYS = 7

MAX_DAILY_INCREASE = 50_000_000

# ── API GraphQL Spotify ───────────────────────────────────────────────────────
GRAPHQL_URL   = "https://api-partner.spotify.com/pathfinder/v2/query"
GETTRACK_HASH = "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294"
APP_VERSION   = "1.2.87.30.gc764ebf1"
_SESSION_FILE = _SCRIPT_DIR.parents[0] / "charts/global/tools/json/spotify_session.json"

START_TIME = None


def print_help() -> None:
    print(
        """
Usage:
  python update_streams.py
      Run normal for yesterday's stats date.

  python update_streams.py YYYY-MM-DD
      Run normal for a specific stats date.

  python update_streams.py --debug-daily
      Retry unfinished tracks for yesterday's stats date, writes to history,
      but skips Twitter / git / forecast / images / notify.

  python update_streams.py --debug-daily YYYY-MM-DD
      Same as above for a specific stats date.

  python update_streams.py --debug-total YYYY-MM-DD
      Re-scrape totals and replace totals for the given date in streams_history.csv.
      Recomputes daily_streams from the previous date. No Twitter / git / forecast / images / notify.

  python update_streams.py --dry-run
      Scrape only. No writes anywhere.

  python update_streams.py --no-post
      Run full pipeline but skip all Twitter posting steps.

  python update_streams.py --reset-last-date
      Delete all rows for the latest date found in streams_history.csv before running.

  python update_streams.py --reset-date YYYY-MM-DD
      Delete all rows for that date before running.

  python update_streams.py --help
      Show this help.

Notes:
  - Normal mode writes official updates and can post/export/push.
    - --no-post keeps processing/export/commit but skips Twitter posts.
  - --debug-daily writes missing updates into history, but stays local/no posting.
  - --debug-total rewrites an existing date's totals in history.
        """.strip()
    )


def get_scrape_date_str() -> str:
    return date.today().isoformat()


def get_stats_date_str() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def get_previous_stats_date_str(stats_date: str) -> str:
    return (date.fromisoformat(stats_date) - timedelta(days=1)).isoformat()


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


def extract_track_id(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"track/([A-Za-z0-9]+)", url)
    return match.group(1) if match else None


def load_album_sections_flat() -> list[dict]:
    """Load album sections from db/discography/albums/*.json as a flat list."""
    if not DB_ALBUMS_DIR.exists():
        return []

    sections: list[dict] = []
    for album_file in sorted(DB_ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            payload = json.loads(album_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        album_name = payload.get("album", "") if isinstance(payload, dict) else ""
        raw_sections = payload.get("sections", []) if isinstance(payload, dict) else []
        if not isinstance(raw_sections, list):
            continue

        for section in raw_sections:
            if not isinstance(section, dict):
                continue
            item = dict(section)
            if not item.get("album"):
                item["album"] = album_name
            sections.append(item)

    return sections


def normalize_spotify_track_url(url: str) -> str:
    track_id = extract_track_id(url)
    if track_id:
        return f"https://open.spotify.com/track/{track_id}"
    return url.strip()


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
    value = value.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
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


def maybe_accept_cookies(page) -> None:
    patterns = [
        r"Accept",
        r"Accept all",
        r"Accepter",
        r"Autoriser",
    ]

    for pattern in patterns:
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=1500)
            page.wait_for_timeout(1000)
            return
        except Exception:
            pass


def launch_browser(playwright):
    return playwright.chromium.launch(
        headless=HEADLESS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )


def launch_persistent_worker_context(playwright, worker_id: int):
    """
    Lance un contexte Playwright persistant (cache disque) pour un worker donné.
    Le cache JS/CSS de Spotify est réutilisé entre les runs → chargements ~50% plus rapides.
    Retourne le context (pas de browser séparé avec launch_persistent_context).
    """
    worker_cache = BROWSER_CACHE_DIR / f"worker_{worker_id}"
    worker_cache.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(worker_cache),
        headless=HEADLESS,
        locale="fr-FR",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )


class AdaptiveWorkerState:
    """
    Partagé entre tous les workers de update_streams.py.
    Suit le taux de 429 sur des fenêtres glissantes et ajuste
    le nombre cible de workers actifs (hill climbing).
    """

    def __init__(self, initial: int) -> None:
        self.target     = initial
        self.lock       = threading.Lock()
        self._win_done  = 0
        self._win_429   = 0
        self._win_start = time.time()

    def record(self, got_429: bool) -> None:
        with self.lock:
            self._win_done += 1
            if got_429:
                self._win_429 += 1

            if self._win_done >= HILL_WINDOW:
                elapsed  = max(time.time() - self._win_start, 0.001)
                rate_429 = self._win_429 / self._win_done
                rate_sps = self._win_done / elapsed

                if rate_429 > HILL_429_THRESHOLD and self.target > HILL_MIN_WORKERS:
                    self.target -= 1
                    print(f"  [hill] 429={rate_429:.0%}  {rate_sps:.2f}/s  -> workers: {self.target}")
                elif rate_429 == 0 and self.target < MAX_PARALLEL_PAGES:
                    self.target += 1
                    print(f"  [hill] 0 429s  {rate_sps:.2f}/s  -> workers: {self.target}")

                self._win_done  = 0
                self._win_429   = 0
                self._win_start = time.time()


class TokenManager:
    """
    Capture Bearer + client-token depuis Spotify une seule fois via Playwright.
    Thread-safe : sur 401, un seul thread re-capture, les autres attendent.
    """

    def __init__(self) -> None:
        self._tokens: dict = {}
        self._lock = threading.Lock()
        self._recapturing = threading.Event()

    def capture(self) -> bool:
        """Ouvre Playwright, charge une track, capture les tokens. Retourne True si succès."""
        tokens: dict = {}

        def on_request(req):
            if "api-partner.spotify.com" in req.url and not tokens.get("bearer"):
                auth = req.headers.get("authorization", "")
                ct   = req.headers.get("client-token", "")
                if auth.startswith("Bearer ") and ct:
                    tokens["bearer"]       = auth[7:]
                    tokens["client_token"] = ct

        print("TokenManager: capture des tokens Spotify via Playwright…")
        ctx_kwargs: dict = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
            ),
        }
        if _SESSION_FILE.exists():
            ctx_kwargs["storage_state"] = str(_SESSION_FILE)

        p = sync_playwright().start()
        browser = None
        try:
            browser = p.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx  = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()
            page.on("request", on_request)
            page.goto(
                "https://open.spotify.com/track/0V3wPSX9ygBnCm8psDIegu",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            deadline = time.time() + 20
            while not tokens.get("bearer") and time.time() < deadline:
                page.wait_for_timeout(300)
        except Exception as e:
            print(f"TokenManager: erreur capture: {e}")
        finally:
            try:
                browser.close()
            except Exception:
                pass
            try:
                p.stop()
            except Exception:
                pass

        if tokens.get("bearer"):
            with self._lock:
                self._tokens = tokens
            print(f"TokenManager: Bearer capturé ({tokens['bearer'][:20]}…)")
            return True
        print("TokenManager: échec — fallback Playwright pour toutes les tracks")
        return False

    def get(self) -> dict:
        with self._lock:
            return dict(self._tokens)

    def mark_expired(self) -> None:
        """Appelé par un worker sur 401 — déclenche une re-capture (un seul thread à la fois)."""
        if self._recapturing.is_set():
            # Attendre que le thread en cours de re-capture finisse
            while self._recapturing.is_set():
                time.sleep(0.5)
            return
        self._recapturing.set()
        try:
            self.capture()
        finally:
            self._recapturing.clear()

    @property
    def available(self) -> bool:
        with self._lock:
            return bool(self._tokens.get("bearer"))


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
        return json.loads(ARTIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_artist_metadata(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARTIST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def update_artist_metadata(pre_scraped: dict | None = None) -> dict:
    existing = load_existing_artist_metadata()
    scraped = pre_scraped if pre_scraped is not None else scrape_artist_metadata()

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


def load_album_track_ids() -> set[str]:
    """Returns track IDs from album files only (excludes songs.json extras)."""
    sections = load_album_sections_flat()
    ids = set()
    for section in sections:
        for track in section.get("tracks", []):
            url = (track.get("url") or track.get("spotify_url") or "").strip()
            tid = extract_track_id(url)
            if tid:
                ids.add(tid)
    return ids


def all_album_tracks_done(stats_date: str) -> bool:
    """Returns True when every album-file track has a history row for stats_date."""
    album_ids = load_album_track_ids()
    if not album_ids:
        return True
    done_ids = load_history_track_ids_for_date(stats_date)
    return album_ids.issubset(done_ids)


def album_tracks_done_for(album_name: str, stats_date: str) -> bool:
    """Returns True when every track from the given album has a history row for stats_date."""
    sections = load_album_sections_flat()
    album_ids = set()
    for section in sections:
        if section.get("album") != album_name:
            continue
        for track in section.get("tracks", []):
            tid = extract_track_id(track.get("url") or track.get("spotify_url") or "")
            if tid:
                album_ids.add(tid)
    if not album_ids:
        return False
    done_ids = load_history_track_ids_for_date(stats_date)
    return album_ids.issubset(done_ids)


def load_active_track_ids_from_discography() -> set[str]:
    active_track_ids = set()

    for data in load_album_sections_flat():
        for track in data.get("tracks", []):
            url = (track.get("url") or track.get("spotify_url") or "").strip()
            track_id = extract_track_id(url)
            if track_id:
                active_track_ids.add(track_id)

    if DB_SONGS_JSON.exists():
        try:
            sections = json.loads(DB_SONGS_JSON.read_text(encoding="utf-8"))
        except Exception:
            sections = []
        for data in sections:
            for track in data.get("tracks", []):
                url = (track.get("url") or track.get("spotify_url") or "").strip()
                track_id = extract_track_id(url)
                if track_id:
                    active_track_ids.add(track_id)

    return active_track_ids


def ensure_history_file() -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not HISTORY_PATH.exists():
        with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "track_id", "streams", "daily_streams"])


def get_last_stats_date_in_history() -> str | None:
    if not HISTORY_PATH.exists():
        return None

    last_date = None
    with HISTORY_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = (row.get("date") or "").strip()
            if d:
                last_date = d
    return last_date


def delete_history_rows_for_date(target_date: str) -> int:
    if not HISTORY_PATH.exists():
        return 0

    with HISTORY_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or ["date", "track_id", "streams", "daily_streams"]

    kept_rows = [r for r in rows if (r.get("date") or "").strip() != target_date]
    removed = len(rows) - len(kept_rows)

    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    return removed


def append_history_row(row: list) -> None:
    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def load_history_rows() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []

    with HISTORY_PATH.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_history_rows(rows: list[dict]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "track_id", "streams", "daily_streams"]
    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "date": row.get("date", ""),
                    "track_id": row.get("track_id", ""),
                    "streams": row.get("streams", ""),
                    "daily_streams": row.get("daily_streams", ""),
                }
            )


def load_history_track_ids_for_date(stats_date: str) -> set[str]:
    if not HISTORY_PATH.exists():
        return set()

    done = set()
    with HISTORY_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("date") or "").strip() == stats_date:
                track_id = (row.get("track_id") or "").strip()
                if track_id:
                    done.add(track_id)
    return done


def get_last_history_total(track_id: str) -> int | None:
    if not HISTORY_PATH.exists():
        return None

    last = None
    with HISTORY_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("track_id") == track_id:
                try:
                    last = int(row["streams"])
                except Exception:
                    pass
    return last


def get_previous_total_before_date(track_id: str, stats_date: str) -> int | None:
    if not HISTORY_PATH.exists():
        return None

    target = date.fromisoformat(stats_date)
    best_date = None
    best_total = None

    with HISTORY_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("track_id") or "").strip() != track_id:
                continue

            d_raw = (row.get("date") or "").strip()
            s_raw = (row.get("streams") or "").strip()
            if not d_raw or not s_raw:
                continue

            try:
                d = date.fromisoformat(d_raw)
                total = int(s_raw)
            except Exception:
                continue

            if d >= target:
                continue

            if best_date is None or d > best_date:
                best_date = d
                best_total = total

    return best_total


def has_real_update(previous_streams: int | None, new_streams: int) -> bool:
    if previous_streams is None:
        return True
    if new_streams <= previous_streams:
        return False
    if new_streams - previous_streams > MAX_DAILY_INCREASE:
        print(
            f"  [ANOMALY REJECTED] {new_streams:,} "
            f"(prev={previous_streams:,}, delta=+{new_streams - previous_streams:,}) "
            f"— exceeds {MAX_DAILY_INCREASE:,}/day cap, skipping"
        )
        return False
    return True


def compute_daily(previous_streams: int | None, new_streams: int) -> int | None:
    if previous_streams is None:
        return None
    diff = new_streams - previous_streams
    if diff < 0:
        return None
    return diff


def load_tracks_from_discography(active_track_ids: set[str] | None = None) -> list[dict]:
    seen: dict[str, dict] = {}

    all_sections = load_album_sections_flat()
    if DB_SONGS_JSON.exists():
        try:
            all_sections.extend(json.loads(DB_SONGS_JSON.read_text(encoding="utf-8")))
        except Exception:
            pass

    for section in all_sections:
        for track in section.get("tracks", []):
            url = (track.get("url") or track.get("spotify_url") or "").strip()
            track_id = extract_track_id(url)
            if not track_id or track_id in seen:
                continue

            title = (track.get("title") or "").strip()
            if not title:
                continue

            if active_track_ids is not None and track_id not in active_track_ids:
                continue

            spotify_url = f"https://open.spotify.com/track/{track_id}"
            image_url = track.get("image_url") or None
            artists = track.get("artists") or []
            primary_artist = track.get("primary_artist") or (artists[0] if artists else None)

            seen[track_id] = {
                "track_id": track_id,
                "title": title,
                "spotify_url": spotify_url,
                "streams": None,
                "daily_streams": None,
                "last_updated": None,
                "image_url": image_url,
                "primary_artist": primary_artist,
                "artists_json": json.dumps(artists),
            }

    tracks = list(seen.values())
    tracks.sort(key=lambda t: t["title"].casefold())
    return tracks


def build_track_lookup(tracks: list[dict]) -> dict[str, list[dict]]:
    lookup: dict[str, list[dict]] = {}
    for track in tracks:
        key = normalize_title(track["title"])
        lookup.setdefault(key, []).append(track)
    return lookup


def load_track_priorities_from_specific_date(target_date: str) -> dict[str, int]:
    result: dict[str, int] = {}

    if not HISTORY_PATH.exists():
        return result

    with HISTORY_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("date") or "").strip() != target_date:
                continue

            track_id = (row.get("track_id") or "").strip()
            daily_raw = (row.get("daily_streams") or "").strip()

            if not track_id or not daily_raw:
                continue

            try:
                daily = int(daily_raw)
            except ValueError:
                continue

            result[track_id] = daily

    return result


def get_priority_top_50_track_ids_from_previous_day(tracks: list[dict], stats_date: str) -> set[str]:
    previous_date = get_previous_stats_date_str(stats_date)
    priorities = load_track_priorities_from_specific_date(previous_date)

    ordered = sorted(
        tracks,
        key=lambda t: (-priorities.get(t["track_id"], 0), t["title"].casefold())
    )
    return {t["track_id"] for t in ordered[:50]}


def save_failed_rows(rows: list[dict]) -> None:
    if not rows:
        if FAILED_PATH.exists():
            FAILED_PATH.unlink()
        return

    with FAILED_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "track_id", "spotify_url", "status"])
        for row in rows:
            writer.writerow(
                [
                    row["title"],
                    row["track_id"],
                    row.get("spotify_url", ""),
                    row.get("status", ""),
                ]
            )


def save_pending_debug_rows(rows: list[dict]) -> None:
    if not rows:
        if PENDING_LOG_PATH.exists():
            PENDING_LOG_PATH.unlink()
        return

    with PENDING_LOG_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "title",
            "track_id",
            "spotify_url",
            "previous_streams",
            "new_streams",
            "delta",
            "reason",
            "raw",
        ])
        for row in rows:
            writer.writerow([
                row.get("title", ""),
                row.get("track_id", ""),
                row.get("spotify_url", ""),
                row.get("previous_streams", ""),
                row.get("new_streams", ""),
                row.get("delta", ""),
                row.get("reason", ""),
                row.get("raw", ""),
            ])


def save_last_successful_updates_json(stats_date: str, updated_results: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": get_scrape_date_str(),
        "stats_date": stats_date,
        "track_ids": [r["track_id"] for r in updated_results if r.get("track_id")],
        "tracks": [
            {
                "track_id": r.get("track_id"),
                "title": r.get("title"),
                "spotify_url": r.get("spotify_url"),
                "streams": r.get("streams"),
                "daily_streams": r.get("daily_streams"),
            }
            for r in updated_results
            if r.get("track_id")
        ],
    }
    LAST_SUCCESSFUL_UPDATE_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_last_unfinished_updates_json(stats_date: str, results: list[dict], failed_results: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    unfinished_map: dict[str, dict] = {}

    for r in results:
        if not r:
            continue
        if r.get("status") in {"pending", "timeout", "error", "not_found"}:
            tid = r.get("track_id")
            if tid:
                unfinished_map[tid] = {
                    "track_id": r.get("track_id"),
                    "title": r.get("title"),
                    "spotify_url": r.get("spotify_url"),
                    "status": r.get("status"),
                    "streams": r.get("streams"),
                    "daily_streams": r.get("daily_streams"),
                    "previous_streams": r.get("previous_streams"),
                    "delta": r.get("delta"),
                    "reason": r.get("reason"),
                    "raw": r.get("raw"),
                }

    for r in failed_results:
        tid = r.get("track_id")
        if tid:
            unfinished_map[tid] = {
                "track_id": r.get("track_id"),
                "title": r.get("title"),
                "spotify_url": r.get("spotify_url"),
                "status": r.get("status"),
                "streams": r.get("streams"),
                "daily_streams": r.get("daily_streams"),
                "previous_streams": r.get("previous_streams"),
                "delta": r.get("delta"),
                "reason": r.get("reason"),
                "raw": r.get("raw"),
            }

    payload = {
        "generated_at": get_scrape_date_str(),
        "stats_date": stats_date,
        "track_ids": list(unfinished_map.keys()),
        "tracks": list(unfinished_map.values()),
    }

    LAST_UNFINISHED_UPDATE_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_last_unfinished_update_track_ids(stats_date: str | None = None) -> set[str]:
    if not LAST_UNFINISHED_UPDATE_JSON.exists():
        return set()

    try:
        payload = json.loads(LAST_UNFINISHED_UPDATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return set()

    payload_date = payload.get("stats_date")
    if stats_date is not None and payload_date and payload_date != stats_date:
        return set()

    track_ids = payload.get("track_ids") or []
    return {tid for tid in track_ids if isinstance(tid, str) and tid}


def load_not_found_streak() -> dict:
    if not NOT_FOUND_STREAK_PATH.exists():
        return {}
    try:
        return json.loads(NOT_FOUND_STREAK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_not_found_streak(streak: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NOT_FOUND_STREAK_PATH.write_text(
        json.dumps(streak, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def update_not_found_streak(streak: dict, not_found_ids: set[str], updated_ids: set[str]) -> None:
    for track_id in not_found_ids:
        streak[track_id] = streak.get(track_id, 0) + 1
    for track_id in updated_ids:
        streak.pop(track_id, None)


def remove_track_from_discography(track_id: str) -> int:
    removed = 0

    if DB_ALBUMS_DIR.exists():
        for album_file in sorted(DB_ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue

            raw_sections = payload.get("sections", [])
            if not isinstance(raw_sections, list):
                continue

            changed = False
            for data in raw_sections:
                if not isinstance(data, dict):
                    continue
                tracks = data.get("tracks", [])
                if not isinstance(tracks, list):
                    tracks = []
                new_tracks = [
                    t for t in tracks
                    if extract_track_id(t.get("url") or t.get("spotify_url") or "") != track_id
                ]
                if len(new_tracks) < len(tracks):
                    data["tracks"] = new_tracks
                    data["track_count"] = len(new_tracks)
                    changed = True
                    removed += 1

            if changed:
                payload["sections"] = raw_sections
                payload["section_count"] = len(raw_sections)
                payload["track_count"] = sum(len(s.get("tracks", [])) for s in raw_sections if isinstance(s, dict))
                album_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    if DB_SONGS_JSON.exists():
        try:
            sections = json.loads(DB_SONGS_JSON.read_text(encoding="utf-8"))
        except Exception:
            sections = []

        changed = False
        for data in sections:
            tracks = data.get("tracks", [])
            new_tracks = [
                t for t in tracks
                if extract_track_id(t.get("url") or t.get("spotify_url") or "") != track_id
            ]
            if len(new_tracks) < len(tracks):
                data["tracks"] = new_tracks
                data["track_count"] = len(new_tracks)
                changed = True
                removed += 1

        if changed:
            DB_SONGS_JSON.write_text(
                json.dumps(sections, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    return removed


def purge_stale_tracks(streak: dict, tracks: list[dict]) -> list[str]:
    deleted = []
    for track_id, count in list(streak.items()):
        if count >= MAX_NOT_FOUND_DAYS:
            title = next((t["title"] for t in tracks if t["track_id"] == track_id), track_id)
            print(
                f"AUTO-DELETE | {title} | track_id={track_id} | "
                f"not found for {count} consecutive days — removing from discography"
            )
            remove_track_from_discography(track_id)
            del streak[track_id]
            deleted.append(track_id)
    return deleted


def _run_early_twitter(stats_date: str) -> None:
    try:
        print("\n[Twitter] Posting early top 15 after top-50 priority tracks are validated...")
        export_for_web.export_for_web()
        subprocess.run([sys.executable, str(_SCRIPT_DIR / "tools" / "scripts" / "migrate_streams_to_csv.py")], check=False)
        subprocess.run([sys.executable, str(_SCRIPT_DIR / "tools" / "scripts" / "post_streams_twitter.py"), stats_date], check=False)
        print("[Twitter] Early post done.")
    except Exception as e:
        print(f"[Twitter] Early post error: {e}")


def incremental_publish_update(
    track: dict,
    stats_date: str,
    publish_lock: threading.Lock,
) -> None:
    if not INCREMENTAL_PUBLISH_ON_UPDATE:
        return

    with publish_lock:
        try:
            print(
                f"Incremental publish | {track['title']} | "
                f"{track['track_id']} | stats_date={stats_date}"
            )
            export_for_web.export_for_web()
            git_commit_and_push(_REPO_ROOT, f"track update {stats_date} {track['track_id']}")
        except Exception as e:
            print(
                f"Incremental publish failed for {track['title']} "
                f"({track['track_id']}): {e}"
            )


def extract_main_track_playcount_from_lines(lines: list[str]) -> tuple[int | None, str | None]:
    if not lines:
        return None, None

    start_idx = None
    for i, line in enumerate(lines):
        if line.strip().lower() in ("titre", "title"):
            start_idx = i
            break

    if start_idx is None:
        return None, None

    end_markers = {
        "connectez-vous",
        "se connecter",
        "artiste",
        "recommandés",
        "basees sur ce titre",
        "basées sur ce titre",
        "titres populaires par",
        "sorties populaires par taylor swift",
    }

    block: list[str] = []
    for line in lines[start_idx + 1:]:
        normalized = normalize_title(line.strip())
        if normalized in end_markers:
            break
        block.append(line.strip())

    if not block:
        return None, None

    for i, line in enumerate(block):
        if is_duration_line(line):
            for j in range(i + 1, min(i + 6, len(block))):
                candidate = block[j].strip()
                if candidate in {"•", "-", ""}:
                    continue
                if is_large_number_line(candidate):
                    value = parse_int_from_text(candidate)
                    if value is not None:
                        return value, candidate

    numeric_candidates = []
    for line in block:
        cleaned = line.strip()
        if is_large_number_line(cleaned):
            value = parse_int_from_text(cleaned)
            if value is not None:
                numeric_candidates.append((value, cleaned))

    if len(numeric_candidates) == 1:
        return numeric_candidates[0]

    return None, None


def extract_recommended_tracks_from_lines(lines: list[str]) -> list[dict]:
    if not lines:
        return []

    start_idx = None
    for i, line in enumerate(lines):
        normalized = normalize_title(line)
        if normalized == "recommandes":
            start_idx = i
            break

    if start_idx is None:
        return []

    block: list[str] = []
    end_markers = {
        "titres populaires par",
        "sorties populaires par taylor swift",
        "sorties populaires par",
        "afficher plus",
    }

    for line in lines[start_idx + 1:]:
        normalized = normalize_title(line)
        if normalized in end_markers:
            break
        block.append(line.strip())

    if not block:
        return []

    results: list[dict] = []
    i = 0
    while i < len(block):
        title = block[i].strip()
        norm_title = normalize_title(title)

        if (
            not title
            or norm_title in {"basees sur ce titre", "basées sur ce titre", "e", "taylor swift"}
            or title in {"•", "-", "..."}
            or is_large_number_line(title)
            or is_duration_line(title)
            or title.isdigit()
        ):
            i += 1
            continue

        found_streams = None
        found_duration = None

        for j in range(i + 1, min(i + 8, len(block))):
            candidate = block[j].strip()

            if is_large_number_line(candidate) and found_streams is None:
                found_streams = parse_int_from_text(candidate)
                continue

            if is_duration_line(candidate) and found_duration is None:
                found_duration = candidate

            if found_streams is not None and found_duration is not None:
                break

        if found_streams is not None:
            results.append(
                {
                    "title": title,
                    "streams": found_streams,
                    "duration": found_duration,
                }
            )

        i += 1

    deduped = []
    seen = set()
    for row in results:
        key = normalize_title(row["title"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    return deduped


def extract_playcount_via_js(page) -> int | None:
    """
    Fallback: scan all [data-testid] elements for a single large numeric value
    that looks like a play count. Returns None if ambiguous or not found.
    """
    try:
        result = page.evaluate("""() => {
            const candidates = [];
            document.querySelectorAll('[data-testid]').forEach(el => {
                const txt = (el.innerText || '').trim();
                if (txt && /^[\\d\u202f\u00a0\\s,.']+$/.test(txt)) {
                    const n = parseInt(txt.replace(/[^\\d]/g, ''));
                    if (!isNaN(n) && n >= 10000) candidates.push(n);
                }
            });
            // Return only if exactly one candidate (unambiguous)
            return candidates.length === 1 ? candidates[0] : null;
        }""")
        if result is not None:
            return int(result)
    except Exception:
        pass
    return None


def extract_page_data(page) -> tuple[int | None, str | None, list[dict]]:
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None, None, []

    if not body_text:
        return None, None, []

    lines = [
        line.replace("\u202f", " ").replace("\xa0", " ").strip()
        for line in body_text.splitlines()
    ]
    lines = [line for line in lines if line]

    total, raw = extract_main_track_playcount_from_lines(lines)
    recs = extract_recommended_tracks_from_lines(lines)

    if total is None:
        # JS fallback: try finding the play count via data-testid attributes
        js_total = extract_playcount_via_js(page)
        if js_total is not None:
            total = js_total
            raw = str(js_total)

    return total, raw, recs


def debug_page_preview(page, title: str, url: str) -> None:
    if not DEBUG_PAGE_PREVIEW:
        return

    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        body_text = ""

    try:
        page_title = page.title()
    except Exception:
        page_title = ""

    print()
    print("=" * 80)
    print(f"TRACK: {title}")
    print(f"URL asked: {url}")
    print(f"URL final: {page.url}")
    print(f"PAGE TITLE: {page_title}")
    print("BODY PREVIEW:")
    print(body_text[:2500])
    print("=" * 80)
    print()


def scrape_track_total(page, title: str, url: str) -> tuple[int | None, str | None, str, list[dict]]:
    clean_url = normalize_spotify_track_url(url)

    for attempt in range(3):
        try:
            page.goto(clean_url, wait_until="commit", timeout=PAGE_GOTO_TIMEOUT_MS)
            page.wait_for_timeout(1000)
            maybe_accept_cookies(page)

            if DEBUG_PAGE_PREVIEW:
                debug_page_preview(page, title, clean_url)

            # Attendre activement l'apparition d'un grand nombre dans le DOM (max 8s)
            # Évite les sleeps fixes (500+1500+3000+5000ms) pour les pages rapides
            try:
                page.wait_for_function(
                    "() => { for (const el of document.querySelectorAll('[data-testid], span, div')) {"
                    "  const n = parseInt((el.innerText || '').replace(/[^\\d]/g, ''));"
                    "  if (!isNaN(n) && n >= 100000) return true; } return false; }",
                    timeout=8000,
                )
            except Exception:
                pass

            total, raw, recs = extract_page_data(page)
            if total is not None:
                return total, raw, "ok", recs

            # Fallback : 2 attentes courtes si le DOM n'était pas encore prêt
            for wait_ms in (1000, 2500):
                page.wait_for_timeout(wait_ms)
                total, raw, recs = extract_page_data(page)
                if total is not None:
                    return total, raw, "ok", recs

            return None, None, "not_found", []

        except PlaywrightTimeoutError:
            print(f"SCRAPE TIMEOUT on {title}: {clean_url} (attempt {attempt + 1}/3)")
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass

        except Exception as e:
            print(f"SCRAPE ERROR on {title}: {e} (attempt {attempt + 1}/3)")
            try:
                page.wait_for_timeout(1000)
            except Exception:
                pass

    return None, None, "timeout", []


def try_apply_track_update(
    track: dict,
    total: int,
    stats_date: str,
    lock: threading.Lock,
    publish_lock: threading.Lock,
    dry_run_mode: bool = False,
) -> dict:
    track_id = track["track_id"]
    last_total = get_last_history_total(track_id)
    daily = compute_daily(last_total, total)

    if last_total is None:
        reason = "first_seen"
        real_update = True
    elif total == last_total:
        reason = "same_total"
        real_update = False
    elif total < last_total:
        reason = "lower_than_previous"
        real_update = False
    elif total - last_total > MAX_DAILY_INCREASE:
        reason = f"anomaly_delta_gt_{MAX_DAILY_INCREASE}"
        real_update = False
    else:
        reason = "updated"
        real_update = True

    if not dry_run_mode and real_update:
        row = [stats_date, track_id, total, daily if daily is not None else ""]
        with lock:
            append_history_row(row)

        status = "updated"

        incremental_publish_update(
            track=track,
            stats_date=stats_date,
            publish_lock=publish_lock,
        )
    else:
        status = "pending"

    return {
        "track_id": track_id,
        "title": track["title"],
        "spotify_url": track["spotify_url"],
        "status": status,
        "streams": total,
        "daily_streams": daily,
        "previous_streams": last_total,
        "delta": (total - last_total) if last_total is not None else None,
        "reason": reason,
    }


def live_progress(i, total, title, result):
    elapsed = time.perf_counter() - START_TIME if START_TIME else 0
    done = max(i - 1, 0) if result is None else i
    remaining = ((elapsed / done) * max(total - done, 0)) if done > 0 else 0
    eta = f"{int(remaining // 60)}m {int(remaining % 60)}s"
    prefix = f"[{i}/{total}] {title}"

    if result is None:
        print(f"{prefix} ... scraping | ETA {eta}")
        return

    status = result.get("status")

    if status == "updated":
        print(
            f"{prefix} OK | total={format_int(result.get('streams'))} | "
            f"daily={format_int(result.get('daily_streams'))} | ETA {eta}"
        )
    elif status == "pending":
        print(
            f"{prefix} PENDING | total={format_int(result.get('streams'))} | "
            f"prev={format_int(result.get('previous_streams'))} | "
            f"delta={format_int(result.get('delta'))} | "
            f"reason={result.get('reason')} | ETA {eta}"
        )
    elif status == "skipped":
        print(f"{prefix} SKIPPED | ETA {eta}")
    elif status == "timeout":
        print(f"{prefix} TIMEOUT | ETA {eta}")
    elif status == "error":
        print(f"{prefix} ERROR | ETA {eta}")
    elif status == "not_found":
        print(f"{prefix} NOT FOUND | ETA {eta}")
    else:
        print(f"{prefix} {status.upper()} | ETA {eta}")


def _probe_on_page(probe_tracks: list[dict], page) -> dict:
    results = []
    successful_probes = 0
    updated_probes = 0

    for track in probe_tracks:
        title = track["title"]
        url = track["spotify_url"]
        total, raw, scrape_status, _ = scrape_track_total(page, title, url)

        if scrape_status == "ok" and total is not None:
            successful_probes += 1
            last_total = get_last_history_total(track["track_id"])
            updated = has_real_update(last_total, total)
            if updated:
                updated_probes += 1

            results.append(
                {
                    "title": title,
                    "status": "ok",
                    "streams": total,
                    "previous_streams": last_total,
                    "updated": updated,
                    "raw": raw,
                }
            )
        else:
            results.append(
                {
                    "title": title,
                    "status": scrape_status,
                    "streams": None,
                    "previous_streams": get_last_history_total(track["track_id"]),
                    "updated": False,
                    "raw": None,
                }
            )

    can_start_full_run = (
        successful_probes >= MIN_SUCCESSFUL_PROBES
        and updated_probes >= MIN_UPDATED_PROBES_TO_START
    )

    return {
        "can_start_full_run": can_start_full_run,
        "successful_probes": successful_probes,
        "updated_probes": updated_probes,
        "results": results,
    }


def fetch_playcount_api(
    track_id: str,
    token_mgr: TokenManager,
    session: "_requests.Session",
) -> int | None:
    """
    Récupère le playcount via l'API GraphQL Spotify.
    Retourne un int, ou None si la track n'est pas trouvée / erreur.
    Sur 401, déclenche une re-capture des tokens et retente une fois.
    """
    tokens = token_mgr.get()
    if not tokens.get("bearer"):
        return None

    body = {
        "variables":     {"uri": f"spotify:track:{track_id}"},
        "operationName": "getTrack",
        "extensions":    {
            "persistedQuery": {
                "version":    1,
                "sha256Hash": GETTRACK_HASH,
            }
        },
    }

    for attempt in range(2):
        headers = {
            "Authorization":       f"Bearer {tokens['bearer']}",
            "client-token":        tokens["client_token"],
            "spotify-app-version": APP_VERSION,
            "app-platform":        "WebPlayer",
            "Accept":              "application/json",
            "Content-Type":        "application/json;charset=UTF-8",
            "Origin":              "https://open.spotify.com",
            "Referer":             "https://open.spotify.com/",
            "User-Agent":          (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
            ),
        }
        try:
            resp = session.post(GRAPHQL_URL, json=body, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                track_union = (data.get("data") or {}).get("trackUnion") or {}
                pc = track_union.get("playcount")
                if pc is not None:
                    return int(pc)
                m = re.search(r'"playcount":\s*"(\d+)"', json.dumps(data))
                return int(m.group(1)) if m else None
            elif resp.status_code == 401 and attempt == 0:
                print("  [API] 401 — re-capture des tokens…")
                token_mgr.mark_expired()
                tokens = token_mgr.get()
                if not tokens.get("bearer"):
                    return None
                continue
            else:
                return None
        except Exception:
            return None
    return None


def _probe_via_api(probe_tracks: list[dict], token_mgr: TokenManager) -> dict | None:
    """
    Probe via API GraphQL. Retourne le même dict que _probe_on_page,
    ou None si l'API n'est pas disponible.
    """
    if not token_mgr.available:
        return None

    session = _requests.Session()
    successful_probes = 0
    updated_probes = 0
    results = []

    try:
        for track in probe_tracks:
            pc        = fetch_playcount_api(track["track_id"], token_mgr, session)
            last_total = get_last_history_total(track["track_id"])

            if pc is not None:
                successful_probes += 1
                updated = has_real_update(last_total, pc)
                if updated:
                    updated_probes += 1
                results.append({
                    "title":            track["title"],
                    "status":           "ok",
                    "streams":          pc,
                    "previous_streams": last_total,
                    "updated":          updated,
                    "raw":              str(pc),
                })
            else:
                results.append({
                    "title":            track["title"],
                    "status":           "not_found",
                    "streams":          None,
                    "previous_streams": last_total,
                    "updated":          False,
                    "raw":              None,
                })
    finally:
        session.close()

    can_start = (
        successful_probes >= MIN_SUCCESSFUL_PROBES
        and updated_probes >= MIN_UPDATED_PROBES_TO_START
    )
    return {
        "can_start_full_run":  can_start,
        "successful_probes":   successful_probes,
        "updated_probes":      updated_probes,
        "results":             results,
    }


def run_probe(tracks: list[dict]) -> dict:
    track_lookup = build_track_lookup(tracks)
    probe_tracks: list[dict] = []

    for title in PROBE_TITLES:
        matches = track_lookup.get(normalize_title(title), [])
        if len(matches) == 1:
            probe_tracks.append(matches[0])

    if not probe_tracks:
        print("Probe skipped: no probe tracks found in database.")
        return {
            "can_start_full_run": True,
            "successful_probes": 0,
            "updated_probes": 0,
            "results": [],
        }

    p = sync_playwright().start()
    browser = launch_browser(p)
    context = browser.new_context(locale="fr-FR")
    page = context.new_page()
    page.route("**/*", block_unneeded)

    try:
        return _probe_on_page(probe_tracks, page)
    finally:
        browser.close()
        p.stop()


def _worker(
    queue,
    results,
    failed_results,
    lock,
    publish_lock,
    on_progress,
    total_tracks,
    dry_run_mode=False,
    early_twitter_trigger=None,
    worker_id: int = 0,
    adaptive: "AdaptiveWorkerState | None" = None,
    priority_top_50_ids: frozenset = frozenset(),
    pre_scraped: dict | None = None,
    token_mgr: "TokenManager | None" = None,
):
    # Hill climbing : attendre que ce slot soit activé avant d'ouvrir le browser.
    # Les threads dormants ne consomment pas de mémoire/CPU tant que target < worker_id.
    if adaptive is not None:
        while True:
            with adaptive.lock:
                if worker_id < adaptive.target:
                    break
            time.sleep(1)

    p = sync_playwright().start()
    context = launch_persistent_worker_context(p, worker_id)
    # Réutiliser l'onglet about:blank ouvert automatiquement par le profil persistant
    page = context.pages[0] if context.pages else context.new_page()
    page.route("**/*", block_unneeded)

    _api_session = _requests.Session()

    try:
        while True:
            # Hill climbing : drain si la cible redescend sous notre id
            if adaptive is not None:
                while True:
                    with adaptive.lock:
                        target = adaptive.target
                    if worker_id < target:
                        break
                    time.sleep(2)

            try:
                item = queue.get_nowait()
            except Empty:
                break

            i = item["index"]
            track = item["track"]
            stats_date = item["stats_date"]
            log_title = f"{track['title']} [{track['track_id'][-6:]}]"

            if on_progress:
                on_progress(i, total_tracks, log_title, None)

            # Use pre-scraped value from artist page if available
            if pre_scraped and track["track_id"] in pre_scraped:
                total = pre_scraped[track["track_id"]]
                raw = str(total)
                scrape_status = "ok"
                print(f"  [pre-scraped] {track['title']} -> {total:,}")
            elif token_mgr is not None and token_mgr.available:
                # Primary: API GraphQL Spotify (3 attempts before Playwright fallback)
                api_result = None
                for _api_attempt in range(3):
                    api_result = fetch_playcount_api(track["track_id"], token_mgr, _api_session)
                    if api_result is not None:
                        break
                    if _api_attempt < 2:
                        time.sleep(1)
                if api_result is not None:
                    total, raw, scrape_status = api_result, str(api_result), "ok"
                else:
                    # Fallback: Playwright (last resort)
                    total, raw, scrape_status, _ = scrape_track_total(
                        page, track["title"], track["spotify_url"]
                    )
            else:
                total, raw, scrape_status, _ = scrape_track_total(
                    page, track["title"], track["spotify_url"]
                )

            if adaptive is not None:
                adaptive.record(got_429=(scrape_status == "timeout"))

            if scrape_status == "timeout":
                result = {
                    "track_id": track["track_id"],
                    "title": track["title"],
                    "spotify_url": track["spotify_url"],
                    "status": "timeout",
                }
                with lock:
                    failed_results.append(dict(result))

            elif scrape_status == "error":
                result = {
                    "track_id": track["track_id"],
                    "title": track["title"],
                    "spotify_url": track["spotify_url"],
                    "status": "error",
                }
                with lock:
                    failed_results.append(dict(result))

            elif scrape_status == "not_found" or total is None:
                # Retry immédiat pour les tracks du top-50 avant de passer aux suivantes
                if track["track_id"] in priority_top_50_ids:
                    for _retry in range(2):
                        try:
                            page.wait_for_timeout(5000)
                        except Exception:
                            pass
                        if token_mgr is not None and token_mgr.available:
                            api_result = fetch_playcount_api(track["track_id"], token_mgr, _api_session)
                            if api_result is not None:
                                total, raw, scrape_status = api_result, str(api_result), "ok"
                            else:
                                total, raw, scrape_status, _ = scrape_track_total(
                                    page, track["title"], track["spotify_url"]
                                )
                        else:
                            total, raw, scrape_status, _ = scrape_track_total(
                                page, track["title"], track["spotify_url"]
                            )
                        if scrape_status == "ok" and total is not None:
                            break

                if scrape_status == "ok" and total is not None:
                    result = try_apply_track_update(
                        track=track,
                        total=total,
                        stats_date=stats_date,
                        lock=lock,
                        publish_lock=publish_lock,
                        dry_run_mode=dry_run_mode,
                    )
                    result["raw"] = raw
                    if early_twitter_trigger:
                        early_twitter_trigger(result)
                else:
                    result = {
                        "track_id": track["track_id"],
                        "title": track["title"],
                        "spotify_url": track["spotify_url"],
                        "status": "not_found",
                    }
                    with lock:
                        failed_results.append(dict(result))
                    # Signaler quand même au trigger que ce top-50 est traité (not_found définitif)
                    if early_twitter_trigger and track["track_id"] in priority_top_50_ids:
                        early_twitter_trigger(result)

            else:
                result = try_apply_track_update(
                    track=track,
                    total=total,
                    stats_date=stats_date,
                    lock=lock,
                    publish_lock=publish_lock,
                    dry_run_mode=dry_run_mode,
                )
                result["raw"] = raw
                if early_twitter_trigger:
                    early_twitter_trigger(result)

            with lock:
                results[i - 1] = result

            if on_progress:
                on_progress(i, total_tracks, log_title, result)

            queue.task_done()

    finally:
        print("Worker finished.")
        try:
            _api_session.close()
        except Exception:
            pass
        try:
            context.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass


def run_update(
    on_progress=None,
    skip_track_ids: set[str] | None = None,
    stats_date_override: str | None = None,
    dry_run_mode: bool = False,
    only_track_ids: set[str] | None = None,
    enable_early_twitter: bool = False,
    token_mgr: "TokenManager | None" = None,
):
    ensure_history_file()

    stats_date = stats_date_override or get_stats_date_str()
    skip_track_ids = skip_track_ids or set()

    active_track_ids = load_active_track_ids_from_discography()
    tracks = load_tracks_from_discography(active_track_ids)
    total_all_tracks = len(tracks)

    previous_day_priorities = load_track_priorities_from_specific_date(
        get_previous_stats_date_str(stats_date)
    )
    tracks.sort(
        key=lambda t: (-previous_day_priorities.get(t["track_id"], 0), t["title"].casefold())
    )

    if only_track_ids is not None:
        tracks = [t for t in tracks if t["track_id"] in only_track_ids]

    total_tracks = len(tracks)

    priority_top_50_ids = get_priority_top_50_track_ids_from_previous_day(tracks, stats_date)

    if priority_top_50_ids and len(priority_top_50_ids) < 50:
        print(f"Warning: only {len(priority_top_50_ids)} priority track(s) found from previous day.")

    _early_fired = threading.Event()
    _priority_lock = threading.Lock()
    # Tracks du top-50 déjà traités (updated OU not_found définitif après retry)
    _processed_priority_ids = set(load_history_track_ids_for_date(stats_date)) & priority_top_50_ids

    def early_twitter_trigger(result: dict):
        if not enable_early_twitter:
            return
        if _early_fired.is_set():
            return
        if result.get("status") == "pending":
            return

        track_id = result.get("track_id")
        if not track_id or track_id not in priority_top_50_ids:
            return

        with _priority_lock:
            _processed_priority_ids.add(track_id)

            if priority_top_50_ids and priority_top_50_ids.issubset(_processed_priority_ids):
                _early_fired.set()
                threading.Thread(target=_run_early_twitter, args=(stats_date,), daemon=True).start()

    # ── Pre-scrape artist page for top 10 tracks ──────────────────────────────
    track_lookup = build_track_lookup(tracks)
    artist_top_titles = scrape_artist_top_tracks()
    pre_scraped: dict[str, int] = {}
    for norm_title, stream_count in artist_top_titles.items():
        matches = track_lookup.get(norm_title, [])
        if len(matches) == 1:
            tid = matches[0]["track_id"]
            pre_scraped[tid] = stream_count
            print(f"  [artist page] {matches[0]['title']} -> {stream_count:,}")
        elif len(matches) > 1:
            print(f"  [artist page] ambiguous title '{norm_title}', skipping")
    print(f"  {len(pre_scraped)} track(s) pre-scraped from artist page")

    already_done_for_stats_date = load_history_track_ids_for_date(stats_date)

    queue = Queue()
    failed_results: list[dict] = []
    results = [None] * total_tracks

    for index, track in enumerate(tracks, 1):
        log_title = f"{track['title']} [{track['track_id'][-6:]}]"

        if track["track_id"] in already_done_for_stats_date or track["track_id"] in skip_track_ids:
            results[index - 1] = {
                "track_id": track["track_id"],
                "title": track["title"],
                "spotify_url": track["spotify_url"],
                "status": "skipped",
            }
            if on_progress:
                on_progress(index, total_tracks, log_title, results[index - 1])
            continue

        queue.put({
            "index": index,
            "track": track,
            "stats_date": stats_date,
        })

    if queue.qsize() > 0:
        lock = threading.Lock()
        publish_lock = threading.Lock()
        initial_workers = min(HILL_INITIAL, queue.qsize())
        max_workers     = min(MAX_PARALLEL_PAGES, queue.qsize())
        adaptive = AdaptiveWorkerState(initial=initial_workers)

        # Spawner max_workers threads dès maintenant.
        # Les workers avec worker_id >= initial_workers attendent leur activation (pas de fenêtre ouverte).
        workers = [
            threading.Thread(
                target=_worker,
                args=(
                    queue,
                    results,
                    failed_results,
                    lock,
                    publish_lock,
                    on_progress,
                    total_tracks,
                    dry_run_mode,
                    early_twitter_trigger,
                    idx,
                    adaptive,
                    priority_top_50_ids,
                    pre_scraped,
                    token_mgr,
                ),
                daemon=True,
            )
            for idx in range(max_workers)
        ]

        for w in workers:
            w.start()

        print(f"Waiting for {max_workers} worker(s) to finish (hill climbing actif, init={initial_workers})...")
        queue.join()
        for w in workers:
            w.join(timeout=5)
        print("All worker threads joined.")

        # ── Retry NOT FOUND (2ème passe, 30s d'attente, 3 workers max) ──────
        not_found_items = [r for r in failed_results if r.get("status") == "not_found"]
        if not_found_items:
            print(f"\n  {len(not_found_items)} NOT FOUND — retry dans 30s avec {min(3, len(not_found_items))} workers...")
            time.sleep(30)

            retry_queue: Queue = Queue()
            for idx, r in enumerate(not_found_items, 1):
                retry_queue.put({
                    "index": idx,
                    "track": {"title": r["title"], "track_id": r["track_id"], "spotify_url": r["spotify_url"]},
                    "stats_date": stats_date,
                })

            retry_total   = retry_queue.qsize()
            retry_results = [None] * retry_total
            retry_failed: list[dict] = []
            retry_adaptive = AdaptiveWorkerState(initial=min(3, retry_total))
            retry_workers  = [
                threading.Thread(
                    target=_worker,
                    args=(retry_queue, retry_results, retry_failed, lock, publish_lock,
                          None, retry_total, dry_run_mode, None, idx, retry_adaptive,
                          frozenset(), None, token_mgr),
                    daemon=True,
                )
                for idx in range(min(3, retry_total))
            ]
            for w in retry_workers:
                w.start()
            retry_queue.join()
            for w in retry_workers:
                w.join(timeout=5)

            # Fusionner dans failed_results : retirer les not_found résolus
            resolved_ids = {r["track_id"] for r in retry_results if r and r.get("status") not in (None, "not_found", "timeout")}
            failed_results[:] = [r for r in failed_results if r.get("track_id") not in resolved_ids]
            print(f"  Retry terminé : {len(resolved_ids)} récupérés, {len(not_found_items) - len(resolved_ids)} toujours NOT FOUND")

    final_done_for_stats_date = load_history_track_ids_for_date(stats_date)
    filtered_results = [r for r in results if r is not None]

    return {
        "stats_date": stats_date,
        "total_tracks": total_tracks,
        "total_all_tracks": total_all_tracks,
        "done_tracks": len(final_done_for_stats_date),
        "remaining_tracks": max(total_tracks - len([r for r in filtered_results if r["status"] in {"updated", "skipped"}]), 0),
        "all_done": len([r for r in filtered_results if r["status"] in {"updated", "skipped"}]) >= total_tracks,
        "updated_this_run": sum(1 for r in filtered_results if r["status"] == "updated"),
        "pending_this_run": sum(1 for r in filtered_results if r["status"] == "pending"),
        "skipped_this_run": sum(1 for r in filtered_results if r["status"] == "skipped"),
        "timeout_this_run": len([r for r in failed_results if r["status"] == "timeout"]),
        "error_this_run": len([r for r in failed_results if r["status"] == "error"]),
        "not_found_this_run": len([r for r in failed_results if r["status"] == "not_found"]),
        "results": filtered_results,
        "failed_results": failed_results,
    }


def print_remaining_details(summary: dict) -> None:
    print()
    print("Tracks still not done for this stats date:")

    remaining_details = []

    for r in summary["results"]:
        if r["status"] == "pending":
            remaining_details.append(
                f"PENDING | {r['title']} | {r.get('track_id', '')} | "
                f"total={format_int(r.get('streams'))} | "
                f"prev={format_int(r.get('previous_streams'))} | "
                f"delta={format_int(r.get('delta'))} | "
                f"reason={r.get('reason')}"
            )

    for r in summary["failed_results"]:
        if r["status"] in {"not_found", "timeout", "error"}:
            remaining_details.append(
                f"{r['status'].upper()} | {r['title']} | "
                f"{r.get('track_id', '')} | {r.get('spotify_url', '')}"
            )

    if remaining_details:
        for line in remaining_details:
            print(line)
    else:
        print("None.")


def print_summary_block(summary: dict) -> None:
    print()
    print(
        f"Progress {summary['stats_date']}: "
        f"done={summary['done_tracks']} "
        f"| run_total={summary['total_tracks']} "
        f"| updated={summary['updated_this_run']} "
        f"| pending={summary['pending_this_run']} "
        f"| timeout={summary['timeout_this_run']} "
        f"| error={summary['error_this_run']} "
        f"| not_found={summary['not_found_this_run']}"
    )


def update_json_logs_from_summary(summary: dict) -> None:
    updated_results = [r for r in summary.get("results", []) if r and r.get("status") == "updated"]
    save_last_successful_updates_json(summary["stats_date"], updated_results)
    save_last_unfinished_updates_json(summary["stats_date"], summary.get("results", []), summary.get("failed_results", []))

    pending_debug_rows = [
        {
            "title": r.get("title"),
            "track_id": r.get("track_id"),
            "spotify_url": r.get("spotify_url"),
            "previous_streams": r.get("previous_streams"),
            "new_streams": r.get("streams"),
            "delta": r.get("delta"),
            "reason": r.get("reason"),
            "raw": r.get("raw"),
        }
        for r in summary.get("results", [])
        if r and r.get("status") == "pending"
    ]
    save_pending_debug_rows(pending_debug_rows)
    save_failed_rows(summary.get("failed_results", []))


def run_debug_total_replace(stats_date: str) -> None:
    ensure_history_file()

    target_track_ids = load_history_track_ids_for_date(stats_date)
    if not target_track_ids:
        print(f"No rows found for {stats_date} in streams_history.csv.")
        return

    active_track_ids = load_active_track_ids_from_discography()
    tracks = load_tracks_from_discography(active_track_ids)
    tracks = [t for t in tracks if t["track_id"] in target_track_ids]
    tracks.sort(key=lambda t: t["title"].casefold())

    print(f"[DEBUG-TOTAL] Re-scraping {len(tracks)} track(s) for {stats_date}...")

    summary = run_update(
        on_progress=live_progress,
        stats_date_override=stats_date,
        dry_run_mode=True,
        only_track_ids=target_track_ids,
        enable_early_twitter=False,
    )

    rows = load_history_rows()
    replacements: dict[str, dict] = {}

    for r in summary["results"]:
        if not r or r.get("status") not in {"updated", "pending"}:
            continue

        track_id = r.get("track_id")
        new_total = r.get("streams")
        if not track_id or new_total is None:
            continue

        prev_total = get_previous_total_before_date(track_id, stats_date)
        new_daily = compute_daily(prev_total, new_total)

        replacements[track_id] = {
            "streams": str(new_total),
            "daily_streams": "" if new_daily is None else str(new_daily),
            "previous_streams": prev_total,
        }

    replaced_count = 0
    for row in rows:
        if (row.get("date") or "").strip() != stats_date:
            continue
        track_id = (row.get("track_id") or "").strip()
        repl = replacements.get(track_id)
        if not repl:
            continue
        row["streams"] = repl["streams"]
        row["daily_streams"] = repl["daily_streams"]
        replaced_count += 1

    save_history_rows(rows)

    log_path = DATA_DIR / f"debug_total_replace_{stats_date}.csv"
    with log_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "track_id", "previous_total_before_date", "new_total", "new_daily", "status", "reason"])
        for r in summary["results"]:
            if not r:
                continue
            track_id = r.get("track_id")
            repl = replacements.get(track_id, {})
            writer.writerow([
                r.get("title", ""),
                track_id or "",
                repl.get("previous_streams", ""),
                r.get("streams", ""),
                repl.get("daily_streams", ""),
                r.get("status", ""),
                r.get("reason", ""),
            ])

    print(f"[DEBUG-TOTAL] Replaced {replaced_count} row(s) for {stats_date}.")
    print(f"[DEBUG-TOTAL] Log written: {log_path}")


def main():
    global START_TIME
    START_TIME = time.perf_counter()

    ensure_history_file()

    if "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        return

    debug_daily_mode = "--debug-daily" in sys.argv
    debug_total_mode = "--debug-total" in sys.argv
    dry_run_mode = "--dry-run" in sys.argv
    no_post_mode = "--no-post" in sys.argv
    reset_last_date_mode = "--reset-last-date" in sys.argv

    if debug_daily_mode and debug_total_mode:
        print("Use either --debug-daily or --debug-total, not both.")
        sys.exit(1)

    remaining_args = [
        a for a in sys.argv[1:]
        if a not in (
            "--debug-daily",
            "--debug-total",
            "--dry-run",
            "--no-post",
            "--reset-last-date",
            "--help",
            "-h",
        )
    ]

    stats_date_override = None
    reset_date_override = None

    i = 0
    while i < len(remaining_args):
        arg = remaining_args[i]

        if arg == "--reset-date":
            if i + 1 >= len(remaining_args):
                print("Missing value after --reset-date (expected YYYY-MM-DD)")
                sys.exit(1)
            reset_date_override = remaining_args[i + 1]
            i += 2
            continue

        try:
            date.fromisoformat(arg)
            stats_date_override = arg
        except ValueError:
            print(f"Invalid argument '{arg}'")
            sys.exit(1)

        i += 1

    if reset_last_date_mode and reset_date_override:
        print("Use either --reset-last-date or --reset-date YYYY-MM-DD, not both.")
        sys.exit(1)

    if reset_last_date_mode:
        last_date = get_last_stats_date_in_history()
        if not last_date:
            print("No history date found to reset.")
        else:
            removed = delete_history_rows_for_date(last_date)
            print(f"[RESET] Removed {removed} row(s) for last stats date: {last_date}")

    if reset_date_override:
        try:
            date.fromisoformat(reset_date_override)
        except ValueError:
            print(f"Invalid reset date '{reset_date_override}', expected YYYY-MM-DD")
            sys.exit(1)

        removed = delete_history_rows_for_date(reset_date_override)
        print(f"[RESET] Removed {removed} row(s) for stats date: {reset_date_override}")

    if dry_run_mode:
        print("[DRY-RUN] Scraping uniquement — aucune modification.")
    elif debug_daily_mode:
        print("[DEBUG-DAILY] Retry unfinished tracks, writes history, no Twitter/git/forecast/images/notify.")
    elif debug_total_mode:
        print("[DEBUG-TOTAL] Replace totals for an existing date in streams_history.csv.")
    else:
        print("[NORMAL] Official run mode.")

    stats_date = stats_date_override or get_stats_date_str()
    print(f"Target stats date: {stats_date}")

    if debug_total_mode:
        if stats_date_override is None:
            print("--debug-total requires a date: python update_streams.py --debug-total YYYY-MM-DD")
            sys.exit(1)
        run_debug_total_replace(stats_date)
        return

    print("Checking kworbs for new extra tracks...")
    try:
        _backfill_cmd = [sys.executable, str(_SCRIPT_DIR / "extras" / "backfill_from_kworb.py")]
        if dry_run_mode:
            _backfill_cmd.append("--dry-run")
        subprocess.run(_backfill_cmd, check=False)
    except Exception as e:
        print(f"Kworbs backfill failed (non-fatal): {e}")

    active_track_ids = load_active_track_ids_from_discography()
    tracks = load_tracks_from_discography(active_track_ids)

    already_done_for_stats_date = load_history_track_ids_for_date(stats_date)
    done_tracks_before_run = len(already_done_for_stats_date)
    total_tracks = len(tracks)

    if debug_daily_mode:
        unfinished_ids = load_last_unfinished_update_track_ids(stats_date)
        if not unfinished_ids:
            print("[DEBUG-DAILY] No matching unfinished track list found, fallback to all not-yet-done tracks.")
            unfinished_ids = {t["track_id"] for t in tracks if t["track_id"] not in already_done_for_stats_date}
        else:
            print(f"[DEBUG-DAILY] Loaded {len(unfinished_ids)} unfinished track(s) from JSON.")
    else:
        unfinished_ids = None

    if dry_run_mode:
        print(f"[DRY-RUN] Scraping {total_tracks} tracks.")
    elif debug_daily_mode:
        print(f"[DEBUG-DAILY] Current progress for {stats_date}: {done_tracks_before_run}/{total_tracks}")
    else:
        print(f"Current progress for {stats_date}: {done_tracks_before_run}/{total_tracks}")

    # Capture des tokens API (une seule fois pour tout le run)
    token_mgr = TokenManager()
    token_mgr.capture()

    should_run_probe = (
        done_tracks_before_run == 0
        and stats_date_override is None
        and not dry_run_mode
        and not debug_daily_mode
    )

    if should_run_probe:
        track_lookup = build_track_lookup(tracks)
        probe_tracks: list[dict] = []
        for title in PROBE_TITLES:
            matches = track_lookup.get(normalize_title(title), [])
            if len(matches) == 1:
                probe_tracks.append(matches[0])

        if not probe_tracks:
            print("Probe skipped: no probe tracks found in database.")
        else:
            def _print_probe(probe):
                print(
                    f"Probe result | successful={probe['successful_probes']} | "
                    f"updated={probe['updated_probes']} | "
                    f"start_full_run={probe['can_start_full_run']}"
                )
                for row in probe["results"]:
                    if row["status"] == "ok":
                        print(
                            f"PROBE {row['title']} | "
                            f"current={format_int(row['streams'])} | "
                            f"previous={format_int(row['previous_streams'])} | "
                            f"updated={'yes' if row['updated'] else 'no'}"
                        )
                    else:
                        print(f"PROBE {row['title']} | status={row['status']}")

            # Essai probe via API
            api_probe = _probe_via_api(probe_tracks, token_mgr)
            if api_probe is not None:
                print("Running probe check... [API]")
                _print_probe(api_probe)
                while not api_probe["can_start_full_run"]:
                    print()
                    print("Spotify does not appear to have started the next daily update yet.")
                    print("Retrying in 1 minute...")
                    time.sleep(60)
                    print("Running probe check... [API]")
                    api_probe = _probe_via_api(probe_tracks, token_mgr)
                    _print_probe(api_probe)
            else:
                # Fallback: Playwright
                _p = sync_playwright().start()
                _browser = launch_browser(_p)
                _context = _browser.new_context(locale="fr-FR")
                _page = _context.new_page()
                _page.route("**/*", block_unneeded)

                try:
                    while True:
                        print("Running probe check... [Playwright]")
                        probe = _probe_on_page(probe_tracks, _page)
                        _print_probe(probe)

                        if probe["can_start_full_run"]:
                            break

                        print()
                        print("Spotify does not appear to have started the next daily update yet.")
                        print("Retrying in 1 minute...")
                        time.sleep(60)
                finally:
                    _browser.close()
                    _p.stop()
    elif done_tracks_before_run < total_tracks and not debug_daily_mode:
        print("Partial progress already exists for this stats date.")
        print("Skipping probe and resuming unfinished tracks.")
    elif debug_daily_mode:
        print("Skipping probe in debug-daily mode.")
    else:
        print("All tracks already appear done for this stats date.")
        print("Skipping probe and refreshing export/publish anyway.")

    print()
    print("=" * 70)
    print("Run")
    print("=" * 70)

    _artist_result: list[dict | None] = [None]

    def _scrape_artist_bg():
        _artist_result[0] = scrape_artist_metadata()

    artist_thread = None
    if not dry_run_mode:
        artist_thread = threading.Thread(target=_scrape_artist_bg, daemon=True)
        artist_thread.start()

    summary = run_update(
        on_progress=live_progress,
        stats_date_override=stats_date_override,
        dry_run_mode=dry_run_mode,
        only_track_ids=unfinished_ids if debug_daily_mode else None,
        enable_early_twitter=(not dry_run_mode and not debug_daily_mode and not no_post_mode),
        token_mgr=token_mgr,
    )
    print_summary_block(summary)

    not_found_ids: set[str] = {
        r["track_id"] for r in summary["failed_results"] if r["status"] == "not_found"
    }

    retry_round = 0
    while (
        not dry_run_mode
        and not debug_daily_mode
        and not summary["all_done"]
        and summary["pending_this_run"] > summary["total_tracks"] // 2
        and retry_round < MAX_PENDING_RETRY_ROUNDS
    ):
        retry_round += 1

        print()
        print(
            f"Detected {summary['pending_this_run']} unchanged track(s) "
            f"for {summary['stats_date']}."
        )
        if not_found_ids:
            print(f"Skipping {len(not_found_ids)} not-found track(s) on this retry.")

        print("Committing partial progress before retry...")
        git_commit_and_push(_REPO_ROOT, f"partial export {summary['stats_date']} (before retry {retry_round})")

        print(
            f"Waiting {PENDING_RETRY_SLEEP_SECONDS // 60} minutes before retry "
            f"({retry_round}/{MAX_PENDING_RETRY_ROUNDS})..."
        )
        time.sleep(PENDING_RETRY_SLEEP_SECONDS)

        print()
        print("=" * 70)
        print(f"Retry round {retry_round}")
        print("=" * 70)

        summary = run_update(
            on_progress=live_progress,
            skip_track_ids=not_found_ids,
            stats_date_override=stats_date_override,
            dry_run_mode=False,
            enable_early_twitter=(not no_post_mode),
            token_mgr=token_mgr,
        )
        not_found_ids.update(
            r["track_id"] for r in summary["failed_results"] if r["status"] == "not_found"
        )
        print_summary_block(summary)

    print_remaining_details(summary)
    update_json_logs_from_summary(summary)

    all_tracks = load_tracks_from_discography()
    updated_ids: set[str] = {
        r["track_id"] for r in summary.get("results", [])
        if r and r.get("status") == "updated"
    }

    streak = load_not_found_streak()
    update_not_found_streak(streak, not_found_ids, updated_ids)
    deleted = purge_stale_tracks(streak, all_tracks)
    if deleted:
        print(f"Auto-deleted {len(deleted)} stale track(s) not found for {MAX_NOT_FOUND_DAYS}+ days.")
    save_not_found_streak(streak)

    if dry_run_mode:
        print("[DRY-RUN] Scraping terminé — aucune modification appliquée.")
        return

    if summary["all_done"]:
        print("All target tracks updated.")
    else:
        print("Run finished, but not all target tracks are done.")
        print("Publishing current data anyway." if not debug_daily_mode else "Keeping local progress only.")

    print("Updating streams history CSV...")
    subprocess.run(
        [sys.executable, str(_SCRIPT_DIR / "tools" / "scripts" / "migrate_streams_to_csv.py")],
        check=False,
    )
    print("Streams history CSV done.")

    if debug_daily_mode:
        print("[DEBUG-DAILY] Skip: Twitter, forecast, images, git, notify.")
    else:
        if no_post_mode:
            print("Skipping Twitter post (--no-post).")
            subprocess.run(
                [
                    sys.executable,
                    str(_SCRIPT_DIR / "tools" / "scripts" / "post_streams_twitter.py"),
                    summary["stats_date"],
                    "--no-post",
                ],
                check=False,
            )
        else:
            print("Posting streams image to Twitter...")
            subprocess.run(
                [sys.executable, str(_SCRIPT_DIR / "tools" / "scripts" / "post_streams_twitter.py"), summary["stats_date"]],
                check=False,
            )
            print("Twitter post done.")

    print("Re-exporting web data...")
    export_for_web.export_for_web()
    print("Web export done.")

    if artist_thread is not None:
        print("Updating artist metadata...")
        artist_thread.join(timeout=60)
        update_artist_metadata(pre_scraped=_artist_result[0])

        print("Re-exporting web data (with artist metadata)...")
        export_for_web.export_for_web()
        print("Web export done.")

    if not debug_daily_mode:
        print("Rebuilding expected milestones forecast...")
        subprocess.run(
            [sys.executable, str(_SCRIPT_DIR / "tools" / "scripts" / "forecast_milestones.py")],
            check=True,
        )
        print("Expected milestones forecast done.")

        print("Refreshing image URLs + track_covers.json...")
        subprocess.run(
            [sys.executable, str(_REPO_ROOT / "scripts" / "fill_images.py")],
            check=True,
        )
        print("Image URLs done.")

        print("Fetching missing track cover images from Spotify...")
        subprocess.run(
            [sys.executable, str(_SCRIPT_DIR / "extras" / "fill_track_images.py")],
            check=False,
        )
        print("Track cover images done.")

        post_script = _SCRIPT_DIR / "tools" / "scripts" / "post_streams_twitter.py"
        if all_album_tracks_done(summary["stats_date"]):
            print("100% des tracks albums/* presents -> generation image top 15...")
            post_cmd = [sys.executable, str(post_script), summary["stats_date"]]
            if no_post_mode:
                post_cmd.append("--no-post")
            subprocess.run(
                post_cmd,
                check=False,
            )
        else:
            missing = load_album_track_ids() - load_history_track_ids_for_date(summary["stats_date"])
            print(f"Image top 15 ignorée : {len(missing)} track(s) albums/* manquant(s) pour {summary['stats_date']}.")

        # ── Album update images (priority order) ──────────────────────────────
        _ALBUM_UPDATE_TARGETS = [
            # "The Life of a Showgirl",           # paused 2026-03-28
            # "THE TORTURED POETS DEPARTMENT",    # paused 2026-03-28
        ]
        _album_img_script = _SCRIPT_DIR / "tools" / "scripts" / "generate_album_update_image.py"
        for _alb in _ALBUM_UPDATE_TARGETS:
            if album_tracks_done_for(_alb, summary["stats_date"]):
                print(f"Generating album update image: {_alb} ...")
                album_cmd = [
                    sys.executable,
                    str(_album_img_script),
                    _alb,
                    summary["stats_date"],
                ]
                if not no_post_mode:
                    album_cmd.append("--post")
                subprocess.run(
                    album_cmd,
                    check=False,
                )
            else:
                try:
                    _sections = load_album_sections_flat()
                    _alb_ids = {
                        extract_track_id(t.get("url") or t.get("spotify_url") or "")
                        for sec in _sections if sec.get("album") == _alb
                        for t in sec.get("tracks", [])
                    } - {""}
                    _done = load_history_track_ids_for_date(summary["stats_date"])
                    print(f"Album update skipped ({_alb}): {len(_alb_ids - _done)}/{len(_alb_ids)} tracks manquants.")
                except Exception:
                    print(f"Album update skipped ({_alb}): impossible de vérifier les tracks.")

        print("Git commit and push...")
        git_commit_and_push(_REPO_ROOT, f"daily final export {summary['stats_date']}")

    elapsed = time.perf_counter() - START_TIME
    print()
    print(f"Finished in {int(elapsed // 60)}m {int(elapsed % 60)}s")
    print(f"Done: {summary['done_tracks']}")
    print(f"Updated this run: {summary['updated_this_run']}")

    if not debug_daily_mode:
        notify(
            NTFY_TOPIC,
            f"{summary['done_tracks']} track(s) enregistrées ({summary['stats_date']})\n"
            f"Durée : {int(elapsed // 60)}m {int(elapsed % 60)}s",
            title="Taylor Swift - Streams mis à jour",
            tags="white_check_mark,chart_increasing",
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")