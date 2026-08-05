from __future__ import annotations

from pathlib import Path

DEFAULT_ACCOUNT = "tsm"
ACCOUNT_HANDLES = {
    "tsm": "@swiftiescharts",
    "flame": "@theflameofanas",
}


def spotify_charts_global_session(repo_root: Path) -> Path:
    return Path(repo_root) / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "twitter_session.json"


def spotify_charts_fr_session(repo_root: Path) -> Path:
    return Path(repo_root) / "collectors" / "spotify" / "charts" / "fr" / "tools" / "json" / "twitter_session.json"


def default_twitter_session(repo_root: Path) -> Path:
    return spotify_charts_global_session(repo_root)


def twitter_account_config(repo_root: Path) -> dict[str, dict[str, object]]:
    root = Path(repo_root)
    return {
        "flame": {
            "handle": ACCOUNT_HANDLES["flame"],
            "session": spotify_charts_fr_session(root),
        },
        "tsm": {
            "handle": ACCOUNT_HANDLES["tsm"],
            "session": spotify_charts_global_session(root),
        },
    }
