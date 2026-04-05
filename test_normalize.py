import re

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_PAREN_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_DASH_SPLIT_RE = re.compile(r"\s+-\s+")

def _normalize_title(value: str) -> str:
    """Best-effort normalization for matching chart CSV titles."""
    s = (value or "").strip().casefold()
    if not s:
        return ""
    s = s.replace("'", "'").replace("'", "'").replace(""", '"').replace(""", '"')

    # Remove bracketed qualifiers: (Taylor's Version), [feat. ...], etc.
    s = _PAREN_RE.sub(" ", s)

    # Keep main title when CSV includes: "Song - Remastered".
    s = _DASH_SPLIT_RE.split(s, maxsplit=1)[0]

    s = _NORMALIZE_RE.sub(" ", s)
    s = " ".join(s.split())
    return s

# Test
test_titles = [
    "The Fate of Ophelia",
    "Opalite",
    "Style",
]

for title in test_titles:
    norm = _normalize_title(title)
    print(f"{title:30s} -> {norm}")
