#!/usr/bin/env python3
"""Backfill the FULL Spotify daily chart (all 200 entries, every artist) for one
or more regions — not just Taylor Swift rows like worldwide/daily.py keeps.

One API request = one (region, date) = the whole top 200, so a year of Global
is ~365 requests. Two outputs per date:

- raw   : snapshots/spotify_charts_full/<region>/<YYYY>/<date>.json.gz
          the untouched API response (source of truth, nothing is lost if a
          field is added to the CSV later);
- table : db/spotify_charts_full/<region>_<YYYY>.csv, rebuilt from the raw
          files at the end of the run (one row per chart entry).

Resumable: a date whose raw file exists is skipped (unless --force), so Ctrl+C
and re-running the same command is free. Tokens: every spotify_session*.json
account (same bearer cache/Playwright logic as worldwide/daily.py), round-robin
rotation on 429, global wait only when all accounts are rate-limited.

    python scripts/backfill_spotify_full_charts.py                      # global, 2025-01-01 -> latest
    python scripts/backfill_spotify_full_charts.py --start 2026-01-01 --regions global us
    python scripts/backfill_spotify_full_charts.py --rebuild-csv-only    # no network
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

ROOT = Path(__file__).resolve().parents[1]
WORLDWIDE_DAILY = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "daily.py"
SESSION_DIR = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json"
RAW_ROOT = ROOT / "snapshots" / "spotify_charts_full"
CSV_ROOT = ROOT / "db" / "spotify_charts_full"
STATE_PATH = RAW_ROOT / "state.json"
COVERS_DIR = RAW_ROOT / "covers"
# Spotify image ids = 16-char size prefix + 24-char hash; same hash, other sizes.
COVER_640_PREFIX = "ab67616d0000b273"
LOG_DIR = ROOT / "runtime" / "logs"

CSV_FIELDS = [
    "date", "rank", "previous_rank", "peak_rank", "peak_date", "entry_rank", "entry_date",
    "days_on_chart", "streak", "streams", "entry_status", "track_id", "track_name",
    "artists", "artist_ids", "labels", "release_date", "image_url",
]
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

def raw_path(region: str, day: str) -> Path:
    return RAW_ROOT / region / day[:4] / f"{day}.json.gz"


def write_raw(region: str, day: str, payload: dict) -> None:
    path = raw_path(region, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"not_published": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def _int(v) -> int | str:
    try:
        return int(v)
    except (TypeError, ValueError):
        return ""


def entries_to_rows(day: str, payload: dict) -> list[dict]:
    rows = []
    for entry in payload.get("entries") or []:
        ced = entry.get("chartEntryData") or {}
        meta = entry.get("trackMetadata") or {}
        uri = meta.get("trackUri") or ""
        artists = meta.get("artists") or []
        rows.append({
            "date": day,
            "rank": _int(ced.get("currentRank")),
            "previous_rank": _int(ced.get("previousRank")),
            "peak_rank": _int(ced.get("peakRank")),
            "peak_date": ced.get("peakDate") or "",
            "entry_rank": _int(ced.get("entryRank")),
            "entry_date": ced.get("entryDate") or "",
            "days_on_chart": _int(ced.get("appearancesOnChart")),
            "streak": _int(ced.get("consecutiveAppearancesOnChart")),
            "streams": _int((ced.get("rankingMetric") or {}).get("value")),
            "entry_status": ced.get("entryStatus") or "",
            "track_id": uri.split(":")[-1] if uri.startswith("spotify:track:") else "",
            "track_name": (meta.get("trackName") or "").strip(),
            "artists": " | ".join(a.get("name", "") for a in artists),
            "artist_ids": " | ".join((a.get("spotifyUri") or "").split(":")[-1] for a in artists),
            "labels": " | ".join(l.get("name", "") for l in meta.get("labels") or []),
            "release_date": meta.get("releaseDate") or "",
            "image_url": meta.get("displayImageUri") or "",
        })
    return rows


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


def run(args) -> int:
    import requests

    log_path = None if args.log_file == "none" else Path(args.log_file or LOG_DIR / f"spotify_full_charts_{datetime.now():%Y%m%d_%H%M%S}.log")
    live = Live(log_path, live=sys.stdout.isatty() and not args.verbose)
    try:
        log = live
        log.event(f"[LOG] {' '.join(sys.argv)}")
        if log_path:
            log.event(f"[LOG] detail -> {log_path}")
        regions = [r.lower() for r in args.regions]
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

        daily = _load_daily_module()
        tokens = Tokens(daily, log)
        session = requests.Session()
        headers = {"Accept": "application/json", "Referer": "https://charts.spotify.com/", "User-Agent": daily._UA}
        next_at = 0.0
        started = time.monotonic()
        done = 0
        pauses = 0
        recent: list[float] = []
        for region, day in todo:
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
        for r, y in touched:
            years_by_region.setdefault(r, set()).add(y)
        for r, years in sorted(years_by_region.items()):
            for y in sorted(years):
                rebuild_csv(r, y, log)
        return 0
    except KeyboardInterrupt:
        live.event("[STOP] Ctrl+C - les dates deja ecrites sont gardees ; relancer la meme commande reprend. "
                   "CSV : --rebuild-csv-only pour le regenerer maintenant.")
        return 130
    finally:
        live.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Backfill the full Spotify daily top 200 (all artists).")
    p.add_argument("--regions", nargs="+", default=["global"], help="Chart regions (default: global). e.g. global us gb")
    p.add_argument("--start", default="2025-01-01", help="First chart date (default 2025-01-01)")
    p.add_argument("--end", default=None, help="Last chart date (default: yesterday)")
    p.add_argument("--request-interval", type=float, default=1.5, help="Seconds between requests (default 1.5)")
    p.add_argument("--force", action="store_true", help="Re-fetch dates whose raw file already exists")
    p.add_argument("--retry-not-published", action="store_true", help="Retry dates that previously returned 404")
    p.add_argument("--rebuild-csv-only", action="store_true", help="No network: rebuild db/spotify_charts_full/*.csv from raw files")
    p.add_argument("--no-covers", action="store_true", help="Do not download cover images (default: every distinct cover, 640 px, once)")
    p.add_argument("--covers-from-raw", action="store_true", help="Only download missing covers referenced by raw files already on disk (no charts API call)")
    p.add_argument("--log-file", default=None, help="Log file (default runtime/logs/spotify_full_charts_<ts>.log, 'none' = off)")
    p.add_argument("--verbose", action="store_true", help="Print every event on the console instead of the live line")
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
