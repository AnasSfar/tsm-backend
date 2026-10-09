"""Signature symbols of each Taylor Swift era (red = scarf + autumn leaves,
1989 = seagulls, reputation = snake...).

Mirror of tsm-frontend/frontend/src/data/eraSymbols.js — keep both in sync.
Era keys = the frontend palette keys (data/rankingPalettes.js::CARD_PALETTES).
Each era: `primary` = the one symbol to use when only one fits, `symbols` =
ordered from most to least iconic, `emoji` = short string for captions.
"""

from __future__ import annotations

import re

ERA_SYMBOLS: dict[str, dict] = {
    "debut": {
        "album": "Taylor Swift",
        "theme": "theme-taylor-swift",
        "primary": "guitar",
        "emoji": "🎸🤠💚",
        "symbols": [
            {"id": "guitar", "label": "Guitar (Teardrops On My Guitar)", "emoji": "🎸"},
            {"id": "cowboy_boots", "label": "Cowboy boots & hat", "emoji": "🤠"},
            {"id": "curly_hair", "label": "Curly hair", "emoji": "➰"},
            {"id": "teardrops", "label": "Teardrops", "emoji": "💧"},
        ],
    },
    "fearless": {
        "album": "Fearless",
        "theme": "theme-fearless",
        "primary": "gold_sparkles",
        "emoji": "✨💛🏰",
        "symbols": [
            {"id": "gold_sparkles", "label": "Gold sparkles & fringe dress", "emoji": "✨"},
            {"id": "thirteen_on_hand", "label": "13 written on her hand", "emoji": "1️⃣3️⃣"},
            {"id": "fairytale_castle", "label": "Fairytale castle (Love Story)", "emoji": "🏰"},
            {"id": "crown", "label": "Princess crown", "emoji": "👑"},
        ],
    },
    "speak-now": {
        "album": "Speak Now",
        "theme": "theme-speak-now",
        "primary": "fireworks",
        "emoji": "🎆💜👗",
        "symbols": [
            {"id": "fireworks", "label": "Fireworks (Long Live / Enchanted)", "emoji": "🎆"},
            {"id": "purple_ballgown", "label": "Purple ballgown", "emoji": "👗"},
            {"id": "wedding", "label": "Wedding (\"Speak now\")", "emoji": "💒"},
        ],
    },
    "red": {
        "album": "Red",
        "theme": "theme-red",
        "primary": "scarf",
        "emoji": "🧣🍁💋",
        "symbols": [
            {"id": "scarf", "label": "Red scarf (All Too Well)", "emoji": "🧣"},
            {"id": "autumn_leaves", "label": "Autumn leaves", "emoji": "🍁"},
            {"id": "red_lips", "label": "Red lipstick", "emoji": "💋"},
            {"id": "hat", "label": "Hat (22 / fedora)", "emoji": "🎩"},
        ],
    },
    "1989": {
        "album": "1989",
        "theme": "theme-1989",
        "primary": "seagulls",
        "emoji": "🕊️📸🌊",
        "symbols": [
            {"id": "seagulls", "label": "Seagulls", "emoji": "🕊️"},
            {"id": "polaroid", "label": "Polaroid", "emoji": "📸"},
            {"id": "sea_sky", "label": "Sea & blue sky", "emoji": "🌊"},
            {"id": "new_york", "label": "New York (Welcome To New York)", "emoji": "🗽"},
        ],
    },
    "reputation": {
        "album": "reputation",
        "theme": "theme-reputation",
        "primary": "snake",
        "emoji": "🐍🖤📰",
        "symbols": [
            {"id": "snake", "label": "Snake", "emoji": "🐍"},
            {"id": "newspaper", "label": "Newspaper print", "emoji": "📰"},
            {"id": "black", "label": "Black & silver", "emoji": "🖤"},
        ],
    },
    "lover": {
        "album": "Lover",
        "theme": "theme-lover",
        "primary": "butterfly",
        "emoji": "🦋💗🌈",
        "symbols": [
            {"id": "butterfly", "label": "Butterfly", "emoji": "🦋"},
            {"id": "hearts", "label": "Hearts", "emoji": "💗"},
            {"id": "pastel_clouds", "label": "Pastel clouds & rainbows", "emoji": "🌈"},
            {"id": "lover_house", "label": "The Lover house", "emoji": "🏠"},
        ],
    },
    "folklore": {
        "album": "folklore",
        "theme": "theme-folklore",
        "primary": "cardigan",
        "emoji": "🧥🌲🤍",
        "symbols": [
            {"id": "cardigan", "label": "Cardigan", "emoji": "🧥"},
            {"id": "pine_forest", "label": "Misty pine forest", "emoji": "🌲"},
            {"id": "cabin", "label": "Cabin in the woods", "emoji": "🛖"},
            {"id": "mirrorball", "label": "Mirrorball", "emoji": "🪩"},
        ],
    },
    "evermore": {
        "album": "evermore",
        "theme": "theme-evermore",
        "primary": "autumn",
        "emoji": "🍂🧵🧥",
        "symbols": [
            {"id": "autumn", "label": "Autumn (fallen leaves, bare woods)", "emoji": "🍂"},
            {"id": "golden_thread", "label": "Golden thread (willow)", "emoji": "🧵"},
            {"id": "plaid_coat", "label": "Plaid coat & braid", "emoji": "🧥"},
            {"id": "champagne", "label": "Champagne (champagne problems)", "emoji": "🍾"},
        ],
    },
    "midnights": {
        "album": "Midnights",
        "theme": "theme-midnights",
        "primary": "moon_stars",
        "emoji": "🌙🕛💎",
        "symbols": [
            {"id": "moon_stars", "label": "Moon & stars", "emoji": "🌙"},
            {"id": "clock", "label": "Clock (3am)", "emoji": "🕛"},
            {"id": "jewels", "label": "Jewels (Bejeweled)", "emoji": "💎"},
            {"id": "lavender_haze", "label": "Lavender haze", "emoji": "💜"},
        ],
    },
    "ttpd": {
        "album": "The Tortured Poets Department",
        "theme": "theme-ttpd",
        "primary": "typewriter",
        "emoji": "⌨️📜🤍",
        "symbols": [
            {"id": "typewriter", "label": "Typewriter", "emoji": "⌨️"},
            {"id": "manuscript", "label": "Manuscript pages", "emoji": "📜"},
            {"id": "quill", "label": "Quill / pen", "emoji": "🖋️"},
            {"id": "black_white", "label": "Black & white", "emoji": "🤍"},
        ],
    },
    "showgirl": {
        "album": "The Life of a Showgirl",
        "theme": "theme-showgirl",
        "primary": "glitter",
        "emoji": "✨🪶🧡",
        "symbols": [
            {"id": "glitter", "label": "Glitter & sequins", "emoji": "✨"},
            {"id": "feathers", "label": "Showgirl feathers", "emoji": "🪶"},
            {"id": "ophelia_water", "label": "Water (The Fate of Ophelia)", "emoji": "🌊"},
            {"id": "orange", "label": "Orange & mint", "emoji": "🧡"},
        ],
    },
    # Not an album: the tour itself (cross-era content).
    "eras": {
        "album": "The Eras Tour",
        "theme": "theme-eras",
        "primary": "friendship_bracelets",
        "emoji": "📿🎟️",
        "symbols": [
            {"id": "friendship_bracelets", "label": "Friendship bracelets", "emoji": "📿"},
            {"id": "ticket", "label": "Tour ticket", "emoji": "🎟️"},
        ],
    },
}

