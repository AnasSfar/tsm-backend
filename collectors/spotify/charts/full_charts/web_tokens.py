#!/usr/bin/env python3
"""Pool of Spotify web-player tokens for enrich.py (GraphQL getTrack), kept
apart from the streams pipeline.

Why not streams' TokenManager: it shares .token_cache.json and the streams
account (spotify_session.json) with update_streams.py, and on a 401 it deletes
that cache and toggles WARP — in the middle of a streams run. Here:

- slot "anon"  : anonymous web-player token (fresh browser, no cookies). Spotify
                 hands out ONE anonymous token per IP, so only one such slot.
- slot "<file>": one per charts account spotify_session*.json, EXCEPT the
                 streams one (spotify_session.json).

Each slot has its own pacing interval (x1.5 on each 429, capped) and its own
block on 429 (Retry-After); requests go to the free slot that can send soonest.
Tokens are cached in snapshots/spotify_charts_full/meta/web_tokens.json
(gitignored); a 401 re-captures that slot only (Playwright, no WARP toggle).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from common import RAW_ROOT, ROOT

SESSION_DIR = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json"
STREAMS_SESSION = SESSION_DIR / "spotify_session.json"  # = spotify_api._SESSION_FILE (streams account)
CACHE_PATH = RAW_ROOT / "meta" / "web_tokens.json"
CAPTURE_URL = "https://open.spotify.com/track/0V3wPSX9ygBnCm8psDIegu"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
MAX_INTERVAL = 2.0


def _sources() -> list[tuple[str, Path | None]]:
    accounts = sorted(p for p in SESSION_DIR.glob("spotify_session*.json") if p.resolve() != STREAMS_SESSION.resolve())
    return [("anon", None)] + [(p.stem, p) for p in accounts]


def capture(sources: list[tuple[str, Path | None]]) -> dict[str, dict]:
    """{name: {bearer, client_token, app_version}} for each source that worked."""
    from playwright.sync_api import sync_playwright

    out: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            for name, state in sources:
                tok: dict = {}

                def on_request(req, tok=tok):
                    if "api-partner.spotify.com" in req.url and not tok.get("bearer"):
                        auth = req.headers.get("authorization", "")
                        ct = req.headers.get("client-token", "")
                        if auth.startswith("Bearer ") and ct:
                            tok.update(bearer=auth[7:], client_token=ct,
                                       app_version=req.headers.get("spotify-app-version", ""))

                ctx = browser.new_context(user_agent=UA, **({"storage_state": str(state)} if state else {}))
                try:
                    page = ctx.new_page()
                    page.on("request", on_request)
                    page.goto(CAPTURE_URL, wait_until="commit", timeout=30_000)
                    deadline = time.time() + 30
                    while not tok.get("bearer") and time.time() < deadline:
                        page.wait_for_timeout(300)
                    if "accounts.spotify.com" in page.url or "login" in page.url:
                        print(f"[TOKENS] {name} : session expiree (redirect login) - ignore")
                    elif tok.get("bearer"):
                        out[name] = dict(tok)
                    else:
                        print(f"[TOKENS] {name} : aucun token intercepte - ignore")
                except Exception as exc:
                    print(f"[TOKENS] {name} : capture impossible ({exc!s:.150})")
                finally:
                    ctx.close()
        finally:
            browser.close()
    return out


class TokenPool:
    def __init__(self, interval: float) -> None:
        self.base_interval = interval
        self.lock = threading.Lock()
        self.recapture_lock = threading.Lock()
        self.sources = dict(_sources())
        cached = {}
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            pass
        tokens = {n: t for n, t in cached.items() if n in self.sources and t.get("bearer")}
        missing = [(n, s) for n, s in self.sources.items() if n not in tokens]
        if missing:
            tokens.update(capture(missing))
            self._save(tokens)
        if not tokens:
            raise SystemExit("[TOKENS] aucun token web Spotify (anonyme ni compte) - albums impossibles")
        # Ignore a duplicate bearer (an account whose cookies fell back to the anonymous token).
        seen: set[str] = set()
        self.slots: list[dict] = []
        for name, tok in tokens.items():
            if tok["bearer"] in seen:
                continue
            seen.add(tok["bearer"])
            self.slots.append({"name": name, "tok": tok, "interval": interval, "next_at": 0.0, "blocked_until": 0.0})
        print(f"[TOKENS] {len(self.slots)} token(s) : {', '.join(s['name'] for s in self.slots)} "
              f"({1 / interval:.0f} req/s max chacun)")

    def _save(self, tokens: dict) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(tokens), encoding="utf-8")

    def acquire(self) -> tuple[int, dict]:
        """Wait for the slot that can send soonest; returns (slot index, token)."""
        while True:
            with self.lock:
                now = time.monotonic()
                i = min(range(len(self.slots)),
                        key=lambda k: max(self.slots[k]["next_at"], self.slots[k]["blocked_until"]))
                slot = self.slots[i]
                ready_at = max(slot["next_at"], slot["blocked_until"])
                if ready_at <= now:
                    slot["next_at"] = now + slot["interval"]
                    return i, dict(slot["tok"])
            time.sleep(min(ready_at - now, 1.0))

    def mark_429(self, i: int, retry_after: float) -> None:
        with self.lock:
            slot = self.slots[i]
            slot["blocked_until"] = time.monotonic() + retry_after
            slot["interval"] = min(MAX_INTERVAL, slot["interval"] * 1.5)
            print(f"[TOKENS] 429 {slot['name']} - pause {retry_after:.0f}s, rythme {1 / slot['interval']:.1f} req/s")

    def mark_ok(self, i: int) -> None:
        with self.lock:  # slowly recover the base rate after a 429
            slot = self.slots[i]
            slot["interval"] = max(self.base_interval, slot["interval"] * 0.995)

    def refresh(self, i: int, bearer: str) -> None:
        """401 on slot i: re-capture it (once, even if several workers saw the 401)."""
        with self.recapture_lock:
            slot = self.slots[i]
            if slot["tok"]["bearer"] != bearer:
                return  # another worker already refreshed it
            name = slot["name"]
            print(f"[TOKENS] 401 {name} - nouvelle capture")
            new = capture([(name, self.sources[name])]).get(name)
            if not new:
                with self.lock:  # dead source (expired session): park it for an hour
                    slot["blocked_until"] = time.monotonic() + 3600
                return
            with self.lock:
                slot["tok"] = new
            try:
                cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                cached = {}
            cached[name] = new
            self._save(cached)
