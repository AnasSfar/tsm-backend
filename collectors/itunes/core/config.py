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

from collectors.spotify.core.data_paths import ARCHIVE_DB_ROOT as ARCHIVE_DB_DIR  # noqa: E402
from collectors.spotify.core.data_paths import itunes_charts_dir  # noqa: E402

RUN_DATE = os.getenv("TSM_DATA_DATE", date.today().isoformat())
DB_DIR = itunes_charts_dir(RUN_DATE)
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Taylor Swift — same catalog identity as the Apple Music collector (iTunes Store
# and Apple Music share one id namespace).
ARTIST_FILTER = "Taylor Swift"
ARTIST_ID = "159260351"

# iTunes Store legacy RSS feeds (purchase charts — distinct from the Apple Music
# streaming/most-played charts handled by collectors/apple_music). No auth.
RSS_BASE = "https://itunes.apple.com"
RSS_SONGS_PATH = "rss/topsongs/limit={limit}/json"
RSS_ALBUMS_PATH = "rss/topalbums/limit={limit}/json"


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


DEFAULT_TIMEOUT = _int_from_env("ITUNES_TIMEOUT", 20)
RETRY_TOTAL = _int_from_env("ITUNES_RETRY_TOTAL", 3)
RETRY_BACKOFF = _float_from_env("ITUNES_RETRY_BACKOFF", 1.5)
# urllib3-level retries: connection errors + hard 5xx only. Throttle statuses
# (403/429/503 — the RSS host uses 403 for bursts) are handled with a longer
# backoff inside core/rss.py::_fetch so the two layers don't compound.
RETRY_STATUS_FORCELIST = (500, 502, 504)
CHART_LIMIT = _int_from_env("ITUNES_CHART_LIMIT", 100)
# The legacy RSS endpoint is far less burst-tolerant than the AMP API — keep
# concurrency low and add a small per-request jitter (see core/rss.py).
WORKERS = max(1, _int_from_env("ITUNES_WORKERS", 3))
# Manual throttle-aware retries inside _fetch, on top of the urllib3 Retry.
THROTTLE_RETRIES = _int_from_env("ITUNES_THROTTLE_RETRIES", 4)
THROTTLE_BASE_SLEEP = _float_from_env("ITUNES_THROTTLE_BASE_SLEEP", 2.0)
REQUEST_JITTER_MAX = _float_from_env("ITUNES_REQUEST_JITTER_MAX", 0.4)

# A storefront that legitimately has no feed for a chart returns 404 — that is an
# empty chart, not a collection failure. Only network / throttle / 5xx errors
# that survive the sequential retry pass count against this budget.
#
# Unlike the Spotify streams pipeline, a missing minor storefront on one
# 2-hourly iTunes snapshot is self-healing (the next snapshot fills it, and
# previous_rank tolerates gaps), so the threshold is looser — but losing a
# CRITICAL storefront (us/gb: the charts everyone quotes) always aborts.
MAX_FAILURE_PCT = _float_from_env("ITUNES_MAX_FAILURE_PCT", 25.0)
CRITICAL_STOREFRONTS = {
    s.strip().lower()
    for s in os.getenv("ITUNES_CRITICAL_STOREFRONTS", "us,gb").split(",")
    if s.strip()
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