ERA_ORDER = list(ERA_SYMBOLS)

_ERA_BY_ALBUM = {
    "taylor swift": "debut",
    "fearless": "fearless",
    "speak now": "speak-now",
    "red": "red",
    "1989": "1989",
    "reputation": "reputation",
    "lover": "lover",
    "folklore": "folklore",
    "evermore": "evermore",
    "midnights": "midnights",
    "the tortured poets department": "ttpd",
    "the life of a showgirl": "showgirl",
}

# "(Taylor's Version)", "(Deluxe)", "(3am Edition)", ": The Anthology", ": The Encore"...
_EDITION_SUFFIX = re.compile(r"\s*(\(.*\)|\[.*\]|:.*|-\s.*)$")


def era_key_for_album(album_name: str | None) -> str | None:
    """'Red (Taylor's Version)' -> 'red'; 'The Life of a Showgirl: The Encore' -> 'showgirl'."""
    name = str(album_name or "").strip().lower()
    while True:
        stripped = _EDITION_SUFFIX.sub("", name).strip()
        if stripped == name:
            break
        name = stripped
    return _ERA_BY_ALBUM.get(name)


def era_symbols_for_album(album_name: str | None) -> dict | None:
    key = era_key_for_album(album_name)
    return ERA_SYMBOLS.get(key) if key else None


def era_emoji_for_album(album_name: str | None, default: str = "") -> str:
    """Primary symbol emoji of the album's era (for captions), or `default`."""
    era = era_symbols_for_album(album_name)
    if not era:
        return default
    return next(s["emoji"] for s in era["symbols"] if s["id"] == era["primary"])
