from __future__ import annotations

import re
from datetime import date, timedelta


def get_scrape_date_str() -> str:
    return date.today().isoformat()


def get_stats_date_str(*, spotify_update_hour: int = 15) -> str:
    from datetime import datetime as _dt
    now = _dt.now()
    lag = 1 if now.hour >= spotify_update_hour else 2
    return (date.today() - timedelta(days=lag)).isoformat()


def get_previous_stats_date_str(stats_date: str) -> str:
    return (date.fromisoformat(stats_date) - timedelta(days=1)).isoformat()


def format_int(value: int | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,}".replace(",", " ")


def block_unneeded(route):
    request = route.request
    url = request.url.lower()
    resource_type = request.resource_type

    blocked_resource_types = {"media", "font", "image"}
    blocked_keywords = (
        "doubleclick",
        "googletagmanager",
        "google-analytics",
        "analytics",
        "facebook",
        "pixel",
        "ads",
        ".mp4",
        ".webm",
        ".mp3",
        ".wav",
        ".ogg",
        ".woff",
        ".woff2",
        ".ttf",
    )

    if resource_type in blocked_resource_types or any(x in url for x in blocked_keywords):
        route.abort()
    else:
        route.continue_()


def launch_browser(playwright, *, headless: bool = True):
    return playwright.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )
