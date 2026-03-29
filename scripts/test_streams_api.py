#!/usr/bin/env python3
"""
Test de l'API GraphQL Spotify pour récupérer les streams de toutes les tracks.
NE TOUCHE PAS à la DB — affiche uniquement les résultats.

Usage :
    python scripts/test_streams_api.py
    python scripts/test_streams_api.py --workers 20
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

import requests
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT    = Path(__file__).resolve().parents[1]
SONGS_JSON   = REPO_ROOT / "db/discography/songs.json"
SESSION_FILE = REPO_ROOT / "collectors/spotify/charts/global/tools/json/spotify_session.json"

GRAPHQL_URL  = "https://api-partner.spotify.com/pathfinder/v2/query"
GETTRACK_HASH = "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294"
APP_VERSION   = "1.2.87.30.gc764ebf1"


# ── Tokens ───────────────────────────────────────────────────────────────────

def get_tokens() -> dict:
    """
    Ouvre Playwright, charge open.spotify.com, capture Bearer + client-token.
    Retourne {"bearer": "...", "client_token": "..."}
    """
    print("Récupération des tokens via Playwright…")
    tokens: dict = {}

    def on_request(req):
        url = req.url
        if "api-partner.spotify.com" in url and not tokens.get("bearer"):
            auth = req.headers.get("authorization", "")
            ct   = req.headers.get("client-token", "")
            if auth.startswith("Bearer ") and ct:
                tokens["bearer"]       = auth[7:]
                tokens["client_token"] = ct

    p = sync_playwright().start()
    browser = None
    try:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            storage_state=str(SESSION_FILE),
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()
        page.on("request", on_request)
        # Charger une track quelconque pour déclencher les deux tokens
        page.goto("https://open.spotify.com/track/0V3wPSX9ygBnCm8psDIegu",
                  wait_until="domcontentloaded", timeout=30_000)
        deadline = time.time() + 20
        while not tokens.get("bearer") and time.time() < deadline:
            page.wait_for_timeout(300)
    finally:
        try: browser.close()
        except Exception: pass
        try: p.stop()
        except Exception: pass

    if not tokens.get("bearer"):
        raise RuntimeError("Tokens non capturés — vérifiez spotify_session.json")

    print(f"Bearer     : {tokens['bearer'][:30]}…")
    print(f"ClientToken: {tokens['client_token'][:30]}…")
    return tokens


# ── API call ─────────────────────────────────────────────────────────────────

def fetch_playcount(track_id: str, tokens: dict, session: requests.Session) -> int | None:
    body = {
        "variables":    {"uri": f"spotify:track:{track_id}"},
        "operationName": "getTrack",
        "extensions":   {
            "persistedQuery": {
                "version":    1,
                "sha256Hash": GETTRACK_HASH,
            }
        },
    }
    headers = {
        "Authorization":        f"Bearer {tokens['bearer']}",
        "client-token":         tokens["client_token"],
        "spotify-app-version":  APP_VERSION,
        "app-platform":         "WebPlayer",
        "Accept":               "application/json",
        "Content-Type":         "application/json;charset=UTF-8",
        "Origin":               "https://open.spotify.com",
        "Referer":              "https://open.spotify.com/",
        "User-Agent":           ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                 "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"),
    }
    try:
        resp = session.post(GRAPHQL_URL, json=body, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # Naviguer dans la réponse GraphQL
            track_union = (data.get("data") or {}).get("trackUnion") or {}
            pc = track_union.get("playcount")
            if pc is not None:
                return int(pc)
            # Fallback : chercher playcount n'importe où
            text = json.dumps(data)
            m = re.search(r'"playcount":\s*"(\d+)"', text)
            return int(m.group(1)) if m else None
        elif resp.status_code == 401:
            return "TOKEN_EXPIRED"
        else:
            return None
    except Exception:
        return None


# ── Chargement des tracks ────────────────────────────────────────────────────

def load_tracks() -> list[tuple[str, str]]:
    songs = json.loads(SONGS_JSON.read_text(encoding="utf-8"))
    tracks = []
    for section in songs:
        for t in section.get("tracks", []):
            url = t.get("url", "")
            m   = re.search(r"/track/([A-Za-z0-9]+)", url)
            if m:
                tracks.append((t["title"], m.group(1)))
    return tracks


# ── Workers ──────────────────────────────────────────────────────────────────

def worker(
    worker_id: int,
    tokens: dict,
    queue: Queue,
    results: dict,
    lock: threading.Lock,
    errors: list,
):
    session = requests.Session()
    try:
        while True:
            try:
                title, track_id = queue.get_nowait()
            except Empty:
                break

            pc = fetch_playcount(track_id, tokens, session)

            with lock:
                if pc == "TOKEN_EXPIRED":
                    errors.append("TOKEN_EXPIRED")
                    queue.task_done()
                    break
                results[title] = pc
                n = len(results)
                if pc is not None:
                    print(f"  [{n:3}] {title[:45]:45} {pc:>15,}")
                else:
                    print(f"  [{n:3}] {title[:45]:45} {'N/A':>15}")

            queue.task_done()
    finally:
        session.close()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    tracks = load_tracks()
    print(f"\n{len(tracks)} tracks trouvées dans songs.json\n")

    tokens = get_tokens()
    print()

    queue   = Queue()
    results = {}
    lock    = threading.Lock()
    errors  = []

    for t in tracks:
        queue.put(t)

    start = time.time()
    print(f"Démarrage avec {args.workers} workers…\n")
    print(f"  {'Track':45} {'Streams':>15}")
    print(f"  {'-'*45} {'-'*15}")

    threads = [
        threading.Thread(
            target=worker,
            args=(i, tokens, queue, results, lock, errors),
            daemon=True,
        )
        for i in range(args.workers)
    ]
    for t in threads:
        t.start()

    queue.join()

    elapsed = time.time() - start
    ok      = sum(1 for v in results.values() if isinstance(v, int))
    total   = sum(v for v in results.values() if isinstance(v, int))

    print(f"\n{'═'*63}")
    print(f"  {ok}/{len(tracks)} tracks récupérées en {elapsed:.1f}s "
          f"({len(tracks)/elapsed:.1f} tracks/s)")
    if errors:
        print(f"  ⚠ Token expiré pendant le run")
    print(f"\n  Top 10 :")
    top = sorted(
        [(t, v) for t, v in results.items() if isinstance(v, int)],
        key=lambda x: -x[1]
    )[:10]
    for title, pc in top:
        print(f"    {title[:45]:45} {pc:>15,}")


if __name__ == "__main__":
    main()
