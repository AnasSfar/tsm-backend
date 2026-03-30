#!/usr/bin/env python3
"""
daily.py - US
Scrape la page Spotify Charts, filtre TS, met a jour ts_history, et poste le tweet.
Usage : python daily.py [--force] [YYYY-MM-DD]

Logique :
- cherche toutes les dates non-postÃ©es des 7 derniers jours
- attend que la page la plus rÃ©cente soit disponible (cutoff Ã  15h)
- lance filter.py pour chaque date manquante
- si plusieurs dates : gÃ©nÃ¨re une image combinÃ©e et un tweet condensÃ©
- poste sur Twitter

Options :
  --force   Supprime le posted.lock de la date cible et relance le pipeline complet.
            Sans date explicite, cible hier.
    --no-post Exécute tout le pipeline mais ignore la publication Twitter.
"""
import re
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.twitter import post_thread, post_with_image, split_tweets
from core.notify import send as notify
from playwright.sync_api import sync_playwright

ROOT                  = Path(__file__).parent
_REPO_ROOT            = ROOT.parents[3]
DATA_DIR              = ROOT / "history"
CHART_ID              = "regional-us-daily"
TWITTER_SESSION       = ROOT / "tools/json/twitter_session.json"
SPOTIFY_SESSION       = ROOT / "tools/json/spotify_session.json"
FILTER_SCRIPT         = ROOT / "tools/scripts/filter.py"
GENERATE_IMAGE_SCRIPT = ROOT / "tools/scripts/generate_chart_image.py"

sys.path.insert(0, str(ROOT / "tools" / "scripts"))
from git_ops import git_commit_and_push
try:
    from config import NTFY_TOPIC
except Exception:
    NTFY_TOPIC = ""



RETRY_SECONDS = 60
CUTOFF_HOUR   = 15
CUTOFF_MINUTE = 30  # abandon si page non dispo à 15h30 le lendemain
LOOKBACK_DAYS = 7   # fenÃªtre de dÃ©tection des jours manquants

_SCRIPT_START = datetime.now()


def log(level: str, message: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}", flush=True)


def lock_path(d: date) -> Path:
    return DATA_DIR / str(d.year) / f"{d.month:02d}" / str(d) / "posted.lock"


def already_posted(d: date) -> bool:
    exists = lock_path(d).exists()
    log("DEBUG", f"posted.lock pour {d}: {'oui' if exists else 'non'}")
    return exists


