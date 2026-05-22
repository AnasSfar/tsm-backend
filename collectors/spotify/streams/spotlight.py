#!/usr/bin/env python3
"""
spotlight.py — scrape une chanson et génère une image "spotlight" carrée, puis poste sur Twitter.

Usage:
  python spotlight.py "Cruel Summer"
  python spotlight.py "Cruel Summer" 2026-03-21
  python spotlight.py "Cruel Summer" 2026-03-21 --no-post
  python spotlight.py "Cruel Summer" --account flame
  python spotlight.py "Cruel Summer" --combined
  python spotlight.py --url https://open.spotify.com/track/1BxfuPKGuaTgP7aM0Bbdwr

Options:
  --no-post   : Generate image but skip Twitter posting (default: will post)
  --no-scrape : Use history CSV total only, skip live scraping or API retry
  --url URL   : Provide Spotify track URL instead of title
  --account   : Twitter account to post with: flame (@theflameofanas) or tsm (@tsmuseum13)
  --combined  : Sum all versions sharing the selected track's song_family

Behavior:
  1. If stream data exists in history for the given date: use it (fast path)
  2. If missing and --no-scrape NOT used: try API retry every 60s (infinite loop)
  3. Otherwise: fall back to Playwright scraping (legacy)
  4. By default: posts to Twitter (use --no-post to suppress)
"""
from __future__ import annotations

import argparse
import base64
import colorsys
import csv
import json
import re
import sys
import threading
import time
import unicodedata
import urllib.request
from datetime import date as date_cls, timedelta
from pathlib import Path

import requests as _requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

try:
    from PIL import Image as _PilImage
    _PIL = True
except ImportError:
    _PIL = False

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR      = Path(__file__).resolve().parent          # streams/
REPO_ROOT       = SCRIPT_DIR.parents[2]                    # repo root
DB_DIR          = REPO_ROOT / "db"
SONGS_JSON      = DB_DIR / "discography" / "songs.json"
ALBUMS_DIR      = DB_DIR / "discography" / "albums"
COVERS_PATH     = DB_DIR / "discography" / "covers.json"
OUT_DIR         = SCRIPT_DIR / "history" / "spotlight"

sys.path.insert(0, str(SCRIPT_DIR.parent))  # collectors/spotify/ for core.*

from core.data_paths import archived_db_file  # noqa: E402
from core.album_emoji import album_emoji  # noqa: E402

HISTORY_PATH    = (
    DB_DIR / "streams_history.csv"
    if (DB_DIR / "streams_history.csv").exists()
    else archived_db_file("streams_history.csv")
)

DEFAULT_ACCOUNT = "tsm"
ACCOUNT_CONFIG = {
    "flame": {
        "handle": "@theflameofanas",
        "session": SCRIPT_DIR.parent / "charts" / "fr" / "tools" / "json" / "twitter_session.json",
    },
    "tsm": {
        "handle": "@tsmuseum13",
        "session": SCRIPT_DIR.parent / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json",
    },
}
PAGE_TIMEOUT_MS = 20_000

# ── API GraphQL Spotify ───────────────────────────────────────────────────────
GRAPHQL_URL   = "https://api-partner.spotify.com/pathfinder/v2/query"
GETTRACK_HASH = "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294"
APP_VERSION   = "1.2.87.30.gc764ebf1"
API_RETRY_SLEEP_SECONDS = 10

