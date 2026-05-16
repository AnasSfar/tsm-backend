#!/usr/bin/env python3
"""
Relogin Spotify Charts via Playwright (headful) et sauvegarde les cookies dans spotify_session.json.
Usage: python refresh_session.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

SESSION_PATH = Path(__file__).resolve().parents[2] / "json" / "spotify_session.json"
CHARTS_URL = "https://charts.spotify.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


def _verify_token(session_path: Path) -> bool:
    import requests

    try:
        data = json.loads(session_path.read_text(encoding="utf-8-sig"))
        cookies = {c["name"]: c["value"] for c in data.get("cookies", []) if c.get("name") and c.get("value")}
        resp = requests.get(
            "https://open.spotify.com/get_access_token",
            params={"reason": "transport", "productType": "web_player"},
            headers={"Accept": "application/json", "User-Agent": UA},
            cookies=cookies,
            timeout=15,
        )
        token = resp.json().get("accessToken", "") if resp.ok else ""
        if token:
            print(f"[OK] token valide: {token[:30]}...")
            return True
        print(f"[FAIL] HTTP {resp.status_code} — token absent")
        return False
    except Exception as e:
        print(f"[FAIL] verification: {e}")
        return False


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[FAIL] playwright non installe — pip install playwright && playwright install chromium")
        return 1

    print(f"[INFO] Session sera sauvegardee dans: {SESSION_PATH}")
    print("[INFO] Une fenetre de navigateur va s'ouvrir.")
    print("[INFO] Connecte-toi a Spotify, puis reviens ici et appuie sur Entree.\n")

    with sync_playwright() as p:
        # Essaie d'abord le vrai Chrome installé (moins détectable), sinon Chromium
        launch_kwargs = dict(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        try:
            browser = p.chromium.launch(channel="chrome", **launch_kwargs)
            print("[INFO] Utilisation de Chrome installé")
        except Exception:
            browser = p.chromium.launch(**launch_kwargs)
            print("[INFO] Chrome non trouvé, utilisation de Chromium")

        context = browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 800},
            # Masque navigator.webdriver
            java_script_enabled=True,
        )
        # Injecte du JS pour cacher les traces Playwright avant chaque page
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)
        page = context.new_page()
        page.goto(CHARTS_URL, wait_until="domcontentloaded", timeout=30_000)

        print("[WAIT] Connecte-toi dans le navigateur...")
        input("       Appuie sur Entree une fois connecte > ")

        # Sauvegarde la session au format Playwright (inclut cookies + localStorage)
        storage = context.storage_state()
        SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        SESSION_PATH.write_text(json.dumps(storage, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] Session sauvegardee ({len(storage.get('cookies', []))} cookies)")
        browser.close()

    print("[CHECK] Verification du token...")
    ok = _verify_token(SESSION_PATH)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
