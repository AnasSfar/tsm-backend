from __future__ import annotations

import os
from pathlib import Path

def _load_dotenv(env_path: Path) -> None:
    """Parse .env sans dépendance externe."""
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv as _ld
        _ld(env_path)
        return
    except ImportError:
        pass
    # Fallback stdlib
    with env_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv(Path(__file__).resolve().parents[3] / ".env")

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
DB_DIR = REPO_ROOT / "db"
TOOLS_JSON_DIR = PACKAGE_ROOT / "tools" / "json"
TOOLS_JSON_DIR.mkdir(parents=True, exist_ok=True)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
CHANNEL_ID = "UCqECaJ8Gagnn7YCbPEzWH6g"  # chaîne officielle Taylor Swift
# Le playlist ID "uploads" s'obtient en remplaçant UC→UU sur le channel ID
# Coût : playlistItems.list = 1 unité/page vs search.list = 100 unités/appel
UPLOADS_PLAYLIST_ID = "UUqECaJ8Gagnn7YCbPEzWH6g"

NTFY_TOPIC = os.getenv("NTFY_TOPIC_YOUTUBE", "taylormuseum-youtube")

CSV_PATH = DB_DIR / "youtube_views_history.csv"
VIDEO_DB_PATH = TOOLS_JSON_DIR / "video_db.json"
HISTORY_PATH = TOOLS_JSON_DIR / "youtube_history.json"

BATCH_SIZE = 50  # max IDs par appel videos.list
API_BASE = "https://www.googleapis.com/youtube/v3"

CSV_FIELDNAMES = ["date", "video_id", "title", "total_views", "daily_views"]
