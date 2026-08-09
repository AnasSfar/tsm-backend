from __future__ import annotations

import re

QUOTE_MAP = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)

_SPACE_RE = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return _SPACE_RE.sub(" ", value.translate(QUOTE_MAP)).strip()


def rank_key(value: str | None) -> str:
    return clean_text(value).casefold()
