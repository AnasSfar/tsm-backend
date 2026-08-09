from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
repo_root_str = str(REPO_ROOT)
if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)
from collectors.spotify.core.data_paths import ARCHIVE_DB_ROOT as ARCHIVE_DB_DIR
from collectors.spotify.core.data_paths import DATA_ROOT, deezer_charts_dir

RUN_DATE = os.getenv("TSM_DATA_DATE", date.today().isoformat())
DB_DIR = deezer_charts_dir(RUN_DATE)
SCRIPTS_DIR = REPO_ROOT / "scripts"

BASE_URL = "https://api.deezer.com"
# Verified live 2026-08-09: https://api.deezer.com/search/artist?q=Taylor%20Swift
# -> id 12246, nb_fan ~12.68M, nb_album 123 (the correct official profile;
# other search hits for "Taylor Swift" are unrelated/low-fan duplicates).
ARTIST_ID = "12246"
ARTIST_NAME = "Taylor Swift"


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


DEFAULT_TIMEOUT = _int_from_env("DEEZER_TIMEOUT", 20)
RETRY_TOTAL = _int_from_env("DEEZER_RETRY_TOTAL", 3)
RETRY_BACKOFF = _float_from_env("DEEZER_RETRY_BACKOFF", 1.0)
RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)
# Deezer's public API allows 50 requests / 5s per IP, no auth required for
# these read-only endpoints (chart, artist, artist/top).
CHART_LIMIT = _int_from_env("DEEZER_CHART_LIMIT", 100)
ARTIST_TOP_LIMIT = _int_from_env("DEEZER_ARTIST_TOP_LIMIT", 50)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
