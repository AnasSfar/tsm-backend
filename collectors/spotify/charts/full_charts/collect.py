#!/usr/bin/env python3
"""Collect the FULL Spotify daily chart (all 200 entries, every artist) for one
or more regions — not just Taylor Swift rows like worldwide/daily.py keeps.
(Moved from scripts/backfill_spotify_full_charts.py on 2026-10-05.)

One API request = one (region, date) = the whole top 200, so a year of Global
is ~365 requests. Two outputs per date:

- raw   : snapshots/spotify_charts_full/<region>/<YYYY>/<date>.json.gz
          the untouched API response (source of truth, nothing is lost if a
          field is added to the CSV later);
- table : db/spotify_charts_full/<region>_<YYYY>.csv, rebuilt from the raw
          files at the end of the run (one row per chart entry).

Then runs enrich.py (albums of new tracks + missing genders, --no-enrich to
skip) and export.py (All Artists page data + R2, --no-export to skip). One instance at a time (snapshots/spotify_charts_full/collect.lock):
run_all_charts.py launches it detached once its daily run is over. If a
collection (e.g. a long backfill) already holds the lock, the new launch leaves
a request file (collect.pending) instead of doing nothing: the running instance
sees it at its next region change, collects the latest days of every region
first, exports the page, then resumes its backfill with a fresh end date.

Resumable: a date whose raw file exists is skipped (unless --force), so Ctrl+C
and re-running the same command is free. Tokens: every spotify_session*.json
account (same bearer cache/Playwright logic as worldwide/daily.py), round-robin
rotation on 429, global wait only when all accounts are rate-limited.

    python collectors/spotify/charts/full_charts/collect.py                       # every region, 2026-01-01 -> latest, then enrich
    python collectors/spotify/charts/full_charts/collect.py --regions global us gb
    python collectors/spotify/charts/full_charts/collect.py --rebuild-csv-only     # no network
"""
from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    COVERS_DIR, CSV_FIELDS, CSV_ROOT, RAW_ROOT, ROOT, STATE_PATH, entries_to_rows, load_state, raw_path,
    raw_regions,
)

WORLDWIDE_DAILY = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "daily.py"
SESSION_DIR = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json"
# Spotify image ids = 16-char size prefix + 24-char hash; same hash, other sizes.
COVER_640_PREFIX = "ab67616d0000b273"
LOG_DIR = ROOT / "runtime" / "logs"

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


# ── logging: full detail in the log file, one live line on the console ──────

class Live:
    def __init__(self, log_path: Path | None, live: bool):
        self.live = live
        self.lock = threading.Lock()
        self.status = ""
        self.status_len = 0
        self.log = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log = log_path.open("a", encoding="utf-8", buffering=1)
        self._stop = threading.Event()
        if live:
            threading.Thread(target=self._animate, daemon=True).start()

    def _write(self, text: str) -> None:
        try:
            sys.stdout.write(text)
        except UnicodeEncodeError:
            sys.stdout.write(text.encode("ascii", "replace").decode("ascii"))
        sys.stdout.flush()

    def _clear(self) -> None:
        if self.status_len:
            self._write("\r" + " " * self.status_len + "\r")
            self.status_len = 0

    def event(self, msg: str, *, console: bool = True) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        with self.lock:
            if self.log:
                self.log.write(line + "\n")
            if console or not self.live:
                self._clear()
                self._write(line + "\n")

    def set_status(self, text: str) -> None:
        self.status = text
        if not self.live:
            return

    def _animate(self) -> None:
        frame = 0
        while not self._stop.wait(0.15):
            width = max(20, shutil.get_terminal_size((120, 20)).columns - 1)
            text = f"{SPINNER[frame % len(SPINNER)]} {datetime.now():%H:%M:%S}  {self.status}"[:width]
            with self.lock:
                pad = max(0, self.status_len - len(text))
                self._write("\r" + text + " " * pad)
                self.status_len = len(text)
            frame += 1

    def close(self) -> None:
        self._stop.set()
        with self.lock:
            self._clear()


# ── tokens (reuse worldwide/daily.py bearer logic) ──────────────────────────

