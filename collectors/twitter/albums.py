from __future__ import annotations

ALBUM_HEARTS: tuple[tuple[str, str], ...] = (
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
)

DEFAULT_ALBUM_HEART = "🤍"
DEFAULT_ALBUM_FALLBACK_EMOJI = "📈"


def album_heart(album: str | None, *, fallback: str = DEFAULT_ALBUM_HEART) -> str:
    normalized = (album or "").casefold().strip()
    for key, heart in ALBUM_HEARTS:
        if key in normalized:
            return heart
    return fallback


def album_emoji(album: str | None, *, fallback: str = DEFAULT_ALBUM_FALLBACK_EMOJI) -> str:
    return album_heart(album, fallback=fallback)


def album_heart_prefix(album: str | None, *, fallback: str = DEFAULT_ALBUM_HEART) -> str:
    return f"{album_heart(album, fallback=fallback)} |"
