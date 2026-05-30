#!/usr/bin/env python3
"""
daily.py - Global
Scrape la page Spotify Charts, filtre TS, met à jour ts_history, et poste le tweet.

Usage :
    python daily.py [--force] [YYYY-MM-DD]

Logique :
- cherche toutes les dates non postées des 7 derniers jours
- commence par la plus ancienne
- lance filter.py pour chaque date manquante
- génère toujours une image pour une seule date
- si image failed alors ne poste pas le thread
- poste sur Twitter

Options :
  --force   Supprime le posted.lock de la date cible et relance le pipeline complet
            (y compris filter même si les données existent déjà). Sans date explicite,
            cible hier.
    --no-post Exécute tout le pipeline mais ignore la publication Twitter.
"""

from __future__ import annotations

import re
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.data_paths import first_existing, legacy_spotify_chart_dir, spotify_chart_dir
from core.notify import send as notify
from core.twitter import post_thread, post_with_image, split_tweets

ROOT = Path(__file__).parent
_REPO_ROOT = ROOT.parents[3]

CHART_ID = "regional-global-daily"
US_CHART_ID = "regional-us-daily"
TWITTER_SESSION = ROOT / "tools/json/twitter_session.json"
SPOTIFY_SESSION = ROOT / "tools/json/spotify_session.json"
FILTER_SCRIPT = ROOT / "tools/script/filter.py"
GENERATE_IMAGE_SCRIPT = ROOT / "tools/script/generate_chart_image.py"
MIGRATE_SCRIPT = ROOT / "tools/script/migrate_charts_to_csv.py"

sys.path.insert(0, str(ROOT / "tools" / "script"))
from git_ops import git_commit_and_push, migrate_archive_csv
try:
    from config import NTFY_TOPIC
except Exception:
    NTFY_TOPIC = ""


RETRY_SECONDS = 60
CUTOFF_HOUR = 15
CUTOFF_MINUTE = 30
LOOKBACK_DAYS = 7
PAGE_TIMEOUT_MS = 120_000
POST_GOTO_WAIT_MS = 6000
ENABLE_GLOBAL_US_COMBINED_IMAGE = False

_SCRIPT_START = datetime.now()


def log(level: str, message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}", flush=True)


def lock_path(d: date) -> Path:
    return spotify_chart_dir("global", d) / "posted.lock"


def updated_lock_path(d: date) -> Path:
    return spotify_chart_dir("global", d) / "updated.lock"


def tweet_path(d: date) -> Path:
    return first_existing(
        spotify_chart_dir("global", d) / "tweet.txt",
        legacy_spotify_chart_dir("global", d) / "tweet.txt",
    )


def chart_csv_path(d: date) -> Path:
    return first_existing(
        spotify_chart_dir("global", d) / "ts_all_songs.csv",
        legacy_spotify_chart_dir("global", d) / "ts_all_songs.csv",
    )


def no_ts_lock_path(d: date) -> Path:
    return first_existing(
        spotify_chart_dir("global", d) / "no_ts.lock",
        legacy_spotify_chart_dir("global", d) / "no_ts.lock",
    )


def already_posted(d: date) -> bool:
    exists = lock_path(d).exists()
    log("DEBUG", f"posted.lock pour {d}: {'oui' if exists else 'non'}")
    return exists


