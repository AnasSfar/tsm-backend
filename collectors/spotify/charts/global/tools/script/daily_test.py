#!/usr/bin/env python3
"""
daily_test.py - Global (DRY RUN — sans publication Twitter)

Identique à daily.py mais ne poste JAMAIS sur Twitter.
Génère tweet.txt + chart_image.png et ouvre l'image pour inspection visuelle.

Usage : python daily_test.py [YYYY-MM-DD]
"""
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
CHART_ID = "regional-global-daily"
SPOTIFY_SESSION       = ROOT / "spotify_session.json"
FILTER_SCRIPT         = ROOT / "filter.py"
REBUILD_SCRIPT        = ROOT / "rebuild.py"
GENERATE_IMAGE_SCRIPT = ROOT / "generate_chart_image.py"

RETRY_SECONDS = 60


def log(level: str, message: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}", flush=True)


def chart_date() -> date:
    if len(sys.argv) > 1:
        try:
            return datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            log("ERROR", f"Date invalide '{sys.argv[1]}', format attendu : YYYY-MM-DD")
            sys.exit(1)
    return datetime.now().date() - timedelta(days=1)


def scraped_csv_path(d: date) -> Path:
    return ROOT / str(d.year) / f"{d.month:02d}" / str(d) / f"{CHART_ID}-{d}.csv"


def scrape_exists(d: date) -> bool:
    p = scraped_csv_path(d)
    exists = p.exists()
    log("DEBUG", f"CSV scrape pour {d}: {'trouvé' if exists else 'absent'} -> {p}")
    return exists


def ts_chart_exists(d: date) -> bool:
    p = ROOT / str(d.year) / f"{d.month:02d}" / str(d) / f"ts_chart_{d}.json"
    return p.exists()


def page_available(d: date) -> bool:
    url = f"https://charts.spotify.com/charts/view/{CHART_ID}/{d}"
    log("CHECK", f"Ouverture {url}")

    with sync_playwright() as p:
        browser = None
        context = None
        try:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )

            if not SPOTIFY_SESSION.exists():
                log("ERROR", f"Session Spotify introuvable: {SPOTIFY_SESSION}")
                return False

            context = browser.new_context(
                storage_state=str(SPOTIFY_SESSION),
                viewport={"width": 1400, "height": 2000},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/133.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )

            page = context.new_page()
            page.set_default_navigation_timeout(60_000)
            page.set_default_timeout(60_000)

            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(6000)

            current_url = page.url.lower()
            log("CHECK", f"URL finale: {page.url}")

            if "login" in current_url or "accounts.spotify.com" in current_url:
                log("CHECK", "Session Spotify expirée ou non connectée")
                return False

            body_text       = (page.locator("body").inner_text() or "").strip()
            body_text_lower = body_text.lower()

            has_streams      = bool(re.search(r"\b\d{1,3},\d{3},\d{3}\b", body_text))
            has_chart_header = "track" in body_text_lower and "streams" in body_text_lower
            has_rank_line    = bool(re.search(r"(?m)^\s*1\s*$", body_text))

            available = has_streams and has_chart_header and has_rank_line
            log("CHECK", f"Page exploitable: {'oui' if available else 'non'}")
            return available

        except Exception as e:
            log("CHECK", f"Erreur: {e}")
            return False
        finally:
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass


def run_filter(d: date) -> str | None:
    log("STEP", "Lancement de filter.py")
    result = subprocess.run(
        [sys.executable, str(FILTER_SCRIPT), str(d)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, flush=True)

    log("STEP", f"filter.py terminé avec code {result.returncode}")
    if result.returncode != 0:
        log("ERROR", "filter.py a échoué")
        return None

    tweet_path = ROOT / str(d.year) / f"{d.month:02d}" / str(d) / "tweet.txt"
    if not tweet_path.exists():
        log("ERROR", "tweet.txt introuvable après filter.py")
        return None

    content = tweet_path.read_text(encoding="utf-8")
    log("INFO", f"tweet.txt chargé ({len(content)} caractères)")
    return content


def run_image_gen(d: date) -> Path | None:
    image_path = ROOT / str(d.year) / f"{d.month:02d}" / str(d) / "chart_image.png"
    log("STEP", "Génération de l'image du chart")
    result = subprocess.run(
        [sys.executable, str(GENERATE_IMAGE_SCRIPT), str(d)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, flush=True)
    if result.returncode != 0:
        log("ERROR", "Génération d'image échouée")
        return None
    return image_path if image_path.exists() else None


def open_image(path: Path):
    """Ouvre l'image avec le visualiseur par défaut du système."""
    import os
    try:
        os.startfile(str(path))          # Windows
    except AttributeError:
        import subprocess as sp
        try:
            sp.run(["open", str(path)])  # macOS
        except Exception:
            sp.run(["xdg-open", str(path)])  # Linux


def main():
    d = chart_date()

    log("INFO", f"[DRY RUN] Date cible : {d}")
    print(f"\n{'=' * 50}\n  daily_test.py (Global) — DRY RUN — {d}\n{'=' * 50}\n", flush=True)

    # Si ts_chart_{d}.json existe déjà, on saute le scrape et génère juste l'image
    if ts_chart_exists(d):
        log("INFO", f"ts_chart_{d}.json trouvé — génération image directe (pas de re-scrape)")
        image_path = run_image_gen(d)
        if image_path:
            log("INFO", f"Image prête : {image_path}")
            open_image(image_path)
        else:
            log("ERROR", "Génération d'image échouée")
            sys.exit(1)
        return

    # Sinon, pipeline complet (scrape → filter → image) sans post Twitter
    if not scrape_exists(d):
        log("INFO", f"En attente de la page Spotify Charts pour {d}")
        attempt = 1
        while True:
            log("WAIT", f"Vérification tentative #{attempt} pour {d}")
            if page_available(d):
                log("INFO", f"Page de {d} détectée")
                break
            log("WAIT", f"Page {d} pas encore dispo, retry dans {RETRY_SECONDS // 60} min")
            attempt += 1
            time.sleep(RETRY_SECONDS)
    else:
        log("INFO", "Scrape déjà présent, passage direct au traitement")

    tweet_content = run_filter(d)
    if not tweet_content:
        log("ERROR", "Traitement échoué")
        sys.exit(1)

    print(f"\n--- Tweet (non publié) ---\n{tweet_content}\n---\n", flush=True)

    image_path = run_image_gen(d)
    if image_path:
        log("INFO", f"Image prête : {image_path}")
        open_image(image_path)
    else:
        log("WARN", "Image non générée")

    log("INFO", "[DRY RUN] Terminé — rien n'a été posté sur Twitter")


if __name__ == "__main__":
    main()
