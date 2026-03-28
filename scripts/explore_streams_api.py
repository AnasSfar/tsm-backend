#!/usr/bin/env python3
"""
Script d'exploration — NE TOUCHE PAS la DB.

Objectif : trouver l'API interne Spotify qui retourne le stream count
           (play count) d'une track, en interceptant les requêtes réseau
           faites par open.spotify.com/track/{id}.

Usage :
    python scripts/explore_streams_api.py
    python scripts/explore_streams_api.py --url https://open.spotify.com/track/06AKEBrKUckW0KREUWRnvT
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# Session Spotify (on ne lit que les cookies, on n'écrit rien)
REPO_ROOT    = Path(__file__).resolve().parents[1]
SESSION_FILE = REPO_ROOT / "collectors/spotify/streams/tools/browser_cache"
# Fallback : session des charts (même compte)
SESSION_CHARTS = REPO_ROOT / "collectors/spotify/charts/global/tools/json/spotify_session.json"

# Track de test par défaut : Anti-Hero
DEFAULT_URL = "https://open.spotify.com/track/0V3wPSX9ygBnCm8psDIegu"


def intercept(track_url: str):
    """
    Ouvre la page track, intercepte TOUS les appels réseau non-triviaux,
    affiche ceux qui semblent contenir des données de streams/playcount.
    """
    print(f"\nOuverture : {track_url}")
    print("Interception des requêtes réseau…\n")

    interesting: list[dict] = []
    all_api_calls: list[tuple] = []

    def on_response(resp):
        try:
            url = resp.url
            status = resp.status
            ct = resp.headers.get("content-type", "")

            # Ignorer CSS, fonts, images, analytics
            rtype = resp.request.resource_type
            if rtype in ("stylesheet", "font", "image"):
                return
            if any(k in url for k in ("google-analytics", "googletagmanager",
                                       "doubleclick", "facebook", "sentry")):
                return

            # Garder les appels API (JSON ou inconnu)
            if "spotify" in url or "scdn.co" in url:
                all_api_calls.append((status, rtype, ct[:40], url))

            if "json" in ct and status == 200:
                try:
                    data = resp.json()
                    text = json.dumps(data)
                    keywords = ["playCount", "play_count", "streams", "monthlyListeners",
                                "listeners", "playcount", "totalPlays"]
                    found = [k for k in keywords if k.lower() in text.lower()]
                    if found:
                        # Capturer aussi la requête complète
                        req = resp.request
                        interesting.append({
                            "url":          url,
                            "keywords":     found,
                            "data":         data,
                            "req_method":   req.method,
                            "req_headers":  dict(req.headers),
                            "req_post":     req.post_data or "",
                        })
                except Exception:
                    pass
        except Exception:
            pass

    session_file = None
    if SESSION_CHARTS.exists():
        session_file = str(SESSION_CHARTS)

    p = sync_playwright().start()
    browser = None
    try:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx_kwargs = {
            "viewport":   {"width": 1280, "height": 800},
            "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/133.0.0.0 Safari/537.36"),
            "locale": "en-US",
        }
        if session_file:
            ctx_kwargs["storage_state"] = session_file

        ctx  = browser.new_context(**ctx_kwargs)
        page = ctx.new_page()
        page.on("response", on_response)

        page.goto(track_url, wait_until="domcontentloaded", timeout=30_000)

        # Attendre que la page soit bien chargée (play count apparaît après JS)
        print("Attente du chargement complet (10s)…")
        page.wait_for_timeout(10_000)

        # Essayer d'attendre l'apparition d'un chiffre de streams dans le DOM
        try:
            page.wait_for_function(
                """() => {
                    const text = document.body.innerText;
                    return /\\d{1,3}(,\\d{3})+/.test(text);
                }""",
                timeout=5_000,
            )
            print("Données numériques détectées dans le DOM.")
        except Exception:
            pass

        # Lire le body text pour chercher les streams
        try:
            body = page.locator("body").inner_text(timeout=3_000) or ""
            print(f"\nBody text (500 premiers chars) :\n{body[:500]}\n")
        except Exception:
            pass

        # Vérifier __NEXT_DATA__
        try:
            next_data_raw = page.evaluate(
                "() => document.getElementById('__NEXT_DATA__')?.textContent || ''"
            )
            if next_data_raw:
                nd = json.loads(next_data_raw)
                nd_text = json.dumps(nd)
                kw_found = [k for k in ["playCount", "play_count", "streams", "monthlyListeners"]
                            if k.lower() in nd_text.lower()]
                if kw_found:
                    print(f"__NEXT_DATA__ contient : {kw_found}")
                    # Trouver et afficher les valeurs
                    for kw in kw_found:
                        idx = nd_text.lower().find(kw.lower())
                        print(f"  {kw} context: …{nd_text[max(0,idx-20):idx+60]}…")
                else:
                    print("__NEXT_DATA__ : aucun keyword de stream trouvé")
        except Exception as e:
            print(f"__NEXT_DATA__ : erreur ({e})")

    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    # ── Résultats ─────────────────────────────────────────────────────────────

    print("\n" + "═" * 60)
    print("TOUS LES APPELS API SPOTIFY INTERCEPTÉS :")
    print("═" * 60)
    for status, rtype, ct, url in all_api_calls:
        print(f"  {status} [{rtype:8}] {url[:100]}")

    print("\n" + "═" * 60)
    print(f"APPELS AVEC KEYWORDS STREAM ({len(interesting)} trouvés) :")
    print("═" * 60)

    if not interesting:
        print("  Aucun appel JSON avec des keywords de stream trouvé.")
    else:
        # Dédupliquer par URL + post_data
        seen = set()
        for item in interesting:
            key = (item["url"], item["req_post"][:100])
            if key in seen:
                continue
            seen.add(key)

            print(f"\n  URL    : {item['url']}")
            print(f"  Method : {item['req_method']}")
            print(f"  Keywords : {item['keywords']}")

            # Extraire les valeurs de playcount
            text = json.dumps(item["data"], ensure_ascii=False)
            for kw in ["playcount", "playCount"]:
                idx = text.lower().find(kw.lower())
                if idx >= 0:
                    print(f"  → {text[max(0,idx-5):idx+60]}")
                    break

            # Headers de la requête (seulement les utiles)
            useful_headers = {k: v for k, v in item["req_headers"].items()
                              if k.lower() in ("authorization", "client-token",
                                               "content-type", "accept",
                                               "spotify-app-version", "app-platform")}
            print(f"  Req headers : {json.dumps(useful_headers, indent=4)}")

            # Body de la requête (GraphQL)
            if item["req_post"]:
                try:
                    body = json.loads(item["req_post"])
                    op = body.get("operationName", "")
                    variables = body.get("variables", {})
                    print(f"  GraphQL operationName : {op}")
                    print(f"  GraphQL variables     : {json.dumps(variables, indent=4)}")
                    # Sauvegarder la requête complète dans un fichier pour réutilisation
                    # Sauvegarder toutes les opérations, getTrack en priorité
                    out = Path(__file__).parent / f"explore_streams_api_{op or 'unknown'}.json"
                    out.write_text(json.dumps({
                        "url":      item["url"],
                        "headers":  useful_headers,
                        "body":     body,
                        "sample_response_playcount": text[max(0, text.lower().find("playcount")-5):
                                                         text.lower().find("playcount")+80],
                    }, indent=2, ensure_ascii=False), encoding="utf-8")
                    print(f"\n  Sauvegardé : {out}")
                except Exception:
                    print(f"  Req body (raw) : {item['req_post'][:300]}")


def main():
    parser = argparse.ArgumentParser(
        description="Explore l'API interne Spotify pour trouver les stream counts."
    )
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"URL de la track à tester (défaut: Anti-Hero)")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    intercept(args.url)


if __name__ == "__main__":
    main()