def mark_posted(d: date) -> None:
    p = lock_path(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    log("INFO", f"posted.lock créé: {p}")


def mark_updated(d: date) -> None:
    p = updated_lock_path(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    log("INFO", f"updated.lock créé: {p}")


def cleanup_tweet_files(dates: list[date]) -> None:
    for d in dates:
        tp = tweet_path(d)
        if tp.exists():
            try:
                tp.unlink()
                log("INFO", f"tweet.txt supprimé pour {d}")
            except Exception as e:
                log("WARN", f"Impossible de supprimer tweet.txt pour {d}: {e}")

    twitter_post = ROOT / "twitter_post.txt"
    if twitter_post.exists():
        try:
            twitter_post.unlink()
            log("INFO", "twitter_post.txt supprimé")
        except Exception as e:
            log("WARN", f"Impossible de supprimer twitter_post.txt: {e}")


def chart_already_processed(d: date) -> bool:
    processed = chart_csv_path(d).exists() or no_ts_lock_path(d).exists()
    log("DEBUG", f"chart déjà traité pour {d}: {'oui' if processed else 'non'}")
    return processed


def get_unposted_dates() -> list[date]:
    today = date.today()
    unposted = [
        today - timedelta(days=i)
        for i in range(1, LOOKBACK_DAYS + 1)
        if not already_posted(today - timedelta(days=i))
    ]
    unposted.sort()
    return unposted[:1]


def past_cutoff() -> bool:
    now = datetime.now()
    return (
        now.date() > _SCRIPT_START.date()
        and (
            now.hour > CUTOFF_HOUR
            or (now.hour == CUTOFF_HOUR and now.minute >= CUTOFF_MINUTE)
        )
    )


def extract_date_from_url(url: str) -> date | None:
    match = re.search(r"/(\d{4}-\d{2}-\d{2})(?:[/?#]|$)", url or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def try_extract_chart_date_from_page(page) -> date | None:
    try:
        body_text = (page.locator("body").inner_text(timeout=5000) or "").strip()
    except Exception:
        body_text = ""

    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b[A-Z][a-z]+ \d{1,2}, \d{4}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, body_text)
        if not match:
            continue
        value = match.group(0)

        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                pass

        try:
            return datetime.strptime(value, "%B %d, %Y").date()
        except ValueError:
            pass

    return extract_date_from_url(page.url)


def page_has_exploitable_chart(page, body_text: str) -> bool:
    body_text_lower = body_text.lower()

    has_streams = bool(re.search(r"\b\d{1,3}(?:[,.\s]\d{3})+\b", body_text))
    has_chart_header = (
        (("track" in body_text_lower) or ("titre" in body_text_lower))
        and (("streams" in body_text_lower) or ("ecoutes" in body_text_lower) or ("écoutes" in body_text_lower))
    )
    has_rank_line = bool(re.search(r"(?m)^\s*(?:1|2|3|4|5)\s*$", body_text))
    try:
        has_download_button = page.locator("button[aria-labelledby='csv_download']").count() > 0
    except Exception:
        has_download_button = False

    log("CHECK", f"has_streams={has_streams}")
    log("CHECK", f"has_chart_header={has_chart_header}")
    log("CHECK", f"has_rank_line={has_rank_line}")
    log("CHECK", f"has_download_button={has_download_button}")

    return has_download_button or (has_streams and (has_chart_header or has_rank_line)) or (has_chart_header and has_rank_line)


def open_chart_page(page, route_value: str, chart_id: str = CHART_ID) -> tuple[bool, date | None]:
    url = f"https://charts.spotify.com/charts/view/{chart_id}/{route_value}"
    log("CHECK", f"Ouverture {url}")

    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('img[src*=\"i.scdn.co\"]').length > 5",
            timeout=10000,
        )
    except Exception:
        pass

    current_url = page.url.lower()
    log("CHECK", f"URL finale: {page.url}")

    if "login" in current_url or "accounts.spotify.com" in current_url:
        log("CHECK", "Session Spotify expirée ou non connectée")
        return False, None

    body_text = (page.locator("body").inner_text() or "").strip()
    if "Log in with Spotify" in body_text:
        log("CHECK", "Session Spotify non valide")
        return False, None

    log("CHECK", f"Longueur texte: {len(body_text)}")

    detected_date = try_extract_chart_date_from_page(page)
    log("CHECK", f"Date détectée: {detected_date if detected_date else 'N/A'}")

    available = page_has_exploitable_chart(page, body_text)
    log("CHECK", f"Page exploitable: {'oui' if available else 'non'}")

    return available, detected_date


def _check_page_once(page, target_date: date, chart_id: str = CHART_ID) -> bool:
    """Une tentative de vérification sur un page Playwright déjà ouvert."""
    try:
        available, _ = open_chart_page(page, str(target_date), chart_id)
        if available:
            return True
    except PlaywrightTimeoutError as e:
        log("CHECK", f"Timeout route datée: {e}")
    except Exception as e:
        log("CHECK", f"Route datée échouée: {e}")

    log("CHECK", "Fallback vers latest ...")

    try:
        available, detected_date = open_chart_page(page, "latest", chart_id)
    except PlaywrightTimeoutError as e:
        log("CHECK", f"Timeout latest: {e}")
        return False
    except Exception as e:
        log("CHECK", f"Erreur latest: {e}")
        return False

    if not detected_date:
        log("CHECK", "Impossible de détecter la date du chart latest")
        return False

    if detected_date != target_date:
        log("CHECK", f"Latest pointe vers {detected_date}, attendu {target_date}")
        return False

    return available


_FILTER_BEARER_CACHE = ROOT / "tools" / "json" / "bearer_cache.json"
_API_CHARTS_BASE = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
_TOKEN_TTL = 50 * 60
_UNAVAILABLE_MARKERS = (
    "HTTP 404",
    "pas encore publi",
    "déjà traité",
    "deja traite",
    "latest pointe vers",
    "latest (",
)


def looks_like_unavailable_chart(output: str) -> bool:
    normalized = output.casefold()
    return any(marker.casefold() in normalized for marker in _UNAVAILABLE_MARKERS)


def _api_chart_available(target_date: date) -> bool | None:
    """Vérifie la disponibilité du chart via l'API Spotify (sans Playwright).
    Retourne True si dispo, False si pas encore publiée, None si token absent/expiré."""
    import json as _json
    import requests as _req
    try:
        if not _FILTER_BEARER_CACHE.exists():
            return None
        data = _json.loads(_FILTER_BEARER_CACHE.read_text(encoding="utf-8-sig"))
        if time.time() - data.get("ts", 0) >= _TOKEN_TTL:
            return None
        token = data.get("token")
        if not token:
            return None
        url = f"{_API_CHARTS_BASE}/{CHART_ID}/{target_date}"
        resp = _req.get(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}, timeout=15)
        log("CHECK", f"API status {resp.status_code} pour {target_date}")
        return resp.status_code == 200
    except Exception as e:
        log("CHECK", f"Erreur check API: {e}")
        return None