def _load_daily_module():
    spec = importlib.util.spec_from_file_location("ww_daily_for_full_charts", WORLDWIDE_DAILY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class Tokens:
    def __init__(self, daily, log: Live):
        self.daily = daily
        self.log = log
        self.tokens: list[str] = []
        self.idx = 0
        self.blocked_until: list[float] = []
        self.refresh()

    def refresh(self) -> None:
        tokens = []
        token, _regions = self.daily._get_bearer_token_and_regions(force_refresh=bool(self.tokens))
        tokens.append(token)
        main_session = Path(getattr(self.daily, "SESSION_FILE", SESSION_DIR / "spotify_session.json"))
        for sf in sorted(SESSION_DIR.glob("spotify_session*.json")):
            if sf.resolve() == main_session.resolve():
                continue
            extra = self.daily._get_bearer_from_cookies(sf)
            if extra:
                tokens.append(extra)
        self.tokens = tokens
        self.blocked_until = [0.0] * len(tokens)
        self.idx %= len(tokens)
        self.log.event(f"[TOKENS] {len(tokens)} compte(s) Spotify charge(s)")

    def current(self) -> str:
        return self.tokens[self.idx]

    def mark_429(self, retry_after: float) -> float:
        """Block the current token; switch to the next free one. Returns seconds
        to wait (0 when another token is free)."""
        now = time.monotonic()
        self.blocked_until[self.idx] = now + retry_after
        for step in range(1, len(self.tokens) + 1):
            j = (self.idx + step) % len(self.tokens)
            if self.blocked_until[j] <= now:
                self.idx = j
                return 0.0
        j = min(range(len(self.tokens)), key=lambda k: self.blocked_until[k])
        self.idx = j
        return max(0.0, self.blocked_until[j] - now)


# ── storage ──────────────────────────────────────────────────────────────────

def write_raw(region: str, day: str, payload: dict) -> None:
    path = raw_path(region, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def rebuild_csv(region: str, year: str, log: Live) -> int:
    files = sorted((RAW_ROOT / region / year).glob("*.json.gz"))
    if not files:
        return 0
    CSV_ROOT.mkdir(parents=True, exist_ok=True)
    out = CSV_ROOT / f"{region}_{year}.csv"
    tmp = out.with_suffix(".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for f in files:
            day = f.name[:10]
            with gzip.open(f, "rt", encoding="utf-8") as g:
                rows = entries_to_rows(day, json.load(g))
            writer.writerows(rows)
            n += len(rows)
    os.replace(tmp, out)
    log.event(f"[CSV] {out.relative_to(ROOT)} : {len(files)} date(s), {n} ligne(s)")
    return n


class CoverDownloader:
    """Every distinct cover (tracks + daily highlights) saved once as a 640 px
    JPEG under snapshots/spotify_charts_full/covers/<hash>.jpg, in background
    threads (the image CDN is not the rate-limited charts API)."""

    def __init__(self, log: Live, enabled: bool):
        from concurrent.futures import ThreadPoolExecutor
        import requests

        self.log = log
        self.enabled = enabled
        self.pool = ThreadPoolExecutor(max_workers=4) if enabled else None
        self.session = requests.Session()
        self.seen: set[str] = set()
        self.saved = 0
        self.failed = 0
        if enabled:
            COVERS_DIR.mkdir(parents=True, exist_ok=True)
            self.seen = {f.stem for f in COVERS_DIR.glob("*.jpg")}

    @staticmethod
    def _urls(payload: dict) -> list[str]:
        urls = [((e.get("trackMetadata") or {}).get("displayImageUri") or "") for e in payload.get("entries") or []]
        urls += [(h.get("displayImageUri") or "") for h in payload.get("highlights") or []]
        return [u for u in urls if "/image/" in u]

    def _fetch(self, key: str, url: str) -> None:
        target = COVERS_DIR / f"{key}.jpg"
        for candidate in (f"https://i.scdn.co/image/{COVER_640_PREFIX}{key}", url):
            try:
                r = self.session.get(candidate, timeout=30)
                if r.status_code == 200 and r.content:
                    tmp = target.with_suffix(".tmp")
                    tmp.write_bytes(r.content)
                    os.replace(tmp, target)
                    self.saved += 1
                    return
            except Exception:
                pass
        self.failed += 1
        self.seen.discard(key)  # retried on a later date / next run
        self.log.event(f"[COVER] echec {url}", console=False)

    def add(self, payload: dict) -> None:
        if not self.enabled:
            return
        for url in self._urls(payload):
            image_id = url.rsplit("/", 1)[-1]
            key = image_id[16:] if len(image_id) == 40 else image_id
            if key in self.seen:
                continue
            self.seen.add(key)
            self.pool.submit(self._fetch, key, url)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=True)
            self.log.event(f"[COVER] {self.saved} cover(s) telechargee(s), {self.failed} echec(s), "
                           f"{len(list(COVERS_DIR.glob('*.jpg')))} au total")


# ── fetch loop ───────────────────────────────────────────────────────────────

def _fmt(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}min{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


# Region-major order (a region is complete before the next starts, so its
# yearly stats are usable early): global + biggest markets first, then A-Z.
FIRST_REGIONS = ("global", "us", "gb", "br", "mx", "de", "fr", "ca", "au", "jp", "es", "it", "ph", "id", "kr")


def order_regions(region_map: dict[str, str]) -> list[str]:
    codes = {c.lower() for c in region_map}
    return [c for c in FIRST_REGIONS if c in codes] + sorted(codes - set(FIRST_REGIONS))


RESTART = 75  # run() stopped early: a newer chart day was requested (collect.pending)


def run(args, *, watch_pending: bool = False) -> int:
    import requests

    log_path = None if args.log_file == "none" else Path(args.log_file or LOG_DIR / f"spotify_full_charts_{datetime.now():%Y%m%d_%H%M%S}.log")
    live = Live(log_path, live=sys.stdout.isatty() and not args.verbose)
    try:
        log = live
        log.event(f"[LOG] {' '.join(sys.argv)}")
        if log_path:
            log.event(f"[LOG] detail -> {log_path}")
        regions = [r.lower() for r in args.regions]
        daily = None
        if "all" in regions:
            if args.rebuild_csv_only or args.covers_from_raw:
                regions = raw_regions()
            else:
                daily = _load_daily_module()
                _token, region_map = daily._get_bearer_token_and_regions()
                regions = order_regions(region_map)
            log.event(f"[REGIONS] all -> {len(regions)} region(s)")
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today() - timedelta(days=1)
        days = []
        d = end
        while d >= start:  # newest first
            days.append(d.isoformat())
            d -= timedelta(days=1)

        state = load_state()
        todo = [
            (r, day) for r in regions for day in days
            if args.force or not raw_path(r, day).exists()
        ]
        if not args.retry_not_published:
            todo = [(r, day) for r, day in todo if day not in state["not_published"].get(r, [])]
        log.event(f"[PLAN] {len(todo)} requete(s) a faire ({', '.join(regions)}, {days[-1]} -> {days[0]}), "
                  f"{len(regions) * len(days) - len(todo)} deja faites")
        touched: set[tuple[str, str]] = set()
        covers = CoverDownloader(log, enabled=not args.no_covers and not args.rebuild_csv_only)
        if args.covers_from_raw:
            # Catch-up: covers of every raw file already on disk (no charts API call).
            for f in sorted(RAW_ROOT.glob("*/*/*.json.gz")):
                with gzip.open(f, "rt", encoding="utf-8") as g:
                    covers.add(json.load(g))
            covers.close()
            return 0
        if args.rebuild_csv_only or not todo:
            covers.close()
            for r in regions:
                for y in sorted({day[:4] for day in days}):
                    rebuild_csv(r, y, log)
            return 0

        daily = daily or _load_daily_module()
        tokens = Tokens(daily, log)
        session = requests.Session()
        headers = {"Accept": "application/json", "Referer": "https://charts.spotify.com/", "User-Agent": daily._UA}
        next_at = 0.0
        started = time.monotonic()
        done = 0
        pauses = 0
        recent: list[float] = []
        built: set[tuple[str, str]] = set()

        def flush_region(r: str) -> None:
            # Region finished (todo is region-major): its tables are usable now, not after ~10 h.
            for key in sorted(k for k in touched if k[0] == r and k not in built):
                rebuild_csv(key[0], key[1], log)
                built.add(key)

        prev_region = None
        interrupted = False
        for region, day in todo:
            if prev_region is not None and region != prev_region:
                flush_region(prev_region)
                if watch_pending and PENDING_PATH.exists():
                    log.event(f"[PENDING] nouvelle journee demandee - pause de la collecte avant {region}, "
                              "derniers jours d'abord")
                    interrupted = True
                    break
            prev_region = region
            chart_id = "regional-global-daily" if region == "global" else f"regional-{region}-daily"
            url = f"{daily._API_BASE}/{chart_id}/{day}"
            t0 = time.monotonic()
            attempts = 0
            while True:
                attempts += 1
                wait = next_at - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                next_at = time.monotonic() + args.request_interval
                left = len(todo) - done
                rate = (sum(recent) / len(recent)) if recent else args.request_interval
                eta = datetime.now() + timedelta(seconds=rate * left)
                live.set_status(
                    f"{region} {day} · {done}/{len(todo)} ({done * 100 // max(1, len(todo))}%) · "
                    f"{rate:.1f}s/date · fin ~{eta:%d/%m %H:%M} · compte {tokens.idx + 1}/{len(tokens.tokens)} · 429×{pauses}"
                )
                try:
                    resp = session.get(url, headers={**headers, "Authorization": f"Bearer {tokens.current()}"}, timeout=30)
                except requests.RequestException as exc:
                    log.event(f"[NET] {region} {day} : {exc!r} - retry 10s", console=False)
                    time.sleep(10)
                    continue
                if resp.status_code == 200:
                    payload = resp.json()
                    n = len(payload.get("entries") or [])
                    if n == 0:
                        log.event(f"[WARN] {region} {day} : 200 mais 0 entree - non ecrit, a relancer")
                        break
                    write_raw(region, day, payload)
                    covers.add(payload)
                    touched.add((region, day[:4]))
                    log.event(f"[OK] {region} {day} : {n} titres ({time.monotonic() - t0:.1f}s)", console=False)
                    break
                if resp.status_code == 429:
                    pauses += 1
                    retry_after = float(resp.headers.get("Retry-After") or 0) or 60.0
                    sleep_for = tokens.mark_429(retry_after)
                    if sleep_for:
                        log.event(f"[429] tous les comptes bloques - attente {_fmt(sleep_for)}", console=False)
                        until = time.monotonic() + sleep_for
                        while time.monotonic() < until:
                            live.set_status(f"{region} {day} · {done}/{len(todo)} · ⏸ 429 tous comptes bloques, reprise dans {_fmt(until - time.monotonic())}")
                            time.sleep(0.5)
                    else:
                        log.event(f"[429] compte bloque -> compte {tokens.idx + 1}/{len(tokens.tokens)}", console=False)
                    continue
                if resp.status_code == 401:
                    log.event("[401] token expire - refresh", console=False)
                    tokens.refresh()
                    continue
                if resp.status_code == 404:
                    # Not published (yet, for the latest days; or ever, for a dead chart).
                    if day < (date.today() - timedelta(days=3)).isoformat():
                        state["not_published"].setdefault(region, [])
                        if day not in state["not_published"][region]:
                            state["not_published"][region].append(day)
                            save_state(state)
                    log.event(f"[404] {region} {day} : chart non publie")
                    break
                log.event(f"[HTTP {resp.status_code}] {region} {day} - retry 15s", console=False)
                if attempts >= 6:
                    log.event(f"[FAIL] {region} {day} : HTTP {resp.status_code} apres {attempts} essais - a relancer")
                    break
                time.sleep(15)
            done += 1
            recent.append(time.monotonic() - t0)
            recent[:] = recent[-60:]

        log.event(f"[DONE] {done} requete(s) en {_fmt(time.monotonic() - started)}, {pauses} 429")
        covers.close()
        years_by_region: dict[str, set[str]] = {}
        for r, y in touched - built:
            years_by_region.setdefault(r, set()).add(y)
        for r, years in sorted(years_by_region.items()):
            for y in sorted(years):
                rebuild_csv(r, y, log)
        return RESTART if interrupted else 0
    except KeyboardInterrupt:
        live.event("[STOP] Ctrl+C - les dates deja ecrites sont gardees ; relancer la meme commande reprend. "
                   "CSV : --rebuild-csv-only pour le regenerer maintenant.")
        return 130
    finally:
        live.close()


# ── single instance (manual catch-up + the run after run_all_charts) ─────────

LOCK_PATH = RAW_ROOT / "collect.lock"
# Written by a launch that found the lock held; consumed by the running instance.
PENDING_PATH = RAW_ROOT / "collect.pending"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock() -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip() or 0)
    except (FileNotFoundError, ValueError):
        pid = 0
    if pid and pid != os.getpid() and _pid_alive(pid):
        PENDING_PATH.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        print(f"[LOCK] une collecte full charts tourne deja (pid {pid}) - demande enregistree "
              f"({PENDING_PATH.name}) : elle collectera et exportera les derniers jours a son prochain changement de region")
        return False
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        if LOCK_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def enrich_after_collect(args) -> int:
    """Albums of the new tracks + missing lead-artist genders (LLM, top N per region)."""
    from enrich import run_enrich

    start_year = int(args.start[:4])
    end_year = int((args.end or (date.today() - timedelta(days=1)).isoformat())[:4])
    regions = None if [r.lower() for r in args.regions] == ["all"] else [r.lower() for r in args.regions]
    rc = 0
    for year in range(start_year, end_year + 1):
        scope = "tous les titres" if args.classify_genders < 0 else f"top {args.classify_genders} par region"
        print(f"\n[ENRICH] {year} : albums + genres ({scope})")
        try:
            run_enrich(regions, str(year), classify_top=args.classify_genders, list_top=100)
        except (Exception, SystemExit) as exc:  # data already collected: never lose the run for this
            print(f"[ENRICH] echec {year}: {exc!r} - relancer enrich.py")
            rc = 1
    return rc


def export_after_collect(args) -> int:
    """All Artists page data (export.py) for every year touched, + R2 upload."""
    from export import run_export

    start_year = int(args.start[:4])
    end_year = int((args.end or (date.today() - timedelta(days=1)).isoformat())[:4])
    regions = None if [r.lower() for r in args.regions] == ["all"] else [r.lower() for r in args.regions]
    rc = 0
    for year in range(start_year, end_year + 1):
        print(f"\n[EXPORT] {year} : page All Artists (+ R2)")
        try:
            rc = max(rc, run_export(regions, str(year)))
        except Exception as exc:
            print(f"[EXPORT] echec {year}: {exc!r} - relancer export.py")
            rc = 1
    return rc


def consume_pending() -> bool:
    try:
        PENDING_PATH.unlink()
        return True
    except FileNotFoundError:
        return False


def latest_days_pass(args) -> int:
    """Requested while a backfill runs: the last few days of every region
    (only the missing ones are fetched), then the page export - no enrich, the
    final enrich/export of the main run completes albums/genders."""
    quick = argparse.Namespace(**vars(args))
    quick.end = None
    quick.start = max(args.start, (date.today() - timedelta(days=4)).isoformat())
    print(f"\n[PENDING] passe prioritaire {quick.start} -> hier, toutes les regions demandees")
    rc = run(quick)
    if rc == 0 and not args.no_export:
        rc = export_after_collect(quick)
    return rc


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Collect the full Spotify daily top 200 (all artists).")
    p.add_argument("--regions", nargs="+", default=["all"],
                   help="Chart regions, or 'all' = every Spotify Charts region (default). e.g. global us gb")
    p.add_argument("--start", default="2026-01-01", help="First chart date (default 2026-01-01)")
    p.add_argument("--end", default=None, help="Last chart date (default: yesterday)")
    p.add_argument("--request-interval", type=float, default=1.5, help="Seconds between requests (default 1.5)")
    p.add_argument("--force", action="store_true", help="Re-fetch dates whose raw file already exists")
    p.add_argument("--retry-not-published", action="store_true", help="Retry dates that previously returned 404")
    p.add_argument("--rebuild-csv-only", action="store_true", help="No network: rebuild db/spotify_charts_full/*.csv from raw files")
    p.add_argument("--no-covers", action="store_true", help="Do not download cover images (default: every distinct cover, 640 px, once)")
    p.add_argument("--covers-from-raw", action="store_true", help="Only download missing covers referenced by raw files already on disk (no charts API call)")
    p.add_argument("--log-file", default=None, help="Log file (default runtime/logs/spotify_full_charts_<ts>.log, 'none' = off)")
    p.add_argument("--verbose", action="store_true", help="Print every event on the console instead of the live line")
    p.add_argument("--no-enrich", action="store_true", help="Do not run enrich.py (albums + genders) after the collection")
    p.add_argument("--no-export", action="store_true", help="Do not run export.py (All Artists page data + R2) at the end")
    p.add_argument("--classify-genders", type=int, default=-1, metavar="N",
                   help="Enrich: LLM-classify missing lead-artist genders of each region's top N songs "
                        "(default -1 = every song shown by the All Artists page, 0 = list only)")
    args = p.parse_args()
    offline = args.rebuild_csv_only or args.covers_from_raw
    if not offline and not acquire_lock():
        return 0
    try:
        if offline:
            return run(args)
        consume_pending()  # a request older than this run is covered by it
        while True:
            # args.end None = "yesterday", recomputed by each run(): a resumed pass also
            # picks up a day published since this process started.
            rc = run(args, watch_pending=True)
            if rc == RESTART:
                consume_pending()
                if latest_days_pass(args) == 130:
                    return 130
                continue
            if rc == 0 and not args.no_enrich:
                rc = enrich_after_collect(args)
            if rc in (0, 1) and not args.no_export:
                # An enrich failure (rc 1) does not block the page: albums are display-only there.
                rc = max(rc, export_after_collect(args))
            if rc in (0, 1) and consume_pending():
                print("\n[PENDING] nouvelle journee demandee pendant enrich/export - nouvelle passe")
                continue
            return rc
    finally:
        if not offline:
            release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
