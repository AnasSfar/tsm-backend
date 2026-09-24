from __future__ import annotations

"""Shared Apple Music card visual identity (2026-09-24) — extracted from
generate_country_card_images.py's THEMES/album-theme logic so a second
Apple Music card generator (post_new_release_progression.py) matches the
site's existing Apple Music card look instead of inventing a new one.
generate_country_card_images.py keeps its own inline copy for now (a proven,
scheduled, daily-running script — not touched to avoid regressing it under
time pressure); if a 3rd caller needs this, migrate that one too and drop
its copy."""

import base64
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO_ROOT = HERE.parents[1]
LOGO_PATH = REPO_ROOT.parent / "tsm-frontend" / "frontend" / "public" / "icons" / "logo.gif"

THEMES: dict[str, dict[str, str]] = {
    "showgirl": {
        "bg": "#eefbf8", "card_bg": "#fbfffd", "border": "#bfe9df", "text": "#16322f",
        "muted": "#597b75", "even_row": "#e9f8f4", "region": "#0f7f7d", "play_btn": "#ff6b35",
        "rank_up": "#2a9d5c", "rank_down": "#cf3f24",
    },
    "midnights": {
        "bg": "#eef2ff", "card_bg": "#f8faff", "border": "#cfd8ff", "text": "#1b2440",
        "muted": "#687394", "even_row": "#edf2ff", "region": "#4657c7", "play_btn": "#5b6ee1",
        "rank_up": "#2a9d5c", "rank_down": "#cf3f24",
    },
    "ttpd": {
        "bg": "#f4f1ec", "card_bg": "#fffdf8", "border": "#d8d0c4", "text": "#2c2822",
        "muted": "#746d63", "even_row": "#f5efe6", "region": "#6f665a", "play_btn": "#9b9387",
        "rank_up": "#2a9d5c", "rank_down": "#cf3f24",
    },
    "lover": {
        "bg": "#fff0f7", "card_bg": "#fffafd", "border": "#ffcfe4", "text": "#331927",
        "muted": "#8a6174", "even_row": "#ffe8f2", "region": "#d83c85", "play_btn": "#e8709a",
        "rank_up": "#4caf7d", "rank_down": "#cf3f24",
    },
    "fearless": {
        "bg": "#fff8dc", "card_bg": "#fffdf2", "border": "#f3d982", "text": "#332711",
        "muted": "#8b7334", "even_row": "#fff3c4", "region": "#a87909", "play_btn": "#d4a017",
        "rank_up": "#4caf7d", "rank_down": "#cf3f24",
    },
    "reputation": {
        "bg": "#f2f2f2", "card_bg": "#ffffff", "border": "#cfcfcf", "text": "#171717",
        "muted": "#6b6b6b", "even_row": "#ededed", "region": "#343434", "play_btn": "#111111",
        "rank_up": "#4caf7d", "rank_down": "#cf3f24",
    },
    "evermore": {
        "bg": "#f9efe5", "card_bg": "#fffaf4", "border": "#e7c7aa", "text": "#332418",
        "muted": "#856650", "even_row": "#f7e5d4", "region": "#a45c2a", "play_btn": "#9b6b3d",
        "rank_up": "#80a040", "rank_down": "#cf3f24",
    },
    "folklore": {
        "bg": "#f2f4f3", "card_bg": "#ffffff", "border": "#d4d9d7", "text": "#202524",
        "muted": "#697370", "even_row": "#e9eeec", "region": "#5e6a66", "play_btn": "#8f989d",
        "rank_up": "#6fb07f", "rank_down": "#cf3f24",
    },
    "1989": {
        "bg": "#eaf8ff", "card_bg": "#f8fdff", "border": "#bfe8fb", "text": "#152a34",
        "muted": "#5c7e8d", "even_row": "#dcf3ff", "region": "#1678a7", "play_btn": "#2aa8dc",
        "rank_up": "#4caf7d", "rank_down": "#cf3f24",
    },
    "red": {
        "bg": "#fff1ef", "card_bg": "#fffafa", "border": "#ffc9c2", "text": "#321817",
        "muted": "#8b5f5a", "even_row": "#ffe5e1", "region": "#b92722", "play_btn": "#b91f2f",
        "rank_up": "#4caf7d", "rank_down": "#cf3f24",
    },
    "speak_now": {
        "bg": "#f8efff", "card_bg": "#fffaff", "border": "#e3c5f5", "text": "#2c1937",
        "muted": "#7b5b8b", "even_row": "#f3e3fc", "region": "#7a36a2", "play_btn": "#8b4bb3",
        "rank_up": "#4caf7d", "rank_down": "#cf3f24",
    },
    "taylor_swift": {
        "bg": "#edf9f1", "card_bg": "#fbfffc", "border": "#bde7ca", "text": "#16291d",
        "muted": "#5d8068", "even_row": "#ddf3e5", "region": "#237c47", "play_btn": "#2e8f5f",
        "rank_up": "#4caf7d", "rank_down": "#cf3f24",
    },
    "apple": {
        "bg": "#f7f8fb", "card_bg": "#ffffff", "border": "#d7dce7", "text": "#1f2530",
        "muted": "#667085", "even_row": "#f1f4f9", "region": "#bf1d47", "play_btn": "#fa243c",
        "rank_up": "#248a55", "rank_down": "#c4352f",
    },
}

COUNTRY_NAMES = {
    "us": "United States", "gb": "United Kingdom", "fr": "France", "ca": "Canada",
    "au": "Australia", "de": "Germany", "jp": "Japan", "br": "Brazil", "mx": "Mexico",
    "it": "Italy", "es": "Spain",
}


def theme_key_for_album(album: str) -> str:
    value = (album or "").lower().strip()
    if "life of a showgirl" in value:
        return "showgirl"
    if "tortured poets" in value:
        return "ttpd"
    if "midnights" in value:
        return "midnights"
    if "evermore" in value:
        return "evermore"
    if "folklore" in value:
        return "folklore"
    if "lover" in value:
        return "lover"
    if "reputation" in value:
        return "reputation"
    if "1989" in value:
        return "1989"
    if "red" in value:
        return "red"
    if "speak now" in value:
        return "speak_now"
    if "fearless" in value:
        return "fearless"
    if "taylor swift" in value or "debut" in value:
        return "taylor_swift"
    return "apple"


def palette_for_album(album: str) -> dict[str, str]:
    return THEMES.get(theme_key_for_album(album), THEMES["apple"])


def country_label(code: str) -> str:
    return COUNTRY_NAMES.get((code or "").lower(), (code or "").upper())


def logo_data_uri() -> str:
    try:
        data = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        return f"data:image/gif;base64,{data}"
    except Exception:
        return ""
