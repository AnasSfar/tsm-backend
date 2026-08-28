#!/usr/bin/env python3
"""
Fetch Spotify daily charts for all available countries in parallel, keep only
Taylor Swift songs, resolve track IDs, and write
runtime/exports/web/site/data/charts_worldwide.json.

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
import csv
import io
import json
import os
import random
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
_REPO_ROOT_FOR_TWITTER = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT_FOR_TWITTER) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_TWITTER))
from core.data_paths import (
    LEGACY_WEBSITE_DATA_DIR,
    WEB_EXPORT_DATA_DIR,
    first_existing,
    legacy_spotify_chart_dir,
    spotify_chart_dir,
)
from core.git_ops import git_commit_and_push
from core.twitter import post_thread, post_with_image, split_tweets
from collectors.twitter.albums import album_emoji as _shared_album_emoji
from collectors.twitter.text import full_charts_update_line  # noqa: E402
from collectors.twitter.prefixes import SPOTIFY_CHART_PREFIX, with_prefix  # noqa: E402
from collectors.comp.chart_card import render_chart_card, write_chart_card_png  # noqa: E402

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
GLOBAL_CHART_IMAGE_SCRIPT = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "script" / "generate_chart_image.py"
_DEFAULT_SESSION_FILE = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "spotify_session.json"
SESSION_FILE        = Path(os.getenv("SPOTIFY_CHARTS_SESSION_FILE", str(_DEFAULT_SESSION_FILE)))
_WORLDWIDE_BEARER_CACHE_FILE = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "bearer_cache.json"
_LEGACY_GLOBAL_BEARER_CACHE_FILE = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "bearer_cache.json"
_SESSION_BEARER_CACHE_FILE = SESSION_FILE.with_name(f"bearer_cache_{SESSION_FILE.stem}.json")
_BEARER_CACHE_FILES = [
    Path(p)
    for p in (
        os.getenv("SPOTIFY_CHARTS_BEARER_CACHE_FILE", "").strip() or None,
        _SESSION_BEARER_CACHE_FILE if SESSION_FILE != _DEFAULT_SESSION_FILE else None,
        _WORLDWIDE_BEARER_CACHE_FILE,
        _LEGACY_GLOBAL_BEARER_CACHE_FILE,
    )
    if p
]
SINGLE_SESSION_TOKEN_POOL = os.getenv("SPOTIFY_CHARTS_SINGLE_SESSION", "").strip().lower() in {"1", "true", "yes", "on"}
_BEARER_TOKEN_TTL   = 50 * 60
OUTPUT_PATH     = WEB_EXPORT_DATA_DIR / "charts_worldwide.json"
HISTORY_ROOT    = ROOT / "snapshots" / "spotify_charts"
TOTAL_DAYS_PATH = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "total_days.json"
TWITTER_SESSION = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "twitter_session.json"
GLOBAL_NEW_RELEASES_SCRIPT = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "scripts" / "post_global_new_releases.py"

WEBSITE_SONGS_PATH = first_existing(WEB_EXPORT_DATA_DIR / "songs.json", LEGACY_WEBSITE_DATA_DIR / "songs.json")
DISCO_SONGS_PATH   = ROOT / "db" / "discography" / "songs.json"
DISCO_MISC_PATH     = ROOT / "db" / "discography" / "misc.json"
DISCO_FEATURES_PATH = ROOT / "db" / "discography" / "features.json"
DISCO_ALBUMS_DIR   = ROOT / "db" / "discography" / "albums"
MANUAL_MAP_PATH    = ROOT / "scripts" / "chart_title_to_track_id.json"

# ── Config ────────────────────────────────────────────────────────────────────
_API_BASE  = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
_UA        = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
TS_NAME         = "Taylor Swift"
SEMAPHORE       = int(os.getenv("SPOTIFY_WORLDWIDE_SEMAPHORE", "1"))
FETCH_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS", "0"))
RATE_LIMIT_MIN_SECONDS = int(os.getenv("SPOTIFY_WORLDWIDE_RATE_LIMIT_MIN_SECONDS", "20"))
# Cap le backoff multiplicatif de GlobalPause (x1, x2, x3...) qui n'avait pas de plafond:
# sous rate-limit soutenu (tous tokens 429 en boucle), la pause pouvait grimper sans fin
# (ex: 76 min de silence total observe en prod le 2026-08-17, sans aucune activite reseau/CPU
# ni log pendant toute la duree, indistinguable d'un vrai hang). On continue de retenter
# indefiniment (jamais sauter de la vraie donnee), mais chaque cycle de pause reste borne et
# reproduit un log a intervalle raisonnable au lieu de pouvoir depasser 1h en silence.
RATE_LIMIT_MAX_SECONDS = int(os.getenv("SPOTIFY_WORLDWIDE_RATE_LIMIT_MAX_SECONDS", "300"))
OVERVIEW_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_WORLDWIDE_OVERVIEW_MAX_ATTEMPTS", "5"))
PLAYWRIGHT_LAUNCH_TIMEOUT_MS = int(os.getenv("SPOTIFY_PLAYWRIGHT_LAUNCH_TIMEOUT_MS", "15000"))
PLAYWRIGHT_GOTO_TIMEOUT_MS = int(os.getenv("SPOTIFY_PLAYWRIGHT_GOTO_TIMEOUT_MS", "15000"))
PLAYWRIGHT_TOKEN_WAIT_SECONDS = int(os.getenv("SPOTIFY_PLAYWRIGHT_TOKEN_WAIT_SECONDS", "10"))
# Cas "chart pas encore propage" (URL datee 404, /latest 200 mais pointe encore sur la veille):
# toujours borne, meme en run quotidien live (FETCH_MAX_ATTEMPTS=0/illimite ne s'applique pas
# ici expres, pour eviter un hang si la region ne publie vraiment pas ce jour-la).
NOT_FOUND_RETRY_ATTEMPTS = int(os.getenv("SPOTIFY_WORLDWIDE_NOT_FOUND_RETRY_ATTEMPTS", "3"))
NOT_FOUND_RETRY_SECONDS = int(os.getenv("SPOTIFY_WORLDWIDE_NOT_FOUND_RETRY_SECONDS", "20"))
PRIORITY_CARD_POST_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_PRIORITY_CARD_POST_MAX_ATTEMPTS", "3"))
PRIORITY_CARD_POST_RETRY_SECONDS = int(os.getenv("SPOTIFY_PRIORITY_CARD_POST_RETRY_SECONDS", "30"))
REQUEST_INTERVAL_SECONDS = float(os.getenv("SPOTIFY_WORLDWIDE_REQUEST_INTERVAL_SECONDS", "2.0"))
SKIP_LATEST_FALLBACK_ON_404 = os.getenv("SPOTIFY_SKIP_LATEST_FALLBACK_ON_404", "").strip().lower() in {"1", "true", "yes", "on"}
IMMEDIATE_REENTRY_POST_MAX_ATTEMPTS = int(os.getenv("SPOTIFY_IMMEDIATE_REENTRY_POST_MAX_ATTEMPTS", "3"))
IMMEDIATE_REENTRY_POST_RETRY_SECONDS = int(os.getenv("SPOTIFY_IMMEDIATE_REENTRY_POST_RETRY_SECONDS", "30"))
_OVERVIEW_URL   = "https://charts-spotify-com-service.spotify.com/auth/v1/overview/GLOBAL"
MULTI_SONG_REGIONAL_POST_MIN_SONGS = 3
MULTI_SONG_REGIONAL_POST_MAX_POSTS = 1
MULTI_SONG_REGIONAL_POST_POOL_SIZE = 3
MULTI_SONG_REGIONAL_NO_REPEAT_DAYS = 1
MULTI_SONG_REGIONAL_WEEKLY_LOOKBACK_DAYS = 7
PRIORITY_POST_REGIONS = ("global", "fr", "us")
SCORED_REGIONAL_POST_EXCLUDED_REGIONS = set(PRIORITY_POST_REGIONS)

# Bonus/malus fixes evenements de chart, independants du rang exact.
# RE-ENTRY > NEW car un retour d'un titre du catalogue est un signal plus fort
# qu'une premiere entree ; un dropout coute plus cher qu'un simple recul de rang.
NEW_ENTRY_BASE = 40.0
NEW_ENTRY_RANK_FACTOR = 0.6
RE_ENTRY_BASE = 80.0
RE_ENTRY_RANK_FACTOR = 0.8
DROPOUT_BASE = 70.0
DROPOUT_RANK_FACTOR = 0.5

def _album_emoji(album: str) -> str:
    return _shared_album_emoji(album, fallback="📊")


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
    for extra_path in (DISCO_SONGS_PATH, DISCO_FEATURES_PATH, DISCO_MISC_PATH):
        if not extra_path.exists():
            continue
        data = _load_json(extra_path)
        if not isinstance(data, list):
            continue
        for section in data:
            if not isinstance(section, dict):
                continue
            album_name = section.get("album", "")
            for track in (section.get("tracks") or []):
                if isinstance(track, dict):
                    merged = {**track}
                    merged.setdefault("album", album_name)
                    yield merged
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


def build_historical_track_id_lookup() -> Dict[str, str]:
    cached = getattr(build_historical_track_id_lookup, "_cache", None)
    if cached is not None:
        return cached
    lookup: Dict[str, str] = {}
    for item in _iter_disco_tracks():
        kept_id = _get_track_id_from_item(item)
        if not kept_id:
            continue
        lookup.setdefault(kept_id, kept_id)
        historical_ids = item.get("historical_track_ids") or []
        if not isinstance(historical_ids, list):
            continue
        for historical_id in historical_ids:
            if isinstance(historical_id, str) and historical_id and historical_id != kept_id:
                lookup[historical_id] = kept_id
    build_historical_track_id_lookup._cache = lookup
    return lookup


def canonical_chart_track_id(track_id: Optional[str], historical_lookup: Dict[str, str]) -> Optional[str]:
    if not track_id:
        return None
    return historical_lookup.get(track_id, track_id)


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


def _exported_done_lock_path(chart_date: str) -> Path:
    return spotify_chart_dir("worldwide", chart_date) / "exported_done.lock"


def _mark_exported_done(chart_date: str) -> None:
    lock = _exported_done_lock_path(chart_date)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("exported_done=true\n", encoding="utf-8")
    print(f"[DONE] exported_done=true -> {lock}", flush=True)


def _load_cached_bearer() -> str | None:
    seen: set[Path] = set()
    for path in _BEARER_CACHE_FILES:
        if path in seen:
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if time.time() - float(data.get("ts", 0)) < _BEARER_TOKEN_TTL:
                token = str(data.get("token") or "").strip()
                if token:
                    print(f"[INFO] Bearer cache utilise: {path}", flush=True)
                    return token
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
                    timeout=PLAYWRIGHT_LAUNCH_TIMEOUT_MS,
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
                    wait_until="domcontentloaded",
                    timeout=PLAYWRIGHT_GOTO_TIMEOUT_MS,
                )
                deadline = time.time() + PLAYWRIGHT_TOKEN_WAIT_SECONDS
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

    # 1. API overview — avec rotation de tokens sur 429
    _overview_tokens = [token]
    if not SINGLE_SESSION_TOKEN_POOL:
        _overview_tokens.extend(
            t
            for sf in sorted(SESSION_FILE.parent.glob("spotify_session*.json"))
            if sf != SESSION_FILE
            for t in [_get_bearer_from_cookies(sf)]
            if t
        )
    _ov_idx = 0
    _ov_exhausted = 0

    def _overview_headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {_overview_tokens[_ov_idx]}",
            "Accept":        "application/json",
            "Referer":       "https://charts.spotify.com/",
            "User-Agent":    _UA,
        }

    _attempt = 0
    while True:
        _attempt += 1
        try:
            resp = _http.get(_OVERVIEW_URL, headers=_overview_headers(), timeout=15)
            if resp.status_code == 429:
                _ov_exhausted += 1
                if _ov_exhausted < len(_overview_tokens):
                    _ov_idx = (_ov_idx + 1) % len(_overview_tokens)
                    print(f"[WARN] Overview 429 — rotation token {_ov_idx + 1}/{len(_overview_tokens)}")
                    continue
                _ov_exhausted = 0
                wait = max(int(resp.headers.get("Retry-After", 20)), RATE_LIMIT_MIN_SECONDS)
                if _attempt >= OVERVIEW_MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Overview 429 apres {_attempt} tentative(s); Retry-After={wait}s"
                    )
                print(f"[WARN] Overview 429 tous tokens — retry dans {wait}s (tentative {_attempt})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except Exception as exc:
            if _attempt >= OVERVIEW_MAX_ATTEMPTS:
                raise RuntimeError(f"Overview indisponible apres {_attempt} tentative(s): {exc}") from exc
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


def _get_bearer_from_cookies(session_file: Path) -> str | None:
    """Get a bearer token from a session file via Playwright (WAF requires real browser TLS)."""
    api_host = _API_BASE.split("//")[1].split("/")[0]
    token_holder: list[str] = []

    def _on_request(req: Any, _th: list = token_holder) -> None:
        if api_host in req.url and not _th:
            auth = req.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                _th.append(auth[7:])

    try:
        p = sync_playwright().start()
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            timeout=PLAYWRIGHT_LAUNCH_TIMEOUT_MS,
        )
        ctx = browser.new_context(
            storage_state=str(session_file),
            user_agent=_UA,
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        page.on("request", _on_request)
        page.goto("https://charts.spotify.com/", wait_until="domcontentloaded", timeout=PLAYWRIGHT_GOTO_TIMEOUT_MS)
        deadline = time.time() + PLAYWRIGHT_TOKEN_WAIT_SECONDS
        while not token_holder and time.time() < deadline:
            page.wait_for_timeout(300)
        browser.close()
        p.stop()
        return token_holder[0] if token_holder else None
    except Exception as exc:
        print(f"[WARN] Token depuis {session_file.name}: {exc}")
        return None


class TokenPool:
    """Round-robin token pool. rotate() returns False when all tokens are exhausted."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._idx = 0
        self._exhausted = 0

    @property
    def current(self) -> str:
        return self._tokens[self._idx]

    def rotate(self) -> bool:
        self._exhausted += 1
        if self._exhausted >= len(self._tokens):
            return False
        self._idx = (self._idx + 1) % len(self._tokens)
        print(f"  [token ] rotation → token {self._idx + 1}/{len(self._tokens)}", flush=True)
        return True

    def reset(self) -> None:
        self._exhausted = 0


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
        previous_rank = _clean_int(ced.get("previousRank"))
        peak_rank = _clean_int(ced.get("peakRank"))
        appearances = _clean_int(ced.get("appearancesOnChart"))
        streak = _clean_int(ced.get("consecutiveAppearancesOnChart"))
        total_days = appearances if appearances is not None else streak
        is_new = previous_rank is None and (peak_rank is None or peak_rank == rank)
        is_re_entry = previous_rank is None and not is_new
        movement = "NEW" if is_new else ("RE" if is_re_entry else None)

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
            "previous_rank": previous_rank,
            "peak_rank":     peak_rank,
            "total_days":    total_days,
            "streak":        streak,
            "is_new":        is_new,
            "is_re_entry":   is_re_entry,
            "movement":      movement,
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


