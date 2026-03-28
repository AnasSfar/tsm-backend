#!/usr/bin/env python3
"""Logger partagé Fr + Global."""
import sys


class Logger:
    def __init__(self):
        self.lines = []

    def log(self, message=""):
        # Encode safely for Windows cp1252 terminals (emojis → ?)
        safe = str(message).encode(sys.stdout.encoding or "utf-8", errors="replace") \
                           .decode(sys.stdout.encoding or "utf-8", errors="replace")
        print(safe)
        self.lines.append(str(message))

    def save(self, filepath):
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))
