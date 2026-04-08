#!/usr/bin/env python3
"""
Backfill historical Spotify stream data from mystreamcount.com.

Fetches per-track daily history from mystreamcount.com and inserts rows
into streams_history.csv for dates BEFORE the earliest existing date per track.
Existing rows are never modified.

Usage:
    python backfill.py                          # The Life of a Showgirl (default)
    python backfill.py --dry-run               # Simulate without writing
    python backfill.py --album folklore        # Other album (filename without .json)
    python backfill.py --reset-state           # Clear progress and restart
    python backfill.py --track-id ID1 ID2 ...  # Force specific track IDs
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date as _date
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# Make extras/ importable (export_for_web lives there)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extras"))
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Paths ──────────────────────────────────────────────────────────────────────
_SCRIPT_DIR  = Path(__file__).resolve().parent
_REPO_ROOT   = _SCRIPT_DIR.parents[3]  # mystreamcount/ → streams/ → spotify/ → collectors/ → root
HISTORY_PATH = _REPO_ROOT / "db" / "streams_history.csv"
ALBUMS_DIR   = _REPO_ROOT / "db" / "discography" / "albums"
STATE_PATH   = _SCRIPT_DIR / "state.json"

# ── mystreamcount.com ──────────────────────────────────────────────────────────
MSC_BASE      = "https://www.mystreamcount.com"
MSC_TRACK_URL = MSC_BASE + "/track/{track_id}"
MSC_API_URL   = MSC_BASE + "/api/track/{track_id}/streams"

POLL_SLEEP_S = 2
POLL_MAX     = 30

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_TRACK_ID_RE = re.compile(r"track/([A-Za-z0-9]+)")


# ── Session ────────────────────────────────────────────────────────────────────

def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=1.0,
        status_forcelist={500, 502, 503, 504},  # NOT 429 — handled manually
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(_HEADERS)
    return session


# ── State ──────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return {
                "done":            set(raw.get("done", [])),
                "playwright_mode": bool(raw.get("playwright_mode", False)),
            }
        except Exception:
            pass
    return {"done": set(), "playwright_mode": False}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {"done": sorted(state["done"]), "playwright_mode": state["playwright_mode"]},
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(STATE_PATH)


# ── Discography ────────────────────────────────────────────────────────────────

def load_tracks_from_album(album_path: Path) -> list[dict]:
    data = json.loads(album_path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    result: list[dict] = []
    for section in data.get("sections", []):
        for t in section.get("tracks", []):
            url = (t.get("url") or t.get("spotify_url") or "").strip()
            m = _TRACK_ID_RE.search(url)
            if not m:
                continue
            track_id = m.group(1)
            if track_id in seen:
                continue
            seen.add(track_id)
            result.append({"track_id": track_id, "title": (t.get("title") or "").strip()})
    return result


def load_tracks_by_ids(track_ids: list[str]) -> list[dict]:
    """Build minimal track dicts from explicit IDs (title = ID)."""
    return [{"track_id": tid, "title": tid} for tid in track_ids]


# ── CSV ────────────────────────────────────────────────────────────────────────

def load_csv_index() -> tuple[list[dict], dict[str, str]]:
    """Returns (all_rows, min_date_per_track)."""
    rows: list[dict] = []
    if HISTORY_PATH.exists():
        with HISTORY_PATH.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

    min_date: dict[str, str] = {}
    for r in rows:
        tid = r.get("track_id", "").strip()
        d   = r.get("date", "").strip()
        if tid and d:
            if tid not in min_date or d < min_date[tid]:
                min_date[tid] = d

    return rows, min_date


def merge_new_rows_into_csv(
    new_rows: list[dict],
    existing_rows: list[dict],
    min_date_per_track: dict[str, str],
    dry_run: bool,
) -> int:
    existing_keys: set[tuple[str, str]] = {
        (r.get("date", ""), r.get("track_id", "")) for r in existing_rows
    }

    filtered: list[dict] = []
    for r in new_rows:
        tid   = r["track_id"]
        d     = r["date"]
        min_d = min_date_per_track.get(tid)

        # Safety: only dates strictly before earliest existing date for this track
        if min_d is not None and d >= min_d:
            continue
        if (d, tid) in existing_keys:
            continue

        filtered.append(r)
        existing_keys.add((d, tid))  # prevent intra-batch dupes

    if not filtered:
        print("Aucune nouvelle ligne à écrire.")
        return 0

    if dry_run:
        print(f"[DRY-RUN] {len(filtered)} nouvelles lignes seraient écrites.")
        for r in filtered[:10]:
            print(f"  {r['date']}  {r['track_id']}  streams={r['streams']}  daily={r['daily_streams']}")
        if len(filtered) > 10:
            print(f"  … et {len(filtered) - 10} autres")
        return len(filtered)

    all_rows = existing_rows + filtered
    all_rows.sort(key=lambda r: (r.get("date", ""), r.get("track_id", "")))

    fieldnames = ["date", "track_id", "streams", "daily_streams"]
    tmp_path   = HISTORY_PATH.with_suffix(".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    tmp_path.replace(HISTORY_PATH)

    print(f"✓ {len(filtered)} nouvelles lignes écrites dans {HISTORY_PATH.name}")
    return len(filtered)


# ── API mystreamcount ──────────────────────────────────────────────────────────

def fetch_csrf_token(session: requests.Session, track_id: str) -> str | None:
    """GET the track page and extract the CSRF token. Returns None on 429."""
    url = MSC_TRACK_URL.format(track_id=track_id)
    try:
        resp = session.get(url, timeout=20)
    except requests.RequestException as e:
        print(f"  GET error: {e}")
        return None

    if resp.status_code == 429:
        return None  # signal rate limit

    if not resp.ok:
        print(f"  GET {url} → {resp.status_code}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    meta = soup.find("meta", {"name": "csrf-token"})
    if meta and meta.get("content"):
        return str(meta["content"])

    # Fallback: regex
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', resp.text)
    return m.group(1) if m else None


def fetch_stream_data_api(
    session: requests.Session,
    track_id: str,
    csrf: str,
) -> tuple[dict | None, str]:
    """POST to the API and poll until ready. Returns (data, status)."""
    api_url = MSC_API_URL.format(track_id=track_id)
    payload  = {"_token": csrf}
    headers  = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRF-TOKEN": csrf,
        "Referer": MSC_TRACK_URL.format(track_id=track_id),
        "Origin": MSC_BASE,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    for attempt in range(POLL_MAX):
        try:
            resp = session.post(api_url, data=payload, headers=headers, timeout=20)
        except requests.RequestException as e:
            print(f"  POST error (attempt {attempt + 1}): {e}")
            return None, "error"

        if resp.status_code == 429:
            return None, "rate_limited"

        if not resp.ok:
            return None, "error"

        try:
            body = resp.json()
        except Exception:
            return None, "error"

        status = body.get("status")
        if status == "ready":
            return body.get("data") or {}, "ok"
        elif status == "processing":
            if attempt < POLL_MAX - 1:
                time.sleep(POLL_SLEEP_S)
            continue
        else:
            return None, "error"

    return None, "error"


# ── Playwright fallback ────────────────────────────────────────────────────────

def _block_heavy(route) -> None:
    rtype = route.request.resource_type
    url   = route.request.url.lower()
    if rtype in {"media", "font", "image"} or any(
        kw in url for kw in ("doubleclick", "googletagmanager", "google-analytics", "facebook")
    ):
        route.abort()
    else:
        route.continue_()


def fetch_via_playwright(track_id: str) -> tuple[dict | None, str]:
    """Navigate to the MSC page and intercept the auto-fired POST response."""
    api_path = f"/api/track/{track_id}/streams"
    page_url = MSC_TRACK_URL.format(track_id=track_id)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(user_agent=_HEADERS["User-Agent"])
        page    = context.new_page()
        page.route("**/*", _block_heavy)

        try:
            with page.expect_response(
                lambda r: api_path in r.url and r.request.method == "POST",
                timeout=40_000,
            ) as response_info:
                page.goto(page_url, wait_until="domcontentloaded", timeout=35_000)

            api_resp = response_info.value
            if api_resp.status == 429:
                return None, "rate_limited"

            body   = api_resp.json()
            status = body.get("status")

            if status == "ready":
                return body.get("data") or {}, "ok"

            if status == "processing":
                # Poll for follow-up responses the page JS fires automatically
                for _ in range(10):
                    try:
                        with page.expect_response(
                            lambda r: api_path in r.url,
                            timeout=5_000,
                        ) as r2_info:
                            page.wait_for_timeout(2_000)
                        b2 = r2_info.value.json()
                        if b2.get("status") == "ready":
                            return b2.get("data") or {}, "ok"
                    except PlaywrightTimeoutError:
                        break
                return None, "error"

            return None, "error"

        except PlaywrightTimeoutError:
            return None, "error"
        except Exception as e:
            print(f"  Playwright error: {e}")
            return None, "error"
        finally:
            browser.close()


# ── Dispatcher ─────────────────────────────────────────────────────────────────

def fetch_track_data(
    session: requests.Session,
    track_id: str,
    playwright_mode: bool,
) -> tuple[dict | None, str, bool]:
    """Returns (data, status, new_playwright_mode)."""
    if playwright_mode:
        data, status = fetch_via_playwright(track_id)
        return data, status, True

    csrf = fetch_csrf_token(session, track_id)
    if csrf is None:
        print("  [RATE LIMIT] CSRF fetch → bascule Playwright")
        data, status = fetch_via_playwright(track_id)
        return data, status, True

    data, status = fetch_stream_data_api(session, track_id, csrf)
    if status == "rate_limited":
        print("  [RATE LIMIT] API POST → bascule Playwright")
        data, status = fetch_via_playwright(track_id)
        return data, status, True

    return data, status, False


# ── Parser + validation ────────────────────────────────────────────────────────

def parse_msc_response(data: dict, track_id: str) -> list[dict]:
    """
    data = {"2024-01-15": {"total": 325060652, "daily": 12450}, ...}
    Returns list of row dicts with track_id tagged, sorted by date asc.
    """
    rows: list[dict] = []
    for date_str, values in data.items():
        try:
            _date.fromisoformat(date_str)
        except (ValueError, TypeError):
            continue
        total = values.get("total")
        if total is None:
            continue
        rows.append({
            "date":          date_str,
            "track_id":      track_id,
            "streams":       int(total),
            "daily_streams": "",  # recalculé depuis les totaux dans validate_monotonic
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def validate_monotonic(rows: list[dict], title: str) -> list[dict]:
    """
    Nettoie les données MSC corrompues :
      1. Supprime les lignes avec streams <= 0 (donnée absente)
      2. Supprime les lignes où le total décroît (impossible physiquement)
      3. Recalcule daily_streams depuis les totaux validés pour les jours consécutifs
         (les daily MSC sont faux quand les totaux alternent entre vraie/fausse valeur)

    Les lignes sont supposées triées par date asc.
    """
    from datetime import date as _d

    clean: list[dict] = []
    prev_streams: int | None = None
    prev_date_obj: _d | None = None
    removed = 0

    for r in rows:
        s = r["streams"]
        d = r["date"]

        # 1. Streams nuls ou négatifs
        if s <= 0:
            print(f"  [WARN] streams=0 ignoré : {d}")
            removed += 1
            continue

        # 2. Total décroissant
        if prev_streams is not None and s < prev_streams:
            print(
                f"  [WARN] décroissance ignorée : {prev_date_obj} {prev_streams:,}"
                f" → {d} {s:,}  (Δ={s - prev_streams:,})"
            )
            removed += 1
            continue

        # 3. Calcul daily = diff depuis le dernier point valide (consécutif ou non)
        #    → premier point : "" (pas de référence)
        #    → tous les autres : total[J] - total[J-1], même s'il y a un trou
        if prev_streams is not None:
            r = {**r, "daily_streams": s - prev_streams}
        # Sinon reste "" (premier point uniquement)

        clean.append(r)
        prev_streams  = s
        prev_date_obj = curr_date_obj

    if removed:
        print(f"  {removed} ligne(s) corrompue(s) supprimée(s) pour «{title}»")

    return clean


# ── Post-merge export ─────────────────────────────────────────────────────────

def _run_export() -> None:
    """Re-exporte les données web + upload R2 après le merge CSV."""
    import os
    import importlib

    print("\nExport web data...")
    try:
        export_for_web = importlib.import_module("export_for_web")
        os.environ.setdefault("UPLOAD_TO_R2", "1")
        export_for_web.export_for_web()
        print("Export web terminé.")
    except Exception as e:
        print(f"[EXPORT] Erreur (non-bloquant) : {e}")


# ── Main orchestration ─────────────────────────────────────────────────────────

def run(tracks: list[dict], dry_run: bool) -> None:
    state   = load_state()
    session = build_session()

    existing_rows, min_date_per_track = load_csv_index()
    print(f"CSV chargé : {len(existing_rows)} lignes existantes")
    if min_date_per_track:
        global_min = min(min_date_per_track.values())
        print(f"Date min globale dans le CSV : {global_min}")

    pending = [t for t in tracks if t["track_id"] not in state["done"]]
    already_done = len(tracks) - len(pending)
    print(f"{len(tracks)} tracks total | {already_done} déjà faits | {len(pending)} à traiter\n")

    playwright_mode = state["playwright_mode"]
    all_new_rows: list[dict] = []

    for i, track in enumerate(pending, 1):
        tid   = track["track_id"]
        title = track["title"]
        min_d = min_date_per_track.get(tid, "—")
        print(f"[{i}/{len(pending)}] {title}  ({tid})  min_date_existante={min_d}")

        if dry_run:
            print("  [DRY-RUN] fetch ignoré\n")
            continue

        data, status, playwright_mode = fetch_track_data(session, tid, playwright_mode)
        state["playwright_mode"] = playwright_mode

        if status == "ok" and data:
            rows = parse_msc_response(data, tid)
            rows = validate_monotonic(rows, title)
            # Show only rows that would actually be inserted
            insertable = [
                r for r in rows
                if (min_date_per_track.get(tid) is None or r["date"] < min_date_per_track[tid])
            ]
            print(f"  {len(rows)} dates valides  |  {len(insertable)} avant {min_d}")
            all_new_rows.extend(rows)
            state["done"].add(tid)
        elif status == "error":
            print(f"  ERREUR — sera réessayé au prochain run")
        else:
            print(f"  Statut inattendu: {status}")

        save_state(state)

        if not playwright_mode:
            time.sleep(1.5)  # polite delay in API mode

        print()

    print("─" * 60)
    if all_new_rows:
        written = merge_new_rows_into_csv(all_new_rows, existing_rows, min_date_per_track, dry_run=False)
        if written and not dry_run:
            _run_export()
    elif not dry_run:
        print("Aucune donnée à fusionner.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill historique streams depuis mystreamcount.com"
    )
    parser.add_argument(
        "--album",
        default="the_life_of_a_showgirl",
        metavar="NAME",
        help="Nom du fichier album JSON sans extension (défaut: the_life_of_a_showgirl)",
    )
    parser.add_argument(
        "--track-id", nargs="+", metavar="ID",
        help="Forcer des track IDs spécifiques (ignore --album)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche ce qui serait fait sans modifier le CSV ni le state",
    )
    parser.add_argument(
        "--reset-state", action="store_true",
        help="Efface state.json avant de démarrer (recommence depuis zéro)",
    )
    args = parser.parse_args()

    if args.reset_state and not args.dry_run:
        if STATE_PATH.exists():
            STATE_PATH.unlink()
            print("State effacé.\n")

    if args.track_id:
        tracks = load_tracks_by_ids(args.track_id)
    else:
        album_path = ALBUMS_DIR / f"{args.album}.json"
        if not album_path.exists():
            print(f"ERREUR : fichier album introuvable : {album_path}")
            sys.exit(1)
        tracks = load_tracks_from_album(album_path)
        print(f"Album : {album_path.name}  →  {len(tracks)} tracks\n")

    run(tracks, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