def wait_for_page(target_date: date) -> bool:
    """Attend que le chart soit disponible via l'API Spotify (sans Playwright)."""
    attempt = 1
    while True:
        if past_cutoff():
            log("WARN", f"{CUTOFF_HOUR}h{CUTOFF_MINUTE:02d} atteint — page {target_date} toujours indisponible, abandon")
            return False

        log("WAIT", f"Vérification tentative #{attempt} pour {target_date}")
        avail = _api_chart_available(target_date)

        if avail is True:
            log("INFO", f"Page de {target_date} détectée (API, tentative #{attempt})")
            return True
        if avail is None:
            log("INFO", "Token absent/expiré — passage direct à filter.py")
            return True

        log("WAIT", f"Page {target_date} pas encore exploitable, retry #{attempt} dans {RETRY_SECONDS // 60} min")
        attempt += 1
        time.sleep(RETRY_SECONDS)


def skip_availability_wait() -> bool:
    return os.getenv("SPOTIFY_CHARTS_ALREADY_AVAILABLE", "").strip().lower() in {"1", "true", "yes"}


def data_ready(d: date) -> bool:
    """Données déjà collectées : CSV + tweet.txt présents."""
    return chart_csv_path(d).exists() and tweet_path(d).exists()


def run_filter(d: date, *, force: bool = False) -> tuple[str | None, bool]:
    log("STEP", f"Lancement de filter.py pour {d}{' (--force)' if force else ''}")

    cmd = [sys.executable, str(FILTER_SCRIPT), str(d)]
    if force:
        cmd.append("--force")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
    )

    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, flush=True)

    log("STEP", f"filter.py terminé avec code {result.returncode}")

    if result.returncode != 0:
        log("ERROR", f"filter.py a échoué (code {result.returncode})")
        unavailable = looks_like_unavailable_chart(f"{result.stdout}\n{result.stderr}")
        return None, unavailable

    tp = tweet_path(d)
    if not tp.exists():
        log("ERROR", f"tweet.txt introuvable après filter.py pour {d}")
        return None, False

    content = tp.read_text(encoding="utf-8-sig")
    log("INFO", f"tweet.txt chargé ({len(content)} caractères)")
    return content, False


