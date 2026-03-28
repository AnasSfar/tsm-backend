#!/usr/bin/env python3
"""Notifications mobile via ntfy.sh."""
import urllib.request
import urllib.error


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

        with urllib.request.urlopen(req, timeout=10):
            pass

    except Exception as e:
        print(f"[NOTIFY] Echec ntfy.sh: {e}", flush=True)
