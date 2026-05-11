#!/usr/bin/env python3
"""
Scrape les streams totaux de toutes les chansons depuis songs.json et albums/*.json.

- Cible par défaut : TOUTES les tracks (y compris kworb extras)
- Écrit uniquement dans streams_history.csv (daily_streams laissé vide)
- --new-only : uniquement les tracks absentes de streams_history.csv

Usage :
    python seed_streams.py                          # toutes les tracks
    python seed_streams.py --dry-run                # affiche sans scraper
    python seed_streams.py --track-id ID1 ID2 ...  # force des track IDs précis
    python seed_streams.py --new-only               # uniquement les tracks sans historique
"""
from __future__ import annotations

import argparse
import csv
import re
import threading
import unicodedata
from datetime import date
from pathlib import Path
from queue import Empty, Queue

import requests as _requests

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# ── Paths ──────────────────────────────────────────────────────────────────────
_SCRIPT_DIR      = Path(__file__).resolve().parent
_REPO_ROOT       = _SCRIPT_DIR.parents[2]
_DB_ROOT         = _REPO_ROOT / "db"
HISTORY_PATH     = _DB_ROOT / "streams_history.csv"
_DISCOGRAPHY_DIR = _DB_ROOT / "discography"
_ALBUMS_DIR      = _DISCOGRAPHY_DIR / "albums"
_SONGS_JSON      = _DISCOGRAPHY_DIR / "songs.json"

# ── Config ─────────────────────────────────────────────────────────────────────
HEADLESS             = True
PAGE_GOTO_TIMEOUT_MS = 45_000
MAX_PARALLEL_PAGES   = 6

# ── API GraphQL Spotify ─────────────────────────────────────────────────────────
_GRAPHQL_URL   = "https://api-partner.spotify.com/pathfinder/v2/query"
_GETTRACK_HASH = "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294"
_APP_VERSION   = "1.2.87.30.gc764ebf1"
_SESSION_FILE  = _SCRIPT_DIR.parents[3] / "charts/global/tools/json/spotify_session.json"


# ── Helpers texte (copiés de update_streams.py) ────────────────────────────────