def build_tweet_content(processed: list[date]) -> str:
    processed = processed[:1]
    if len(processed) == 1:
        d = processed[0]
        return f"📈 | Taylor Swift on Spotify Global Charts yesterday ({d.strftime('%B %d, %Y')}) :"

def generate_image(processed: list[date]) -> Path | None:
    log("STEP", "Génération de l'image du chart")

    d = processed[0]
    img_args = [sys.executable, str(GENERATE_IMAGE_SCRIPT), str(d)]

    img_result = subprocess.run(
        img_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
    )

    if img_result.stdout:
        print(img_result.stdout, flush=True)
    if img_result.stderr:
        print(img_result.stderr, flush=True)

    if img_result.returncode != 0:
        log("WARN", "Génération d'image échouée — publication sans image")
        return None

    new_path = spotify_chart_dir("global", d) / "chart_image.png"
    legacy_path = legacy_spotify_chart_dir("global", d) / "chart_image.png"
    image_path = new_path if new_path.exists() else legacy_path

    if not image_path.exists():
        log("WARN", f"Image attendue introuvable: {image_path}")
        return None

    return image_path


def ensure_us_image_for_date(target_date: date) -> Path | None:
    """Capture US Spotify chart page image directly from this global pipeline."""
    if not SPOTIFY_SESSION.exists():
        log("WARN", f"Session Spotify introuvable: {SPOTIFY_SESSION}")
        return None

    date_str = str(target_date)
    out_path = spotify_chart_dir("global", target_date) / "us_chart_capture.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log("STEP", f"Preparing US chart image for {date_str}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            storage_state=str(SPOTIFY_SESSION),
            viewport={"width": 1400, "height": 2600},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/133.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = context.new_page()
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
        page.set_default_timeout(PAGE_TIMEOUT_MS)

        try:
            if not _check_page_once(page, target_date, US_CHART_ID):
                log("WARN", f"US chart not exploitable for {date_str}")
                return None

            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            page.locator("body").screenshot(path=str(out_path))
        except Exception as e:
            log("WARN", f"US image capture failed for {date_str}: {e}")
            return None
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    if not out_path.exists():
        log("WARN", f"US image missing after capture: {out_path}")
        return None

    return out_path


def build_global_us_combined_image(global_image: Path, us_image: Path, target_date: date) -> Path | None:
    """Build a single image with global on top and US on bottom."""
    if Image is None or ImageOps is None:
        log("WARN", "Pillow not installed; cannot build combined Global+US image")
        return None

    try:
        global_img = Image.open(global_image).convert("RGB")
        us_img = Image.open(us_image).convert("RGB")
    except Exception as e:
        log("WARN", f"Unable to open images for merge: {e}")
        return None

    pad = 18
    width = global_img.width
    top = ImageOps.pad(global_img, (width, global_img.height), color=(255, 255, 255), centering=(0.5, 0.5))

    # Keep the US part readable and compact compared to the TS global card.
    us_fit = ImageOps.contain(us_img, (width, max(220, int(global_img.height * 0.9))))
    bottom = ImageOps.pad(us_fit, (width, us_fit.height), color=(255, 255, 255), centering=(0.5, 0.5))

    merged = Image.new("RGB", (width, top.height + bottom.height + pad), (248, 248, 248))
    merged.paste(top, (0, 0))
    merged.paste(bottom, (0, top.height + pad))

    out_path = spotify_chart_dir("global", target_date) / "chart_image_global_us.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(out_path, format="PNG")
    log("INFO", f"Combined Global+US image saved: {out_path}")
    return out_path


