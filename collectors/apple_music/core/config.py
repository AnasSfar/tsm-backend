from __future__ import annotations

import os
from datetime import date
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
DATA_ROOT = REPO_ROOT / "data"
ARCHIVE_DB_DIR = DATA_ROOT / "_archive" / "original" / "db"
RUN_DATE = os.getenv("TSM_DATA_DATE", date.today().isoformat())
DB_DIR = DATA_ROOT / RUN_DATE[:4] / RUN_DATE[5:7] / RUN_DATE / "apple_music"
SCRIPTS_DIR = REPO_ROOT / "scripts"
TOOLS_JSON_DIR = PACKAGE_ROOT / "tools" / "json"
TOOLS_JSON_DIR.mkdir(parents=True, exist_ok=True)

APPLE_MUSIC_HOME = "https://music.apple.com/fr/new"
ARTIST_FILTER = "Taylor Swift"
ARTIST_ID = "159260351"
DEFAULT_STOREFRONT = "fr"


def _int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _list_from_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = [part.strip().lower() for part in raw.split(",") if part.strip()]
    return values or default


DEFAULT_TIMEOUT = _int_from_env("APPLE_MUSIC_TIMEOUT", 20)
RETRY_TOTAL = _int_from_env("APPLE_MUSIC_RETRY_TOTAL", 3)
RETRY_BACKOFF = _float_from_env("APPLE_MUSIC_RETRY_BACKOFF", 1.0)
RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)
TOKEN_CACHE_PATH = TOOLS_JSON_DIR / "apple_music_token.json"
CHART_LIMIT = _int_from_env("APPLE_MUSIC_CHART_LIMIT", 200)
WORKERS = max(1, _int_from_env("APPLE_MUSIC_WORKERS", 12))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

COUNTRIES = _list_from_env(
    "APPLE_MUSIC_COUNTRIES",
    [
        # North America
        "us", "ca", "mx",
        # South America
        "ar", "br", "cl", "co", "cr", "do", "ec", "gt", "hn", "pa", "pe", "py", "sv", "uy",
        # Europe
        "at", "be", "bg", "ch", "cz", "de", "dk", "ee", "es", "fi", "fr", "gb",
        "gr", "hr", "hu", "ie", "is", "it", "lt", "lu", "lv", "mk", "mt", "nl",
        "no", "pl", "pt", "ro", "rs", "se", "si", "sk", "tr", "ua",
        # Middle East
        "ae", "bh", "il", "jo", "kw", "lb", "om", "qa", "sa",
        # Africa
        "eg", "gh", "ke", "ma", "ng", "tn", "tz", "za",
        # Asia-Pacific
        "au", "cn", "hk", "id", "in", "jp", "kz", "mo", "mn", "my",
        "nz", "ph", "pk", "sg", "th", "tw", "vn",
    ],
)
GENRES = [
    ("14", "Pop"),
    ("6", "Country"),
    ("18", "Hip-Hop/Rap"),
    ("21", "Rock"),
    ("10", "Singer/Songwriter"),
    ("20", "Alternative"),
    ("15", "R&B/Soul"),
    ("17", "Dance"),
]