def parse_int_from_text(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = value.replace("\u2019", "'").replace("\u2018", "'")
    value = re.sub(r"\s+", " ", value)
    return value


def is_duration_line(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", text.strip()))


def is_large_number_line(text: str) -> bool:
    cleaned = text.strip().replace("\u202f", " ").replace("\xa0", " ")
    if not re.fullmatch(r"[\d\s,.\']+", cleaned):
        return False
    value = parse_int_from_text(cleaned)
    return value is not None and value >= 1000


def normalize_spotify_track_url(url: str) -> str:
    match = re.search(r"track/([A-Za-z0-9]+)", url)
    if match:
        return f"https://open.spotify.com/track/{match.group(1)}"
    return url.strip()


# ── API GraphQL helpers ─────────────────────────────────────────────────────────

def _capture_tokens() -> dict:
    """Ouvre Playwright une fois, capture Bearer + client-token. Retourne {} si échec."""
    tokens: dict = {}

    def on_request(req):
        if "api-partner.spotify.com" in req.url and not tokens.get("bearer"):
            auth = req.headers.get("authorization", "")
            ct   = req.headers.get("client-token", "")
            if auth.startswith("Bearer ") and ct:
                tokens["bearer"]       = auth[7:]
                tokens["client_token"] = ct

    print("Capture des tokens Spotify via Playwright…")
    ctx_kwargs: dict = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
        ),
    }
    if _SESSION_FILE.exists():
        ctx_kwargs["storage_state"] = str(_SESSION_FILE)

    import time as _time
    p = sync_playwright().start()
    browser = None
    try:
        browser = p.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx  = browser.new_context(**ctx_kwargs)
        page = ctx.new_page()
        page.on("request", on_request)
        page.goto(
            "https://open.spotify.com/track/0V3wPSX9ygBnCm8psDIegu",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        deadline = _time.time() + 20
        while not tokens.get("bearer") and _time.time() < deadline:
            page.wait_for_timeout(300)
    except Exception as e:
        print(f"  Erreur capture tokens: {e}")
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    if tokens.get("bearer"):
        print(f"  Bearer capturé ({tokens['bearer'][:20]}…)")
    else:
        print("  Capture échouée — fallback Playwright pour toutes les tracks")
    return tokens


def fetch_playcount_api(track_id: str, tokens: dict, session: "_requests.Session") -> int | None:
    """Récupère le playcount via API GraphQL. Retourne None si indisponible."""
    if not tokens.get("bearer"):
        return None
    import json as _json
    body = {
        "variables":     {"uri": f"spotify:track:{track_id}"},
        "operationName": "getTrack",
        "extensions":    {
            "persistedQuery": {"version": 1, "sha256Hash": _GETTRACK_HASH}
        },
    }
    headers = {
        "Authorization":       f"Bearer {tokens['bearer']}",
        "client-token":        tokens["client_token"],
        "spotify-app-version": _APP_VERSION,
        "app-platform":        "WebPlayer",
        "Accept":              "application/json",
        "Content-Type":        "application/json;charset=UTF-8",
        "Origin":              "https://open.spotify.com",
        "Referer":             "https://open.spotify.com/",
        "User-Agent":          (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
        ),
    }
    try:
        resp = session.post(_GRAPHQL_URL, json=body, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            track_union = (data.get("data") or {}).get("trackUnion") or {}
            pc = track_union.get("playcount")
            if pc is not None:
                return int(pc)
            m = re.search(r'"playcount":\s*"(\d+)"', _json.dumps(data))
            return int(m.group(1)) if m else None
        elif resp.status_code == 401:
            return None  # token expiré → fallback Playwright
        else:
            return None
    except Exception:
        return None


# ── Playwright helpers ─────────────────────────────────────────────────────────

def block_unneeded(route) -> None:
    url = route.request.url.lower()
    rtype = route.request.resource_type
    blocked_types = {"media", "font"}
    blocked_kw = (
        "doubleclick", "googletagmanager", "google-analytics", "analytics",
        "facebook", "pixel", "ads",
        ".mp4", ".webm", ".mp3", ".wav", ".ogg", ".woff", ".woff2", ".ttf",
    )
    if rtype in blocked_types or any(x in url for x in blocked_kw):
        route.abort()
    else:
        route.continue_()


def maybe_accept_cookies(page) -> None:
    for pattern in [r"Accept", r"Accept all", r"Accepter", r"Autoriser"]:
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=1500)
            page.wait_for_timeout(800)
            return
        except Exception:
            pass


def launch_browser(playwright):
    return playwright.chromium.launch(
        headless=HEADLESS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )


# ── Extraction du playcount ────────────────────────────────────────────────────

def extract_main_track_playcount(lines: list[str]) -> int | None:
    if not lines:
        return None

    start_idx = None
    for i, line in enumerate(lines):
        if line.strip().lower() == "titre":
            start_idx = i
            break

    if start_idx is None:
        return None

    end_markers = {
        "connectez-vous", "se connecter", "artiste",
        "recommandés", "basees sur ce titre", "basées sur ce titre",
        "titres populaires par", "sorties populaires par taylor swift",
    }

    block: list[str] = []
    for line in lines[start_idx + 1:]:
        if normalize_title(line.strip()) in end_markers:
            break
        block.append(line.strip())

    if not block:
        return None

    # Priorité : nombre qui suit une durée (MM:SS)
    for i, line in enumerate(block):
        if is_duration_line(line):
            for j in range(i + 1, min(i + 6, len(block))):
                candidate = block[j].strip()
                if candidate in {"•", "-", ""}:
                    continue
                if is_large_number_line(candidate):
                    return parse_int_from_text(candidate)

    # Fallback : seul grand nombre dans le bloc
    candidates = [
        parse_int_from_text(line)
        for line in block
        if is_large_number_line(line)
    ]
    candidates = [v for v in candidates if v is not None]
    if len(candidates) == 1:
        return candidates[0]

    return None


def scrape_track(page, title: str, url: str) -> tuple[int | None, str]:
    """Retourne (streams, status) où status ∈ 'ok' | 'not_found' | 'timeout'."""
    clean_url = normalize_spotify_track_url(url)

    for attempt in range(3):
        try:
            page.goto(clean_url, wait_until="commit", timeout=PAGE_GOTO_TIMEOUT_MS)
            page.wait_for_timeout(2000)
            maybe_accept_cookies(page)

            for wait_ms in (1500, 3000, 5000):
                try:
                    body_text = page.locator("body").inner_text(timeout=5000)
                except Exception:
                    body_text = ""

                if body_text:
                    lines = [
                        line.replace("\u202f", " ").replace("\xa0", " ").strip()
                        for line in body_text.splitlines()
                        if line.strip()
                    ]
                    total = extract_main_track_playcount(lines)
                    if total is not None:
                        return total, "ok"

                page.wait_for_timeout(wait_ms)

            return None, "not_found"

        except PlaywrightTimeoutError:
            print(f"  TIMEOUT {title} (attempt {attempt + 1}/3)")
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
        except Exception as e:
            print(f"  ERROR {title}: {e} (attempt {attempt + 1}/3)")
            try:
                page.wait_for_timeout(1000)
            except Exception:
                pass

    return None, "timeout"


# ── Discography + CSV ──────────────────────────────────────────────────────────

def _track_id_from_url(url: str) -> str | None:
    m = re.search(r"track/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def _load_all_tracks_from_json() -> list[dict]:
    import json
    seen: dict[str, dict] = {}

    all_sections = []
    if _ALBUMS_DIR.exists():
        for album_file in sorted(_ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            album_name = payload.get("album", "") if isinstance(payload, dict) else ""
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                if not isinstance(section, dict):
                    continue
                item = dict(section)
                if not item.get("album"):
                    item["album"] = album_name
                all_sections.append(item)

    if _SONGS_JSON.exists():
        all_sections.extend(json.loads(_SONGS_JSON.read_text(encoding="utf-8-sig")))

    for section in all_sections:
            for t in section.get("tracks", []):
                url      = (t.get("url") or t.get("spotify_url") or "").strip()
                track_id = _track_id_from_url(url)
                title    = (t.get("title") or "").strip()
                if not track_id or not title or track_id in seen:
                    continue
                seen[track_id] = {
                    "track_id":   track_id,
                    "title":      title,
                    "spotify_url": f"https://open.spotify.com/track/{track_id}",
                    "streams":    None,
                }
    return sorted(seen.values(), key=lambda x: x["title"].casefold())


def _seeded_track_ids() -> set[str]:
    """Track IDs already present in streams_history.csv."""
    if not HISTORY_PATH.exists():
        return set()
    ids: set[str] = set()
    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("track_id"):
                ids.add(row["track_id"])
    return ids


def load_target_tracks(track_ids: list[str] | None, new_only: bool) -> list[dict]:
    all_tracks = _load_all_tracks_from_json()
    if track_ids:
        wanted = set(track_ids)
        return [t for t in all_tracks if t["track_id"] in wanted]
    if new_only:
        seeded = _seeded_track_ids()
        return [t for t in all_tracks if t["track_id"] not in seeded]
    return all_tracks


def apply_to_history(scraped: dict[str, int]) -> None:
    """Met à jour streams_history.csv en utilisant la dernière date existante.

    - Ligne existante pour cette date → streams mis à jour, daily_streams inchangé
    - Pas de ligne pour cette date    → nouvelle ligne ajoutée avec daily_streams vide
    """
    if not scraped:
        return

    fieldnames = ["date", "track_id", "streams", "daily_streams"]
    rows: list[dict] = []

    if HISTORY_PATH.exists():
        with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                fieldnames = list(reader.fieldnames)
            rows = list(reader)

    # Dernière date présente dans le CSV (point de référence)
    dates = [r["date"] for r in rows if r.get("date")]
    ref_date = max(dates) if dates else date.today().isoformat()
    print(f"Date de référence : {ref_date}")

    # Index des lignes existantes pour ref_date : track_id → index dans rows
    ref_index = {
        r["track_id"]: i
        for i, r in enumerate(rows)
        if r.get("date") == ref_date
    }

    updated = 0
    added = 0
    for track_id, streams in scraped.items():
        if track_id in ref_index:
            rows[ref_index[track_id]]["streams"] = streams
            updated += 1
        else:
            rows.append({
                "date": ref_date,
                "track_id": track_id,
                "streams": streams,
                "daily_streams": "",
            })
            added += 1

    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"streams_history.csv : {updated} mis à jour, {added} ajoutés (date {ref_date})")


# ── Worker ─────────────────────────────────────────────────────────────────────

def worker(
    queue: Queue,
    total: int,
    print_lock: threading.Lock,
    counters: dict,
    scraped: dict,
    scraped_lock: threading.Lock,
    tokens: dict | None = None,
) -> None:
    p = sync_playwright().start()
    browser = launch_browser(p)
    context = browser.new_context()
    page = context.new_page()
    page.route("**/*", block_unneeded)
    api_session = _requests.Session()

    try:
        while True:
            try:
                i, track = queue.get_nowait()
            except Empty:
                break

            with print_lock:
                print(f"[{i}/{total}] {track['title']} … ", end="", flush=True)

            # Primary: API GraphQL
            streams = None
            status  = "not_found"
            if tokens:
                api_result = fetch_playcount_api(track["track_id"], tokens, api_session)
                if api_result is not None:
                    streams, status = api_result, "ok"

            # Fallback: Playwright
            if status != "ok":
                streams, status = scrape_track(page, track["title"], track["spotify_url"])

            with print_lock:
                if status == "ok" and streams is not None:
                    print(f"OK  {streams:,}")
                    counters["ok"] += 1
                elif status == "not_found":
                    print("NOT FOUND")
                    counters["not_found"] += 1
                else:
                    print(f"FAILED ({status})")
                    counters["errors"] += 1

            if status == "ok" and streams is not None:
                with scraped_lock:
                    scraped[track["track_id"]] = streams

            queue.task_done()
    finally:
        api_session.close()
        browser.close()
        p.stop()


# ── Main ───────────────────────────────────────────────────────────────────────

def run(track_ids: list[str] | None, dry_run: bool, new_only: bool) -> None:
    tracks = load_target_tracks(track_ids, new_only)

    if not tracks:
        print("Aucune track à traiter.")
        return

    print(f"{len(tracks)} track(s) à scraper :")
    for t in tracks:
        print(f"  {t['title']}  [{t['track_id']}]")

    if dry_run:
        print("\n[DRY-RUN] Aucune écriture.")
        return

    print()

    tokens = _capture_tokens()
    print()

    queue: Queue = Queue()
    for i, track in enumerate(tracks, 1):
        queue.put((i, track))

    print_lock  = threading.Lock()
    scraped_lock = threading.Lock()
    scraped: dict[str, int] = {}
    counters = {"ok": 0, "not_found": 0, "errors": 0}

    n_workers = min(MAX_PARALLEL_PAGES, len(tracks))
    threads = [
        threading.Thread(
            target=worker,
            args=(queue, len(tracks), print_lock, counters, scraped, scraped_lock, tokens),
            daemon=True,
        )
        for _ in range(n_workers)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print()
    print(f"Résultat : {counters['ok']} OK  |  {counters['not_found']} introuvables  |  {counters['errors']} erreurs")

    apply_to_history(scraped)
    print("daily_streams non modifié — lancez update_streams.py pour les obtenir.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed streams totaux pour les nouvelles chansons"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche les tracks ciblées sans scraper"
    )
    parser.add_argument(
        "--track-id", nargs="+", metavar="ID",
        help="Force des track IDs spécifiques"
    )
    parser.add_argument(
        "--new-only", action="store_true",
        help="Uniquement les tracks sans streams (streams IS NULL)"
    )
    args = parser.parse_args()

    run(
        track_ids=args.track_id,
        dry_run=args.dry_run,
        new_only=args.new_only,
    )


if __name__ == "__main__":
    main()