def maybe_upload_to_r2() -> None:
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        log("INFO", "R2 upload skipped (UPLOAD_TO_R2 explicitly disabled)")
        return

    r2_script = _REPO_ROOT / "scripts" / "r2.py"
    if not r2_script.exists():
        log("WARN", f"R2 upload script missing: {r2_script}")
        return

    log("STEP", "Uploading exported data to R2")
    result = subprocess.run([sys.executable, str(r2_script)], check=False, cwd=str(_REPO_ROOT))
    if result.returncode != 0:
        log("WARN", f"R2 upload failed with code {result.returncode} (non-blocking)")


def main() -> None:
    date_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    no_post = "--no-post" in sys.argv
    post_only = "--post-only" in sys.argv

    if date_args:
        try:
            target = datetime.strptime(date_args[0], "%Y-%m-%d").date()
        except ValueError:
            log("ERROR", f"Date invalide '{date_args[0]}', format attendu : YYYY-MM-DD")
            sys.exit(1)
        if force:
            lp = lock_path(target)
            if lp.exists():
                lp.unlink()
                log("INFO", f"--force: posted.lock supprimé pour {target}")
        unposted = [target]
    else:
        if force:
            yesterday = date.today() - timedelta(days=1)
            lp = lock_path(yesterday)
            if lp.exists():
                lp.unlink()
                log("INFO", f"--force: posted.lock supprimé pour {yesterday}")
        unposted = get_unposted_dates()
        if force and not unposted:
            unposted = [date.today() - timedelta(days=1)]

    log("INFO", f"Heure locale: {datetime.now()}")
    log("INFO", f"Script: {Path(__file__).name}")
    log("INFO", f"Répertoire: {ROOT}")

    print(f"\n{'=' * 50}\n  daily.py (Global)\n{'=' * 50}\n", flush=True)

    if not unposted:
        log("INFO", "Tout est déjà posté")
        return

    log("INFO", f"Dates à poster: {[str(d) for d in unposted]}")

    # Mode post-only : worldwide a déjà collecté les données, on saute filter.py
    if post_only:
        target = unposted[0]
        chart_json = spotify_chart_dir("global", target) / f"ts_chart_{target}.json"
        if not chart_json.exists():
            log("ERROR", f"--post-only: ts_chart_{target}.json absent pour {target}")
            sys.exit(1)
        log("INFO", "Mode --post-only : données fournies par worldwide, skip filter.py")
        processed = [target]
        mark_updated(target)
        tweet_content = build_tweet_content(processed)
        (ROOT / "twitter_post.txt").write_text(tweet_content, encoding="utf-8")
        log("INFO", "twitter_post.txt mis à jour")
        print(f"\nPost :\n{tweet_content}\n", flush=True)
        image_path = generate_image(processed)
        log("STEP", "Publication Twitter")
        if no_post:
            log("INFO", "Publication Twitter ignorée (--no-post)")
            posted = True
        else:
            if image_path:
                posted = post_with_image(tweet_content, image_path, TWITTER_SESSION)
            else:
                posted = post_thread(split_tweets(tweet_content), TWITTER_SESSION)
        if posted:
            for d in processed:
                mark_posted(d)
            log("INFO", "Terminé avec succès (--post-only)")
        else:
            log("ERROR", "Publication Twitter échouée (--post-only)")
            sys.exit(1)
        return

    # Dates qui nécessitent encore le scraping
    needs_scraping = [d for d in unposted if not data_ready(d) or force]
    already_ready = [d for d in unposted if data_ready(d) and not force]

    if already_ready:
        log("INFO", f"Données déjà collectées, skip filter : {[str(d) for d in already_ready]}")

    if needs_scraping and skip_availability_wait():
        log("INFO", "Disponibilite Spotify deja validee par run_all_charts, attente locale ignoree")
        needs_scraping_to_wait = []
    else:
        needs_scraping_to_wait = needs_scraping

    for d in needs_scraping_to_wait:
        if not wait_for_page(d):
            log("WARN", f"Page {d} jamais devenue disponible, date ignorée")
            continue

    results: dict[date, str] = {}
    unavailable_dates: list[date] = []
    for d in unposted:
        if data_ready(d) and not force:
            content = tweet_path(d).read_text(encoding="utf-8-sig")
            log("INFO", f"tweet.txt existant chargé pour {d} ({len(content)} caractères)")
            results[d] = content
        else:
            content, unavailable = run_filter(d, force=force)
            if content:
                results[d] = content
            elif unavailable:
                unavailable_dates.append(d)
                log("WARN", f"Chart {d} indisponible ou latest déjà traité, date ignorée")
            else:
                log("WARN", f"filter.py a échoué pour {d}, date ignorée")

    if not results:
        if unavailable_dates and len(unavailable_dates) == len(unposted):
            log("WARN", f"Aucun nouveau chart disponible: {[str(d) for d in unavailable_dates]}")
            return
        log("ERROR", "Aucun traitement réussi")
        sys.exit(1)

    processed = sorted(results.keys())
    for d in processed:
        mark_updated(d)

    tweet_content = build_tweet_content(processed)
    (ROOT / "twitter_post.txt").write_text(tweet_content, encoding="utf-8")
    log("INFO", "twitter_post.txt mis à jour")
    print(f"\nPost :\n{tweet_content}\n", flush=True)

    image_path = generate_image(processed)

    if ENABLE_GLOBAL_US_COMBINED_IMAGE and image_path and len(processed) == 1:
        target_date = processed[0]
        us_image = ensure_us_image_for_date(target_date)
        if us_image:
            combined = build_global_us_combined_image(image_path, us_image, target_date)
            if combined:
                image_path = combined
    log("STEP", "Publication Twitter")
    if no_post:
        log("INFO", "Publication Twitter ignorée (--no-post)")
        posted = True
    else:
        log("STEP", "Publication Twitter")
        if image_path:
            posted = post_with_image(tweet_content, image_path, TWITTER_SESSION)
        else:
            posted = post_thread(split_tweets(tweet_content), TWITTER_SESSION)

    if posted:
        for d in processed:
            mark_posted(d)

        cleanup_tweet_files(processed)

        log("INFO", f"Terminé avec succès ({len(processed)} date(s) postée(s))")

        migrate_archive_csv(MIGRATE_SCRIPT)
        maybe_upload_to_r2()

        if NTFY_TOPIC:
            try:
                notify(
                    NTFY_TOPIC,
                    tweet_content,
                    title="Taylor Swift Global - Posté",
                    tags="white_check_mark,earth_globe_europe-africa",
                )
            except Exception as e:
                log("WARN", f"ntfy notification failed (non-blocking): {e}")

        git_commit_and_push(_REPO_ROOT)
    else:
        log("ERROR", "Publication Twitter échouée, posted.lock non créé")

        if NTFY_TOPIC:
            try:
                notify(
                    NTFY_TOPIC,
                    "La publication Twitter a échoué.",
                    title="Taylor Swift Global - Erreur",
                    tags="x,warning",
                    priority="high",
                )
            except Exception as e:
                log("WARN", f"ntfy notification failed (non-blocking): {e}")

        sys.exit(1)


if __name__ == "__main__":
    import atexit as _atexit
    _t0 = time.perf_counter()
    _atexit.register(lambda: log("INFO", f"Terminé en {int((time.perf_counter() - _t0) // 60)}m {int((time.perf_counter() - _t0) % 60):02d}s"))
    main()
