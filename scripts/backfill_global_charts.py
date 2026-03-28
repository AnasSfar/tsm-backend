#!/usr/bin/env python3
"""
Backfill db/charts_history_global.csv et db/charts_history_fr.csv

Architecture hybride :
  1. Playwright ONCE pour obtenir un Bearer token OAuth
  2. requests pur pour toutes les dates (vite, pas de browser)

URL de l'API :
  https://charts-spotify-com-service.spotify.com/auth/v0/charts/{chart_id}/{date}

Usage :
    python scripts/backfill_global_charts.py
    python scripts/backfill_global_charts.py --charts global
    python scripts/backfill_global_charts.py --charts fr
    python scripts/backfill_global_charts.py --start 2019-01-01 --end 2022-12-31
    python scripts/backfill_global_charts.py --dl-workers 10 --filter-workers 4
    python scripts/backfill_global_charts.py --headless          # browser sans fenêtre
    python scripts/backfill_global_charts.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from queue import Empty, Queue

import requests
from playwright.sync_api import sync_playwright

# ── Repo paths ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]

CHARTS: dict[str, dict] = {
    "global": {
        "label":        "GLOBAL",
        "chart_id":     "regional-global-daily",
        "session":      REPO_ROOT / "collectors/spotify/charts/global/tools/json/spotify_session.json",
        "history_root": REPO_ROOT / "collectors/spotify/charts/global/history",
        "archive_csv":  REPO_ROOT / "db/charts_history_global.csv",
    },
    "fr": {
        "label":        "FR    ",
        "chart_id":     "regional-fr-daily",
        "session":      REPO_ROOT / "collectors/spotify/charts/fr/tools/json/spotify_session.json",
        "history_root": REPO_ROOT / "collectors/spotify/charts/fr/history",
        "archive_csv":  REPO_ROOT / "db/charts_history_fr.csv",
    },
}

FIELDNAMES = ["date", "song_name", "rank", "streams", "previous_rank", "peak_rank", "total_days"]
TS_NAME    = "Taylor Swift"
API_BASE   = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"

# Sentinel pour arrêter les filter workers
_SENTINEL = object()

# ── Hill climbing ────────────────────────────────────────────────────────────
HILL_WINDOW          = 20
HILL_ERROR_THRESHOLD = 0.20
HILL_MIN_WORKERS     = 1

# ── HTTP constants ───────────────────────────────────────────────────────────
HTTP_TIMEOUT     = 30
RETRY_COUNT      = 3
RETRY_WAIT       = 2.0
RETRY_WAIT_429   = 15.0
TOKEN_REFRESH_AT = 2800   # secondes (tokens Spotify durent ~3600s)

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://charts.spotify.com/",
}


# ══════════════════════════════════════════════════════════════════════════════
# Token Bearer (Playwright une seule fois par chart)
# ══════════════════════════════════════════════════════════════════════════════

def get_bearer_token(session_file: Path, headless: bool, log_fn) -> str:
    """
    Lance Playwright, charge charts.spotify.com, capture le Bearer token
    utilisé par la page pour appeler l'API interne Spotify Charts.
    Retourne le token (sans le préfixe 'Bearer ').
    """
    log_fn("Récupération du Bearer token via Playwright…")
    token_holder: list[str] = []

    def on_request(req):
        if (
            "charts-spotify-com-service.spotify.com" in req.url
            and not token_holder
        ):
            auth = req.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token_holder.append(auth[7:])

    p = sync_playwright().start()
    try:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            storage_state=str(session_file),
            user_agent=BASE_HEADERS["User-Agent"],
        )
        page = ctx.new_page()
        page.on("request", on_request)

        # Charger la page d'accueil suffit pour déclencher l'OAuth flow
        page.goto(
            "https://charts.spotify.com/charts/view/regional-global-daily/latest",
            wait_until="domcontentloaded",
            timeout=30_000,
        )

        deadline = time.time() + 20
        while not token_holder and time.time() < deadline:
            page.wait_for_timeout(300)

    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    if token_holder:
        log_fn(f"Token obtenu : {token_holder[0][:30]}…")
        return token_holder[0]
    raise RuntimeError("Impossible d'obtenir le Bearer token — vérifiez spotify_session.json")


# ══════════════════════════════════════════════════════════════════════════════
# TokenManager — partagé entre les workers d'un même chart
# ══════════════════════════════════════════════════════════════════════════════

class TokenManager:
    """Thread-safe Bearer token avec refresh automatique."""

    def __init__(self, session_file: Path, headless: bool, log_fn) -> None:
        self._session_file = session_file
        self._headless     = headless
        self._log          = log_fn
        self._lock         = threading.Lock()
        self._token        = ""
        self._issued_at    = 0.0

    def get(self) -> str:
        with self._lock:
            if not self._token or time.time() - self._issued_at > TOKEN_REFRESH_AT:
                self._token     = get_bearer_token(self._session_file, self._headless, self._log)
                self._issued_at = time.time()
            return self._token

    def invalidate(self) -> None:
        """Forcer un refresh au prochain appel (appelé sur 401)."""
        with self._lock:
            self._issued_at = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Parsing de la réponse JSON de l'API Spotify Charts
# ══════════════════════════════════════════════════════════════════════════════

def _int_or_none(v) -> int | None:
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _int_for_csv(v) -> int | str:
    try:
        return int(v) if v not in (None, "", "None") else ""
    except (ValueError, TypeError):
        return ""


def _parse_api_entries(data: dict) -> list[dict]:
    rows = []
    entries = data.get("entries") or data.get("chartEntryList") or []
    for entry in entries:
        ced  = entry.get("chartEntryData") or entry
        meta = entry.get("trackMetadata") or ced.get("trackMetadata") or {}

        rank    = ced.get("currentRank") or ced.get("rank")
        prev    = ced.get("previousRank")
        peak    = ced.get("peakRank")
        streak  = ced.get("consecutiveAppearancesOnChart") or ced.get("streak")
        streams = (ced.get("rankingMetric") or {}).get("value") or ced.get("streams")

        track   = (meta.get("trackName") or ced.get("trackName") or "").replace('\u2019', "'").replace('\u2018', "'")
        artists = meta.get("artists") or []
        if artists:
            artist_str = ", ".join(a.get("name", "") for a in artists if a.get("name"))
        else:
            artist_str = meta.get("artistName") or ced.get("artistName") or ""

        if not track or rank is None:
            continue

        rows.append({
            "rank":          int(rank),
            "track_name":    track.strip(),
            "artist_names":  artist_str.strip(),
            "streams":       _int_or_none(streams),
            "previous_rank": _int_or_none(prev),
            "peak_rank":     _int_or_none(peak),
            "total_days":    _int_or_none(streak),
        })

    rows.sort(key=lambda r: r["rank"])
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# CSV helpers
# ══════════════════════════════════════════════════════════════════════════════

def _dates_in_archive(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    with csv_path.open(encoding="utf-8") as f:
        return {row["date"] for row in csv.DictReader(f)}


def _is_processed(history_root: Path, d: str) -> bool:
    out = history_root / d[:4] / d[5:7] / d
    return (
        (out / "ts_all_songs.csv").exists()
        or (out / "no_ts.lock").exists()
        or (out / "page_not_found.lock").exists()
    )


def _write_date_to_csv(
    archive_csv: Path,
    chart_date: str,
    ts_rows: list[dict],
    csv_lock: threading.Lock,
    written_dates: set,
) -> None:
    with csv_lock:
        if chart_date in written_dates:
            return
        write_header = not archive_csv.exists()
        archive_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            if write_header:
                w.writeheader()
            for r in ts_rows:
                w.writerow({
                    "date":          chart_date,
                    "song_name":     r.get("track_name", ""),
                    "rank":          _int_for_csv(r.get("rank")),
                    "streams":       _int_for_csv(r.get("streams")),
                    "previous_rank": _int_for_csv(r.get("previous_rank")),
                    "peak_rank":     _int_for_csv(r.get("peak_rank")),
                    "total_days":    _int_for_csv(r.get("total_days")),
                })
        written_dates.add(chart_date)


# ══════════════════════════════════════════════════════════════════════════════
# Hill climbing
# ══════════════════════════════════════════════════════════════════════════════

class AdaptiveWorkerState:
    def __init__(self, initial: int, max_workers: int, label: str, log_fn) -> None:
        self.target      = initial
        self.max_workers = max_workers
        self.label       = label
        self._log        = log_fn
        self.lock        = threading.Lock()
        self._win_done   = 0
        self._win_errors = 0
        self._win_start  = time.time()

    def record(self, got_error: bool) -> None:
        with self.lock:
            self._win_done += 1
            if got_error:
                self._win_errors += 1
            if self._win_done < HILL_WINDOW:
                return

            elapsed  = max(time.time() - self._win_start, 0.001)
            rate_err = self._win_errors / self._win_done
            speed    = self._win_done / elapsed

            if rate_err > HILL_ERROR_THRESHOLD and self.target > HILL_MIN_WORKERS:
                self.target -= 1
                self._log(
                    f"[hill] erreurs={rate_err:.0%}  {speed:.1f} req/s"
                    f"  → dl workers: {self.target + 1} → {self.target}"
                )
            elif rate_err == 0 and self.target < self.max_workers:
                self.target += 1
                self._log(
                    f"[hill] 0 erreur  {speed:.1f} req/s"
                    f"  → dl workers: {self.target - 1} → {self.target}"
                )

            self._win_done   = 0
            self._win_errors = 0
            self._win_start  = time.time()


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1 — Download workers  (requests pur, token Bearer partagé)
# ══════════════════════════════════════════════════════════════════════════════

def _download_worker(
    worker_id: int,
    chart_id: str,
    token_mgr: TokenManager,
    dl_queue: Queue,
    filter_queue: Queue,
    adaptive: AdaptiveWorkerState,
    log_fn,
    abort_flag: threading.Event,
):
    # Hill climbing : attendre l'activation de ce slot
    while not abort_flag.is_set():
        with adaptive.lock:
            if worker_id < adaptive.target:
                break
        time.sleep(0.3)
    if abort_flag.is_set():
        return

    log_fn(f"  [dl-{worker_id}] démarrage")
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    try:
        while not abort_flag.is_set():
            with adaptive.lock:
                target = adaptive.target
            if worker_id >= target:
                log_fn(f"  [dl-{worker_id}] désactivé (target={target})")
                break

            try:
                chart_date = dl_queue.get_nowait()
            except Empty:
                break

            url    = f"{API_BASE}/{chart_id}/{chart_date}"
            status = "error"
            data   = None

            for attempt in range(RETRY_COUNT):
                token = token_mgr.get()
                try:
                    resp = session.get(
                        url,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=HTTP_TIMEOUT,
                    )

                    if resp.status_code == 200:
                        try:
                            data   = resp.json()
                            status = "ok"
                        except Exception:
                            status = "error"
                        break

                    elif resp.status_code == 404:
                        status = "page_not_found"
                        break

                    elif resp.status_code == 401:
                        # Token expiré → invalider et retenter
                        token_mgr.invalidate()
                        log_fn(f"  [dl-{worker_id}] token expiré, refresh…")
                        continue

                    elif resp.status_code == 403:
                        status = "session_error"
                        break

                    elif resp.status_code == 429:
                        ra = float(resp.headers.get("Retry-After", RETRY_WAIT_429))
                        log_fn(f"  [dl-{worker_id}] rate-limit, pause {ra:.0f}s")
                        time.sleep(min(ra, RETRY_WAIT_429))

                    else:
                        if attempt < RETRY_COUNT - 1:
                            time.sleep(RETRY_WAIT)

                except requests.exceptions.Timeout:
                    if attempt < RETRY_COUNT - 1:
                        time.sleep(RETRY_WAIT)
                except requests.exceptions.RequestException:
                    if attempt < RETRY_COUNT - 1:
                        time.sleep(RETRY_WAIT)

            adaptive.record(got_error=(status == "error"))

            if status == "session_error":
                log_fn(f"  [dl-{worker_id}] STOP — session expirée")
                abort_flag.set()
                filter_queue.put((chart_date, "session_error", None))
                dl_queue.task_done()
                break

            filter_queue.put((chart_date, status, data))
            dl_queue.task_done()

    except Exception as e:
        log_fn(f"  [dl-{worker_id}] crash: {e}")
    finally:
        session.close()
        log_fn(f"  [dl-{worker_id}] arrêté")


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — Filter workers  (parse JSON, filtre TS, écrit CSV)
# ══════════════════════════════════════════════════════════════════════════════

def _filter_worker(
    worker_id: int,
    cfg: dict,
    filter_queue: Queue,
    done: dict,
    done_lock: threading.Lock,
    csv_lock: threading.Lock,
    written_dates: set,
    log_fn,
):
    hist_root   = cfg["history_root"]
    archive_csv = cfg["archive_csv"]

    log_fn(f"  [filter-{worker_id}] démarrage")

    try:
        while True:
            item = filter_queue.get()

            if item is _SENTINEL:
                filter_queue.task_done()
                break

            chart_date, dl_status, api_data = item
            out_dir = hist_root / chart_date[:4] / chart_date[5:7] / chart_date
            out_dir.mkdir(parents=True, exist_ok=True)

            if dl_status == "session_error":
                with done_lock:
                    done[chart_date] = "session_error"
                filter_queue.task_done()
                break

            elif dl_status == "page_not_found":
                (out_dir / "page_not_found.lock").touch()
                status = "page_not_found"

            elif dl_status == "error":
                status = "error"

            else:  # "ok"
                rows = _parse_api_entries(api_data)
                del api_data   # libération mémoire immédiate

                if not rows:
                    (out_dir / "page_not_found.lock").touch()
                    status = "page_not_found"
                else:
                    ts_rows = [r for r in rows if TS_NAME.lower() in r["artist_names"].lower()]

                    if ts_rows:
                        fields = ["rank", "track_name", "artist_names", "streams",
                                  "previous_rank", "peak_rank", "total_days"]
                        with open(out_dir / "ts_all_songs.csv", "w", newline="", encoding="utf-8") as f:
                            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                            w.writeheader()
                            w.writerows(ts_rows)
                        (out_dir / f"ts_chart_{chart_date}.json").write_text(
                            json.dumps(ts_rows, ensure_ascii=False, default=str),
                            encoding="utf-8",
                        )
                        _write_date_to_csv(archive_csv, chart_date, ts_rows, csv_lock, written_dates)
                        status = "done"
                    else:
                        (out_dir / "no_ts.lock").touch()
                        status = "no_ts"

            icon = {"done": "✓", "no_ts": "—", "page_not_found": "✗", "error": "⚠"}.get(status, "?")
            with done_lock:
                done[chart_date] = status
                n = len(done)
            log_fn(f"  {chart_date}  {icon} {status:15s}  [f{worker_id}]  (#{n})")
            filter_queue.task_done()

    except Exception as e:
        log_fn(f"  [filter-{worker_id}] crash: {e}")
    finally:
        log_fn(f"  [filter-{worker_id}] arrêté")


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _parse_date_arg(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _iter_dates(start: date, end: date):
    cur = end
    while cur >= start:
        yield str(cur)
        cur -= timedelta(days=1)


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrateur
# ══════════════════════════════════════════════════════════════════════════════

def backfill_chart(
    chart_key: str,
    cfg: dict,
    to_process: list[str],
    initial_dl: int,
    max_dl: int,
    n_filter: int,
    headless: bool,
    dry_run: bool,
    print_lock: threading.Lock,
):
    label       = cfg["label"]
    archive_csv = cfg["archive_csv"]

    def log(msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with print_lock:
            print(f"[{label}] {ts}  {msg}", flush=True)

    if dry_run:
        log(f"{len(to_process)} date(s) à traiter (dry-run)")
        for d in to_process[:20]:
            log(f"  {d}")
        if len(to_process) > 20:
            log(f"  ... +{len(to_process) - 20} dates")
        return

    log(f"Démarrage : {len(to_process)} date(s)  dl={initial_dl}..{max_dl}  filter={n_filter}")

    # Token Bearer obtenu une seule fois via Playwright
    token_mgr = TokenManager(cfg["session"], headless, log)
    token_mgr.get()   # eager load (fail fast si session invalide)

    dl_queue      = Queue()
    filter_queue  = Queue()
    done: dict[str, str] = {}
    done_lock     = threading.Lock()
    csv_lock      = threading.Lock()
    written_dates = _dates_in_archive(archive_csv)
    abort_flag    = threading.Event()

    for d in to_process:
        dl_queue.put(d)

    adaptive = AdaptiveWorkerState(
        initial=min(initial_dl, max_dl, len(to_process)),
        max_workers=max_dl,
        label=label,
        log_fn=log,
    )

    dl_threads = [
        threading.Thread(
            target=_download_worker,
            args=(idx, cfg["chart_id"], token_mgr, dl_queue, filter_queue, adaptive, log, abort_flag),
            daemon=True,
            name=f"{chart_key}-dl-{idx}",
        )
        for idx in range(max_dl)
    ]
    filter_threads = [
        threading.Thread(
            target=_filter_worker,
            args=(idx, cfg, filter_queue, done, done_lock, csv_lock, written_dates, log),
            daemon=True,
            name=f"{chart_key}-filter-{idx}",
        )
        for idx in range(n_filter)
    ]

    for t in filter_threads:
        t.start()
    for t in dl_threads:
        t.start()

    dl_queue.join()
    for _ in range(n_filter):
        filter_queue.put(_SENTINEL)
    filter_queue.join()

    for t in dl_threads + filter_threads:
        t.join(timeout=5)

    counts: dict[str, int] = {}
    for v in done.values():
        counts[v] = counts.get(v, 0) + 1

    log(f"Terminé — {counts.get('done', 0)} date(s) avec TS écrites dans le CSV")

    with print_lock:
        print(f"\n[{label}] ── Résumé ─────────────────────────────────")
        print(f"[{label}]   ✓ done           : {counts.get('done', 0)}")
        print(f"[{label}]   — no_ts          : {counts.get('no_ts', 0)}  (page OK, pas de TS)")
        print(f"[{label}]   ✗ page_not_found : {counts.get('page_not_found', 0)}  (date inexistante)")
        print(f"[{label}]   ⚠ error          : {counts.get('error', 0)}  (à relancer)")
        if counts.get("session_error"):
            print(f"[{label}]   🔐 session       : {counts['session_error']}  ← session à renouveler")
        print(flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Backfill Spotify charts via l'API interne (Bearer token + requests)."
    )
    parser.add_argument("--charts",         default="both", choices=["both", "global", "fr"])
    parser.add_argument("--start",          default="2017-01-01")
    parser.add_argument("--end",            default=None)
    parser.add_argument("--dl-workers",     type=int, default=8)
    parser.add_argument("--max-dl-workers", type=int, default=16)
    parser.add_argument("--filter-workers", type=int, default=4)
    parser.add_argument("--headless",       action="store_true", help="Browser sans fenêtre")
    parser.add_argument("--dry-run",        action="store_true")
    args = parser.parse_args()

    start_date = _parse_date_arg(args.start)
    end_date   = _parse_date_arg(args.end) if args.end else date.today() - timedelta(days=1)

    if start_date > end_date:
        print(f"Erreur : --start {start_date} > --end {end_date}")
        return

    active = ["global", "fr"] if args.charts == "both" else [args.charts]

    print(f"Plage      : {start_date} → {end_date}")
    print(f"Charts     : {', '.join(active)}")
    print(f"Workers    : dl={args.dl_workers}..{args.max_dl_workers}  filter={args.filter_workers}")
    print()

    all_dates = list(_iter_dates(start_date, end_date))

    work: dict[str, list[str]] = {}
    for key in active:
        cfg      = CHARTS[key]
        archived = _dates_in_archive(cfg["archive_csv"])
        to_process = [
            d for d in all_dates
            if d not in archived and not _is_processed(cfg["history_root"], d)
        ]
        print(f"[{cfg['label']}] {len(archived)} dates dans CSV, {len(to_process)}/{len(all_dates)} à traiter")
        work[key] = to_process

    print()

    if not any(work.values()):
        print("Rien à faire.")
        return

    print_lock = threading.Lock()

    for key in active:
        if not work[key]:
            continue
        backfill_chart(
            key, CHARTS[key], work[key],
            args.dl_workers, args.max_dl_workers, args.filter_workers,
            args.headless, args.dry_run, print_lock,
        )
        if key != active[-1]:
            print(f"\n{'─' * 55}\n")

    print("Backfill terminé.")


if __name__ == "__main__":
    main()
