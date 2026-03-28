from __future__ import annotations

import re
from typing import Any

from .config import ARTIST_FILTER, ARTIST_ID

QUOTE_MAP = str.maketrans(
    {
        "\u2019": "'",
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)

_SPACE_RE = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return _SPACE_RE.sub(" ", value.translate(QUOTE_MAP)).strip()


def rank_key(value: str | None) -> str:
    return clean_text(value).casefold()


def is_taylor_swift_song(item: dict[str, Any], attrs: dict[str, Any] | None = None) -> bool:
    attrs = attrs or item.get("attributes", {}) or {}

    artist_id = str(item.get("artistId", "")).strip()
    artist_name = clean_text(item.get("artistName") or attrs.get("artistName") or "").casefold()

    if artist_id == ARTIST_ID:
        return True
    if ARTIST_FILTER.casefold() in artist_name:
        return True

    relationships = item.get("relationships", {}) or {}
    artists_block = relationships.get("artists", {}) or {}
    for artist in artists_block.get("data", []) or []:
        rel_id = str(artist.get("id", "")).strip()
        rel_name = clean_text((artist.get("attributes") or {}).get("name", "")).casefold()
        if rel_id == ARTIST_ID or ARTIST_FILTER.casefold() in rel_name:
            return True

    return False


def build_artwork_url(artwork: dict[str, Any] | None, size: int = 300) -> str:
    if not artwork:
        return ""
    url = artwork.get("url", "")
    if not url:
        return ""
    return url.replace("{w}", str(size)).replace("{h}", str(size))
