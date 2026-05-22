from __future__ import annotations


ALBUM_EMOJI = (
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


def album_emoji(album: str | None, *, fallback: str = "📈") -> str:
    normalized = (album or "").casefold()
    for key, emoji in ALBUM_EMOJI:
        if key in normalized:
            return emoji
    return fallback