class GlobalPause:
    """
    States: open → paused → probing → taken → open | paused
    - On 429: try rotating the token pool first (immediate resume).
    - If all tokens exhausted: pause with multiplicative backoff (x1, x2, x3…).
    - After pause: one probe worker goes through; on success all resume.
    """

    def __init__(self, pool: TokenPool) -> None:
        self._pool = pool
        self._cond = asyncio.Condition()
        self._state = "open"
        self._resume_at: float = 0.0
        self._consecutive = 0

    async def wait(self) -> None:
        async with self._cond:
            while True:
                if self._state == "open":
                    return
                if self._state == "probing":
                    self._state = "taken"
                    return
                await self._cond.wait()

    async def trigger(self, seconds: int) -> None:
        async with self._cond:
            if self._state not in ("open", "taken"):
                return  # already handling
            if self._pool.rotate():
                self._state = "open"
                self._cond.notify_all()
                return
            # All tokens exhausted — real pause
            self._consecutive += 1
            effective = min(seconds * self._consecutive, RATE_LIMIT_MAX_SECONDS)
            loop = asyncio.get_running_loop()
            resume_at = loop.time() + effective
            if resume_at <= self._resume_at:
                return
            self._resume_at = resume_at
            self._state = "paused"
            print(f"  [pause ] tous tokens épuisés — pause {effective}s (x{self._consecutive})", flush=True)
            self._cond.notify_all()
        asyncio.create_task(self._resume(effective))

    async def _resume(self, seconds: int) -> None:
        await asyncio.sleep(seconds)
        async with self._cond:
            if self._state == "paused" and asyncio.get_running_loop().time() >= self._resume_at - 0.1:
                self._pool.reset()
                self._state = "probing"
                print("  [pause ] reprise — sonde en cours", flush=True)
                self._cond.notify(1)

    async def mark_success(self) -> None:
        async with self._cond:
            if self._state != "open":
                self._consecutive = 0
                self._pool.reset()
                self._state = "open"
                print("  [pause ] sonde OK — tous les workers reprennent", flush=True)
                self._cond.notify_all()


class RequestPacer:
    def __init__(self, interval_seconds: float) -> None:
        self._interval = max(0.0, float(interval_seconds or 0.0))
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if now < self._next_at:
                await asyncio.sleep(self._next_at - now)
                now = loop.time()
            self._next_at = now + self._interval