# ── Helpers ───────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _extract_track_id(url: str) -> str | None:
    m = re.search(r"track/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def _clean_title_for_filename(title: str) -> str:
    """Convert track title to safe filename part: 'Cruel Summer' -> 'Cruel_Summer'."""
    # Keep alphanumerics, spaces, hyphens, apostrophes
    title = title.strip()
    # Replace spaces and special chars with underscore
    title = re.sub(r"[^\w\s-]", "", title)  # Remove most special chars
    title = re.sub(r"[\s-]+", "_", title)   # Replace spaces/hyphens with underscore
    return title


def _validate_date(date_str: str) -> bool:
    """Validate YYYY-MM-DD format."""
    try:
        date_cls.fromisoformat(date_str)
        return True
    except ValueError:
        return False


def _fmt(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", "\u202f")


def _cover_palette(img_bytes: bytes) -> tuple[str, str]:
    """Extract cover colors spatially and return (gradient_css, accent_hex).

    Projects every pixel onto the 135° gradient axis (top-left → bottom-right),
    divides into N strips, averages each strip's non-neutral pixels, and builds
    a gradient whose stops match the real spatial layout of the cover.
    """
    _FALLBACK = ("#1db954", "#1db954")
    if not _PIL or not img_bytes:
        return _FALLBACK
    try:
        from io import BytesIO

        SIZE = 100
        img = _PilImage.open(BytesIO(img_bytes)).convert("RGB").resize((SIZE, SIZE), _PilImage.LANCZOS)
        pixels = list(img.getdata())

        N_STRIPS = 6
        strips: list[list[tuple[int, int, int]]] = [[] for _ in range(N_STRIPS)]

        for idx, (r, g, b) in enumerate(pixels):
            x = idx % SIZE
            y = idx // SIZE
            # Project onto 135° axis: 0 = top-left, 1 = bottom-right
            t = (x + y) / (2 * (SIZE - 1))
            strips[min(int(t * N_STRIPS), N_STRIPS - 1)].append((r, g, b))

        def _strip_color(px: list[tuple[int, int, int]]) -> tuple[str, float] | None:
            """Average non-neutral pixels in a strip, boost saturation."""
            non_neutral = [
                p for p in px
                if not (p[0] > 210 and p[1] > 210 and p[2] > 210)
                and not (p[0] < 35  and p[1] < 35  and p[2] < 35)
            ]
            pool = non_neutral or px
            if not pool:
                return None
            avg_r = sum(p[0] for p in pool) / len(pool)
            avg_g = sum(p[1] for p in pool) / len(pool)
            avg_b = sum(p[2] for p in pool) / len(pool)
            h, s, v = colorsys.rgb_to_hsv(avg_r / 255, avg_g / 255, avg_b / 255)
            s = min(1.0, s * 1.25)
            v = min(1.0, max(0.28, v))
            r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
            return f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}", s

        strip_results = [_strip_color(s) for s in strips]

        # Build gradient stops: strip i covers [i/N, (i+1)/N]
        hex_stops: list[tuple[str, int]] = []  # (hex, pct)
        for i, res in enumerate(strip_results):
            if res is None:
                continue
            hex_col, _ = res
            pct_start = round(i / N_STRIPS * 100)
            hex_stops.append((hex_col, pct_start))

        if not hex_stops:
            return _FALLBACK

        # Deduplicate consecutive identical colors for a cleaner CSS string
        deduped: list[tuple[str, int]] = [hex_stops[0]]
        for col, pct in hex_stops[1:]:
            if col != deduped[-1][0]:
                deduped.append((col, pct))

        if len(deduped) == 1:
            return deduped[0][0], deduped[0][0]

        stops_css = ", ".join(f"{c} {p}%" for c, p in deduped)
        gradient   = f"linear-gradient(135deg, {stops_css})"

        # accent = strip with highest saturation
        accent_hex = max(
            (r for r in strip_results if r is not None),
            key=lambda r: r[1]
        )[0]

        return gradient, accent_hex

    except Exception:
        return _FALLBACK

# ── API Token Management ──────────────────────────────────────────────────────
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

        p = sync_playwright().start()
        browser = None
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--no-proxy-server"],
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
        print("TokenManager: échec de capture des tokens")
        return False

    def get(self) -> dict:
        with self._lock:
            return dict(self._tokens)

    def mark_expired(self) -> None:
        """Appelé sur 401 — déclenche une re-capture (un seul thread à la fois)."""
        if self._recapturing.is_set():
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


def fetch_playcount_api(
    track_id: str,
    token_mgr: TokenManager,
    session: _requests.Session,
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


def fetch_stream_with_retry(track_id: str, stats_date: str, total_yesterday: int | None) -> int | None:
    """
    Fetch stream data from API, retrying every minute until available.
    Returns total streams for the track on stats_date, or None if unavailable.
    Updates streams_history.csv with fetched data.
    """
    token_mgr = TokenManager()
    session = _requests.Session()
    retry_count = 0
    
    print(f"\nFetching stream data for {track_id} on {stats_date}…")
    
    while True:
        if not token_mgr.available:
            print(f"Attempt {retry_count + 1}: Capturing Spotify tokens…")
            token_mgr.capture()
        
        if not token_mgr.available:
            print(f"Attempt {retry_count + 1}: Token capture failed, will retry…")
            retry_count += 1
            time.sleep(API_RETRY_SLEEP_SECONDS)
            continue
        
        try:
            total_today = fetch_playcount_api(track_id, token_mgr, session)
            
            if total_today is not None:
                # Calculate daily streams
                daily_today = None
                if total_yesterday is not None and total_yesterday > 0:
                    daily_today = max(0, total_today - total_yesterday)
                else:
                    daily_today = ""
                
                # Check if data is actually updated: daily should be > 0 if we have yesterday's data
                # If daily == 0 and we have yesterday data, it likely means Spotify hasn't updated yet
                if daily_today == 0 and total_yesterday is not None:
                    print(f"Attempt {retry_count + 1}: Total unchanged since yesterday (data not yet updated), retrying in {API_RETRY_SLEEP_SECONDS}s…")
                    retry_count += 1
                    time.sleep(API_RETRY_SLEEP_SECONDS)
                    continue
                
                # Data is valid and updated, return it (don't write to CSV - that's update_streams.py's job)
                print(f"  ✓ Data acquired! Total: {total_today:,}, Daily: {daily_today}")
                return total_today
            
            print(f"Attempt {retry_count + 1}: Stream data not yet available, retrying in {API_RETRY_SLEEP_SECONDS}s…")
            retry_count += 1
            time.sleep(API_RETRY_SLEEP_SECONDS)
            
        except Exception as e:
            print(f"Attempt {retry_count + 1}: API error: {e}, retrying in {API_RETRY_SLEEP_SECONDS}s…")
            retry_count += 1
            time.sleep(API_RETRY_SLEEP_SECONDS)

# ── Discography ───────────────────────────────────────────────────────────────
def load_all_tracks() -> list[dict]:
    tracks = []
    seen: set[str] = set()

    all_sections = []
    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            album_name = payload.get("album", "") if isinstance(payload, dict) else ""
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                if not isinstance(section, dict):
                    continue
                item = dict(section)
                if not item.get("album"):
                    item["album"] = album_name
                all_sections.append(item)

    if SONGS_JSON.exists():
        try:
            all_sections.extend(json.loads(SONGS_JSON.read_text(encoding="utf-8-sig")))
        except Exception:
            pass

    for section in all_sections:
        for t in section.get("tracks", []):
            url = (t.get("url") or t.get("spotify_url") or "").strip()
            tid = _extract_track_id(url)
            if not tid or tid in seen:
                continue
            seen.add(tid)
            artists = t.get("artists") or []
            tracks.append({
                "track_id":   tid,
                "title":      (t.get("title") or "").strip(),
                "artist":     t.get("primary_artist") or (artists[0] if artists else "Taylor Swift"),
                "spotify_url": f"https://open.spotify.com/track/{tid}",
                "image_url":  (t.get("image_url") or "").strip(),
                "type":       t.get("type", "album"),
                "single_image": (t.get("single_image") or "").strip(),
                "song_family": t.get("song_family", ""),
                "album":      section.get("album", ""),
            })
    return tracks


def find_track(query: str, tracks: list[dict]) -> dict | None:
    """Find a track by ID, exact name, or fuzzy substring matching."""
    tid = _extract_track_id(query)
    if tid:
        return next((t for t in tracks if t["track_id"] == tid), None)
    
    q = _norm(query)
    
    # Exact match (normalized)
    exact = next((t for t in tracks if _norm(t["title"]) == q), None)
    if exact:
        return exact
    
    # Substring match (normalized)
    substring = next((t for t in tracks if q in _norm(t["title"])), None)
    if substring:
        return substring
    
    # Fuzzy match: search for tracks that contain multiple words from the query
    # This handles cases like "Elizabeth Taylor (So Glamourous Cabaret Version)"
    # matching "Elizabeth Taylor" in the discography
    query_words = set(w for w in q.split("_") if w and len(w) > 2)  # Filter short words
    if query_words:
        best_match = None
        best_score = 0
        
        for track in tracks:
            track_words = set(w for w in _norm(track["title"]).split("_") if w and len(w) > 2)
            # Calculate how many query words match in the track title
            matching_words = len(query_words & track_words)
            if matching_words > best_score:
                best_score = matching_words
                best_match = track
        
        if best_match and best_score > 0:
            return best_match
    
    return None


def find_combined_tracks(track: dict, tracks: list[dict]) -> list[dict]:
    """Return all versions sharing the selected track's song_family."""
    family = (track.get("song_family") or "").strip()
    if not family:
        return [track]
    related = [t for t in tracks if (t.get("song_family") or "").strip() == family]
    return related or [track]


def _get_song_family_single_image_map() -> dict:
    """Returns {song_family → single_image} mapping for version inheritance."""
    family_map = {}
    
    all_sections = []
    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            album_name = payload.get("album", "") if isinstance(payload, dict) else ""
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                if not isinstance(section, dict):
                    continue
                item = dict(section)
                if not item.get("album"):
                    item["album"] = album_name
                all_sections.append(item)
    
    if SONGS_JSON.exists():
        try:
            all_sections.extend(json.loads(SONGS_JSON.read_text(encoding="utf-8-sig")))
        except Exception:
            pass
    
    for section in all_sections:
        for t in section.get("tracks", []):
            song_family = t.get("song_family", "")
            single_image = (t.get("single_image") or "").strip()
            if song_family and single_image and str(single_image).startswith("http"):
                family_map[song_family] = single_image
    
    return family_map


def get_cover_url(track: dict, covers: dict) -> str:
    """
    Returns cover URL for a track.
    
    Priority:
      - If type == "standalone" or "alternate_version":
        * single_image (from same song_family) > image_url (NEVER album cover)
      - Otherwise: covers.json (album) > image_url
    """
    track_type = track.get("type", "album")
    track_img = track.get("image_url", "")
    single_img = track.get("single_image", "")
    song_family = track.get("song_family", "")
    album = track.get("album", "")
    
    # Singles et versions alternatives : JAMAIS d'album cover
    if track_type in ("standalone", "alternate_version"):
        # Check if this track's song_family has a single_image
        family_map = _get_song_family_single_image_map()
        if song_family and song_family in family_map:
            family_img = family_map[song_family]
            if str(family_img).startswith("http"):
                return family_img
        
        # Own single_image
        if single_img and str(single_img).startswith("http"):
            return single_img
        
        # Track image fallback
        if track_img and str(track_img).startswith("http"):
            return track_img
        
        return ""
    
    # Tracks normaux : priorité album cover → image_url
    if album:
        cover = covers.get(_norm(album), "")
        if cover and str(cover).startswith("http"):
            return cover
    
    # Track image fallback
    if track_img and str(track_img).startswith("http"):
        return track_img
    
    return ""


def load_covers() -> dict:
    if not COVERS_PATH.exists():
        return {}
    covers = json.loads(COVERS_PATH.read_text(encoding="utf-8-sig"))
    return {
        _norm(v.get("title", "")): v["cover_url"]
        for v in covers.values()
        if "cover_url" in v and v.get("title")
    }

# ── History ───────────────────────────────────────────────────────────────────
def load_history_for_track(track_id: str, stats_date: str) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    """Returns (total_today, total_yesterday, daily_today, daily_yesterday, daily_last_week).
    daily_* sont lus directement depuis la colonne daily_streams du CSV.
    """
    return load_history_for_tracks([track_id], stats_date)


def load_history_for_tracks(track_ids: list[str], stats_date: str) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    """Returns summed (total_today, total_yesterday, daily_today, daily_yesterday, daily_last_week)."""
    d0 = date_cls.fromisoformat(stats_date)
    dates = {str(d0): "today", str(d0 - timedelta(1)): "y1", str(d0 - timedelta(7)): "w1"}
    totals:  dict[str, int] = {}
    dailies: dict[str, int] = {}
    wanted = set(track_ids)

    if not HISTORY_PATH.exists():
        return None, None, None, None, None

    with HISTORY_PATH.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("track_id") not in wanted:
                continue
            d = (row.get("date") or "").strip()
            if d not in dates:
                continue
            key = dates[d]
            try:
                totals[key] = totals.get(key, 0) + int(row["streams"] or 0)
            except ValueError:
                pass
            try:
                v = (row.get("daily_streams") or "").strip()
                if v:
                    dailies[key] = dailies.get(key, 0) + int(v)
            except ValueError:
                pass

    return totals.get("today"), totals.get("y1"), dailies.get("today"), dailies.get("y1"), dailies.get("w1")


# ── Scraping ──────────────────────────────────────────────────────────────────
def _block_unneeded(route) -> None:
    url = route.request.url.lower()
    if route.request.resource_type in {"media", "font", "image"} or any(
        x in url for x in ("doubleclick", "googletagmanager", "google-analytics",
                           "analytics", "facebook", "pixel", "ads")
    ):
        route.abort()
    else:
        route.continue_()


def _extract_playcount(page) -> int | None:
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None

    lines = [l.replace("\u202f", " ").replace("\xa0", " ").strip()
             for l in body.splitlines() if l.strip()]

    start = next((i for i, l in enumerate(lines) if l.strip().lower() in ("titre", "title")), None)
    if start is None:
        return None

    end_markers = {"connectez-vous", "se connecter", "artiste", "recommandes",
                   "recommandés", "basees sur ce titre", "basées sur ce titre",
                   "titres populaires par", "sorties populaires par taylor swift"}

    block: list[str] = []
    for l in lines[start + 1:]:
        if _norm(l) in end_markers:
            break
        block.append(l)

    def _is_large(t: str) -> bool:
        c = t.strip().replace("\u202f", " ").replace("\xa0", " ")
        if not re.fullmatch(r"[\d\s,.\']+", c):
            return False
        try:
            return int(re.sub(r"[^\d]", "", c)) >= 1000
        except ValueError:
            return False

    for i, l in enumerate(block):
        if re.fullmatch(r"\d{1,2}:\d{2}", l.strip()):
            for j in range(i + 1, min(i + 6, len(block))):
                c = block[j].strip()
                if c in {"•", "-", ""}:
                    continue
                if _is_large(c):
                    try:
                        return int(re.sub(r"[^\d]", "", c))
                    except ValueError:
                        pass

    # JS fallback
    try:
        r = page.evaluate("""() => {
            const cs = [];
            document.querySelectorAll('[data-testid], span, div').forEach(el => {
                const t = (el.innerText||'').trim();
                if (/^[\\d\\u202f\\u00a0\\s,.']+$/.test(t)) {
                    const n = parseInt(t.replace(/[^\\d]/g,''));
                    if (!isNaN(n) && n >= 10000) cs.push(n);
                }
            });
            return cs.length === 1 ? cs[0] : null;
        }""")
        if r is not None:
            return int(r)
    except Exception:
        pass
    return None


def scrape_track(track: dict) -> int | None:
    print(f"Scraping : {track['title']} …")
    attempt = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx  = browser.new_context(locale="fr-FR")
        page = ctx.new_page()
        page.route("**/*", _block_unneeded)
        try:
            while True:
                attempt += 1
                try:
                    page.goto(track["spotify_url"], wait_until="commit", timeout=PAGE_TIMEOUT_MS)
                    try:
                        page.wait_for_function(
                            "() => { for (const el of document.querySelectorAll('[data-testid], span, div')) {"
                            "  const n = parseInt((el.innerText||'').replace(/[^\\d]/g,''));"
                            "  if (!isNaN(n) && n >= 100000) return true; } return false; }",
                            timeout=8000,
                        )
                    except Exception:
                        pass
                    total = _extract_playcount(page)
                    if total is not None:
                        print(f"  → {total:,} streams")
                        return total
                    for wait_ms in (1000, 2500):
                        page.wait_for_timeout(wait_ms)
                        total = _extract_playcount(page)
                        if total is not None:
                            print(f"  → {total:,} streams")
                            return total
                    print(f"  Not found (attempt {attempt}), retrying …")
                    page.wait_for_timeout(3000)
                except PlaywrightTimeoutError:
                    print(f"  Timeout (attempt {attempt}), retrying …")
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"  Erreur (attempt {attempt}): {e}, retrying …")
                    page.wait_for_timeout(3000)
        finally:
            browser.close()

# ── Image ─────────────────────────────────────────────────────────────────────
_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
    background: var(--page-bg,
        radial-gradient(circle at 12% 18%, rgba(29,185,84,.13), transparent 30%),
        radial-gradient(circle at 84% 16%, rgba(126,87,255,.10), transparent 32%),
        linear-gradient(180deg,#f4f7f8 0%,#edf3f4 100%));
  width:800px;
  padding:16px;
  color:#101828;
}
.container{
  border-radius:18px;
  overflow:hidden;
  box-shadow:0 14px 40px rgba(16,24,40,.10),0 2px 8px rgba(16,24,40,.06);
  display:flex;
  flex-direction:column;
}
.main-row{
  display:flex;
  flex-direction:row;
  background:#fff;
}
.cover-col{
  flex:0 0 280px;
  padding:24px 20px 24px 24px;
  display:flex;align-items:center;justify-content:center;
  border-right:1px solid rgba(16,24,40,.07);
}
.cover-art{
  width:232px;height:232px;
  border-radius:12px;object-fit:cover;
  box-shadow:0 16px 40px rgba(0,0,0,.22),0 4px 12px rgba(0,0,0,.12);
}
.cover-ph{
  width:232px;height:232px;border-radius:12px;
  background:#dde3ea;
  display:flex;align-items:center;justify-content:center;
  font-size:56px;
}
.info-col{
  flex:1;
  display:flex;flex-direction:column;
  padding:20px 22px 20px 20px;
  gap:14px;
}
.song-name{
  font-size:24px;font-weight:900;color:#101828;
  letter-spacing:-.3px;line-height:1.15;
}
.song-artist{font-size:12px;color:#667085;margin-top:2px;}
.song-date{font-size:13px;font-weight:600;color:#344054;margin-top:1px;}
.combined-badge{
  display:inline-flex;align-items:center;align-self:flex-start;
  margin-top:7px;padding:4px 8px;border-radius:999px;
  font-size:9px;font-weight:900;letter-spacing:.08em;text-transform:uppercase;
  color:#fff;background:var(--accent, #1db954);
  box-shadow:0 5px 14px rgba(16,24,40,.14);
}
.daily-block{
  border-radius:12px;
  padding:14px 18px 10px;
  text-align:center;
}
.daily-num{
  color:#fff;
  font-size:56px;font-weight:900;
  letter-spacing:-.04em;line-height:1;
}
.stat-row{
  display:grid;grid-template-columns:1fr 1fr;gap:10px;
}
.stat-card{
  background:rgba(241,245,246,.96);
  border-radius:10px;padding:12px 14px;
  border:1px solid rgba(16,24,40,.07);
}
.stat-card.highlight{
    background: linear-gradient(135deg, rgba(241,245,246,.98), rgba(255,255,255,.96));
    border: 2px solid var(--accent, #0055cc);
    box-shadow: 0 8px 32px rgba(var(--accent-rgb, 0,85,204),.16), inset 0 1px 0 rgba(255,255,255,.60);
}
.stat-label{
  font-size:10px;font-weight:700;
  text-transform:uppercase;letter-spacing:.08em;
  color:#667085;margin-bottom:5px;
}
.stat-val{font-size:24px;font-weight:800;color:#101828;letter-spacing:-.02em;}
.stat-sub{font-size:13px;font-weight:600;color:#667085;margin-top:4px;}
.stat-card.highlight .stat-sub{
  font-size:14px;
  font-weight:700;
  color:var(--accent, #0055cc);
}
.pos{color:#067647}
.neg{color:#b42318}
.neutral{color:#667085}
.ftr{
  background:rgba(241,245,246,.96);
  padding:7px 16px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid rgba(16,24,40,.07);
}
.ftr-handle{font-size:11px;font-weight:700}
.ftr-date{font-size:11px;color:#667085;font-weight:500}
.stat-card-gold{
  background:linear-gradient(135deg,#7a5800,#c8920a,#f5c518,#c8920a,#7a5800);
  border:none;
}
.stat-card-gold.highlight{
    box-shadow: 0 8px 32px rgba(255,215,0,.20), inset 0 1px 0 rgba(255,255,255,.60);
    outline:2px solid rgba(255,255,255,.28);
    outline-offset:0px;
}
.stat-card-gold .stat-label{color:rgba(255,255,255,.80);}
.stat-card-gold .stat-val{color:#fff;}
.stat-card-gold .stat-sub{color:rgba(255,255,255,.75);font-weight:700;}
"""


_MILESTONES = [
    100_000_000, 200_000_000, 300_000_000, 400_000_000, 500_000_000,
    600_000_000, 700_000_000, 800_000_000, 900_000_000,
    1_000_000_000, 1_500_000_000, 2_000_000_000, 2_500_000_000,
    3_000_000_000, 3_500_000_000, 4_000_000_000, 5_000_000_000,
]


def _just_crossed_milestone(total: int, total_yesterday: int | None) -> int | None:
    if total_yesterday is None:
        return None
    for m in _MILESTONES:
        if total_yesterday < m <= total:
            return m
    return None


def _fmt_milestone(m: int) -> str:
    if m >= 1_000_000_000 and m % 1_000_000_000 == 0:
        return f"{m // 1_000_000_000}B"
    if m >= 1_000_000_000:
        return f"{m / 1_000_000_000:.1f}B"
    return f"{m // 1_000_000}M"


def _fetch_image(url: str) -> tuple[str, bytes]:
    if not url:
        return "", b""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
            ct   = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            return f"data:{ct};base64,{base64.b64encode(data).decode()}", data
    except Exception:
        return "", b""


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", (hex_color or "").strip())
    if not m:
        return None
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    # WCAG relative luminance (sRGB)
    def _lin(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _average_luminance_from_gradient(gradient_css: str) -> float | None:
    # Extract all #RRGGBB stops and average their luminance.
    hexes = re.findall(r"#[0-9a-fA-F]{6}", gradient_css or "")
    rgbs = [_hex_to_rgb(h) for h in hexes]
    rgbs = [c for c in rgbs if c is not None]
    if not rgbs:
        return None
    lums = [_relative_luminance(c) for c in rgbs]
    return sum(lums) / len(lums)


def _pick_block_colors_for_background(gradient_css: str) -> tuple[str, str]:
    """Return (block_bg_hex, block_text_hex) for max contrast vs background."""
    lum = _average_luminance_from_gradient(gradient_css)
    # If background is bright → choose black block; if dark → choose white block.
    if lum is not None and lum >= 0.55:
        return "#000000", "#ffffff"
    return "#ffffff", "#000000"


def _build_html(
    title: str,
    artist: str,
    daily: int | None,
    comparison_daily: int | None,
    comparison_label: str,
    total: int,
    cover_uri: str,
    gradient: str,
    accent_hex: str,
    date_fmt: str,
    handle: str,
    combined: bool = False,
    milestone: int | None = None,
    highlight: str = "total",
) -> str:
    has_daily    = daily is not None and daily >= 0
    daily_fmt    = _fmt(daily) if has_daily else "—"
    daily_prefix = "+" if has_daily else ""
    daily_prefix = "+" if has_daily else ""
    total_fmt = _fmt(total)

    # Variation de daily streams vs selected comparison baseline.
    if daily is not None and comparison_daily is not None and comparison_daily > 0:
        delta     = daily - comparison_daily
        pct       = delta / comparison_daily * 100
        sign      = "+" if delta >= 0 else "−"
        vs_str    = f"{sign}{_fmt(abs(delta))}"
        pct_str   = f"{pct:+.1f}%"
        vs_cls    = "pos" if delta >= 0 else "neg"
    else:
        vs_str    = "—"
        pct_str   = ""
        vs_cls    = "neutral"

    cover_html = (
        f'<img class="cover-art" src="{cover_uri}" />'
        if cover_uri else
        '<div class="cover-ph">🎵</div>'
    )

    block_bg, block_text = _pick_block_colors_for_background(gradient)
    block_border = "rgba(16,24,40,.12)" if block_bg.lower() == "#ffffff" else "rgba(255,255,255,.18)"

    highlight_vs = "highlight" if highlight == "vs" else ""
    highlight_vs_style = f'style="border-color:{accent_hex};--accent:{accent_hex}"' if highlight == "vs" else ""
    highlight_total = "highlight" if highlight == "total" else ""
    highlight_total_style = f'style="border-color:{accent_hex};--accent:{accent_hex}"' if highlight == "total" else ""
    combined_badge = '<div class="combined-badge">Combined Versions</div>' if combined else ""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{_CSS}</style></head>
<body style="--page-bg: {gradient};">
<div class="container" style="--accent:{accent_hex}">
  <div class="main-row">
    <div class="cover-col">
      {cover_html}
    </div>
    <div class="info-col">
      <div>
        <div class="song-name">{title}</div>
        <div class="song-artist">{artist}</div>
        <div class="song-date">{date_fmt}</div>
        {combined_badge}
      </div>
            <div class="daily-block" style="background:{block_bg}; border:1px solid {block_border}">
                <div class="daily-num" style="color:{block_text}">{daily_prefix}{daily_fmt}</div>
      </div>
      <div class="stat-row">
                <div class="stat-card {highlight_vs}" {highlight_vs_style}>
          <div class="stat-label">vs {comparison_label}</div>
          <div class="stat-val {vs_cls}">{vs_str}</div>
          <div class="stat-sub">{pct_str}</div>
        </div>
                <div class="stat-card {highlight_total}{' stat-card-gold' if milestone else ''}" {highlight_total_style}>
          <div class="stat-label">Total Streams</div>
          <div class="stat-val">{total_fmt}</div>
          <div class="stat-sub">{'🏆 ' + _fmt_milestone(milestone) + ' MILESTONE' if milestone else 'SINCE RELEASE'}</div>
        </div>
      </div>
    </div>
  </div>
  <div class="ftr">
    <span class="ftr-handle" style="color:{accent_hex}">{handle}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div>
</body></html>"""


def generate_spotlight_image(
    track: dict,
    total_scraped: int,
    total_yesterday: int | None,
    comparison_daily: int | None,
    comparison_label: str,
    cover_url: str,
    stats_date: str,
    handle: str,
    combined: bool = False,
    highlight: str = "total",
) -> Path:
    from datetime import datetime
    date_fmt = datetime.strptime(stats_date, "%Y-%m-%d").strftime("%B %d, %Y")

    print("Téléchargement de la cover …")
    cover_uri, cover_bytes = _fetch_image(cover_url)

    gradient, accent_hex = _cover_palette(cover_bytes) if cover_bytes else ("#1db954", "#1db954")
    print(f"Gradient : {gradient}")
    print(f"Accent   : {accent_hex}")

    daily     = (total_scraped - total_yesterday) if total_yesterday is not None else None
    milestone = _just_crossed_milestone(total_scraped, total_yesterday)
    if milestone:
        print(f"Milestone atteint : {_fmt_milestone(milestone)}")

    html = _build_html(
        title           = track["title"],
        artist          = track.get("artist", "Taylor Swift"),
        daily           = daily,
        comparison_daily = comparison_daily,
        comparison_label = comparison_label,
        total           = total_scraped,
        cover_uri       = cover_uri,
        gradient        = gradient,
        accent_hex      = accent_hex,
        date_fmt        = date_fmt,
        handle          = handle,
        combined        = combined,
        milestone       = milestone,
        highlight       = highlight,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tid           = track["track_id"]
    title_clean   = _clean_title_for_filename(track["title"])
    combined_suffix = "__combined" if combined else ""
    out_path      = OUT_DIR / f"{title_clean}__{stats_date}{combined_suffix}.png"
    tmp_html      = OUT_DIR / f"_spotlight_{tid}.html"
    tmp_html.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 800, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()

    print(f"Image générée : {out_path}")
    return out_path

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Spotlight image for one Taylor Swift track with Twitter posting.")
    parser.add_argument("title", nargs="?", help="Track title (or use --url for Spotify URL)")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--url",       help="Spotify track URL (alternative to title)")
    parser.add_argument("--no-post",   action="store_true", help="Generate image but skip Twitter posting (default: will post)")
    parser.add_argument("--no-scrape", action="store_true", help="Use history CSV total only, skip API and scraping")
    parser.add_argument("--post",      action="store_true", help="[Deprecated] Explicitly post to Twitter (now default)")
    parser.add_argument("--combined", dest="combined", action="store_true", help="Sum all versions sharing the selected track's song_family")
    parser.add_argument("--no-combined", dest="combined", action="store_false", help="Use only the selected Spotify track")
    parser.set_defaults(combined=False)
    parser.add_argument(
        "--compare",
        choices=["yesterday", "last-week"],
        default="yesterday",
        help="Comparison used in the tweet text: yesterday or last-week.",
    )
    parser.add_argument(
        "--highlight",
        choices=["vs", "total"],
        default="vs",
        help="Which stat card to emphasize: 'vs' (vs yesterday) or 'total' (total streams).",
    )
    parser.add_argument(
        "--account",
        choices=sorted(ACCOUNT_CONFIG),
        default=DEFAULT_ACCOUNT,
        help="Twitter account to post with: flame (@theflameofanas) or tsm (@tsmuseum13).",
    )
    parser.add_argument(
        "--session",
        help="Path to a Twitter session JSON file (overrides the selected account session).",
    )
    args = parser.parse_args()
    account = ACCOUNT_CONFIG[args.account]
    handle = account["handle"]
    twitter_session = Path(args.session) if args.session else account["session"]

    if args.url and args.date is None and args.title and _validate_date(args.title):
        args.date = args.title
        args.title = None

    query = args.url or args.title
    if not query:
        parser.print_help()
        sys.exit(1)

    stats_date = args.date or str(date_cls.today() - timedelta(days=1))
    if not _validate_date(stats_date):
        print(f"Invalid date format: {stats_date!r}. Use YYYY-MM-DD.")
        sys.exit(1)

    tracks = load_all_tracks()
    track  = find_track(query, tracks)
    if not track:
        print(f"Track not found in discography: {query!r}")
        sys.exit(1)

    print(f"Track      : {track['title']} [{track['track_id']}]")
    print(f"Stats date : {stats_date}")
    print(f"Account    : {args.account} ({handle})")
    related_tracks = find_combined_tracks(track, tracks) if args.combined else [track]
    related_track_ids = [t["track_id"] for t in related_tracks]
    print(f"Combined   : {'yes' if args.combined else 'no'} ({len(related_tracks)} track{'s' if len(related_tracks) != 1 else ''})")

    total_today_hist, total_yesterday, daily_today_hist, daily_yesterday, daily_last_week = load_history_for_tracks(related_track_ids, stats_date)
    print(f"History    : today={total_today_hist}, yesterday={total_yesterday}, daily_today={daily_today_hist}, daily_yesterday={daily_yesterday}, daily_last_week={daily_last_week}")

    # Validate history data: if daily==0 and we have yesterday data, it's not fully updated yet
    history_is_valid = True
    if total_today_hist is not None and total_yesterday is not None:
        if daily_today_hist == 0:
            print("[!] History data found but not fully updated (daily=0 despite yesterday data). Will attempt API refresh…")
            history_is_valid = False

    if total_today_hist is not None and history_is_valid:
        total_scraped = total_today_hist
        print(f"Data found in history, using it : {total_scraped:,}")
    elif args.combined:
        print("No valid combined history data available. Aborting to avoid posting partial single-track data.")
        sys.exit(1)
    elif args.no_scrape:
        print("No valid history data available and --no-scrape specified. Aborting.")
        sys.exit(1)
    else:
        # Try API retry first
        print("History data not found or not fully updated. Attempting API fetch with retry loop…")
        total_scraped = fetch_stream_with_retry(track["track_id"], stats_date, total_yesterday)
        
        if total_scraped is None:
            # Fallback to Playwright scraping
            print("\nAPI retry completed. Falling back to Playwright scrape…")
            total_scraped = scrape_track(track)
            if total_scraped is None:
                print("Scrape failed and no history available. Aborting.")
                sys.exit(1)

    covers    = load_covers()
    cover_url = get_cover_url(track, covers)
    if not cover_url:
        print("Warning: no cover found.")

    comparison_daily = daily_last_week if args.compare == "last-week" else daily_yesterday
    comparison_label = "Last Week" if args.compare == "last-week" else "Yesterday"

    img_path = generate_spotlight_image(
        track           = track,
        total_scraped   = total_scraped,
        total_yesterday = total_yesterday,
        comparison_daily = comparison_daily,
        comparison_label = comparison_label,
        cover_url       = cover_url,
        stats_date      = stats_date,
        handle          = handle,
        combined        = args.combined,
        highlight       = args.highlight,
    )

    # New default: POST to Twitter unless --no-post is specified
    post_requested = not args.no_post
    
    # Generate tweet text (shown in all cases)
    from datetime import datetime
    daily = (total_scraped - total_yesterday) if total_yesterday else None
    
    # For tweet text, use simpler formatting without special unicode characters
    daily_tweet = f"{int(daily):,}" if daily and daily >= 0 else "?"
    total_tweet = f"{int(total_scraped):,}"
    
    # Format date as 'April 6th, 2026'
    def ordinal(n):
        if 10 <= n % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
        return str(n) + suffix

    date_obj = datetime.strptime(stats_date, "%Y-%m-%d")
    date_fmt_long = date_obj.strftime("%A, %B {S}, %Y").replace('{S}', ordinal(date_obj.day))

    def movement_line(current_daily: int | None, baseline_daily: int | None, label: str) -> str | None:
        if current_daily is None or baseline_daily is None or baseline_daily <= 0:
            return None
        diff = current_daily - baseline_daily
        pct = diff / baseline_daily * 100
        verb = "rose" if diff > 0 else "fell" if diff < 0 else "remained stable"
        diff_fmt = f"{abs(int(diff)):,}"
        pct_fmt = f"{pct:+.1f}%"
        if diff == 0:
            return f"The song remained stable [0.0%] compared to {label}."
        return f"The song {verb} {diff_fmt} streams [{pct_fmt}] compared to {label}."

    # Compose tweet in requested format
    tweet_lines = []
    combined_suffix = " across all versions" if args.combined else ""
    gainer_period = "weekly" if args.compare == "last-week" else "daily"
    emoji = album_emoji(track.get("album"))
    tweet_lines.append(
        f'{emoji} "{track["title"]}" was one of the biggest {gainer_period} gainers '
        f'yesterday{combined_suffix}, {date_fmt_long}. It received {daily_tweet} streams.'
    )
    if args.compare == "last-week":
        comparison = movement_line(daily, daily_last_week, "last week")
    else:
        comparison = movement_line(daily, daily_yesterday, "yesterday")
    if comparison:
        tweet_lines.append(comparison)
    else:
        tweet_lines.append(f'Total streams: {total_tweet}.')
    try:
        if args.combined:
            print("Best-day-since note skipped: combined mode.")
        else:
            from best_day_since import best_day_since_for_track, row_label
            best_day = best_day_since_for_track(track["track_id"], stats_date, min_days=14)
            if best_day:
                tweet_lines.append(f"The song earned its {row_label(best_day)}.")
    except Exception as e:
        print(f"Best-day-since note skipped: {e}")
    tweet = "\n\n".join(tweet_lines)
    print(f"\nTweet : {tweet}")
    
    # Post to Twitter if requested
    if post_requested:
        if not twitter_session.exists():
            print(f"Twitter session not found: {twitter_session}")
            print("Generate image successfully, but skipping Twitter post.")
        else:
            try:
                from core.twitter import post_with_image
                success = post_with_image(tweet, img_path, twitter_session)
                if success:
                    print("✓ Posté avec succès.")
                else:
                    print("✗ Échec du post Twitter.")
                    sys.exit(1)
            except ImportError as e:
                print(f"Twitter module not available: {e}")
                print("Image generated successfully, but could not post to Twitter.")
    else:
        print("\nTwitter post suppressed (--no-post).")


if __name__ == "__main__":
    main()
