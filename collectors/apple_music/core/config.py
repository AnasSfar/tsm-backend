from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
DB_DIR = REPO_ROOT / "db"
SCRIPTS_DIR = REPO_ROOT / "scripts"
TOOLS_JSON_DIR = PACKAGE_ROOT / "tools" / "json"
TOOLS_JSON_DIR.mkdir(parents=True, exist_ok=True)

APPLE_MUSIC_HOME = "https://music.apple.com/fr/new"
ARTIST_FILTER = "Taylor Swift"
ARTIST_ID = "159260351"
DEFAULT_STOREFRONT = "fr"
DEFAULT_TIMEOUT = 20
TOKEN_CACHE_PATH = TOOLS_JSON_DIR / "apple_music_token.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

COUNTRIES = ["us", "fr", "gb", "de", "au"]
GENRES = [
    ("14", "Pop"),
    ("6", "Country"),
    ("18", "Hip-Hop/Rap"),
    ("21", "Rock"),
    ("10", "Singer/Songwriter"),
]