async def _fetch_region(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    pause: GlobalPause,
    pacer: RequestPacer,
    pool: TokenPool,
    region: str,
    chart_date: str,
    base_headers: dict[str, str],
) -> tuple[str, list[dict]]:
    chart_id = "regional-global-daily" if region == "global" else f"regional-{region}-daily"
    url = f"{_API_BASE}/{chart_id}/{chart_date}"
    latest_url = f"{_API_BASE}/{chart_id}/latest"
    attempt = 0
    not_found_attempts = 0
    while True:
        attempt += 1
        headers = {**base_headers, "Authorization": f"Bearer {pool.current}"}
        async with sem:
            await pause.wait()
            try:
                await pacer.wait()
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        rows = _parse_ts_entries(data)
                        await pause.mark_success()
                        print(f"  [{region:>6}] {len(rows)} TS entries ({chart_date})")
                        return region, rows
                    if resp.status == 404:
                        if SKIP_LATEST_FALLBACK_ON_404:
                            print(f"  [{region:>6}] 404 date - no chart")
                            return region, []
                        await pacer.wait()
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
                                    print(f"  [{region:>6}] {len(rows)} TS entries ({chart_date}, via latest)")
                                    return region, rows
                                if not_found_attempts < NOT_FOUND_RETRY_ATTEMPTS:
                                    not_found_attempts += 1
                                    print(
                                        f"  [{region:>6}] 404 date, latest={latest_date or 'unknown'} "
                                        f"- pas encore propage, retry dans {NOT_FOUND_RETRY_SECONDS}s "
                                        f"(tentative {not_found_attempts}/{NOT_FOUND_RETRY_ATTEMPTS})"
                                    )
                                    await asyncio.sleep(NOT_FOUND_RETRY_SECONDS)
                                    continue
                                print(
                                    f"  [{region:>6}] 404 date, latest={latest_date or 'unknown'} "
                                    f"- no chart for {chart_date} (apres {not_found_attempts} retry)"
                                )
                                return region, []
                            if latest_resp.status == 404:
                                print(f"  [{region:>6}] 404 date+latest - no chart")
                                return region, []
                            raise RuntimeError(
                                f"{region}: dated chart 404 and latest HTTP {latest_resp.status}"
                            )
                    if resp.status == 429:
                        if FETCH_MAX_ATTEMPTS > 0 and attempt >= FETCH_MAX_ATTEMPTS:
                            print(f"  [{region:>6}] SKIP — 429 after {attempt} attempts, giving up on this region for {chart_date}")
                            return region, None
                        wait = max(int(resp.headers.get("Retry-After", 20)), RATE_LIMIT_MIN_SECONDS)
                        print(f"  [{region:>6}] 429 — pause globale {wait}s (tentative {attempt})")
                        await pause.trigger(wait)
                        continue
                    if resp.status == 401:
                        raise TokenExpired(f"{region}: HTTP 401")
                    if 400 <= resp.status < 500:
                        print(f"  [{region:>6}] SKIP — HTTP {resp.status}, giving up on this region for {chart_date}")
                        return region, None
                    print(f"  [{region:>6}] HTTP {resp.status} — retry dans 10s (tentative {attempt})")
            except asyncio.TimeoutError:
                if FETCH_MAX_ATTEMPTS > 0 and attempt >= FETCH_MAX_ATTEMPTS:
                    print(f"  [{region:>6}] SKIP — timeout after {attempt} attempts, giving up on this region for {chart_date}")
                    return region, None
                print(f"  [{region:>6}] timeout — retry dans 10s (tentative {attempt})")
            except Exception as exc:
                if isinstance(exc, TokenExpired):
                    raise
                if FETCH_MAX_ATTEMPTS > 0 and attempt >= FETCH_MAX_ATTEMPTS:
                    print(f"  [{region:>6}] SKIP — {exc!r} after {attempt} attempts, giving up on this region for {chart_date}")
                    return region, None
                print(f"  [{region:>6}] erreur ({exc}) — retry dans 10s (tentative {attempt})")
        await asyncio.sleep(min(10 * attempt, 60))


async def _run_async(
    chart_date: str,
    tokens: list[str],
    regions: dict[str, str],
    *,
    immediate_reentry_ctx: dict | None = None,
    csv_sync_ctx: dict | None = None,
) -> dict[str, list[dict] | None]:
    base_headers = {
        "Accept":     "application/json",
        "Referer":    "https://charts.spotify.com/",
        "User-Agent": _UA,
    }
    pool = TokenPool(tokens)
    sem = asyncio.Semaphore(SEMAPHORE)
    pause = GlobalPause(pool)
    pacer = RequestPacer(REQUEST_INTERVAL_SECONDS)
    pacing_label = f", pacing={REQUEST_INTERVAL_SECONDS:.2f}s" if REQUEST_INTERVAL_SECONDS > 0 else ""
    print(f"[INFO] Concurrence fixe: {SEMAPHORE} workers, {len(tokens)} token(s){pacing_label}", flush=True)
    async with aiohttp.ClientSession() as session:
        async def _fetch_and_notify(region: str) -> tuple[str, list[dict] | None]:
            region_out, rows = await _fetch_region(session, sem, pause, pacer, pool, region, chart_date, base_headers)
            if rows and csv_sync_ctx is not None:
                try:
                    _sync_region_csv_immediately(
                        chart_date,
                        region_out,
                        rows,
                        csv_sync_ctx["manual_lookup"],
                        csv_sync_ctx["track_lookup"],
                        csv_sync_ctx["historical_lookup"],
                    )
                except Exception as exc:
                    print(f"[WARN] Immediate CSV sync failed for {region_out}: {exc}", flush=True)
            if rows and immediate_reentry_ctx is not None:
                try:
                    _maybe_trigger_immediate_reentries(
                        chart_date,
                        region_out,
                        regions.get(region_out, region_out),
                        rows,
                        immediate_reentry_ctx["manual_lookup"],
                        immediate_reentry_ctx["track_lookup"],
                        immediate_reentry_ctx["historical_lookup"],
                        immediate_reentry_ctx["prev_country_counts"],
                        immediate_reentry_ctx["has_prev_snapshot"],
                        immediate_reentry_ctx["threads"],
                    )
                except Exception as exc:
                    print(f"[WARN] Immediate re-entry check failed for {region_out}: {exc}", flush=True)
            return region_out, rows

        tasks = [_fetch_and_notify(region) for region in regions]
        results = await asyncio.gather(*tasks)
    return dict(results)


def _run_regions_sync(
    chart_date: str,
    tokens: list[str],
    regions: dict[str, str],
    *,
    immediate_reentry_ctx: dict | None = None,
    csv_sync_ctx: dict | None = None,
) -> dict[str, list[dict]]:
    def _make_coro():
        return _run_async(
            chart_date, tokens, regions,
            immediate_reentry_ctx=immediate_reentry_ctx,
            csv_sync_ctx=csv_sync_ctx,
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_make_coro())

    result_holder: list[dict[str, list[dict]]] = []
    error_holder: list[BaseException] = []

    def _runner() -> None:
        try:
            result_holder.append(asyncio.run(_make_coro()))
        except BaseException as exc:
            error_holder.append(exc)

    worker = threading.Thread(target=_runner, name="spotify-worldwide-async", daemon=True)
    worker.start()
    worker.join()
    if error_holder:
        raise error_holder[0]
    return result_holder[0]


def _run_async_with_token_refresh(
    chart_date: str,
    tokens: list[str],
    regions: dict[str, str],
    *,
    immediate_reentry_ctx: dict | None = None,
    csv_sync_ctx: dict | None = None,
) -> tuple[list[str], dict[str, str], dict[str, list[dict]]]:
    try:
        return tokens, regions, _run_regions_sync(
            chart_date, tokens, regions,
            immediate_reentry_ctx=immediate_reentry_ctx,
            csv_sync_ctx=csv_sync_ctx,
        )
    except TokenExpired as exc:
        print(f"[WARN] Bearer token refuse par Spotify ({exc}); refresh et retry date {chart_date}.", flush=True)
        new_token, _all_regions = _get_bearer_token_and_regions(force_refresh=True)
        tokens = [new_token] + tokens[1:]
        return tokens, regions, _run_regions_sync(
            chart_date, tokens, regions,
            immediate_reentry_ctx=immediate_reentry_ctx,
            csv_sync_ctx=csv_sync_ctx,
        )


# ── Entry point ────────────────────────────────────────────────────────────────