def mark_posted(d: date):
    p = lock_path(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    log("INFO", f"posted.lock crÃ©Ã©: {p}")


def tweet_path(d: date) -> Path:
    return DATA_DIR / str(d.year) / f"{d.month:02d}" / str(d) / "tweet.txt"


def cleanup_tweet_files(dates: list[date]) -> None:
    for d in dates:
        tp = tweet_path(d)
        if tp.exists():
            try:
                tp.unlink()
                log("INFO", f"tweet.txt supprimÃ© pour {d}")
            except Exception as e:
                log("WARN", f"Impossible de supprimer tweet.txt pour {d}: {e}")

    twitter_post = ROOT / "twitter_post.txt"
    if twitter_post.exists():
        try:
            twitter_post.unlink()
            log("INFO", "twitter_post.txt supprimÃ©")
        except Exception as e:
            log("WARN", f"Impossible de supprimer twitter_post.txt: {e}")


def get_unposted_dates() -> list[date]:
    """Retourne les dates non-postÃ©es des LOOKBACK_DAYS derniers jours, du plus ancien au plus rÃ©cent."""
    today = date.today()
    unposted = [
        today - timedelta(days=i)
        for i in range(1, LOOKBACK_DAYS + 1)
        if not already_posted(today - timedelta(days=i))
    ]
    unposted.sort()
    return unposted


def past_cutoff() -> bool:
    now = datetime.now()
    return (
        now.date() > _SCRIPT_START.date()
        and (
            now.hour > CUTOFF_HOUR
            or (now.hour == CUTOFF_HOUR and now.minute >= CUTOFF_MINUTE)
        )
    )


def page_available(d: date) -> bool:
    url = f"https://charts.spotify.com/charts/view/{CHART_ID}/{d}"
    log("CHECK", f"Ouverture {url}")

    with sync_playwright() as p:
        browser = None
        context = None
        try:
            browser = p.chromium.launch(
                headless=True,
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
                log("CHECK", "Session Spotify expirÃ©e ou non connectÃ©e")
                return False

            body_text = (page.locator("body").inner_text() or "").strip()
            body_text_lower = body_text.lower()

            log("CHECK", f"Longueur texte: {len(body_text)}")

            has_streams      = bool(re.search(r"\b\d{1,3}(?:[,.\s]\d{3})+\b", body_text))
            has_chart_header = (
                (("track" in body_text_lower) or ("titre" in body_text_lower))
                and (("streams" in body_text_lower) or ("ecoutes" in body_text_lower) or ("écoutes" in body_text_lower))
            )
            has_rank_line    = bool(re.search(r"(?m)^\s*(?:1|2|3|4|5)\s*$", body_text))
            try:
                has_download_button = page.locator("button[aria-labelledby='csv_download']").count() > 0
            except Exception:
                has_download_button = False

            log("CHECK", f"has_streams={has_streams}")
            log("CHECK", f"has_chart_header={has_chart_header}")
            log("CHECK", f"has_rank_line={has_rank_line}")
            log("CHECK", f"has_download_button={has_download_button}")

            available = has_download_button or (has_streams and (has_chart_header or has_rank_line)) or (has_chart_header and has_rank_line)
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
    log("STEP", f"Lancement de filter.py pour {d}")
    result = subprocess.run(
        [sys.executable, str(FILTER_SCRIPT), str(d)],
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

    log("STEP", f"filter.py terminÃ© avec code {result.returncode}")

    if result.returncode != 0:
        log("ERROR", f"filter.py a Ã©chouÃ© (code {result.returncode})")
        return None

    tp = tweet_path(d)
    if not tp.exists():
        log("ERROR", "tweet.txt introuvable aprÃ¨s filter.py")
        return None

    content = tp.read_text(encoding="utf-8")
    log("INFO", f"tweet.txt chargÃ© ({len(content)} caractÃ¨res)")
    return content


def build_multi_tweet(dates: list[date]) -> str:
    parts = [datetime.strptime(str(d), "%Y-%m-%d").strftime("%B %d") for d in dates]
    year  = dates[-1].year
    return f"Taylor Swift on {' & '.join(parts)}, {year}"


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
        raise RuntimeError(f"R2 upload failed with code {result.returncode}")


def main():
    force = "--force" in sys.argv
    no_post = "--no-post" in sys.argv
    date_args = [a for a in sys.argv[1:] if not a.startswith("--")]

    # Mode manuel : python daily.py [--force] [YYYY-MM-DD]
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
                log("INFO", f"--force: posted.lock supprimÃ© pour {target}")
        unposted = [target]
    else:
        if force:
            yesterday = date.today() - timedelta(days=1)
            lp = lock_path(yesterday)
            if lp.exists():
                lp.unlink()
                log("INFO", f"--force: posted.lock supprimÃ© pour {yesterday}")
        unposted = get_unposted_dates()
        if force and not unposted:
            unposted = [date.today() - timedelta(days=1)]

    log("INFO", f"Heure locale: {datetime.now()}")
    log("INFO", f"Script: {Path(__file__).name}")
    log("INFO", f"RÃ©pertoire: {ROOT}")

    print(f"\n{'=' * 50}\n  daily.py (US)\n{'=' * 50}\n", flush=True)

    if not unposted:
        log("INFO", "Tout est dÃ©jÃ  postÃ©")
        return

    log("INFO", f"Dates Ã  poster: {[str(d) for d in unposted]}")
    target = unposted[0]  # la plus rÃ©cente dÃ©bloquera les autres

    # Attendre que la page cible soit disponible (cutoff Ã  CUTOFF_HOUR)
    attempt = 1
    while True:
        if past_cutoff():
            log("WARN", f"{CUTOFF_HOUR}h{CUTOFF_MINUTE:02d} atteint — page {target} toujours indisponible, abandon")
            return

        log("WAIT", f"VÃ©rification tentative #{attempt} pour {target}")
        if page_available(target):
            log("INFO", f"Page de {target} dÃ©tectÃ©e")
            break

        log("WAIT", f"Page {target} pas encore exploitable, retry #{attempt} dans {RETRY_SECONDS // 60} min")
        attempt += 1
        time.sleep(RETRY_SECONDS)

    # Traiter chaque date non-postÃ©e
    results: dict[date, str] = {}
    for d in unposted:
        content = run_filter(d)
        if content:
            results[d] = content
        else:
            log("WARN", f"filter.py a Ã©chouÃ© pour {d}, date ignorÃ©e")

    if not results:
        log("ERROR", "Aucun traitement rÃ©ussi")
        sys.exit(1)

    processed = sorted(results.keys())

    # Contenu du tweet
    _last_date = processed[-1]
    _date_fmt  = _last_date.strftime("%B %d, %Y")
    tweet_content = f"US | Taylor Swift on Spotify US Charts yesterday ({_date_fmt}) :"

    (ROOT / "twitter_post.txt").write_text(tweet_content, encoding="utf-8")
    log("INFO", "twitter_post.txt mis Ã  jour")
    print(f"\nPost :\n{tweet_content}\n", flush=True)

    # GÃ©nÃ©rer l'image (simple ou combinÃ©e)
    log("STEP", "GÃ©nÃ©ration de l'image du chart")
    if len(processed) == 1:
        d = processed[0]
        image_path = DATA_DIR / str(d.year) / f"{d.month:02d}" / str(d) / "chart_image.png"
        img_args = [sys.executable, str(GENERATE_IMAGE_SCRIPT), str(d)]
    else:
        image_path = ROOT / "chart_image_multi.png"
        img_args = [sys.executable, str(GENERATE_IMAGE_SCRIPT)] + [str(d) for d in processed]

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
        log("WARN", "GÃ©nÃ©ration d'image Ã©chouÃ©e â€” publication sans image")
        image_path = None

    # Poster
    if no_post:
        log("INFO", "Publication Twitter ignorée (--no-post)")
        posted = True
    else:
        log("STEP", "Publication Twitter")
        if image_path and image_path.exists():
            posted = post_with_image(tweet_content, image_path, TWITTER_SESSION)
        else:
            posted = post_thread(split_tweets(tweet_content), TWITTER_SESSION)

    if posted:
        for d in processed:
            mark_posted(d)

        cleanup_tweet_files(processed)

        log("INFO", f"TerminÃ© avec succÃ¨s ({len(processed)} date(s) postÃ©e(s))")

        git_commit_and_push(_REPO_ROOT)
        maybe_upload_to_r2()

        notify(
            NTFY_TOPIC,
            tweet_content,
            title="Taylor Swift FR - PostÃ©",
            tags="white_check_mark,musical_note",
        )
    else:
        log("ERROR", "Publication Twitter Ã©chouÃ©e, posted.lock non crÃ©Ã©")
        notify(
            NTFY_TOPIC,
            "La publication Twitter a Ã©chouÃ©.",
            title="Taylor Swift FR - Erreur",
            tags="x,warning",
            priority="high",
        )
        sys.exit(1)


if __name__ == "__main__":
    import atexit as _atexit
    _t0 = time.perf_counter()
    _atexit.register(lambda: log("INFO", f"TerminÃ© en {int((time.perf_counter() - _t0) // 60)}m {int((time.perf_counter() - _t0) % 60):02d}s"))
    main()

