#!/usr/bin/env python3
"""
fix_streams.py — Scrape le total actuel de toutes les chansons et corrige
la dernière ligne du CSV.

Améliorations v2 :
  - Images bloquées (gain réseau, Spotify charge ~10 covers par page)
  - wait_for_function() au lieu de sleeps fixes (pages rapides → sortie immédiate)
  - Cache navigateur persistant par worker (user_data_dir, JS bundle mis en cache)
  - Retry automatique des NOT FOUND (2ème passe 30s après, 3 workers)
  - Hill climbing adaptatif : ajuste le nombre de workers selon le taux de 429

Le daily_streams n'est PAS touché : il sera recalculé à la prochaine update.

Usage:
  python fix_streams.py             # corrige tout
  python fix_streams.py --dry-run  # affiche sans écrire
"""
from __future__ import annotations

import csv
import json
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path
from queue import Empty, Queue

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR  = Path(__file__).resolve().parent
_REPO_ROOT   = _SCRIPT_DIR.parents[2]
_DB_ROOT     = _REPO_ROOT / "db"

sys.path.insert(0, str(_SCRIPT_DIR / "tools" / "scripts"))
sys.path.insert(0, str(_SCRIPT_DIR / "extras"))

import export_for_web          # noqa: E402
from git_ops import git_commit_and_push  # noqa: E402

HISTORY_PATH = _DB_ROOT / "streams_history.csv"
ALBUMS_DIR   = _DB_ROOT / "discography" / "albums"
SONGS_JSON   = _DB_ROOT / "discography" / "songs.json"
SESSION_PATH = _SCRIPT_DIR / "tools" / "json" / "spotify_session.json"
CACHE_DIR    = _SCRIPT_DIR / "tools" / "browser_cache"

PAGE_GOTO_TIMEOUT_MS = 20_000
HEADLESS             = True
NUM_WORKERS          = 6        # point de départ pour le hill climbing
RATE_LIMIT_WAIT      = 60       # secondes d'attente si 429
MAX_WORKERS          = 10       # plafond du hill climbing
MIN_WORKERS          = 2        # plancher du hill climbing
HILL_WINDOW          = 20       # nombre de completions par fenêtre d'évaluation
HILL_429_THRESHOLD   = 0.15     # taux de 429 au-delà duquel on retire 1 worker

_START_TIME: float | None = None

# ---------------------------------------------------------------------------
# Hill climbing adaptatif
# ---------------------------------------------------------------------------

class AdaptiveWorkerState:
    """
    Partagé entre tous les workers.
    Suit le taux de 429 sur des fenêtres glissantes et ajuste
    le nombre cible de workers actifs.
    """

    def __init__(self, initial: int) -> None:
        self.target        = initial
        self.lock          = threading.Lock()
        self._win_done     = 0
        self._win_429      = 0
        self._win_start    = time.time()

    def record(self, got_429: bool) -> None:
        with self.lock:
            self._win_done += 1
            if got_429:
                self._win_429 += 1

            if self._win_done >= HILL_WINDOW:
                elapsed  = max(time.time() - self._win_start, 0.001)
                rate_429 = self._win_429 / self._win_done
                rate_sps = self._win_done / elapsed

                if rate_429 > HILL_429_THRESHOLD and self.target > MIN_WORKERS:
                    self.target -= 1
                    print(f"  [hill] 429={rate_429:.0%}  {rate_sps:.2f} songs/s  → workers: {self.target}")
                elif rate_429 == 0 and self.target < MAX_WORKERS:
                    self.target += 1
                    print(f"  [hill] 0 429s  {rate_sps:.2f} songs/s  → workers: {self.target}")

                self._win_done  = 0
                self._win_429   = 0
                self._win_start = time.time()

# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------