def _fmt_streams(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "N/A"


def _to_int(value) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _multi_song_region_lock_path(chart_date: str, region: str) -> Path:
    return spotify_chart_dir("worldwide", chart_date) / "regional_posts" / f"posted_{region}.lock"


def _multi_song_region_chart_image_path(chart_date: str, region: str) -> Path:
    return spotify_chart_dir(region, chart_date) / "chart_image.png"


def _load_snapshot_by_track(chart_date: str) -> dict[str, list[dict]]:
    path = _worldwide_history_path(chart_date)
    if not path.exists():
        path = legacy_spotify_chart_dir("worldwide", chart_date) / f"ts_worldwide_{chart_date}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(f"[WARN] Could not load worldwide snapshot ({chart_date}): {exc}", flush=True)
        return {}
    by_track = data.get("by_track") if isinstance(data, dict) else None
    return by_track if isinstance(by_track, dict) else {}


def _snapshot_entry_for_region(snapshot: dict[str, list[dict]], track_id: str | None, region: str) -> dict | None:
    if not track_id:
        return None
    for entry in snapshot.get(str(track_id), []):
        if isinstance(entry, dict) and entry.get("country") == region:
            return entry
    return None


def _apply_track_id_history(chart_date: str, by_region: dict[str, list[dict]]) -> None:
    """Use stable Spotify track IDs to correct title-change false NEW/RE states."""
    current_date = datetime.strptime(chart_date, "%Y-%m-%d").date()
    prev_day = (current_date - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_day_by_track = _load_snapshot_by_track(prev_day)
    if not prev_day_by_track:
        return

    corrected = 0
    for region, rows in by_region.items():
        for row in rows:
            track_id = row.get("_track_id_uri") or row.get("track_id")
            prev_entry = _snapshot_entry_for_region(prev_day_by_track, track_id, region)
            if not prev_entry:
                continue

            prev_rank = _to_int(prev_entry.get("rank"))
            if row.get("previous_rank") in (None, "") and prev_rank is not None:
                row["previous_rank"] = prev_rank
                corrected += 1

            if row.get("is_new") or row.get("is_re_entry") or row.get("movement") in {"NEW", "RE"}:
                row["is_new"] = False
                row["is_re_entry"] = False
                row["movement"] = None
                corrected += 1

            prev_total_days = _to_int(prev_entry.get("total_days"))
            total_days = _to_int(row.get("total_days"))
            if prev_total_days is not None and (total_days is None or total_days <= 1):
                row["total_days"] = prev_total_days + 1
                corrected += 1

            prev_peak = _to_int(prev_entry.get("peak_rank"))
            rank = _to_int(row.get("rank"))
            peak_rank = _to_int(row.get("peak_rank"))
            if rank is not None and prev_peak is not None and (peak_rank is None or peak_rank > min(prev_peak, rank)):
                row["peak_rank"] = min(prev_peak, rank)
                corrected += 1

    if corrected:
        print(f"[INFO] Track-ID history corrections applied: {corrected}", flush=True)


def _enrich_multi_song_region_rows(chart_date: str, by_region: dict[str, list[dict]]) -> None:
    current_date = datetime.strptime(chart_date, "%Y-%m-%d").date()
    prev_day = (current_date - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_week = (current_date - timedelta(days=MULTI_SONG_REGIONAL_WEEKLY_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    prev_day_by_track = _load_snapshot_by_track(prev_day)
    prev_week_by_track = _load_snapshot_by_track(prev_week)

    for region, rows in by_region.items():
        for row in rows:
            track_id = row.get("_track_id_uri") or row.get("track_id")
            streams = _to_int(row.get("streams"))
            prev_day_entry = _snapshot_entry_for_region(prev_day_by_track, track_id, region)
            prev_week_entry = _snapshot_entry_for_region(prev_week_by_track, track_id, region)

            if row.get("stream_change") in (None, "") and prev_day_entry and streams is not None:
                previous_streams = _to_int(prev_day_entry.get("streams"))
                if previous_streams and previous_streams > 0:
                    stream_change = streams - previous_streams
                    row["stream_change"] = stream_change
                    row["stream_change_pct"] = round(stream_change / previous_streams * 100, 2)

            if prev_week_entry and streams is not None:
                previous_week_streams = _to_int(prev_week_entry.get("streams"))
                if previous_week_streams and previous_week_streams > 0:
                    weekly_stream_change = streams - previous_week_streams
                    row["weekly_stream_change"] = weekly_stream_change
                    row["weekly_stream_change_pct"] = round(weekly_stream_change / previous_week_streams * 100, 2)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _rank_weight(rank: int) -> float:
    """Poids d'une position: bouger en haut du chart vaut plus qu'en bas (#1 ≈ x2, #200 = x1)."""
    return 1.0 + (200 - _clamp(rank, 1, 200)) / 200.0


def _region_score(region: str, rows: list[dict], prev_day_by_track: dict[str, list[dict]]) -> tuple[float, dict]:
    """Score signe d'une region: les hausses rapportent des points, les baisses en coutent.

    Par chanson:
    - mouvement de rang (previous_rank - rank), pondere par la position
    - NEW: +NEW_ENTRY_BASE + (201 - rank) * NEW_ENTRY_RANK_FACTOR
    - RE-ENTRY (plus fort que NEW, retour d'un titre du catalogue):
      +RE_ENTRY_BASE + (201 - rank) * RE_ENTRY_RANK_FACTOR
    - bonus nouveau peak (+15 pondere position)
    - variation de streams jour (%) bornee a +/-50, poids 1.2 ; semaine (%), poids 0.5
    Par region:
    - malus pour chaque chanson sortie du chart depuis hier (evenement fort,
      pas juste un recul de rang): -DROPOUT_BASE - (201 - prev_rank) * DROPOUT_RANK_FACTOR
    """
    score = 0.0
    moves_up = moves_down = entries_in = 0
    charting_ids: set[str] = set()
    for row in rows:
        track_id = str(row.get("_track_id_uri") or row.get("track_id") or "")
        if track_id:
            charting_ids.add(track_id)
        rank = _to_int(row.get("rank"))
        if rank is None:
            continue
        prev_rank = _to_int(row.get("previous_rank"))

        if row.get("is_new"):
            score += NEW_ENTRY_BASE + (201 - _clamp(rank, 1, 200)) * NEW_ENTRY_RANK_FACTOR
            entries_in += 1
        elif row.get("is_re_entry"):
            score += RE_ENTRY_BASE + (201 - _clamp(rank, 1, 200)) * RE_ENTRY_RANK_FACTOR
            entries_in += 1
        elif prev_rank is not None and prev_rank > 0:
            delta = prev_rank - rank
            score += delta * _rank_weight(min(rank, prev_rank))
            if delta > 0:
                moves_up += 1
                peak_rank = _to_int(row.get("peak_rank"))
                if peak_rank is not None and rank <= peak_rank:
                    score += 15 * _rank_weight(rank)
            elif delta < 0:
                moves_down += 1

        pct = row.get("stream_change_pct")
        if isinstance(pct, (int, float)):
            score += _clamp(float(pct), -50.0, 50.0) * 1.2
        weekly_pct = row.get("weekly_stream_change_pct")
        if isinstance(weekly_pct, (int, float)):
            score += _clamp(float(weekly_pct), -50.0, 50.0) * 0.5

    dropouts = 0
    for track_id, entries in prev_day_by_track.items():
        if str(track_id) in charting_ids:
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("country") == region:
                prev_rank = _to_int(entry.get("rank"))
                if prev_rank is not None:
                    score -= DROPOUT_BASE + (201 - _clamp(prev_rank, 1, 200)) * DROPOUT_RANK_FACTOR
                    dropouts += 1
                break

    detail = {"up": moves_up, "down": moves_down, "in": entries_in, "out": dropouts, "songs": len(rows)}
    return round(score, 1), detail


def _regions_posted_on(chart_date: str) -> set[str]:
    posted: set[str] = set()
    for base in (spotify_chart_dir("worldwide", chart_date), legacy_spotify_chart_dir("worldwide", chart_date)):
        for lock in (base / "regional_posts").glob("posted_*.lock"):
            posted.add(lock.stem[len("posted_"):])
    return posted


def _regions_posted_recently(chart_date: str, days: int = MULTI_SONG_REGIONAL_NO_REPEAT_DAYS) -> set[str]:
    current = datetime.strptime(chart_date, "%Y-%m-%d").date()
    posted: set[str] = set()
    for offset in range(1, days + 1):
        posted |= _regions_posted_on((current - timedelta(days=offset)).strftime("%Y-%m-%d"))
    return posted


def _build_multi_song_region_tweet(chart_date: str, region: str, region_name: str, rows: list[dict]) -> str:
    date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%A, %B %d, %Y")
    count = len(rows)
    lines = [with_prefix(f"Taylor Swift on Spotify {region_name} Charts on {date_fmt} :", SPOTIFY_CHART_PREFIX)]
    if count >= 2:
        lines.extend([
            "",
            f"{count} songs charting in {region_name}.",
        ])
    lines.extend([
        "",
        full_charts_update_line(region=region),
    ])
    return "\n".join(lines)


def _generate_multi_song_region_image(chart_date: str, region: str, region_name: str) -> Path | None:
    if not GLOBAL_CHART_IMAGE_SCRIPT.exists():
        print(f"[WARN] Regional image skipped for {region}: script missing: {GLOBAL_CHART_IMAGE_SCRIPT}", flush=True)
        return None
    result = subprocess.run(
        [
            sys.executable,
            str(GLOBAL_CHART_IMAGE_SCRIPT),
            chart_date,
            "--region",
            region,
            "--region-name",
            region_name,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, flush=True)
    if result.returncode != 0:
        print(f"[WARN] Regional image generation failed for {region} (code {result.returncode})", flush=True)
        return None
    image_path = _multi_song_region_chart_image_path(chart_date, region)
    if not image_path.exists():
        print(f"[WARN] Regional image missing after generation for {region}: {image_path}", flush=True)
        return None
    return image_path


def _post_multi_song_regions(
    chart_date: str,
    regions: dict[str, str],
    by_region: dict[str, list[dict]],
    *,
    force: bool = False,
) -> None:
    if not TWITTER_SESSION.exists():
        print(f"[WARN] Multi-song regional posts skipped: Twitter session missing: {TWITTER_SESSION}", flush=True)
        return

    already_posted_today = _regions_posted_on(chart_date)
    if already_posted_today and not force:
        print(
            f"[SKIP] Regional scored post already done for {chart_date}: "
            f"{', '.join(sorted(already_posted_today))}",
            flush=True,
        )
        return

    prev_day = (datetime.strptime(chart_date, "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_day_by_track = _load_snapshot_by_track(prev_day)

    scored: list[tuple[str, list[dict], float, dict]] = []
    for region, rows in by_region.items():
        if region in SCORED_REGIONAL_POST_EXCLUDED_REGIONS or len(rows) < MULTI_SONG_REGIONAL_POST_MIN_SONGS:
            continue
        score, detail = _region_score(region, rows, prev_day_by_track)
        scored.append((region, rows, score, detail))
    scored.sort(key=lambda item: item[2], reverse=True)
    if scored:
        print(
            "[INFO] Scores regionaux: " + ", ".join(
                f"{region}={score:+.1f} (up:{d['up']} down:{d['down']} in:{d['in']} out:{d['out']})"
                for region, _, score, d in scored[:10]
            ),
            flush=True,
        )

    positive = [c for c in scored if c[2] > 0]
    recent = _regions_posted_recently(chart_date)
    eligible = [c for c in positive if c[0] not in recent]
    excluded = [c[0] for c in positive if c[0] in recent]
    if excluded:
        print(f"[INFO] Regions exclues (postees la veille): {', '.join(excluded)}", flush=True)

    pool = eligible[:MULTI_SONG_REGIONAL_POST_POOL_SIZE]
    if not pool:
        print("[INFO] No multi-song regional Spotify posts needed.", flush=True)
        return

    total_weight = sum(c[2] for c in pool)
    print(
        f"[INFO] Tirage pondere par score parmi top {len(pool)}: "
        + ", ".join(f"{c[0]}={c[2] / total_weight * 100:.0f}%" for c in pool),
        flush=True,
    )
    candidates: list[tuple[str, list[dict], float, dict]] = []
    pool_left = list(pool)
    for _ in range(min(MULTI_SONG_REGIONAL_POST_MAX_POSTS, len(pool_left))):
        pick = random.choices(pool_left, weights=[c[2] for c in pool_left], k=1)[0]
        pool_left.remove(pick)
        candidates.append(pick)
    print(f"[INFO] Region(s) choisie(s): {', '.join(c[0] for c in candidates)}", flush=True)

    for region, rows, _score, _detail in candidates:
        lock_path = _multi_song_region_lock_path(chart_date, region)
        if lock_path.exists() and not force:
            print(f"[SKIP] regional post {region} already done for {chart_date}", flush=True)
            continue
        region_name = regions.get(region, region.upper())
        tweet = _build_multi_song_region_tweet(chart_date, region, region_name, rows)
        image_path = _generate_multi_song_region_image(chart_date, region, region_name)
        if not image_path:
            print(f"[WARN] Regional Spotify post skipped for {region}: image unavailable", flush=True)
            continue
        print(f"[regional-post] {region}: {tweet}", flush=True)
        if post_with_image(tweet, image_path, TWITTER_SESSION, skip_if=lambda lp=lock_path: lp.exists() and not force):
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.touch()
            print(f"[INFO] Posted regional Spotify update: {region}", flush=True)
        else:
            print(f"[WARN] Regional Spotify post failed: {region}", flush=True)


# ── Immediate re-entry posting (per-country, during collection) ────────────────
#
# Un titre absent de TOUS les pays la veille qui reapparait aujourd'hui dans une
# region est une vraie "re-entree dans les Spotify Charts" (pas juste un aller-
# retour dans le classement d'un seul pays). Poste des que cette region est
# collectee, sans attendre la fin de la collecte worldwide ni l'etape "cards"
# separee de run_all_charts.py. Exclut "global" (deja gere par
# _post_priority_global_new_card). Choix produit assume: un titre qui re-entre
# dans plusieurs pays le meme jour poste un tweet par pays (voir SKILL
# spotify-charts). Le NEW single-region (premier debut jamais charte) reste
# gere par generate_card_images.py en Phase 3 — non couvert ici.
_immediate_reentry_lock = threading.Lock()


def _immediate_reentry_slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    return text[:80] or "track"


def _load_prev_country_counts(chart_date: str) -> tuple[dict[str, int], bool]:
    prev_date = (datetime.strptime(chart_date, "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_path = _worldwide_history_path(prev_date)
    if not prev_path.exists():
        prev_path = legacy_spotify_chart_dir("worldwide", prev_date) / f"ts_worldwide_{prev_date}.json"
    if not prev_path.exists():
        return {}, False
    try:
        prev_data = json.loads(prev_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}, False
    prev_by_track = prev_data.get("by_track", {})
    if not isinstance(prev_by_track, dict):
        return {}, False
    counts = {tid: len(entries) for tid, entries in prev_by_track.items() if isinstance(entries, list)}
    return counts, True


def _build_song_meta() -> dict[str, dict]:
    cached = getattr(_build_song_meta, "_cache", None)
    if cached is not None:
        return cached
    meta: dict[str, dict] = {}
    for item in _iter_website_songs():
        tid = _get_track_id_from_item(item)
        if tid:
            meta.setdefault(tid, item)
    _build_song_meta._cache = meta
    return meta


def _cover_url_from_meta(song: dict) -> str:
    for key in ("image_url", "apple_music_image_url", "cover_url", "album_image_url"):
        value = str(song.get(key) or "").strip()
        if value.startswith("http"):
            return value
    return ""


def _immediate_reentry_posted_path(chart_date: str) -> Path:
    # Meme fichier/cle que generate_card_images.py (first_single_region_posted.json,
    # cle "{slug}_chart_card") : la detection RE immediate ici et la card standalone
    # "first single region entry" de Phase 3 partagent le meme verrou pour ne jamais
    # poster deux fois la meme chanson (Phase 3 saute le post si deja present).
    return spotify_chart_dir("worldwide", chart_date) / "cards" / "first_single_region_posted.json"


def _immediate_reentry_already_posted(chart_date: str, lock_key: str) -> bool:
    posted_path = _immediate_reentry_posted_path(chart_date)
    if not posted_path.exists():
        return False
    try:
        data = json.loads(posted_path.read_text(encoding="utf-8"))
        return lock_key in set(data.get("posted", []))
    except Exception:
        return False


def _mark_immediate_reentry_posted(chart_date: str, lock_key: str) -> None:
    posted_path = _immediate_reentry_posted_path(chart_date)
    with _immediate_reentry_lock:
        already: set[str] = set()
        if posted_path.exists():
            try:
                data = json.loads(posted_path.read_text(encoding="utf-8"))
                already = set(data.get("posted", []))
            except Exception:
                pass
        already.add(lock_key)
        posted_path.parent.mkdir(parents=True, exist_ok=True)
        posted_path.write_text(
            json.dumps({"date": chart_date, "posted": sorted(already)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _immediate_entry_is_re(row: dict) -> bool:
    return str(row.get("movement") or "").strip().upper() == "RE" or bool(row.get("is_re_entry"))


def _build_immediate_reentry_tweet(title: str, album: str, region_name: str, row: dict, chart_date: str) -> str:
    try:
        date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%A, %B %d, %Y")
    except Exception:
        date_fmt = chart_date
    emoji = _shared_album_emoji(album, fallback="📊")
    rank = row.get("rank", "?")
    streams = _fmt_streams(row.get("streams"))
    overall_url = full_charts_update_line(region="overall", label="🔗 See full update here")
    verb = "re-entered the Spotify Charts" if _immediate_entry_is_re(row) else "charted on Spotify"
    return (
        f'{emoji} | "{title}" {verb} in {region_name} at #{rank} '
        f"with {streams} streams on {date_fmt}.\n\n{overall_url}"
    )


def _post_immediate_reentry_card(
    chart_date: str,
    region: str,
    region_name: str,
    track_id: str,
    title: str,
    row: dict,
) -> None:
    slug = _immediate_reentry_slugify(title)
    lock_key = f"{slug}_chart_card"
    if _immediate_reentry_already_posted(chart_date, lock_key):
        return

    song_meta = _build_song_meta().get(track_id, {})
    album = str(song_meta.get("primary_album") or song_meta.get("album") or "").strip()
    cover_url = _cover_url_from_meta(song_meta)
    badge_text, badge_class = ("RE", "re") if _immediate_entry_is_re(row) else ("NEW", "new")

    try:
        dt = datetime.strptime(chart_date, "%Y-%m-%d")
        day = dt.day
        suffix = "TH" if 10 <= day % 100 <= 20 else {1: "ST", 2: "ND", 3: "RD"}.get(day % 10, "TH")
        date_pill = f"Spotify {region_name} Charts - {dt.strftime('%B').upper()} {day}{suffix} {dt.year}"
        footer_date = dt.strftime("%B %d, %Y")
    except Exception:
        date_pill = f"Spotify {region_name} Charts - {chart_date}"
        footer_date = chart_date

    html_content = render_chart_card(
        title=title,
        eyebrow=f"Spotify {region_name} Charts",
        subtitle=album or region_name,
        stats=[
            {
                "label": "Rank",
                "value": f"#{row.get('rank')}" if row.get("rank") is not None else "#-",
                "badge": badge_text,
                "badge_class": badge_class,
            },
            {
                "label": "Streams",
                "value": f"{int(row['streams']):,}" if row.get("streams") is not None else "-",
                "badge": "",
                "badge_class": "flat",
                "delta": "",
                "delta_class": "flat",
            },
        ],
        cover_url=cover_url,
        footer_left="@swiftiescharts",
        footer_right=footer_date,
        extra=region_name,
        badge_text=date_pill,
    )

    out_dir = spotify_chart_dir("worldwide", chart_date) / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slug}_chart_card.png"
    tmp_path = out_dir / f"{slug}_chart_card.html"
    try:
        write_chart_card_png(html_content, out_path, tmp_path, width=920, height=344, export_frame=True)
    except Exception as exc:
        print(f"[WARN] Immediate {badge_text} card render failed for {title!r} ({region}): {exc}", flush=True)
        return

    tweet_text = _build_immediate_reentry_tweet(title, album, region_name, row, chart_date)
    print(f"[INFO] Immediate {badge_text} entry detected: {title!r} in {region_name} — posting standalone...", flush=True)
    for attempt in range(1, IMMEDIATE_REENTRY_POST_MAX_ATTEMPTS + 1):
        if post_with_image(
            tweet_text,
            out_path,
            TWITTER_SESSION,
            skip_if=lambda: _immediate_reentry_already_posted(chart_date, lock_key),
        ):
            _mark_immediate_reentry_posted(chart_date, lock_key)
            print(f"[INFO] Posted immediate {badge_text} card: {title!r} ({region})", flush=True)
            return
        print(
            f"[WARN] Immediate {badge_text} post failed for {title!r} ({region}), "
            f"tentative {attempt}/{IMMEDIATE_REENTRY_POST_MAX_ATTEMPTS}",
            flush=True,
        )
        if attempt < IMMEDIATE_REENTRY_POST_MAX_ATTEMPTS:
            time.sleep(IMMEDIATE_REENTRY_POST_RETRY_SECONDS)
    print(f"[WARN] Immediate {badge_text} post abandoned for {title!r} ({region})", flush=True)


def _maybe_trigger_immediate_reentries(
    chart_date: str,
    region: str,
    region_name: str,
    rows: list[dict],
    manual_lookup: dict[str, str],
    track_lookup: dict[str, str],
    historical_lookup: dict[str, str],
    prev_country_counts: dict[str, int],
    has_prev_snapshot: bool,
    threads: list[threading.Thread],
) -> None:
    if region == "global" or not has_prev_snapshot or not rows:
        return
    for row in rows:
        movement = str(row.get("movement") or "").strip().upper()
        if movement not in ("RE", "NEW") and not row.get("is_re_entry") and not row.get("is_new"):
            continue
        track_id = row.get("_track_id_uri") or resolve_track_id(row.get("track_name", ""), manual_lookup, track_lookup)
        track_id = canonical_chart_track_id(track_id, historical_lookup)
        if not track_id:
            continue
        if int(prev_country_counts.get(track_id, 0) or 0) != 0:
            continue
        title = row.get("track_name") or track_id
        thread = threading.Thread(
            target=_post_immediate_reentry_card,
            args=(chart_date, region, region_name, track_id, title, dict(row)),
            daemon=True,
            name=f"immediate-reentry-{region}",
        )
        threads.append(thread)
        thread.start()


# ── Incremental per-country CSV sync (during collection) ───────────────────────
#
# db/charts_history_<region>.csv est normalement mis a jour par
# scripts/sync_spotify_country_charts_from_worldwide.py, appele une seule fois en
# fin de run_all_charts.py une fois TOUTE la collecte worldwide terminee (ce
# script reste tel quel, en filet de secours idempotent — utile en backfill/
# catchup ou si une region a ete synced ici avec un echec). Ici on ecrit la
# meme ligne CSV des qu'une region vient d'etre collectee, pour ne pas attendre
# la fin de la collecte des ~75 autres regions avant que cette region soit
# visible dans son historique.
_CSV_HISTORY_FIELDNAMES = [
    "date", "track_id", "song_name", "rank", "streams",
    "previous_rank", "peak_rank", "total_days", "streak", "movement",
]
_csv_sync_lock = threading.Lock()


def _csv_history_row(chart_date: str, track_id: str, song_name: str, row: dict) -> dict[str, str]:
    previous_rank = _to_int(row.get("previous_rank"))
    return {
        "date": chart_date,
        "track_id": track_id,
        "song_name": song_name,
        "rank": str(_to_int(row.get("rank")) or ""),
        "streams": str(_to_int(row.get("streams")) or 0),
        "previous_rank": str(previous_rank) if previous_rank else "",
        "peak_rank": str(_to_int(row.get("peak_rank")) or ""),
        "total_days": str(_to_int(row.get("total_days")) or ""),
        "streak": str(_to_int(row.get("streak")) or ""),
        "movement": str(row.get("movement") or ""),
    }


def _sync_region_csv_immediately(
    chart_date: str,
    region: str,
    rows: list[dict],
    manual_lookup: dict[str, str],
    track_lookup: dict[str, str],
    historical_lookup: dict[str, str],
) -> None:
    if not rows:
        return
    # Meme correction que la voie batch (_apply_track_id_history sur by_region
    # complet, appliquee plus tard dans main()) mais sur cette seule region tout
    # de suite : la fonction est deja par-ligne/par-region, donc l'appliquer ici
    # en avance donne le meme resultat, et evite d'ecrire en CSV un faux NEW/RE
    # (changement de titre) que la correction batch aurait sinon supprime.
    # Idempotente : la reappliquer plus tard sur les memes lignes ne fait rien.
    _apply_track_id_history(chart_date, {region: rows})
    id_to_name = _build_id_to_name()
    csv_rows: list[dict[str, str]] = []
    for row in rows:
        track_id = row.get("_track_id_uri") or resolve_track_id(row.get("track_name", ""), manual_lookup, track_lookup)
        track_id = canonical_chart_track_id(track_id, historical_lookup)
        if not track_id:
            continue
        song_name = id_to_name.get(track_id) or row.get("track_name") or track_id
        csv_rows.append(_csv_history_row(chart_date, track_id, song_name, row))
    if not csv_rows:
        return

    # sync_spotify_country_charts_from_worldwide.py normalise "gb" (code Spotify)
    # vers "uk" pour le nom de fichier CSV (BASE_COUNTRIES["uk"] = ("gb", "uk")) ;
    # sans ce mapping on ecrirait un charts_history_gb.csv distinct et jamais lu.
    csv_region = "uk" if region == "gb" else region
    csv_path = ROOT / "db" / f"charts_history_{csv_region}.csv"
    added = 0
    with _csv_sync_lock:
        existing_keys: set[tuple[str, str]] = set()
        if csv_path.exists():
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for existing in csv.DictReader(handle):
                    existing_keys.add((existing.get("date", ""), existing.get("track_id") or existing.get("song_name", "")))
        new_rows = [r for r in csv_rows if (r["date"], r["track_id"]) not in existing_keys]
        if new_rows:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            needs_newline = csv_path.exists() and csv_path.stat().st_size > 0
            if needs_newline:
                needs_newline = not csv_path.read_bytes().endswith((b"\n", b"\r"))
            with csv_path.open("a", encoding="utf-8", newline="") as handle:
                if needs_newline:
                    handle.write("\n")
                writer = csv.DictWriter(handle, fieldnames=_CSV_HISTORY_FIELDNAMES)
                if csv_path.stat().st_size == 0:
                    writer.writeheader()
                writer.writerows(new_rows)
            added = len(new_rows)
    if added:
        print(f"[INFO] Synced {added} row(s) → db/charts_history_{csv_region}.csv", flush=True)


def _build_id_to_name() -> dict[str, str]:
    cached = getattr(_build_id_to_name, "_cache", None)
    if cached is not None:
        return cached
    id_to_name: dict[str, str] = {}
    for item in _iter_website_songs():
        tid = _get_track_id_from_item(item)
        if not tid:
            continue
        name = (item.get("title") or item.get("name") or "").strip()
        if name:
            id_to_name.setdefault(tid, name)
    for item in _iter_disco_tracks():
        tid = _get_track_id_from_item(item)
        if not tid:
            continue
        name = (item.get("title") or item.get("name") or "").strip()
        if name:
            id_to_name.setdefault(tid, name)
    _build_id_to_name._cache = id_to_name
    return id_to_name


def _build_id_to_album() -> dict[str, str]:
    cached = getattr(_build_id_to_album, "_cache", None)
    if cached is not None:
        return cached
    id_to_album: dict[str, str] = {}
    for item in _iter_website_songs():
        tid = _get_track_id_from_item(item)
        if tid:
            album = (item.get("album") or "").strip()
            if album:
                id_to_album.setdefault(tid, album)
    for item in _iter_disco_tracks():
        tid = _get_track_id_from_item(item)
        if tid:
            album = (item.get("album") or "").strip()
            if album:
                id_to_album.setdefault(tid, album)
    _build_id_to_album._cache = id_to_album
    return id_to_album


def _load_snapshot_by_region(chart_date: str) -> tuple[dict[str, list[dict]], dict[str, str]]:
    path = _worldwide_history_path(chart_date)
    if not path.exists():
        path = legacy_spotify_chart_dir("worldwide", chart_date) / f"ts_worldwide_{chart_date}.json"
    if not path.exists():
        print(f"[FAIL] Multi-song regional posts: snapshot missing for {chart_date}", flush=True)
        return {}, {}

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(f"[FAIL] Multi-song regional posts: snapshot unreadable ({exc})", flush=True)
        return {}, {}

    by_track = data.get("by_track") if isinstance(data, dict) else None
    if not isinstance(by_track, dict):
        print(f"[FAIL] Multi-song regional posts: invalid snapshot format: {path}", flush=True)
        return {}, {}

    id_to_name = _build_id_to_name()
    by_region: dict[str, list[dict]] = {}
    regions: dict[str, str] = {}
    for track_id, entries in by_track.items():
        if not isinstance(entries, list):
            continue
        track_name = id_to_name.get(str(track_id), str(track_id))
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            region = str(entry.get("country") or "").strip()
            if not region:
                continue
            region_name = str(entry.get("country_name") or region.upper()).strip()
            regions.setdefault(region, region_name)
            row = {
                "rank": entry.get("rank"),
                "track_name": track_name,
                "artist_names": TS_NAME,
                "_track_id_uri": str(track_id),
                "streams": entry.get("streams"),
                "previous_rank": entry.get("previous_rank"),
                "peak_rank": entry.get("peak_rank"),
                "total_days": entry.get("total_days"),
                "is_new": bool(entry.get("is_new")),
                "is_re_entry": bool(entry.get("is_re_entry")),
                "movement": entry.get("movement"),
                "stream_change": entry.get("stream_change"),
                "stream_change_pct": entry.get("stream_change_pct"),
                "weekly_stream_change": entry.get("weekly_stream_change"),
                "weekly_stream_change_pct": entry.get("weekly_stream_change_pct"),
            }
            by_region.setdefault(region, []).append(row)

    for rows in by_region.values():
        rows.sort(key=lambda row: row.get("rank") or 9999)
    print(f"[INFO] Loaded snapshot for regional posts: {len(by_region)} region(s)", flush=True)
    return by_region, regions


def _post_multi_song_regions_from_snapshot(
    chart_date: str,
    regions: dict[str, str],
    *,
    force: bool = False,
) -> int:
    by_region, snapshot_regions = _load_snapshot_by_region(chart_date)
    if not by_region:
        return 1
    regions = {**snapshot_regions, **regions}
    _enrich_multi_song_region_rows(chart_date, by_region)

    track_lookup = build_track_lookup()
    manual_lookup = build_manual_mapping()
    multi_song_region_rows = {
        region: rows
        for region, rows in by_region.items()
        if region not in SCORED_REGIONAL_POST_EXCLUDED_REGIONS and len(rows) >= MULTI_SONG_REGIONAL_POST_MIN_SONGS
    }
    for region, rows in multi_song_region_rows.items():
        _write_regional_ts_chart(chart_date, region, rows, manual_lookup, track_lookup)

    _post_multi_song_regions(
        chart_date,
        regions,
        multi_song_region_rows,
        force=force,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch worldwide Spotify charts for Taylor Swift songs."
    )
    parser.add_argument("date_pos", nargs="?", metavar="YYYY-MM-DD")
    parser.add_argument("--date", metavar="YYYY-MM-DD")
    parser.add_argument("--dates", nargs="+", metavar="YYYY-MM-DD")
    parser.add_argument("--dates-file", metavar="PATH")
    parser.add_argument("--backfill-from", metavar="YYYY-MM-DD")
    parser.add_argument("--backfill-to", metavar="YYYY-MM-DD")
    parser.add_argument(
        "--no-post",
        action="store_true",
        help="Skip Twitter post.",
    )
    parser.add_argument(
        "--post-song-updates",
        action="store_true",
        help="Post the legacy text-only per-song worldwide updates.",
    )
    parser.add_argument(
        "--post-priority-global-new",
        action="store_true",
        help="Post the priority Global NEW card as soon as the global chart is fetched.",
    )
    parser.add_argument(
        "--post-priority-region",
        action="append",
        choices=PRIORITY_POST_REGIONS,
        default=[],
        help="Post this regional chart as soon as its priority fetch is written.",
    )
    parser.add_argument(
        "--post-multi-song-regions",
        action="store_true",
        help=(
            f"Post {MULTI_SONG_REGIONAL_POST_MAX_POSTS} non-priority region picked score-weighted at random "
            f"among the top {MULTI_SONG_REGIONAL_POST_POOL_SIZE} scored regions "
            f"(min {MULTI_SONG_REGIONAL_POST_MIN_SONGS} songs, positive score, not posted the previous day)."
        ),
    )
    parser.add_argument(
        "--post-multi-song-regions-only",
        action="store_true",
        help="Post multi-song regional cards from an existing worldwide snapshot without fetching Spotify.",
    )
    parser.add_argument(
        "--force-priority-global-new",
        action="store_true",
        help="Ignore the posted lock for the priority Global NEW card.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch all regions even if already present for this date.",
    )
    parser.add_argument(
        "--backfill-mode",
        action="store_true",
        help="Historical data-only mode: no latest write, no R2, no git commit, no total_days store write.",
    )
    args = parser.parse_args()

    if args.dates or args.dates_file or args.backfill_from or args.backfill_to:
        try:
            if args.dates_file:
                dates_path = Path(args.dates_file)
                try:
                    raw_dates = dates_path.read_text(encoding="utf-8").splitlines()
                except OSError as exc:
                    print(f"[ERROR] Could not read dates file {dates_path}: {exc}")
                    return 1
                chart_dates = [
                    datetime.strptime(raw.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
                    for raw in raw_dates
                    if raw.strip()
                ]
                if not chart_dates:
                    print(f"[ERROR] Empty dates file: {dates_path}")
                    return 1
            elif args.dates:
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
        failed_dates: list[str] = []
        try:
            os.environ["CHARTS_RUN_ALL"] = "1"
            for idx, chart_date in enumerate(chart_dates, 1):
                print(f"\n[BACKFILL] worldwide {idx}/{len(chart_dates)}: {chart_date}", flush=True)
                sys.argv = [original_argv[0], chart_date]
                if args.no_post:
                    sys.argv.append("--no-post")
                if args.force:
                    sys.argv.append("--force")
                if args.backfill_mode:
                    sys.argv.append("--backfill-mode")
                try:
                    rc = main()
                except Exception as exc:
                    print(f"[BACKFILL] {chart_date} raised {exc!r}; continuing with remaining dates.", flush=True)
                    rc = 1
                if rc != 0:
                    failed_dates.append(chart_date)
                    print(f"[BACKFILL] {chart_date} failed (code {rc}); continuing with remaining dates.", flush=True)
        finally:
            sys.argv = original_argv
            if original_run_all is None:
                os.environ.pop("CHARTS_RUN_ALL", None)
            else:
                os.environ["CHARTS_RUN_ALL"] = original_run_all
        elapsed = time.perf_counter() - started
        ok_count = len(chart_dates) - len(failed_dates)
        print(f"[ OK ] worldwide backfill {ok_count}/{len(chart_dates)} date(s) en {elapsed:.1f}s")
        if failed_dates:
            print(f"[BACKFILL] {len(failed_dates)} date(s) failed: {', '.join(failed_dates)}")
        if not args.backfill_mode:
            git_commit_and_push(ROOT, f"charts worldwide backfill {chart_dates[0]} -> {chart_dates[-1]}")
        return 1 if failed_dates else 0

    raw_date = args.date or args.date_pos or str(date.today() - timedelta(days=1))
    try:
        chart_date = datetime.strptime(raw_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        print(f"[ERROR] Invalid date: {raw_date!r}")
        return 1

    if args.post_multi_song_regions_only:
        print(f"[INFO] chart_date = {chart_date}")
        return _post_multi_song_regions_from_snapshot(chart_date, {}, force=args.force)

    if not SESSION_FILE.exists():
        print(f"[ERROR] Session file not found: {SESSION_FILE}")
        return 1

    print(f"[INFO] chart_date = {chart_date}")
    if getattr(_get_bearer_token_and_regions, "_cache", None) is None:
        print("[INFO] Acquiring bearer token and discovering regions via Playwright/cache...")
    else:
        print("[INFO] Using cached bearer token and regions.")
    token, regions = _get_bearer_token_and_regions()
    tokens: list[str] = [token]
    if not SINGLE_SESSION_TOKEN_POOL:
        for sf in sorted(SESSION_FILE.parent.glob("spotify_session*.json")):
            if sf == SESSION_FILE:
                continue
            t = _get_bearer_from_cookies(sf)
            if t:
                tokens.append(t)
                print(f"[INFO] Token supplémentaire chargé depuis {sf.name}")
    print(f"[INFO] {len(tokens)} token(s) disponible(s). {len(regions)} regions to fetch.")


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
    historical_lookup = build_historical_track_id_lookup()

    id_to_name = _build_id_to_name()
    id_to_album = _build_id_to_album()

    # Contexte pour la detection RE immediate par pays (voir "Immediate re-entry
    # posting" plus haut) : desactive en backfill/multi-dates/--no-post, comme les
    # autres mecanismes de post live de ce script.
    immediate_reentry_ctx: dict | None = None
    if not args.no_post and not args.backfill_mode and not args.dates and not args.dates_file:
        prev_country_counts, has_prev_snapshot = _load_prev_country_counts(chart_date)
        immediate_reentry_ctx = {
            "manual_lookup": manual_lookup,
            "track_lookup": track_lookup,
            "historical_lookup": historical_lookup,
            "prev_country_counts": prev_country_counts,
            "has_prev_snapshot": has_prev_snapshot,
            "threads": [],
        }

    # Contexte pour le sync CSV immediat par pays (voir "Incremental per-country
    # CSV sync" plus haut) : independant de --no-post (ecrire l'historique n'est
    # pas un post), mais desactive en backfill/multi-dates ou son propre pipeline
    # de sync dedie prend deja le relais.
    csv_sync_ctx: dict | None = None
    if not args.backfill_mode and not args.dates and not args.dates_file:
        csv_sync_ctx = {
            "manual_lookup": manual_lookup,
            "track_lookup": track_lookup,
            "historical_lookup": historical_lookup,
        }

    regions_to_fetch = {k: v for k, v in regions.items() if k not in already_done}

    # Phase 1 : fetch global (toujours, meme --no-post) et fr/us en priorité pour poster
    # pendant la phase 2. "global" doit etre confirme (retry selon FETCH_MAX_ATTEMPTS) avant
    # de lancer le fetch des autres regions worldwide en phase 2 — jamais en parallele.
    post_priority_global_new = args.post_priority_global_new and not args.dates and not args.dates_file
    priority_post_regions = set(args.post_priority_region or [])
    if post_priority_global_new:
        priority_post_regions.add("global")
    _PRIORITY = {"global"} | (set(PRIORITY_POST_REGIONS) if not args.no_post else set()) | priority_post_regions
    priority_to_fetch = {k: v for k, v in regions_to_fetch.items() if k in _PRIORITY}
    other_to_fetch    = {k: v for k, v in regions_to_fetch.items() if k not in _PRIORITY}

    t0 = time.perf_counter()
    _priority_card_thread: threading.Thread | None = None
    if priority_to_fetch:
        print(f"[INFO] Phase 1 : fetch prioritaire ({', '.join(sorted(priority_to_fetch))})…")
        tokens, _, priority_results = _run_async_with_token_refresh(
            chart_date, tokens, priority_to_fetch,
            immediate_reentry_ctx=immediate_reentry_ctx, csv_sync_ctx=csv_sync_ctx,
        )
        _apply_track_id_history(chart_date, priority_results)
        print(f"[INFO] Phase 1 terminée en {time.perf_counter() - t0:.1f}s")
        for region in PRIORITY_POST_REGIONS:
            if region in priority_results and priority_results[region]:
                _write_regional_ts_chart(chart_date, region, priority_results[region], manual_lookup, track_lookup)
        if (not args.no_post or post_priority_global_new) and priority_results.get("global") and GLOBAL_NEW_RELEASES_SCRIPT.exists():
            def _post_priority_global_new_card() -> None:
                print("[INFO] Priority Global NEW card check...", flush=True)
                for attempt in range(1, PRIORITY_CARD_POST_MAX_ATTEMPTS + 1):
                    cmd = [sys.executable, str(GLOBAL_NEW_RELEASES_SCRIPT), chart_date, "--post"]
                    if args.force_priority_global_new or attempt == PRIORITY_CARD_POST_MAX_ATTEMPTS:
                        cmd.append("--force")
                    result = subprocess.run(
                        cmd,
                        cwd=str(ROOT),
                    )
                    if result.returncode == 0:
                        return
                    print(
                        f"[WARN] Priority Global NEW card failed (code {result.returncode}, "
                        f"tentative {attempt}/{PRIORITY_CARD_POST_MAX_ATTEMPTS})",
                        flush=True,
                    )
                    if attempt < PRIORITY_CARD_POST_MAX_ATTEMPTS:
                        time.sleep(PRIORITY_CARD_POST_RETRY_SECONDS)
                print("[WARN] Priority Global NEW card: abandon apres plusieurs tentatives", flush=True)
            _priority_card_thread = threading.Thread(
                target=_post_priority_global_new_card,
                daemon=True,
                name="priority-global-new-card",
            )
            _priority_card_thread.start()
    else:
        priority_results = {}

    # Lancer le posting global/fr en background pendant le fetch des autres régions
    _posting_thread: threading.Thread | None = None
    regional_post_scripts = {
        "global": GLOBAL_DAILY,
        "fr": FR_DAILY,
        "us": ROOT / "collectors" / "spotify" / "charts" / "us" / "daily.py",
    }
    if args.no_post:
        regions_to_post = [region for region in PRIORITY_POST_REGIONS if region in priority_post_regions]
    else:
        regions_to_post = [region for region in PRIORITY_POST_REGIONS if region in priority_to_fetch]
    regions_to_post = [region for region in regions_to_post if region in priority_results and priority_results[region]]
    if regions_to_post:
        def _post_regional() -> None:
            if _priority_card_thread is not None and _priority_card_thread.is_alive():
                print("[INFO] Waiting for priority Global NEW card before regional posts...", flush=True)
                _priority_card_thread.join(timeout=600)
                if _priority_card_thread.is_alive():
                    print("[WARN] Priority Global NEW card still running after 10 minutes; regional posts continue.", flush=True)
            for region in regions_to_post:
                script = regional_post_scripts[region]
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
        tokens, _, other_results = _run_async_with_token_refresh(
            chart_date, tokens, other_to_fetch,
            immediate_reentry_ctx=immediate_reentry_ctx, csv_sync_ctx=csv_sync_ctx,
        )
        print(f"[INFO] Phase 2 terminée en {time.perf_counter() - t0:.1f}s total")
    else:
        other_results = {}

    by_region = {**priority_results, **other_results}
    skipped_regions = sorted(region for region, rows in by_region.items() if rows is None)
    if skipped_regions:
        print(
            f"[WARN] {len(skipped_regions)} region(s) skipped after max fetch attempts for {chart_date} "
            f"(not written, not counted as zero): {', '.join(skipped_regions)}",
            flush=True,
        )
        by_region = {region: rows for region, rows in by_region.items() if rows is not None}
    _apply_track_id_history(chart_date, by_region)
    for region in PRIORITY_POST_REGIONS:
        if region in by_region and by_region[region]:
            _write_regional_ts_chart(chart_date, region, by_region[region], manual_lookup, track_lookup)

    _multi_song_post_thread: threading.Thread | None = None
    if args.post_multi_song_regions:
        if _posting_thread is not None and _posting_thread.is_alive():
            print("[INFO] Waiting for priority regional posts before scored regional posts...", flush=True)
            _posting_thread.join(timeout=600)
            if _posting_thread.is_alive():
                print("[WARN] Priority regional posts still running after 10 minutes; scored regional posts continue.", flush=True)
        _enrich_multi_song_region_rows(chart_date, by_region)
        multi_song_region_rows = {
            region: rows
            for region, rows in by_region.items()
            if region not in {"global", "fr"} and len(rows) >= MULTI_SONG_REGIONAL_POST_MIN_SONGS
        }
        for region, rows in multi_song_region_rows.items():
            _write_regional_ts_chart(chart_date, region, rows, manual_lookup, track_lookup)
        if multi_song_region_rows:
            _multi_song_post_thread = threading.Thread(
                target=lambda: _post_multi_song_regions(
                    chart_date,
                    regions,
                    multi_song_region_rows,
                    force=args.force,
                ),
                daemon=True,
                name="multi-song-regional-posting",
            )
            _multi_song_post_thread.start()

    by_track: dict[str, list[dict]] = {}
    track_names: dict[str, str] = {}
    unresolved: list[dict]          = []

    for region, rows in by_region.items():
        country_name = regions[region]
        for row in rows:
            track_id: Optional[str] = row.get("_track_id_uri")
            if not track_id:
                track_id = resolve_track_id(row["track_name"], manual_lookup, track_lookup)
            track_id = canonical_chart_track_id(track_id, historical_lookup)
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
                "streak":         row.get("streak"),
                "is_new":         bool(row.get("is_new")),
                "is_re_entry":    bool(row.get("is_re_entry")),
                "movement":       row.get("movement"),
                "stream_change":  row.get("stream_change"),
                "stream_change_pct": row.get("stream_change_pct"),
                "weekly_stream_change": row.get("weekly_stream_change"),
                "weekly_stream_change_pct": row.get("weekly_stream_change_pct"),
            })

    # Merge back already-skipped entries from the previous run of the same date.
    # Without this, re-runs would silently discard countries collected in earlier runs.
    if existing_by_track and already_done:
        for raw_track_id, old_entries in existing_by_track.items():
            track_id = canonical_chart_track_id(str(raw_track_id), historical_lookup)
            if not track_id:
                continue
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
            api_total_days = _to_int(entry.get("total_days"))
            if api_total_days is not None:
                entry["total_days"] = api_total_days
                total_days_store[key] = api_total_days
            else:
                stored = total_days_store.get(key, 0)
                entry["total_days"] = stored + 1
                total_days_store[key] = stored + 1

    if not args.backfill_mode:
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
    if skipped_regions:
        output["skipped_regions"] = skipped_regions

    per_date_path = _worldwide_history_path(chart_date)
    per_date_path.parent.mkdir(parents=True, exist_ok=True)
    per_date_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] Written → {per_date_path}")

    if not args.backfill_mode:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[DONE] Written latest → {OUTPUT_PATH}")
        updated_lock = _updated_lock_path(chart_date)
        updated_lock.touch()
        print(f"[DONE] Written -> {updated_lock}")
        maybe_upload_to_r2(chart_date, force=args.force)

    if args.post_song_updates and not args.no_post and TWITTER_SESSION.exists():
        has_prev = prev_path.exists()
        locks_dir = per_date_path.parent
        date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%A, %B %d, %Y")
        sorted_tracks = sorted(by_track.items(), key=lambda kv: len(kv[1]), reverse=True)
        url = full_charts_update_line(region="overall", label="🔗 See full update here")
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
                        f"at #{rank} with {streams_str} streams on {date_fmt}.\n\n{url}"
                    )
                else:
                    tweet = (
                        f'{emoji} | "{song_name}" has re-entered the Spotify Charts in {count} countries '
                        f"on {date_fmt}.\n\n{url}"
                    )
                reentry_items.append((track_id, tweet))
            else:
                if has_prev:
                    diff = count - prev_count
                    diff_str = f"+{diff}" if diff >= 0 else str(diff)
                    country_str = f"{count} countries ({diff_str})"
                else:
                    country_str = f"{count} countries"
                regular_items.append((track_id, f'{emoji} | "{song_name}" charted in {country_str} on Spotify on {date_fmt}.\n\n{url}'))

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

    if _priority_card_thread is not None and _priority_card_thread.is_alive():
        print("[INFO] Attente fin Priority Global NEW card...", flush=True)
        _priority_card_thread.join(timeout=600)
        if _priority_card_thread.is_alive():
            print("[WARN] Priority Global NEW card toujours en cours apres 10 minutes", flush=True)

    if _multi_song_post_thread is not None and _multi_song_post_thread.is_alive():
        print("[INFO] Attente fin posts regions multi-titres...", flush=True)
        _multi_song_post_thread.join(timeout=600)
        if _multi_song_post_thread.is_alive():
            print("[WARN] Posts regions multi-titres toujours en cours apres 10 minutes", flush=True)

    if immediate_reentry_ctx is not None and immediate_reentry_ctx["threads"]:
        # Threads daemon (voir _maybe_trigger_immediate_reentries) : sans ce join,
        # le process peut sortir avant qu'un post immediat en cours ne se termine.
        pending = [t for t in immediate_reentry_ctx["threads"] if t.is_alive()]
        if pending:
            print(f"[INFO] Attente fin de {len(pending)} post(s) RE immediat(s)...", flush=True)
        for t in immediate_reentry_ctx["threads"]:
            t.join(timeout=600)
        still_alive = [t.name for t in immediate_reentry_ctx["threads"] if t.is_alive()]
        if still_alive:
            print(f"[WARN] {len(still_alive)} post(s) RE immediat(s) toujours en cours apres 10 minutes", flush=True)

    if not args.backfill_mode:
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
            "track_id":      row.get("_track_id_uri") or resolve_track_id(row.get("track_name", ""), manual_lookup, track_lookup),
            "streams":       row.get("streams"),
            "previous_rank": row.get("previous_rank"),
            "peak_rank":     row.get("peak_rank"),
            "total_days":    row.get("total_days"),
            "streak":        row.get("streak"),
            "is_new":        bool(row.get("is_new")),
            "is_re_entry":   bool(row.get("is_re_entry")),
            "movement":      row.get("movement"),
            "stream_change":  row.get("stream_change"),
            "stream_change_pct": row.get("stream_change_pct"),
            "weekly_stream_change": row.get("weekly_stream_change"),
            "weekly_stream_change_pct": row.get("weekly_stream_change_pct"),
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


def maybe_upload_to_r2(chart_date: str, *, force: bool = False) -> None:
    exported_lock = _exported_done_lock_path(chart_date)
    if exported_lock.exists() and not force:
        print(f"[INFO] R2 upload skipped ({exported_lock.name} exists; use --force to re-export)")
        return

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
            _mark_exported_done(chart_date)
            return
        wait = 30 * attempt
        print(f"[WARN] R2 upload failed (exit {result.returncode}), retry dans {wait}s (tentative {attempt}/5)")
        time.sleep(wait)
    print("[ERROR] R2 upload failed après 5 tentatives — poursuite sans upload")


if __name__ == "__main__":
    raise SystemExit(main())
