#!/usr/bin/env python3
"""
Fetch Spotify daily charts for all available countries in parallel, keep only
Taylor Swift songs, resolve track IDs, and write
website/site/data/charts_worldwide.json.

Also writes a per-date snapshot to:
collectors/spotify/charts/worldwide/history/YYYY/MM/YYYY-MM-DD/ts_worldwide_YYYY-MM-DD.json

The list of countries is discovered dynamically from the Spotify Charts API
overview endpoint (auth/v1/overview/GLOBAL) — no hardcoded country list.

Usage:
    python daily.py                   # uses today's date
    python daily.py 2026-03-28        # positional date
    python daily.py --date 2026-03-28 # named date
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import aiohttp
import requests as _requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright

_CORE_DIR = Path(__file__).resolve().parents[4] / "collectors" / "spotify"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
from core.data_paths import legacy_spotify_chart_dir, spotify_chart_dir
from core.git_ops import git_commit_and_push
from core.twitter import post_thread, split_tweets

def _build_http_session() -> _requests.Session:
    retry = Retry(total=3, connect=3, read=3, backoff_factor=1.0,
                  status_forcelist=(500, 502, 503, 504), raise_on_status=False)
    s = _requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    return s

_http = _build_http_session()

# ── Paths ─────────────────────────────────────────────────────────────────────
# collectors/spotify/charts/worldwide/daily.py → parents[4] = tsm-backend/
ROOT            = Path(__file__).resolve().parents[4]
GLOBAL_DAILY    = ROOT / "collectors" / "spotify" / "charts" / "global" / "daily.py"
FR_DAILY        = ROOT / "collectors" / "spotify" / "charts" / "fr" / "daily.py"
SESSION_FILE        = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "spotify_session.json"
_BEARER_CACHE_FILE  = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "bearer_cache.json"
_BEARER_TOKEN_TTL   = 50 * 60
OUTPUT_PATH     = ROOT / "website" / "site" / "data" / "charts_worldwide.json"
HISTORY_ROOT    = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "history"
TOTAL_DAYS_PATH = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "total_days.json"
TWITTER_SESSION = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json"

WEBSITE_SONGS_PATH = ROOT / "website" / "site" / "data" / "songs.json"
DISCO_SONGS_PATH   = ROOT / "db" / "discography" / "songs.json"
DISCO_ALBUMS_DIR   = ROOT / "db" / "discography" / "albums"
MANUAL_MAP_PATH    = ROOT / "scripts" / "chart_title_to_track_id.json"

# ── Config ────────────────────────────────────────────────────────────────────
_API_BASE  = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
_UA        = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
TS_NAME         = "Taylor Swift"
SEMAPHORE       = int(os.getenv("SPOTIFY_WORLDWIDE_SEMAPHORE", "10"))
ADAPTIVE_MIN    = int(os.getenv("SPOTIFY_WORLDWIDE_ADAPTIVE_MIN", "5"))
ADAPTIVE_MAX    = int(os.getenv("SPOTIFY_WORLDWIDE_ADAPTIVE_MAX", str(max(SEMAPHORE, 20))))
ADAPTIVE_STEP_SUCCESSES = int(os.getenv("SPOTIFY_WORLDWIDE_ADAPTIVE_STEP_SUCCESSES", "25"))
FETCH_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS", "0"))
SKIP_LATEST_FALLBACK_ON_404 = os.getenv("SPOTIFY_SKIP_LATEST_FALLBACK_ON_404", "").strip().lower() in {"1", "true", "yes", "on"}
_OVERVIEW_URL   = "https://charts-spotify-com-service.spotify.com/auth/v1/overview/GLOBAL"

_ALBUM_EMOJI: list[tuple[str, str]] = [
    ("the life of a showgirl", "❤️‍🔥"),
    ("the tortured poets department", "🤍"),
    ("midnights", "💙"),
    ("evermore", "🤎"),
    ("folklore", "🩶"),
    ("lover", "🩷"),
    ("reputation", "🖤"),
    ("1989", "🩵"),
    ("red", "❤️"),
    ("speak now", "💜"),
    ("fearless", "💛"),
    ("taylor swift", "💚"),
]


def _album_emoji(album: str) -> str:
    al = album.lower().strip()
    for key, emoji in _ALBUM_EMOJI:
        if al.startswith(key) or key in al:
            return emoji
    return "🎵"


# ── Text normalisation helpers (inlined from scripts/chartr2.py) ──────────────
_TRACK_ID_RE  = re.compile(r"track/([A-Za-z0-9]+)")
_PARENS_RE    = re.compile(r"\s*[\(\[].*?[\)\]]")
_FEAT_RE      = re.compile(r"\s+(feat\.|featuring|ft\.)\s+.*$", re.IGNORECASE)
_MULTISPACE   = re.compile(r"\s+")


def _normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return _MULTISPACE.sub(" ", s.lower().strip())


def _simplify_title(title: str) -> str:
    s = _normalize_text(title)
    s = _FEAT_RE.sub("", s)
    s = _PARENS_RE.sub("", s)
    for token in ("taylor's version", "taylors version", "from the vault",
                  "remix", "acoustic", "live", "version"):
        s = s.replace(token, "")
    return _MULTISPACE.sub(" ", s).strip(" -").strip()


def _possible_keys(title: str) -> set[str]:
    keys = set()
    n = _normalize_text(title)
    s = _simplify_title(title)
    if n:
        keys.add(n)
    if s:
        keys.add(s)
    s2 = s.replace("'", "").replace("\u2019", "")
    if s2:
        keys.add(s2)
    return {k for k in keys if k}


def _extract_track_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = _TRACK_ID_RE.search(url)
    return m.group(1) if m else None


def _get_track_id_from_item(item: Dict[str, Any]) -> Optional[str]:
    for key in ("track_id", "id"):
        val = item.get(key)
        if val:
            return str(val)
    for key in ("spotify_url", "url", "track_url"):
        val = item.get(key)
        if isinstance(val, str):
            tid = _extract_track_id_from_url(val)
            if tid:
                return tid
    return None


def _title_fields(item: Dict[str, Any]) -> list[str]:
    return [
        v.strip()
        for key in ("title", "name", "base_title", "title_clean", "song_family")
        if isinstance(v := item.get(key), str) and v.strip()
    ]


def _load_json(path: Path) -> Any:
    # utf-8-sig strips one BOM; lstrip handles a double-BOM edge case
    return json.loads(path.read_text(encoding="utf-8-sig").lstrip("﻿"))


def _iter_website_songs() -> Iterable[Dict[str, Any]]:
    if WEBSITE_SONGS_PATH.exists():
        data = _load_json(WEBSITE_SONGS_PATH)
        if isinstance(data, list):
            yield from (x for x in data if isinstance(x, dict))
        elif isinstance(data, dict) and isinstance(data.get("songs"), list):
            yield from (x for x in data["songs"] if isinstance(x, dict))


def _iter_disco_tracks() -> Iterable[Dict[str, Any]]:
    if DISCO_SONGS_PATH.exists():
        data = _load_json(DISCO_SONGS_PATH)
        if isinstance(data, list):
            yield from (x for x in data if isinstance(x, dict))
    if DISCO_ALBUMS_DIR.exists():
        for album_file in sorted(DISCO_ALBUMS_DIR.glob("*.json"),
                                 key=lambda p: p.name.casefold()):
            payload = _load_json(album_file)
            if not isinstance(payload, dict):
                continue
            album_name = payload.get("album", "")
            for section in payload.get("sections", []):
                for track in (section.get("tracks") or []):
                    if isinstance(track, dict):
                        merged = {**track}
                        merged.setdefault("album", album_name)
                        yield merged


def build_track_lookup() -> Dict[str, str]:
    cached = getattr(build_track_lookup, "_cache", None)
    if cached is not None:
        return cached
    lookup: Dict[str, str] = {}
    for item in _iter_website_songs():
        tid = _get_track_id_from_item(item)
        if not tid:
            continue
        for field in _title_fields(item):
            for key in _possible_keys(field):
                lookup.setdefault(key, tid)
    for item in _iter_disco_tracks():
        tid = _get_track_id_from_item(item)
        if not tid:
            continue
        for field in _title_fields(item):
            for key in _possible_keys(field):
                lookup.setdefault(key, tid)
    build_track_lookup._cache = lookup
    return lookup


def build_manual_mapping() -> Dict[str, str]:
    cached = getattr(build_manual_mapping, "_cache", None)
    if cached is not None:
        return cached
    if not MANUAL_MAP_PATH.exists():
        return {}
    data = _load_json(MANUAL_MAP_PATH)
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in data.items():
        for key in _possible_keys(k):
            out[key] = str(v)
    build_manual_mapping._cache = out
    return out


def resolve_track_id(
    song_name: str,
    manual: Dict[str, str],
    lookup: Dict[str, str],
) -> Optional[str]:
    keys = _possible_keys(song_name)
    # 1. manual override
    for key in keys:
        if key in manual:
            return manual[key]
    # 2. exact match
    for key in keys:
        if key in lookup:
            return lookup[key]
    # 3. substring inclusion
    for key in keys:
        for k, tid in lookup.items():
            if key in k or k in key:
                return tid
    # 4. fuzzy prefix
    for key in keys:
        for k, tid in lookup.items():
            if abs(len(key) - len(k)) <= 3 and key[:10] == k[:10]:
                return tid
    return None


# ── Spotify helpers ────────────────────────────────────────────────────────────

def _clean_int(value: object) -> Optional[int]:
    if value is None:
        return None

    try:
        n = int(float(str(value).strip()))
        return n if n > 0 else None
    except (ValueError, TypeError):
        return None


def _worldwide_history_path(chart_date: str) -> Path:
    return spotify_chart_dir("worldwide", chart_date) / f"ts_worldwide_{chart_date}.json"


def _updated_lock_path(chart_date: str) -> Path:
    return spotify_chart_dir("worldwide", chart_date) / "updated.lock"


def _load_cached_bearer() -> str | None:
    try:
        data = json.loads(_BEARER_CACHE_FILE.read_text(encoding="utf-8-sig"))
        if time.time() - float(data.get("ts", 0)) < _BEARER_TOKEN_TTL:
            token = str(data.get("token") or "").strip()
            return token or None
    except Exception:
        pass
    return None


class TokenExpired(RuntimeError):
    pass


def _get_bearer_token_and_regions(*, force_refresh: bool = False) -> tuple[str, dict[str, str]]:
    """
    Récupère le Bearer token via le cache global si disponible, sinon via Playwright.
    Extrait la liste des régions via l'API overview (+ HTML si Playwright a tourné).
    """
    from bs4 import BeautifulSoup

    cached = getattr(_get_bearer_token_and_regions, "_cache", None)
    cached_ts = float(getattr(_get_bearer_token_and_regions, "_cache_ts", 0))
    if not force_refresh and cached is not None and time.time() - cached_ts < (_BEARER_TOKEN_TTL - 300):
        print("[INFO] Bearer token et regions recuperes depuis le cache process.", flush=True)
        return cached

    if cached is not None and not force_refresh:
        print("[INFO] Cache bearer process expire, refresh token.", flush=True)

    cached_token = None if force_refresh else _load_cached_bearer()
    if cached_token:
        print("[INFO] Bearer token récupéré depuis le cache global.", flush=True)
        token = cached_token
        html_holder: list[str] = []
    else:
        _MAX_PW_ATTEMPTS = 3
        _PW_RETRY_DELAY = 15
        api_host = _API_BASE.split("//")[1].split("/")[0]

        token_holder: list[str] = []
        html_holder = []

        for pw_attempt in range(_MAX_PW_ATTEMPTS):
            if pw_attempt > 0:
                print(f"[INFO] Playwright retry {pw_attempt}/{_MAX_PW_ATTEMPTS - 1} (attente {_PW_RETRY_DELAY}s)…", flush=True)
                time.sleep(_PW_RETRY_DELAY)

            token_holder = []
            html_holder = []

            def _on_request(req: Any, _th: list = token_holder, _ah: str = api_host) -> None:
                if _ah in req.url and not _th:
                    auth = req.headers.get("authorization", "")
                    if auth.startswith("Bearer "):
                        _th.append(auth[7:])

            p = sync_playwright().start()
            browser = None
            _pw_error: Exception | None = None
            try:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                ctx = browser.new_context(
                    storage_state=str(SESSION_FILE),
                    user_agent=_UA,
                    viewport={"width": 1280, "height": 800},
                )
                page = ctx.new_page()
                page.on("request", _on_request)
                page.goto(
                    "https://charts.spotify.com/",
                    wait_until="networkidle",
                    timeout=45_000,
                )
                deadline = time.time() + 20
                while not token_holder and time.time() < deadline:
                    page.wait_for_timeout(300)
                html_holder.append(page.content())
            except Exception as e:
                _pw_error = e
                print(f"[WARN] Playwright tentative {pw_attempt + 1} échouée: {e}", flush=True)
            finally:
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass
                try:
                    p.stop()
                except Exception:
                    pass

            if _pw_error is None:
                break
            if pw_attempt == _MAX_PW_ATTEMPTS - 1:
                raise _pw_error

        if not token_holder:
            raise RuntimeError(
                "Bearer token not found — check global/tools/json/spotify_session.json"
            )
        token = token_holder[0]

    # 1. API overview
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
        "Referer":       "https://charts.spotify.com/",
        "User-Agent":    _UA,
    }
    _attempt = 0
    while True:
        _attempt += 1
        try:
            resp = _http.get(_OVERVIEW_URL, headers=headers, timeout=15)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 30))
                print(f"[WARN] Overview 429 — retry dans {wait}s (tentative {_attempt})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except Exception as exc:
            wait = min(30 * _attempt, 300)
            print(f"[WARN] Overview erreur ({exc}) — retry dans {wait}s (tentative {_attempt})")
            time.sleep(wait)
    country_filters = resp.json().get("countryFilters") or []
    api_regions: dict[str, str] = {
        c["code"].lower(): c["readableName"]
        for c in country_filters
        if c.get("code") and c.get("readableName")
    }

    # 2. Extraction HTML exhaustive
    html = html_holder[0] if html_holder else ""
    soup = BeautifulSoup(html, "html.parser")
    region_map = {}
    # Cherche tous les <option> dans les menus déroulants (country/region)
    for select in soup.find_all("select"):
        for opt in select.find_all("option"):
            code = opt.get("value", "").lower()
            name = opt.text.strip()
            if code and name and code != "global":
                region_map[code] = name
    # Parfois, les régions sont dans un objet JS global (window.__INITIAL_STATE__)
    # On tente de parser les codes présents dans le HTML brut
    import re as _re
    for m in _re.finditer(r'"code":"([a-zA-Z0-9_-]+)","readableName":"([^"]+)"', html):
        code, name = m.group(1).lower(), m.group(2)
        if code and name:
            region_map[code] = name

    # 3. Fusionne toutes les sources (API + HTML)
    all_regions = dict(api_regions)
    for code, name in region_map.items():
        if code not in all_regions:
            all_regions[code] = name

    # 4. Garantit les régions clés toujours présentes
    REQUIRED_REGIONS = {
        "global": "Global",
        "fr": "France",
        "us": "United States",
        "gb": "United Kingdom",
        "de": "Germany",
        "au": "Australia",
        "ca": "Canada",
        "br": "Brazil",
        "mx": "Mexico",
        "es": "Spain",
        "it": "Italy",
        "nl": "Netherlands",
        "se": "Sweden",
        "no": "Norway",
        "fi": "Finland",
        "pl": "Poland",
        "at": "Austria",
        "ch": "Switzerland",
        "be": "Belgium",
        "pt": "Portugal",
        "nz": "New Zealand",
        "ie": "Ireland",
        "jp": "Japan",
        "sg": "Singapore",
        "ph": "Philippines",
        "id": "Indonesia",
        "my": "Malaysia",
        "tw": "Taiwan",
        "ar": "Argentina",
        "cl": "Chile",
        "co": "Colombia",
        "pe": "Peru",
        "za": "South Africa",
        "in": "India",
        # Additional confirmed Spotify Charts markets
        "ae": "United Arab Emirates",
        "cz": "Czech Republic",
        "dk": "Denmark",
        "ee": "Estonia",
        "hk": "Hong Kong",
        "hu": "Hungary",
        "is": "Iceland",
        "il": "Occupied Palestine",
        "kr": "South Korea",
        "lt": "Lithuania",
        "lu": "Luxembourg",
        "lv": "Latvia",
        "pa": "Panama",
        "py": "Paraguay",
        "ro": "Romania",
        "sa": "Saudi Arabia",
        "sk": "Slovakia",
        "th": "Thailand",
        "tr": "Turkey",
        "uy": "Uruguay",
        "vn": "Vietnam",
        "bg": "Bulgaria",
        "bo": "Bolivia",
        "cr": "Costa Rica",
        "cy": "Cyprus",
        "do": "Dominican Republic",
        "ec": "Ecuador",
        "gt": "Guatemala",
        "hn": "Honduras",
        "ni": "Nicaragua",
        "si": "Slovenia",
        "sv": "El Salvador",
    }
    added = []
    for code, name in REQUIRED_REGIONS.items():
        if code not in all_regions:
            all_regions[code] = name
            added.append(code)
    if added:
        print(f"[INFO] Force-added {len(added)} required regions: {', '.join(added)}")

    print(f"[INFO] Discovered {len(all_regions)} regions total (API + HTML + required)")
    result = (token, all_regions)
    _get_bearer_token_and_regions._cache = result
    _get_bearer_token_and_regions._cache_ts = time.time()
    return result


def _parse_ts_entries(data: dict) -> list[dict]:
    """Parse API response; keep only Taylor Swift entries; extract trackUri when present."""
    rows: list[dict] = []
    for entry in (data.get("entries") or []):
        ced  = entry.get("chartEntryData") or {}
        meta = entry.get("trackMetadata") or {}

        artists    = meta.get("artists") or []
        artist_str = ", ".join(a.get("name", "") for a in artists if a.get("name"))
        if TS_NAME.lower() not in artist_str.lower():
            continue

        rank       = _clean_int(ced.get("currentRank"))
        track_name = (meta.get("trackName") or "").strip()
        if not track_name or rank is None:
            continue

        # trackUri: "spotify:track:4cluDES4hQEUhmXj6TXkSo"
        track_uri = meta.get("trackUri") or ""
        track_id_from_uri: Optional[str] = (
            track_uri.split(":")[-1]
            if track_uri.startswith("spotify:track:") else None
        )

        rows.append({
            "rank":          rank,
            "track_name":    track_name,
            "artist_names":  artist_str,
            "streams":       _clean_int((ced.get("rankingMetric") or {}).get("value")),
            "previous_rank": _clean_int(ced.get("previousRank")),
            "peak_rank":     _clean_int(ced.get("peakRank")),
            "total_days":    _clean_int(ced.get("consecutiveAppearancesOnChart")),
            "_track_id_uri": track_id_from_uri,
        })
    return rows


def _find_first_date(value) -> str | None:
    if isinstance(value, str):
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", value)
        return match.group(0) if match else None
    if isinstance(value, dict):
        for key in ("date", "chartDate", "displayDate", "latestDate"):
            found = _find_first_date(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_first_date(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_first_date(item)
            if found:
                return found
    return None


class AdaptiveLimiter:
    def __init__(self, start: int, minimum: int, maximum: int, step_successes: int) -> None:
        self.limit = max(1, start)
        self.minimum = max(1, min(minimum, self.limit))
        self.maximum = max(self.limit, maximum)
        self.step_successes = max(1, step_successes)
        self.active = 0
        self.successes_since_change = 0
        self.cond = asyncio.Condition()

    async def __aenter__(self) -> "AdaptiveLimiter":
        async with self.cond:
            while self.active >= self.limit:
                await self.cond.wait()
            self.active += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        async with self.cond:
            self.active -= 1
            self.cond.notify_all()

    async def mark_success(self) -> None:
        async with self.cond:
            self.successes_since_change += 1
            if self.limit < self.maximum and self.successes_since_change >= self.step_successes:
                self.limit += 1
                self.successes_since_change = 0
                print(f"  [limit ] montee concurrence -> {self.limit}", flush=True)
                self.cond.notify_all()

    async def mark_rate_limited(self, retry_after: int) -> None:
        async with self.cond:
            new_limit = max(self.minimum, max(1, self.limit // 2))
            self.successes_since_change = 0
            if new_limit < self.limit:
                self.limit = new_limit
                print(f"  [limit ] 429 recu, concurrence -> {self.limit} (retry {retry_after}s)", flush=True)
            self.cond.notify_all()


async def _fetch_region(
    session: aiohttp.ClientSession,
    sem: AdaptiveLimiter,
    region: str,
    chart_date: str,
    headers: dict[str, str],
) -> tuple[str, list[dict]]:
    chart_id = "regional-global-daily" if region == "global" else f"regional-{region}-daily"
    url = f"{_API_BASE}/{chart_id}/{chart_date}"
    latest_url = f"{_API_BASE}/{chart_id}/latest"
    attempt = 0
    while True:
        attempt += 1
        async with sem:
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        rows = _parse_ts_entries(data)
                        await sem.mark_success()
                        print(f"  [{region:>6}] {len(rows)} TS entries ({chart_date})")
                        return region, rows
                    if resp.status == 404:
                        # Chart absent pour cette région à cette date — donnée valide vide
                        if SKIP_LATEST_FALLBACK_ON_404:
                            await sem.mark_success()
                            print(f"  [{region:>6}] 404 date - no chart")
                            return region, []
                        async with session.get(
                            latest_url,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as latest_resp:
                            if latest_resp.status == 200:
                                latest_data = await latest_resp.json(content_type=None)
                                latest_date = _find_first_date(latest_data)
                                if latest_date == chart_date:
                                    rows = _parse_ts_entries(latest_data)
                                    await sem.mark_success()
                                    print(f"  [{region:>6}] {len(rows)} TS entries ({chart_date}, via latest)")
                                    return region, rows
                                raise RuntimeError(
                                    f"{region}: dated chart 404 and latest points to {latest_date!r}, expected {chart_date}"
                                )
                            if latest_resp.status == 404:
                                await sem.mark_success()
                                print(f"  [{region:>6}] 404 date+latest - no chart")
                                return region, []
                            raise RuntimeError(
                                f"{region}: dated chart 404 and latest HTTP {latest_resp.status}"
                            )
                    if resp.status == 429:
                        wait = int(resp.headers.get("Retry-After", 30))
                        await sem.mark_rate_limited(wait)
                        print(f"  [{region:>6}] 429 — retry dans {wait}s (tentative {attempt})")
                        await asyncio.sleep(wait)
                        continue
                    if resp.status == 401:
                        raise TokenExpired(f"{region}: HTTP 401")
                    if 400 <= resp.status < 500:
                        raise RuntimeError(f"{region}: HTTP {resp.status}")
                    print(f"  [{region:>6}] HTTP {resp.status} — retry dans 10s (tentative {attempt})")
            except asyncio.TimeoutError:
                if FETCH_MAX_ATTEMPTS > 0 and attempt >= FETCH_MAX_ATTEMPTS:
                    raise RuntimeError(f"{region}: timeout after {attempt} attempts")
                print(f"  [{region:>6}] timeout — retry dans 10s (tentative {attempt})")
            except Exception as exc:
                if isinstance(exc, TokenExpired):
                    raise
                if FETCH_MAX_ATTEMPTS > 0 and attempt >= FETCH_MAX_ATTEMPTS:
                    raise
                print(f"  [{region:>6}] erreur ({exc}) — retry dans 10s (tentative {attempt})")
        await asyncio.sleep(min(10 * attempt, 60))


async def _run_async(chart_date: str, token: str, regions: dict[str, str]) -> dict[str, list[dict]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
        "Referer":       "https://charts.spotify.com/",
        "User-Agent":    _UA,
    }
    sem = AdaptiveLimiter(SEMAPHORE, ADAPTIVE_MIN, ADAPTIVE_MAX, ADAPTIVE_STEP_SUCCESSES)
    print(
        f"[INFO] Adaptive concurrency: start={sem.limit}, min={sem.minimum}, "
        f"max={sem.maximum}, +1/{sem.step_successes} succes",
        flush=True,
    )
    async with aiohttp.ClientSession() as session:
        tasks = [
            _fetch_region(session, sem, region, chart_date, headers)
            for region in regions
        ]
        results = await asyncio.gather(*tasks)
    return dict(results)


def _run_async_with_token_refresh(
    chart_date: str,
    token: str,
    regions: dict[str, str],
) -> tuple[str, dict[str, str], dict[str, list[dict]]]:
    try:
        return token, regions, asyncio.run(_run_async(chart_date, token, regions))
    except TokenExpired as exc:
        print(f"[WARN] Bearer token refuse par Spotify ({exc}); refresh et retry date {chart_date}.", flush=True)
        token, _all_regions = _get_bearer_token_and_regions(force_refresh=True)
        return token, regions, asyncio.run(_run_async(chart_date, token, regions))


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch worldwide Spotify charts for Taylor Swift songs."
    )
    parser.add_argument("date_pos", nargs="?", metavar="YYYY-MM-DD")
    parser.add_argument("--date", metavar="YYYY-MM-DD")
    parser.add_argument("--dates", nargs="+", metavar="YYYY-MM-DD")
    parser.add_argument("--backfill-from", metavar="YYYY-MM-DD")
    parser.add_argument("--backfill-to", metavar="YYYY-MM-DD")
    parser.add_argument(
        "--no-post",
        action="store_true",
        help="Skip Twitter post.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch all regions even if already present for this date.",
    )
    args = parser.parse_args()

    if args.dates or args.backfill_from or args.backfill_to:
        try:
            if args.dates:
                chart_dates = [
                    datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
                    for raw in args.dates
                ]
            else:
                if not args.backfill_from or not args.backfill_to:
                    print("[ERROR] --backfill-from and --backfill-to must be used together")
                    return 1
                start_day = datetime.strptime(args.backfill_from, "%Y-%m-%d").date()
                end_day = datetime.strptime(args.backfill_to, "%Y-%m-%d").date()
                if start_day > end_day:
                    print(f"[ERROR] --backfill-from ({start_day}) > --backfill-to ({end_day})")
                    return 1
                chart_dates = []
                cur = start_day
                while cur <= end_day:
                    chart_dates.append(cur.isoformat())
                    cur += timedelta(days=1)
        except ValueError as exc:
            print(f"[ERROR] Invalid backfill date: {exc}")
            return 1

        original_argv = sys.argv[:]
        original_run_all = os.environ.get("CHARTS_RUN_ALL")
        started = time.perf_counter()
        try:
            os.environ["CHARTS_RUN_ALL"] = "1"
            for idx, chart_date in enumerate(chart_dates, 1):
                print(f"\n[BACKFILL] worldwide {idx}/{len(chart_dates)}: {chart_date}", flush=True)
                sys.argv = [original_argv[0], chart_date]
                if args.no_post:
                    sys.argv.append("--no-post")
                if args.force:
                    sys.argv.append("--force")
                rc = main()
                if rc != 0:
                    return rc
        finally:
            sys.argv = original_argv
            if original_run_all is None:
                os.environ.pop("CHARTS_RUN_ALL", None)
            else:
                os.environ["CHARTS_RUN_ALL"] = original_run_all
        print(f"[ OK ] worldwide backfill {len(chart_dates)} date(s) en {time.perf_counter() - started:.1f}s")
        git_commit_and_push(ROOT, f"charts worldwide backfill {chart_dates[0]} -> {chart_dates[-1]}")
        return 0

    raw_date = args.date or args.date_pos or str(date.today() - timedelta(days=1))
    try:
        chart_date = datetime.strptime(raw_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        print(f"[ERROR] Invalid date: {raw_date!r}")
        return 1

    if not SESSION_FILE.exists():
        print(f"[ERROR] Session file not found: {SESSION_FILE}")
        return 1

    print(f"[INFO] chart_date = {chart_date}")
    if getattr(_get_bearer_token_and_regions, "_cache", None) is None:
        print("[INFO] Acquiring bearer token and discovering regions via Playwright/cache...")
    else:
        print("[INFO] Using cached bearer token and regions.")
    token, regions = _get_bearer_token_and_regions()
    print(f"[INFO] Token acquired. {len(regions)} regions to fetch.")


    # Pré-skip des pays déjà présents pour cette date (sauf si --force)
    already_done: set[str] = set()
    existing_by_track: dict[str, list[dict]] = {}
    if not args.force and OUTPUT_PATH.exists():
        try:
            with open(OUTPUT_PATH, encoding="utf-8-sig") as f:
                data = json.load(f)
            if data.get("date") == chart_date and "by_track" in data:
                existing_by_track = data["by_track"]
                for entries in data["by_track"].values():
                    for entry in entries:
                        if "country" in entry:
                            already_done.add(entry["country"])
            if already_done:
                print(f"[INFO] Skipping {len(already_done)} regions already present for {chart_date}")
        except Exception as e:
            print(f"[WARN] Could not parse existing output: {e}")

    # Résolution des tracks en avance pour écrire les fichiers régionaux dès la phase 1 terminée
    print("[INFO] Resolving track IDs…")
    track_lookup  = build_track_lookup()
    manual_lookup = build_manual_mapping()

    id_to_name: dict[str, str] = {}
    id_to_album: dict[str, str] = {}
    for _item in _iter_website_songs():
        _tid = _get_track_id_from_item(_item)
        if _tid:
            _name = (_item.get("title") or _item.get("name") or "").strip()
            if _name:
                id_to_name.setdefault(_tid, _name)
            _album = (_item.get("album") or "").strip()
            if _album:
                id_to_album.setdefault(_tid, _album)
    for _item in _iter_disco_tracks():
        _tid = _get_track_id_from_item(_item)
        if _tid:
            _name = (_item.get("title") or _item.get("name") or "").strip()
            if _name:
                id_to_name.setdefault(_tid, _name)
            _album = (_item.get("album") or "").strip()
            if _album:
                id_to_album.setdefault(_tid, _album)

    regions_to_fetch = {k: v for k, v in regions.items() if k not in already_done}

    # Phase 1 : fetch global et fr en priorité pour poster pendant la phase 2
    _PRIORITY = set() if args.no_post else {"global", "fr"}
    priority_to_fetch = {k: v for k, v in regions_to_fetch.items() if k in _PRIORITY}
    other_to_fetch    = {k: v for k, v in regions_to_fetch.items() if k not in _PRIORITY}

    t0 = time.perf_counter()
    if priority_to_fetch:
        print(f"[INFO] Phase 1 : fetch prioritaire ({', '.join(sorted(priority_to_fetch))})…")
        token, _, priority_results = _run_async_with_token_refresh(chart_date, token, priority_to_fetch)
        print(f"[INFO] Phase 1 terminée en {time.perf_counter() - t0:.1f}s")
        for region in ("global", "fr"):
            if region in priority_results and priority_results[region]:
                _write_regional_ts_chart(chart_date, region, priority_results[region], manual_lookup, track_lookup)
    else:
        priority_results = {}

    # Lancer le posting global/fr en background pendant le fetch des autres régions
    _posting_thread: threading.Thread | None = None
    if not args.no_post and priority_to_fetch:
        def _post_regional() -> None:
            for script in (GLOBAL_DAILY, FR_DAILY):
                if not script.exists():
                    continue
                result = subprocess.run(
                    [sys.executable, str(script), "--post-only", chart_date],
                    cwd=str(ROOT),
                )
                if result.returncode != 0:
                    print(f"[WARN] {script.name} --post-only a échoué (code {result.returncode})", flush=True)
        _posting_thread = threading.Thread(target=_post_regional, daemon=True, name="regional-posting")
        _posting_thread.start()

    # Phase 2 : fetch toutes les autres régions
    if other_to_fetch:
        print(f"[INFO] Phase 2 : fetch {len(other_to_fetch)} regions (semaphore={SEMAPHORE})...")
        token, _, other_results = _run_async_with_token_refresh(chart_date, token, other_to_fetch)
        print(f"[INFO] Phase 2 terminée en {time.perf_counter() - t0:.1f}s total")
    else:
        other_results = {}

    by_region = {**priority_results, **other_results}

    by_track: dict[str, list[dict]] = {}
    track_names: dict[str, str] = {}
    unresolved: list[dict]          = []

    for region, rows in by_region.items():
        country_name = regions[region]
        for row in rows:
            track_id: Optional[str] = row.get("_track_id_uri")
            if not track_id:
                track_id = resolve_track_id(row["track_name"], manual_lookup, track_lookup)
            if not track_id:
                unresolved.append({"region": region, "track_name": row["track_name"]})
                continue
            track_names.setdefault(track_id, row["track_name"])
            prev_rank = row.get("previous_rank")
            rank = row["rank"]
            rank_change = (prev_rank - rank) if (prev_rank and rank) else None
            by_track.setdefault(track_id, []).append({
                "country":        region,
                "country_name":   country_name,
                "rank":           rank,
                "previous_rank":  prev_rank,
                "rank_change":    rank_change,
                "streams":        row["streams"],
                "peak_rank":      row["peak_rank"],
                "total_days":     row["total_days"],
            })

    # Merge back already-skipped entries from the previous run of the same date.
    # Without this, re-runs would silently discard countries collected in earlier runs.
    if existing_by_track and already_done:
        for track_id, old_entries in existing_by_track.items():
            kept = [e for e in old_entries if e.get("country") in already_done]
            if not kept:
                continue
            if track_id not in by_track:
                by_track[track_id] = kept
            else:
                new_countries = {e["country"] for e in by_track[track_id]}
                for entry in kept:
                    if entry["country"] not in new_countries:
                        by_track[track_id].append(entry)

    # Enrich with stream_change / stream_change_pct from previous day's snapshot
    prev_date = (datetime.strptime(chart_date, "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_path = _worldwide_history_path(prev_date)
    if not prev_path.exists():
        prev_path = legacy_spotify_chart_dir("worldwide", prev_date) / f"ts_worldwide_{prev_date}.json"
    prev_by_track: dict[str, list[dict]] = {}
    if prev_path.exists():
        try:
            prev_data = json.loads(prev_path.read_text(encoding="utf-8-sig"))
            prev_by_track = prev_data.get("by_track", {})
        except Exception as exc:
            print(f"[WARN] Could not load previous day snapshot ({prev_date}): {exc}")

    # Load persistent total_days store seeded by backfill_total_days.py.
    total_days_store: dict[str, int] = {}
    if TOTAL_DAYS_PATH.exists():
        try:
            total_days_store = json.loads(TOTAL_DAYS_PATH.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            print(f"[WARN] Could not load total_days store: {exc}")

    for track_id, entries in by_track.items():
        prev_entries = prev_by_track.get(track_id, [])
        prev_by_country = {e["country"]: e for e in prev_entries}
        for entry in entries:
            prev = prev_by_country.get(entry["country"])
            prev_streams = prev.get("streams") if prev else None
            curr_streams = entry.get("streams")
            if prev_streams and curr_streams and prev_streams > 0:
                stream_change = curr_streams - prev_streams
                entry["stream_change"] = stream_change
                entry["stream_change_pct"] = round(stream_change / prev_streams * 100, 2)
            else:
                entry["stream_change"] = None
                entry["stream_change_pct"] = None

            key = f"{track_id}|{entry['country']}"
            stored = total_days_store.get(key, 0)
            entry["total_days"] = stored + 1
            total_days_store[key] = stored + 1

    try:
        TOTAL_DAYS_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOTAL_DAYS_PATH.write_text(
            json.dumps(total_days_store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[WARN] Could not save total_days store: {exc}")

    # Sort each track's country list by rank
    for entries in by_track.values():
        entries.sort(key=lambda e: (e["rank"] or 9999))

    total_appearances = sum(len(v) for v in by_track.values())
    print(f"[INFO] {len(by_track)} unique tracks, {total_appearances} country appearances")
    if unresolved:
        names = {r["track_name"] for r in unresolved}
        print(f"[WARN] {len(unresolved)} unresolved appearances ({len(names)} unique songs): "
              + ", ".join(sorted(names)[:5]) + ("…" if len(names) > 5 else ""))

    output = {"date": chart_date, "by_track": by_track}

    per_date_path = _worldwide_history_path(chart_date)
    per_date_path.parent.mkdir(parents=True, exist_ok=True)
    per_date_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] Written → {per_date_path}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] Written latest → {OUTPUT_PATH}")
    updated_lock = _updated_lock_path(chart_date)
    updated_lock.touch()
    print(f"[DONE] Written -> {updated_lock}")
    maybe_upload_to_r2()

    if not args.no_post and TWITTER_SESSION.exists():
        has_prev = prev_path.exists()
        locks_dir = per_date_path.parent
        date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
        sorted_tracks = sorted(by_track.items(), key=lambda kv: len(kv[1]), reverse=True)
        url = "🔗 See full update here : https://thetsmuseum.app/charts?region=overall&view=today"
        reentry_items: list[tuple[str, str]] = []
        regular_items: list[tuple[str, str]] = []

        for track_id, entries in sorted_tracks:
            song_name = track_names.get(track_id) or id_to_name.get(track_id) or track_id
            count = len(entries)
            prev_count = len(prev_by_track.get(track_id, []))
            emoji = _album_emoji(id_to_album.get(track_id, ""))

            if has_prev and prev_count == 0:
                if count == 1:
                    e = entries[0]
                    region_name = e.get("country_name") or e.get("country", "")
                    rank = e.get("rank", "?")
                    streams = e.get("streams")
                    streams_str = f"{streams:,}" if streams else "N/A"
                    tweet = (
                        f'{emoji} | "{song_name}" has re-entered the {region_name} Spotify Charts '
                        f"at #{rank} with {streams_str} streams, yesterday ({date_fmt}).\n\n{url}"
                    )
                else:
                    tweet = (
                        f'{emoji} | "{song_name}" has re-entered the Spotify Charts in {count} countries '
                        f"yesterday ({date_fmt}).\n\n{url}"
                    )
                reentry_items.append((track_id, tweet))
            else:
                if has_prev:
                    diff = count - prev_count
                    diff_str = f"+{diff}" if diff >= 0 else str(diff)
                    country_str = f"{count} countries ({diff_str})"
                else:
                    country_str = f"{count} countries"
                regular_items.append((track_id, f'{emoji} | "{song_name}" charted in {country_str} on Spotify yesterday ({date_fmt}).\n\n{url}'))

        all_items = reentry_items + regular_items
        pending = [(tid, tw) for tid, tw in all_items if not (locks_dir / f"posted_{tid}.lock").exists()]
        print(f"[INFO] {len(all_items)} song(s) total, {len(pending)} to post ({len(all_items) - len(pending)} already done).")

        first = True
        for track_id, tweet in pending:
            if not first:
                time.sleep(30)
            first = False
            ok = post_thread([tweet], TWITTER_SESSION)
            if ok:
                (locks_dir / f"posted_{track_id}.lock").touch()
                print(f"[INFO] Posted: {track_id}")
            else:
                print(f"[WARN] Failed: {track_id}")

    if _posting_thread is not None and _posting_thread.is_alive():
        print("[INFO] Attente fin posting global/fr…", flush=True)
        _posting_thread.join(timeout=600)
        if _posting_thread.is_alive():
            print("[WARN] Posting global/fr toujours en cours après 10 minutes", flush=True)

    git_commit_and_push(ROOT, f"charts worldwide {chart_date}")
    return 0


def _write_regional_ts_chart(
    chart_date: str,
    region: str,
    rows: list[dict],
    manual_lookup: dict[str, str],
    track_lookup: dict[str, str],
) -> None:
    """Écrit ts_chart_{date}.json pour global/fr au format attendu par generate_chart_image.py."""
    chart_entries = [
        {
            "rank":          row.get("rank"),
            "track_name":    row.get("track_name", ""),
            "artist_names":  row.get("artist_names", TS_NAME),
            "streams":       row.get("streams"),
            "previous_rank": row.get("previous_rank"),
            "peak_rank":     row.get("peak_rank"),
            "total_days":    row.get("total_days"),
            "streak":        row.get("total_days"),
            "image_url":     None,
        }
        for row in sorted(rows, key=lambda r: r.get("rank") or 9999)
    ]
    out_dir = spotify_chart_dir(region, chart_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    chart_path = out_dir / f"ts_chart_{chart_date}.json"
    chart_path.write_text(json.dumps(chart_entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] Written regional chart → {chart_path}", flush=True)
    (out_dir / "updated.lock").touch()


def maybe_upload_to_r2() -> None:
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        print("[INFO] R2 upload skipped (UPLOAD_TO_R2 explicitly disabled)")
        return

    r2_script = ROOT / "scripts" / "r2.py"
    if not r2_script.exists():
        print(f"[WARN] R2 upload script missing: {r2_script}")
        return

    print("[STEP] Uploading exported data to R2")
    for attempt in range(1, 6):
        result = subprocess.run([sys.executable, str(r2_script)], check=False, cwd=str(ROOT))
        if result.returncode == 0:
            return
        wait = 30 * attempt
        print(f"[WARN] R2 upload failed (exit {result.returncode}), retry dans {wait}s (tentative {attempt}/5)")
        time.sleep(wait)
    print("[ERROR] R2 upload failed après 5 tentatives — poursuite sans upload")


if __name__ == "__main__":
    raise SystemExit(main())
