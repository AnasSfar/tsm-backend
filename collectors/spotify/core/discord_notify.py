"""Pont vers notifiers/discord (module Discord independant des collectors).

    from core.discord_notify import discord_send
    discord_send("spotify-charts", [(texte, image)], kind="global_daily", key="global_daily_2026-09-25")

Jamais d'exception ; no-op si le module ou le webhook est absent.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def discord_send(channel, posts, *, kind=None, key=None, priority=None, thread=None) -> bool:
    """`thread` = region code (global, us, gb, fr, ca...): posts into that
    region's Discord thread when one is configured, else the main channel."""
    try:
        from notifiers.discord import send
        return send(channel, posts, kind=kind, key=key, priority=priority, thread=thread)
    except Exception as exc:
        print(f"[DISCORD] echec (ignore): {exc}", flush=True)
        return False