def parse_int_from_text(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def extract_track_id(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"track/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def normalize_spotify_track_url(url: str) -> str:
    tid = extract_track_id(url)
    return f"https://open.spotify.com/track/{tid}" if tid else url.strip()


def is_duration_line(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", text.strip()))


def is_large_number_line(text: str) -> bool:
    cleaned = text.strip().replace("\u202f", " ").replace("\xa0", " ")
    if not re.fullmatch(r"[\d\s,.\']+", cleaned):
        return False
    v = parse_int_from_text(cleaned)
    return v is not None and v >= 1000


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.lower().strip())


def maybe_accept_cookies(page) -> None:
    for pattern in (r"Accept", r"Accept all", r"Accepter", r"Autoriser"):
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=1500)
            page.wait_for_timeout(800)
            return
        except Exception:
            pass


def extract_main_track_playcount_from_lines(lines: list[str]) -> int | None:
    start_idx = None
    for i, line in enumerate(lines):
        if line.strip().lower() in ("titre", "title"):
            start_idx = i
            break
    if start_idx is None:
        return None

    end_markers = {
        "connectez-vous", "se connecter", "artiste", "recommandes", "recommandés",
        "basees sur ce titre", "basées sur ce titre",
        "titres populaires par", "sorties populaires par taylor swift",
    }
    block: list[str] = []
    for line in lines[start_idx + 1:]:
        if normalize_title(line.strip()) in end_markers:
            break
        block.append(line.strip())

    if not block:
        return None

    for i, line in enumerate(block):
        if is_duration_line(line):
            for j in range(i + 1, min(i + 6, len(block))):
                c = block[j].strip()
                if c in {"•", "-", ""}:
                    continue
                if is_large_number_line(c):
                    return parse_int_from_text(c)

    numerics = [parse_int_from_text(l) for l in block if is_large_number_line(l.strip())]
    numerics = [v for v in numerics if v is not None]
    return numerics[0] if len(numerics) == 1 else None


def extract_playcount_via_js(page) -> int | None:
    try:
        result = page.evaluate("""() => {
            const candidates = [];
            document.querySelectorAll('[data-testid]').forEach(el => {
                const txt = (el.innerText || '').trim();
                if (txt && /^[\\d\u202f\u00a0\\s,.']+$/.test(txt)) {
                    const n = parseInt(txt.replace(/[^\\d]/g, ''));
                    if (!isNaN(n) && n >= 10000) candidates.push(n);
                }
            });
            return candidates.length === 1 ? candidates[0] : null;
        }""")
        if result is not None:
            return int(result)
    except Exception:
        pass
    return None


# JS utilisé par wait_for_function pour détecter l'apparition du play count
_WAIT_FOR_PLAYCOUNT_JS = (
    "() => { "
    "  for (const el of document.querySelectorAll('[data-testid], span, div')) { "
    "    const n = parseInt((el.innerText || '').replace(/[^\\d]/g, '')); "
    "    if (!isNaN(n) && n >= 100000) return true; "
    "  } "
    "  return false; "
    "}"
)


def scrape_total(page, title: str, url: str, adaptive: AdaptiveWorkerState) -> int | None:
    """
    Scrape le total de streams d'un track.
    Utilise wait_for_function() au lieu de sleeps fixes.
    Signale le résultat à l'AdaptiveWorkerState.
    """
    clean_url = normalize_spotify_track_url(url)
    got_429   = False

    for attempt in range(3):
        try:
            response = page.goto(clean_url, wait_until="commit", timeout=PAGE_GOTO_TIMEOUT_MS)

            if response and response.status == 429:
                got_429 = True
                print(f"  429 {title} — attente {RATE_LIMIT_WAIT}s...")
                adaptive.record(got_429=True)
                page.wait_for_timeout(RATE_LIMIT_WAIT * 1000)
                continue

            page.wait_for_timeout(500)
            maybe_accept_cookies(page)

            # Vérification 429 dans le body
            try:
                snippet = page.locator("body").inner_text(timeout=2000)[:500]
                if "429" in snippet and "too many" in snippet.lower():
                    got_429 = True
                    print(f"  429 body {title} — attente {RATE_LIMIT_WAIT}s...")
                    adaptive.record(got_429=True)
                    page.wait_for_timeout(RATE_LIMIT_WAIT * 1000)
                    continue
            except Exception:
                pass

            # Attendre activement l'apparition d'un grand nombre dans le DOM
            try:
                page.wait_for_function(_WAIT_FOR_PLAYCOUNT_JS, timeout=8000)
            except Exception:
                pass

            # Extraction (une seule tentative après l'attente)
            try:
                body = page.locator("body").inner_text(timeout=5000)
            except Exception:
                body = ""

            if body:
                lines = [
                    l.replace("\u202f", " ").replace("\xa0", " ").strip()
                    for l in body.splitlines() if l.strip()
                ]
                total = extract_main_track_playcount_from_lines(lines)
                if total is not None:
                    adaptive.record(got_429=False)
                    return total
                total = extract_playcount_via_js(page)
                if total is not None:
                    adaptive.record(got_429=False)
                    return total

            # Pas trouvé après l'attente → fallback avec retries courts
            for wait_ms in (1000, 2500):
                page.wait_for_timeout(wait_ms)
                try:
                    body = page.locator("body").inner_text(timeout=4000)
                except Exception:
                    body = ""
                if body:
                    lines = [
                        l.replace("\u202f", " ").replace("\xa0", " ").strip()
                        for l in body.splitlines() if l.strip()
                    ]
                    total = extract_main_track_playcount_from_lines(lines)
                    if total is not None:
                        adaptive.record(got_429=False)
                        return total
                    total = extract_playcount_via_js(page)
                    if total is not None:
                        adaptive.record(got_429=False)
                        return total

            adaptive.record(got_429=False)
            return None

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

    adaptive.record(got_429=got_429)
    return None


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_tracks_from_discography() -> list[dict]:
    seen: dict[str, dict] = {}

    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                for track in section.get("tracks", []):
                    url = (track.get("url") or track.get("spotify_url") or "").strip()
                    tid = extract_track_id(url)
                    if not tid or tid in seen:
                        continue
                    title = (track.get("title") or "").strip()
                    if not title:
                        continue
                    seen[tid] = {
                        "track_id": tid,
                        "title":    title,
                        "url":      f"https://open.spotify.com/track/{tid}",
                    }

    if SONGS_JSON.exists():
        try:
            sections = json.loads(SONGS_JSON.read_text(encoding="utf-8-sig"))
        except Exception:
            sections = []
        for section in sections:
            for track in section.get("tracks", []):
                url = (track.get("url") or track.get("spotify_url") or "").strip()
                tid = extract_track_id(url)
                if not tid or tid in seen:
                    continue
                title = (track.get("title") or "").strip()
                if not title:
                    continue
                seen[tid] = {
                    "track_id": tid,
                    "title":    title,
                    "url":      f"https://open.spotify.com/track/{tid}",
                }
    return sorted(seen.values(), key=lambda t: t["title"].casefold())


def load_csv_rows() -> tuple[list[str], list[dict]]:
    if not HISTORY_PATH.exists():
        return [], []
    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def save_csv_rows(fieldnames: list[str], rows: list[dict]) -> None:
    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def find_last_row_index(rows: list[dict], track_id: str) -> int | None:
    last_idx = None
    for i, r in enumerate(rows):
        if (r.get("track_id") or "").strip() == track_id:
            last_idx = i
    return last_idx


def parse_streams(row: dict) -> int | None:
    raw = (row.get("streams") or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _worker(
    worker_id:    int,
    task_queue:   Queue,
    results:      list[dict],
    print_lock:   threading.Lock,
    total_tasks:  int,
    adaptive:     AdaptiveWorkerState,
    done_counter: list[int] | None = None,
) -> None:
    # Hill climbing : attendre que ce slot soit activé avant d'ouvrir le browser.
    # Quand target augmente, ce thread se réveille, ouvre sa fenêtre et commence à travailler.
    while True:
        with adaptive.lock:
            if worker_id < adaptive.target:
                break
        time.sleep(1)

    # Initialisation du browser (seulement quand le slot est actif)
    worker_cache = CACHE_DIR / f"worker_{worker_id}"
    worker_cache.mkdir(parents=True, exist_ok=True)

    p = sync_playwright().start()
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(worker_cache),
        headless=HEADLESS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )

    # Injecter la session Spotify (cookies) si disponible
    if SESSION_PATH.exists():
        try:
            session_data = json.loads(SESSION_PATH.read_text(encoding="utf-8-sig"))
            cookies = session_data.get("cookies", [])
            if cookies:
                context.add_cookies(cookies)
        except Exception:
            pass

    def block_unneeded(route):
        blocked_types = {"media", "font", "image"}
        blocked_kw    = ("doubleclick", "googletagmanager", "google-analytics",
                         "analytics", "facebook", ".mp4", ".webm", ".mp3",
                         ".woff", ".woff2", ".ttf")
        if (route.request.resource_type in blocked_types
                or any(x in route.request.url.lower() for x in blocked_kw)):
            route.abort()
        else:
            route.continue_()

    context.route("**/*", block_unneeded)
    # Réutiliser l'onglet about:blank ouvert automatiquement par le profil persistant
    page = context.pages[0] if context.pages else context.new_page()

    try:
        while True:
            # Hill climbing : si la cible redescend sous notre id, pause (drain naturel)
            while True:
                with adaptive.lock:
                    target = adaptive.target
                if worker_id < target:
                    break
                time.sleep(2)

            try:
                item = task_queue.get_nowait()
            except Empty:
                break

            i            = item["index"]
            track        = item["track"]
            last_idx     = item["last_idx"]
            stored_total = item["stored_total"]
            stored_date  = item["stored_date"]
            title        = track["title"]

            scraped = scrape_total(page, title, track["url"], adaptive)

            with print_lock:
                if done_counter is not None:
                    done_counter[0] += 1
                    done = done_counter[0]
                else:
                    done = i
                elapsed = time.perf_counter() - _START_TIME if _START_TIME else 0
                remaining = (elapsed / done) * max(total_tasks - done, 0) if done > 0 else 0
                eta_str = f"  ETA {int(remaining // 60)}m{int(remaining % 60):02d}s"
                prefix = f"[{i:3}/{total_tasks}]{eta_str}  {title:<50}"
                if scraped is None:
                    print(f"{prefix} NOT FOUND")
                    results.append({"status": "not_found", "title": title,
                                    "track_id": track["track_id"], "url": track["url"],
                                    "last_idx": last_idx})
                elif scraped == stored_total:
                    print(f"{prefix} = {scraped:>15,}  (inchangé)")
                    results.append({"status": "unchanged", "title": title})
                else:
                    delta = scraped - (stored_total or 0)
                    sign  = "+" if delta >= 0 else ""
                    print(f"{prefix} FIX {scraped:>15,}  (was {stored_total:,}  {sign}{delta:,})  [{stored_date}]")
                    results.append({
                        "status":      "fixed",
                        "title":       title,
                        "date":        stored_date,
                        "old_total":   stored_total,
                        "new_total":   scraped,
                        "delta_total": delta,
                        "last_idx":    last_idx,
                    })

            task_queue.task_done()
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass


def _run_workers(
    task_queue:       Queue,
    results:          list[dict],
    print_lock:       threading.Lock,
    total_tasks:      int,
    adaptive:         AdaptiveWorkerState,
    n_workers:        int,
    worker_id_offset: int = 0,
    done_counter:     list[int] | None = None,
) -> None:
    threads = [
        threading.Thread(
            target=_worker,
            args=(worker_id_offset + idx, task_queue, results, print_lock, total_tasks, adaptive, done_counter),
            daemon=True,
        )
        for idx in range(n_workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _START_TIME
    _START_TIME = time.perf_counter()

    dry_run = "--dry-run" in sys.argv
    print(f"fix_streams.py — dry_run={dry_run}, workers_init={NUM_WORKERS}")
    print()

    tracks           = load_tracks_from_discography()
    fieldnames, rows = load_csv_rows()
    print(f"  {len(tracks)} tracks discography | {len(rows)} lignes CSV")
    print()

    if not fieldnames:
        fieldnames = ["date", "track_id", "streams", "daily_streams"]

    # ── Passe principale ──────────────────────────────────────────────────
    task_queue:       Queue     = Queue()
    skipped_no_history          = 0
    tracks_by_id: dict[str, dict] = {t["track_id"]: t for t in tracks}

    for i, track in enumerate(tracks, 1):
        last_idx = find_last_row_index(rows, track["track_id"])
        if last_idx is None:
            skipped_no_history += 1
            continue
        task_queue.put({
            "index":        i,
            "track":        track,
            "last_idx":     last_idx,
            "stored_total": parse_streams(rows[last_idx]),
            "stored_date":  rows[last_idx].get("date", "?"),
        })

    total_tasks = task_queue.qsize()
    print(f"  {total_tasks} tracks à scraper | {skipped_no_history} sans historique")
    print()

    results:      list[dict]    = []
    print_lock:   threading.Lock = threading.Lock()
    done_counter: list[int]      = [0]
    initial = min(NUM_WORKERS, total_tasks)
    adaptive = AdaptiveWorkerState(initial=initial)

    # Spawner MAX_WORKERS threads dès maintenant.
    # Les workers avec worker_id >= initial attendent leur activation (pas de fenêtre ouverte).
    # Quand adaptive.target monte, ils se réveillent et ouvrent leur browser.
    _run_workers(task_queue, results, print_lock, total_tasks, adaptive,
                 n_workers=min(MAX_WORKERS, total_tasks), done_counter=done_counter)

    # ── Retry NOT FOUND ───────────────────────────────────────────────────
    not_found = [r for r in results if r.get("status") == "not_found"]
    if not_found:
        print(f"\n  {len(not_found)} NOT FOUND — retry dans 30s avec {min(3, len(not_found))} workers...")
        time.sleep(30)

        retry_queue: Queue = Queue()
        for idx, r in enumerate(not_found, 1):
            track = tracks_by_id.get(r["track_id"])
            if not track:
                continue
            retry_queue.put({
                "index":        idx,
                "track":        track,
                "last_idx":     r["last_idx"],
                "stored_total": parse_streams(rows[r["last_idx"]]) if r["last_idx"] is not None else None,
                "stored_date":  rows[r["last_idx"]].get("date", "?") if r["last_idx"] is not None else "?",
            })

        retry_results: list[dict] = []
        retry_total   = retry_queue.qsize()
        retry_adaptive = AdaptiveWorkerState(initial=min(3, retry_total))

        _run_workers(retry_queue, retry_results, print_lock, retry_total, retry_adaptive,
                     n_workers=min(3, retry_total), worker_id_offset=NUM_WORKERS)

        # Fusionner les résultats du retry dans results[]
        found_ids = {r["track_id"] for r in not_found}
        results   = [r for r in results if r.get("track_id") not in found_ids or r["status"] != "not_found"]
        results.extend(retry_results)

    # ── Appliquer les corrections ─────────────────────────────────────────
    fixed_results = [r for r in results if r.get("status") == "fixed"]
    if not dry_run and fixed_results:
        for r in fixed_results:
            rows[r["last_idx"]]["streams"] = str(r["new_total"])
        save_csv_rows(fieldnames, rows)
        print(f"\nCSV mis à jour ({len(fixed_results)} correction(s)).")
    elif dry_run:
        print("\n[DRY-RUN] Aucune écriture.")
    else:
        print("\nAucune correction nécessaire.")

    counts: dict[str, int] = {}
    for r in results:
        s = r.get("status", "?")
        counts[s] = counts.get(s, 0) + 1

    elapsed = time.perf_counter() - _START_TIME if _START_TIME else 0

    print()
    print("=" * 60)
    print(f"  Corrigés   : {counts.get('fixed', 0)}")
    print(f"  Inchangés  : {counts.get('unchanged', 0)}")
    print(f"  NOT FOUND  : {counts.get('not_found', 0)}")
    print(f"  Sans histo : {skipped_no_history}")
    print(f"  Durée      : {int(elapsed // 60)}m {int(elapsed % 60):02d}s")
    print("=" * 60)

    if fixed_results:
        print()
        print("Détail des corrections :")
        for r in fixed_results:
            sign = "+" if r["delta_total"] >= 0 else ""
            print(
                f"  {r['title']:<45} [{r['date']}]  "
                f"{r['old_total']:>13,} -> {r['new_total']:>13,}  ({sign}{r['delta_total']:,})"
            )

    if not dry_run and fixed_results:
        print()
        print("Exporting for web...")
        export_for_web.export_for_web()
        print("Git commit and push...")
        git_commit_and_push(_REPO_ROOT, f"fix streams {counts.get('fixed', 0)} corrections")


if __name__ == "__main__":
    main()
