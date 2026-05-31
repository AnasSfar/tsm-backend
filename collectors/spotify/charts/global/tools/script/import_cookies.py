#!/usr/bin/env python3
"""
Convertit un export Cookie-Editor (JSON) en session Playwright spotify_session.json.

Usage:
    1. Connecte-toi sur charts.spotify.com dans ton vrai Chrome
    2. Installe l'extension Cookie-Editor
    3. Sur charts.spotify.com : Cookie-Editor → Export → JSON → copie
    4. Colle dans un fichier, ex: cookies_export.json
    5. Lance: python import_cookies.py cookies_export.json
       ou:    python import_cookies.py cookies_export.json --output spotify_session_2.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

_DEFAULT_SESSION = Path(__file__).resolve().parents[2] / "json" / "spotify_session.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


def convert_cookie(c: dict) -> dict:
    out: dict = {
        "name":   c.get("name", ""),
        "value":  c.get("value", ""),
        "domain": c.get("domain", ""),
        "path":   c.get("path", "/"),
        "secure": c.get("secure", False),
        "httpOnly": c.get("httpOnly", False),
        "sameSite": c.get("sameSite", "Lax"),
    }
    exp = c.get("expirationDate") or c.get("expires")
    if exp:
        out["expires"] = int(exp)
    return out


def verify_token(cookies: list[dict]) -> str | None:
    jar = {c["name"]: c["value"] for c in cookies if c.get("name") and c.get("value")}
    try:
        resp = requests.get(
            "https://open.spotify.com/get_access_token",
            params={"reason": "transport", "productType": "web_player"},
            headers={"Accept": "application/json", "User-Agent": UA},
            cookies=jar,
            timeout=15,
        )
        token = resp.json().get("accessToken", "") if resp.ok else ""
        return token or None
    except Exception as exc:
        print(f"[WARN] Vérification token: {exc}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Fichier JSON exporté par Cookie-Editor")
    parser.add_argument("--output", "-o", type=Path, default=_DEFAULT_SESSION)
    args = parser.parse_args()

    if not args.input.exists():
        print(f"[FAIL] Fichier introuvable: {args.input}")
        return 1

    raw = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        print("[FAIL] Format inattendu — Cookie-Editor exporte une liste JSON")
        return 1

    cookies = [convert_cookie(c) for c in raw if c.get("name") and c.get("value")]
    print(f"[INFO] {len(cookies)} cookies convertis")

    session = {"cookies": cookies, "origins": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Session sauvegardée → {args.output}")

    print("[CHECK] Vérification du token Spotify...")
    token = verify_token(cookies)
    if token:
        print(f"[OK] Token valide: {token[:30]}...")
        return 0
    else:
        print("[FAIL] Token invalide — vérifie que tu étais bien connecté sur charts.spotify.com")
        return 1


if __name__ == "__main__":
    sys.exit(main())
