from __future__ import annotations

from .albums import DEFAULT_ALBUM_HEART
from .sessions import ACCOUNT_HANDLES

ACCOUNT_HANDLE = ACCOUNT_HANDLES["tsm"]

DEFAULT_POST_PREFIX = "📈 |"
THREAD_PREFIX = "🧵 |"
BEST_DAY_PREFIX = "🏆 |"
STREAMS_PREFIX = DEFAULT_POST_PREFIX
OVERTAKE_PREFIX = DEFAULT_POST_PREFIX
SPOTIFY_CHART_PREFIX = DEFAULT_POST_PREFIX
SPOTLIGHT_DEFAULT_HEART = DEFAULT_ALBUM_HEART

MOST_STREAMED_SONGS_TITLE = "Taylor Swift's Most Streamed Songs"
SPOTIFY_COUNTER_NAME = "Taylor Swift's Spotify Counter"


def with_prefix(text: str, prefix: str = DEFAULT_POST_PREFIX) -> str:
    body = str(text or "").strip()
    clean_prefix = str(prefix or DEFAULT_POST_PREFIX).strip()
    if not body:
        return clean_prefix
    if body.startswith(clean_prefix):
        return body
    return f"{clean_prefix} {body}"

def spotlight_prefix(album_heart: str | None = None) -> str:
    heart = str(album_heart or "").strip() or SPOTLIGHT_DEFAULT_HEART
    return f"{heart} |"

def thread_prefix(text: str) -> str:
    return with_prefix(text, THREAD_PREFIX)


def default_post_prefix(text: str) -> str:
    return with_prefix(text, DEFAULT_POST_PREFIX)
