#!/usr/bin/env python3
"""Notifications mobile via ntfy.sh."""
import urllib.request
import urllib.error
import os


NTFY_TIMEOUT_SECONDS = 30

_DEAD_LOCAL_PROXY_VALUES = {
    "http://127.0.0.1:9",
    "https://127.0.0.1:9",
}


def _sanitize_dead_local_proxy_env() -> list[str]:
    """Drop sandbox deny-port proxy vars before calling ntfy."""
    removed: list[str] = []
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        value = os.environ.get(name, "").strip().lower()
        if value in _DEAD_LOCAL_PROXY_VALUES:
            os.environ.pop(name, None)
            removed.append(name)
    return removed


def send(topic: str, message: str, title: str = "", tags: str = "", priority: str = "default"):
    """
    Envoie une notification via ntfy.sh.

    Args:
        topic:    Ton topic ntfy (ex: 'taylormuseum-fr')
        message:  Corps de la notification
        title:    Titre (optionnel)
        tags:     Emoji/tags ntfy séparés par virgule (ex: 'tada,musical_note')
        priority: 'low', 'default', 'high', 'urgent'
    """
    if not topic:
        return

    try:
        removed_proxy_vars = _sanitize_dead_local_proxy_env()
        if removed_proxy_vars:
            print(
                "[NOTIFY] Ignored dead local proxy env var(s): "
                + ", ".join(sorted(removed_proxy_vars)),
                flush=True,
            )

        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            method="POST",
        )
        if title:
            req.add_header("Title", title)
        if tags:
            req.add_header("Tags", tags)
        if priority and priority != "default":
            req.add_header("Priority", priority)

        if removed_proxy_vars:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=NTFY_TIMEOUT_SECONDS):
                pass
        else:
            with urllib.request.urlopen(req, timeout=NTFY_TIMEOUT_SECONDS):
                pass

    except Exception as e:
        print(f"[NOTIFY] Echec ntfy.sh: {e}", flush=True)
