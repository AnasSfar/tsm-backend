#!/usr/bin/env python3
"""Backfill Spotify Charts snapshots safely, with a resumable done-state file.

This wrapper splits the pending date range into `--workers` contiguous chunks
and runs the worldwide collector once per chunk (via --dates-file), with
--no-post. Each worker is a single long-running subprocess that loops over its
whole batch of dates internally, so the Python/import/Playwright/bearer-token
startup cost is paid once per worker instead of once per date. After each
worker finishes, every date in its chunk is checked against the worldwide
snapshot on disk and recorded in a JSON state file. Re-running the command
skips completed dates unless --refetch-done is passed.
"""
from __future__ import annotations

import argparse
import atexit
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORLDWIDE_DAILY = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "daily.py"
SYNC_COUNTRY_CSVS = ROOT / "scripts" / "sync_spotify_country_charts_from_worldwide.py"
BACKFILL_TRACK_IDS = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "backfill_charts_history_track_ids.py"
BACKFILL_TOTAL_DAYS = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "backfill_total_days.py"
ENRICH_WORLDWIDE_SNAPSHOTS = ROOT / "scripts" / "enrich_spotify_worldwide_snapshots.py"
UPLOAD_R2 = ROOT / "scripts" / "r2.py"
DEFAULT_STATE = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "run_all_backfill_done.json"
DEFAULT_GAPS_STATE = DEFAULT_STATE.with_name("run_all_backfill_gaps_done.json")
DEFAULT_SESSION_DIR = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json"

# Spotify stopped publishing these ~31 regional daily charts around 2019-08-24
# (all our charts_history_<region>.csv freeze on that exact date — it was the end
# of an old TSM backfill batch, and regular collection resumed later with only
# ~37 "live" regions). Requesting them for a later date just returns 404, which
# still costs a global-pacer slot. They ARE fetched for dates on/before the
# cutoff (they were tracked then). Disable with --include-discontinued-regions.
DISCONTINUED_REGION_CUTOFF = "2019-08-24"
DISCONTINUED_REGIONS = tuple(
    "ar bg bo cl co cr do ec eg es fi gr gt hn in is it jp ma mx "
    "ni pa pe py ro sv th tr uy vn za".split()
)


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _date_range(start: date, end: date) -> list[str]:
    out: list[str] = []
    cur = start
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _snapshot_path(chart_date: str) -> Path:
    return (
        ROOT
        / "snapshots"
        / "spotify_charts"
        / chart_date[:4]
        / chart_date[5:7]
        / chart_date
        / "worldwide"
        / f"ts_worldwide_{chart_date}.json"
    )


def _snapshot_is_usable(chart_date: str) -> bool:
    path = _snapshot_path(chart_date)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    by_track = payload.get("by_track")
    skipped_regions = payload.get("skipped_regions") or []
    # A no-Taylor day can be an exact empty snapshot. An empty snapshot where
    # regions were skipped is incomplete and must not be treated as done.
    if isinstance(by_track, dict) and not by_track and skipped_regions:
        return False
    return isinstance(by_track, dict)


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"done_dates": [], "failed_dates": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"done_dates": [], "failed_dates": {}}
    if not isinstance(payload, dict):
        return {"done_dates": [], "failed_dates": {}}
    payload.setdefault("done_dates", [])
    payload.setdefault("failed_dates", {})
    return payload


def _save_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["done_dates"] = sorted(set(payload.get("done_dates") or []))
    payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _snapshot_skipped_regions(chart_date: str) -> set[str]:
    """Regions daily.py gave up on for this date (max fetch attempts reached on
    429/timeout). daily.py still exits 0 in that case, so a skipped region must
    never be counted as done."""
    try:
        payload = json.loads(_snapshot_path(chart_date).read_text(encoding="utf-8-sig"))
    except Exception:
        return set()
    if not isinstance(payload, dict):
        return set()
    return {str(r).lower() for r in (payload.get("skipped_regions") or [])}


# --------------------------------------------------------------------------
# Live log: every line (ours + daily.py's) is prefixed with the wall-clock time
# and teed to a log file; daily.py's output is parsed to report how long each
# date and each 429 pause took, plus a heartbeat when nothing prints.
# --------------------------------------------------------------------------

def _hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}min{seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}"
    return f"{seconds // 86400}j{(seconds % 86400) // 3600:02d}h"


# Lines that stay visible in the console in live mode (everything else only goes
# to the log file). Matched on the line without its [HH:MM:SS] prefix.
_CONSOLE_PREFIXES = (
    "[LOG]", "[SWEEP]", "[WARN]", "[ERROR]", "[FAIL]", "[PLAN]", "[QUEUE]",
    "[GAPS]", "[STATE]", "[ OK ]", "[DONE] done=", "Traceback",
)


class _TimestampTee:
    """sys.stdout replacement. Every line goes, prefixed with [HH:MM:SS], to the
    log file. Console: everything too (--verbose / not a terminal), or in live
    mode only the important lines + one animated status line redrawn in place."""

    def __init__(self, stream, log_path: Path | None, *, live: bool = False):
        self._stream = stream
        self._lock = threading.Lock()
        self._at_line_start = True
        self._partial = ""
        self._status_len = 0
        self.live = live
        self._log = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = log_path.open("a", encoding="utf-8", buffering=1)

    def _console(self, text: str) -> None:
        try:
            self._stream.write(text)
        except UnicodeEncodeError:
            self._stream.write(text.encode("ascii", "replace").decode("ascii"))
        self._stream.flush()

    def _clear_status(self) -> None:
        if self._status_len:
            self._console("\r" + " " * self._status_len + "\r")
            self._status_len = 0

    def write(self, text: str) -> int:
        if not text:
            return 0
        with self._lock:
            out = []
            for piece in text.splitlines(keepends=True):
                if self._at_line_start and piece.strip("\r\n"):
                    out.append(f"[{_hms()}] ")
                out.append(piece)
                self._at_line_start = piece.endswith("\n")
            chunk = "".join(out)
            if self._log is not None:
                self._log.write(chunk)
            if not self.live:
                self._console(chunk)
                return len(text)
            self._partial += chunk
            *lines, self._partial = self._partial.split("\n")
            shown = [ln for ln in lines if ln[11:].strip().startswith(_CONSOLE_PREFIXES)]
            if shown:
                self._clear_status()
                self._console("\n".join(shown) + "\n")
        return len(text)

    def status(self, text: str) -> None:
        """Redraw the single live status line in place."""
        if not self.live:
            return
        width = max(20, shutil.get_terminal_size((120, 20)).columns - 1)
        text = text[:width]
        with self._lock:
            pad = max(0, self._status_len - len(text))
            self._console("\r" + text + " " * pad)
            self._status_len = len(text)

    def end_status(self) -> None:
        with self._lock:
            self._clear_status()

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


class _RunStats:
    """Cumulative timings across the whole wrapper run + what the live status
    line shows."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.dates = 0
        self.seconds = 0.0
        self.recent: deque[float] = deque(maxlen=60)
        self.pause_seconds = 0.0
        self.pauses = 0
        self.rotations = 0
        self.token = ""
        self.with_data = 0
        self.timer: _ChildTimer | None = None
        self.sweep_pos = ""              # "3/67"
        self.sweep_left_at_region = 0    # sweep date-fetches left when the region started
        # Called (region_code, date) once daily.py has WRITTEN the snapshot for a
        # region it fetched (0+ entries or a clean 404) -> per-date checkpoint.
        self.on_region_fetched = None

    def avg(self) -> float | None:
        """Rolling s/date over the last 60 dates (the cumulative average is
        skewed by dates skipped instantly because already fetched)."""
        with self.lock:
            if self.recent:
                return sum(self.recent) / len(self.recent)
            return self.seconds / self.dates if self.dates else None


RUN_STATS = _RunStats()
HEARTBEAT_SECONDS = 60.0

_RE_DATE = re.compile(r"\[BACKFILL\] worldwide (\d+)/(\d+): (\d{4}-\d{2}-\d{2})")
_RE_429 = re.compile(r"\[\s*(\w+)\] 429")
_RE_PAUSE_OK = re.compile(r"sonde OK")
_RE_SKIP = re.compile(r"\[\s*(\w+)\] SKIP")
_RE_ROTATION = re.compile(r"rotation .* token (\d+/\d+)")
_RE_ENTRIES = re.compile(r"\[\s*(\w+)\] (\d+) TS entries")
_RE_NO_CHART = re.compile(r"\[\s*(\w+)\] 404 date(?:\+latest)? - no chart")
_RE_WRITTEN = re.compile(r"\[DONE\] Written")
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _ChildTimer:
    """Parses one daily.py output stream: [TIMING] lines (log file) + the state
    shown by the live status line."""

    def __init__(self, label: str):
        self.label = label
        self.cur_date: str | None = None
        self.cur_idx = 0
        self.total = 0
        self.date_started = 0.0
        self.local_dates = 0
        self.local_seconds = 0.0
        self.pause_started: float | None = None
        self.pause_tries = 0
        self.last_output = time.monotonic()
        self.fetched_regions: list[str] = []  # fetched for cur_date, snapshot not yet written

    def _close_date(self, now: float) -> None:
        if self.cur_date is None:
            return
        took = now - self.date_started
        self.local_dates += 1
        self.local_seconds += took
        with RUN_STATS.lock:
            RUN_STATS.dates += 1
            RUN_STATS.seconds += took
            RUN_STATS.recent.append(took)
        avg = self.local_seconds / self.local_dates
        left = max(0, self.total - self.cur_idx)
        eta = ""
        if left:
            finish = datetime.now() + timedelta(seconds=avg * left)
            eta = f" | reste {left} -> fin ~{finish.strftime('%H:%M')} ({_fmt_duration(avg * left)})"
        print(
            f"[TIMING] {self.label} {self.cur_date} en {took:.1f}s | moy {avg:.1f}s/date | "
            f"{self.cur_idx}/{self.total}{eta}",
            flush=True,
        )
        self.cur_date = None

    def feed(self, line: str) -> None:
        now = time.monotonic()
        self.last_output = now
        m = _RE_DATE.search(line)
        if m:
            self._close_date(now)
            self.cur_idx, self.total, self.cur_date = int(m.group(1)), int(m.group(2)), m.group(3)
            self.date_started = now
            self.fetched_regions = []
            return
        if _RE_WRITTEN.search(line):
            cb = RUN_STATS.on_region_fetched
            if cb is not None and self.cur_date:
                for region in self.fetched_regions:
                    cb(region, self.cur_date)
            self.fetched_regions = []
            return
        m = _RE_NO_CHART.search(line)
        if m:
            self.fetched_regions.append(m.group(1).lower())
            return
        m = _RE_ROTATION.search(line)
        if m:
            with RUN_STATS.lock:
                RUN_STATS.rotations += 1
                RUN_STATS.token = m.group(1)
            return
        m = _RE_ENTRIES.search(line)
        if m:
            self.fetched_regions.append(m.group(1).lower())
            if int(m.group(2)) > 0:
                with RUN_STATS.lock:
                    RUN_STATS.with_data += 1
            return
        if _RE_429.search(line):
            if self.pause_started is None:
                self.pause_started = now
                self.pause_tries = 0
            self.pause_tries += 1
            return
        if _RE_PAUSE_OK.search(line) and self.pause_started is not None:
            waited = now - self.pause_started
            with RUN_STATS.lock:
                RUN_STATS.pauses += 1
                RUN_STATS.pause_seconds += waited
                total_pause = RUN_STATS.pause_seconds
            print(
                f"[TIMING] {self.label} pause 429 terminee : {_fmt_duration(waited)} "
                f"({self.pause_tries} tentative(s)) | cumul pauses du run : {_fmt_duration(total_pause)}",
                flush=True,
            )
            self.pause_started = None
            return
        m = _RE_SKIP.search(line)
        if m:
            print(
                f"[WARN] {self.label} region {m.group(1)} abandonnee pour {self.cur_date} "
                "-> restera en attente, refetchee au prochain run",
                flush=True,
            )
            self.pause_started = None

    def finish(self) -> None:
        self._close_date(time.monotonic())


def _status_text(frame: int) -> str:
    spin = _SPINNER[frame % len(_SPINNER)]
    t = RUN_STATS.timer
    head = f"{spin} {_hms()}"
    if t is None or not t.total:
        return f"{head}  demarrage..."
    now = time.monotonic()
    done = max(0, t.cur_idx - 1)
    pct = done / t.total
    width = 18
    fill = int(round(pct * width))
    bar = "█" * fill + "░" * (width - fill)
    rate = RUN_STATS.avg() or 0.0
    region_left = max(0, t.total - done)
    label = t.label + (f" ({RUN_STATS.sweep_pos})" if RUN_STATS.sweep_pos else "")
    parts = [f"{bar} {done}/{t.total} {pct * 100:3.0f}%"]
    if t.pause_started is not None:
        parts.append(f"⏸ pause 429 {_fmt_duration(now - t.pause_started)} (essai {t.pause_tries})")
    elif t.cur_date:
        running = now - t.date_started
        parts.append(t.cur_date + (f" ({_fmt_duration(running)})" if running >= 5 else ""))
    if rate:
        parts.append(f"{rate:.1f}s/date")
        region_end = datetime.now() + timedelta(seconds=rate * region_left)
        parts.append(f"region ~{region_end:%H:%M}")
        if RUN_STATS.sweep_left_at_region:
            global_left = max(0, RUN_STATS.sweep_left_at_region - done)
            global_end = datetime.now() + timedelta(seconds=rate * global_left)
            parts.append(
                f"total {global_left:,} restants ≈ {_fmt_duration(rate * global_left)} "
                f"(fin {global_end:%d/%m %H:%M})".replace(",", " ")
            )
    extra = f"429×{RUN_STATS.pauses}"
    if RUN_STATS.token:
        extra += f" token {RUN_STATS.token}"
    parts.append(extra)
    parts.append(f"{RUN_STATS.with_data} dates avec TS")
    return f"{head}  {label}  " + " · ".join(parts)


def _start_status_animation(tee: _TimestampTee) -> threading.Event:
    stop = threading.Event()

    def _loop() -> None:
        frame = 0
        while not stop.wait(0.15):
            try:
                tee.status(_status_text(frame))
            except Exception:
                pass
            frame += 1
        tee.end_status()

    threading.Thread(target=_loop, name="live-status", daemon=True).start()
    return stop


def _run(
    cmd: list[str],
    *,
    dry_run: bool,
    env: dict[str, str] | None = None,
    label: str | None = None,
) -> int:
    print("[RUN] " + " ".join(cmd), flush=True)
    if dry_run:
        return 0
    child_env = dict(env if env is not None else os.environ)
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    timer = _ChildTimer(label or (Path(cmd[1]).stem if len(cmd) > 1 else "run"))
    RUN_STATS.timer = timer
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    stop = threading.Event()

    def _heartbeat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS / 2):
            silent = time.monotonic() - timer.last_output
            if silent < HEARTBEAT_SECONDS:
                continue
            where = (
                f"date {timer.cur_date} en cours depuis {_fmt_duration(time.monotonic() - timer.date_started)}"
                if timer.cur_date else "demarrage / sync"
            )
            pausing = (
                f", en pause 429 depuis {_fmt_duration(time.monotonic() - timer.pause_started)}"
                if timer.pause_started is not None else ""
            )
            print(
                f"[HEARTBEAT] {timer.label} : aucune sortie depuis {_fmt_duration(silent)} "
                f"({where}{pausing}) | process lance depuis {_fmt_duration(time.monotonic() - started)}",
                flush=True,
            )
            timer.last_output = time.monotonic()  # one heartbeat per silent period

    hb = threading.Thread(target=_heartbeat, name="heartbeat", daemon=True)
    hb.start()
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            print(line, flush=True)
            timer.feed(line)
        rc = proc.wait()
    finally:
        stop.set()
    timer.finish()
    print(
        f"[TIMING] {timer.label} process termine (rc={rc}) en {_fmt_duration(time.monotonic() - started)}",
        flush=True,
    )
    return rc


def _mark_existing_snapshots(state: dict, dates: list[str]) -> int:
    done = set(state.get("done_dates") or [])
    added = 0
    for chart_date in dates:
        if chart_date not in done and _snapshot_is_usable(chart_date):
            done.add(chart_date)
            added += 1
    state["done_dates"] = sorted(done)
    return added


def _session_files() -> list[Path]:
    return sorted(DEFAULT_SESSION_DIR.glob("spotify_session*.json"))


def _csv_dates_present(region: str) -> set[str]:
    """Distinct chart dates already recorded in db/charts_history_<region>.csv.

    This is the durable, git-tracked store the site reads from — unlike the local
    snapshots/ tree, which is scratch and often incomplete on a given machine.
    Taylor charts every day globally, so a date missing from charts_history_global
    is a date we never collected worldwide.
    """
    path = ROOT / "db" / f"charts_history_{region}.csv"
    out: set[str] = set()
    if not path.exists():
        print(f"[WARN] --gaps-from-csv: {path} not found; treating every date as missing for {region}")
        return out
    with path.open(encoding="utf-8-sig") as f:
        next(f, None)  # header
        for line in f:
            cell = line.split(",", 1)[0].strip()
            if len(cell) == 10 and cell[4] == "-" and cell[7] == "-":
                out.add(cell)
    return out


def _proven_gap_dates(region: str) -> tuple[set[str], set[str]]:
    """Dates where Spotify's own counters PROVE Taylor charted in this region but
    db/charts_history_<region>.csv has no row for that song. Two signals (both raw
    Spotify fields: streak = consecutiveAppearancesOnChart, total_days =
    appearancesOnChart):

    - exact: a row with streak s on date D means the song charted every day
      D-(s-1) .. D-1 -> each of those days missing for that song is proven.
    - bounded: two consecutive rows of the same song on D1 < D2 whose total_days
      jumps by more than 1 (e.g. 800 -> 805) mean the missing appearances sit
      strictly between D1 and D2 -> those in-between dates are candidates.

    Returns (exact, bounded). Fetching a candidate that turns out empty is
    harmless (the full sweep would fetch it anyway); this only sets priority."""
    import csv

    path = ROOT / "db" / f"charts_history_{region}.csv"
    if not path.exists():
        return set(), set()
    rows_by_track: dict[str, list[tuple[str, int | None, int | None]]] = {}
    present_by_track: dict[str, set[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            day = (row.get("date") or "").strip()
            key = (row.get("track_id") or "").strip() or (row.get("song_name") or "").strip().lower()
            if len(day) != 10 or not key:
                continue

            def _int(v: str | None) -> int | None:
                try:
                    return int(float(v)) if v not in (None, "") else None
                except ValueError:
                    return None

            rows_by_track.setdefault(key, []).append((day, _int(row.get("total_days")), _int(row.get("streak"))))
            present_by_track.setdefault(key, set()).add(day)

    exact: set[str] = set()
    bounded: set[str] = set()
    for key, rows in rows_by_track.items():
        present = present_by_track[key]
        rows.sort(key=lambda r: (r[0], r[1] or 0))
        prev: tuple[str, int | None] | None = None
        for day, total, streak in rows:
            d = _parse_date(day)
            if streak and streak > 1:
                for k in range(1, min(streak, 4000)):
                    back = (d - timedelta(days=k)).isoformat()
                    if back not in present:
                        exact.add(back)
            if prev is not None and total is not None and prev[1] is not None:
                p_day, p_total = prev
                missing = total - p_total - 1
                if missing > 0 and p_day != day:
                    window = []
                    cur = _parse_date(p_day) + timedelta(days=1)
                    while cur < d:
                        if cur.isoformat() not in present:
                            window.append(cur.isoformat())
                        cur += timedelta(days=1)
                    # Only dense windows: the missing appearances cover at least
                    # half of the absent days. A 300 -> 900 jump across a multi-year
                    # hole would otherwise flag the whole hole (left to the sweep).
                    if window and missing * 2 >= len(window):
                        bounded.update(window)
            if prev is None or prev[0] != day:
                prev = (day, total)
    return exact, bounded - exact


def _snapshot_has_region(chart_date: str, region_code: str) -> bool:
    path = _snapshot_path(chart_date)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    for entries in (payload.get("by_track") or {}).values():
        for entry in entries:
            if entry.get("country") == region_code:
                return True
    return False


# uk history CSV <-> Spotify chart code
_CSV_TO_CHART_CODE = {"uk": "gb"}


def _csv_row_count(region: str) -> int:
    path = ROOT / "db" / f"charts_history_{region}.csv"
    if not path.exists():
        return 0
    with path.open(encoding="utf-8-sig") as f:
        return max(0, sum(1 for _ in f) - 1)


# Regions Taylor has essentially never charted in (near-empty history, no real
# regional chart to speak of) — excluded from the bare auto-sweep, still allowed
# if named explicitly.
_SWEEP_MIN_HISTORY_ROWS = 60


def _sweep_region_codes(explicit: list[str] | None) -> list[str]:
    """The region CSV codes to sweep, ordered richest-history first.
    Explicit list (kept in given order), or every 2-letter
    db/charts_history_<cc>.csv (minus global and near-empty ones)."""
    if explicit:
        return [c.strip().lower() for c in explicit if c.strip()]
    out: list[str] = []
    for p in (ROOT / "db").glob("charts_history_*.csv"):
        code = p.stem[len("charts_history_"):]
        if len(code) == 2 and code.isalpha() and _csv_row_count(code) >= _SWEEP_MIN_HISTORY_ROWS:
            out.append(code)
    # Richest history first: a region with lots of 2017-2019 rows almost certainly
    # still has a live chart, so its gap dates are real recoverable data.
    out.sort(key=_csv_row_count, reverse=True)
    return out


def _run_sync_pass(args, touched_dates: set[str], all_dates: list[str]) -> int:
    if args.no_sync:
        return 0
    rc = _run([sys.executable, str(SYNC_COUNTRY_CSVS)], dry_run=args.dry_run)
    if rc != 0:
        return rc
    rc = _run([sys.executable, str(BACKFILL_TRACK_IDS), "--rebuild-ts-history"], dry_run=args.dry_run)
    if rc != 0:
        return rc
    enrich_lo, enrich_hi = (
        (min(touched_dates), max(touched_dates)) if touched_dates else (all_dates[0], all_dates[-1])
    )
    rc = _run([sys.executable, str(ENRICH_WORLDWIDE_SNAPSHOTS), "--start", enrich_lo, "--end", enrich_hi], dry_run=args.dry_run)
    if rc != 0:
        return rc
    rc = _run([sys.executable, str(BACKFILL_TOTAL_DAYS)], dry_run=args.dry_run)
    if rc != 0:
        return rc
    if args.upload_r2:
        for chart_date in sorted(touched_dates):
            rc = _run(
                [
                    sys.executable, str(UPLOAD_R2), "--charts-only", "--worldwide-snapshot-only",
                    "--skip-history-upload", "--skip-db-upload", "--skip-images-upload",
                    "--new-date", chart_date,
                ],
                dry_run=args.dry_run,
            )
            if rc != 0:
                return rc
    return 0


def _chunks(items: list[str], n: int) -> list[list[str]]:
    """Split items into n contiguous chunks (as even as possible), dropping empty ones."""
    if n <= 0:
        return [items] if items else []
    size, extra = divmod(len(items), n)
    out: list[list[str]] = []
    start = 0
    for i in range(n):
        this_size = size + (1 if i < extra else 0)
        if this_size == 0:
            continue
        out.append(items[start : start + this_size])
        start += this_size
    return out


def _run_chunk(
    chart_dates: list[str],
    *,
    session_file: Path,
    force: bool,
    dry_run: bool,
    per_worker_semaphore: int,
    request_interval: float,
    fetch_max_attempts: int,
    rate_limit_max: int,
    regions: list[str] | None = None,
    exclude_regions: list[str] | None = None,
    label: str | None = None,
    single_session: bool = True,
) -> tuple[list[str], int, float, str]:
    """Fetch a whole batch of dates in a single subprocess (one process per worker,
    not one per date), so Python/import/Playwright/bearer-token/region-discovery
    startup cost is paid once per worker instead of once per date."""
    env = os.environ.copy()
    env["SPOTIFY_CHARTS_SESSION_FILE"] = str(session_file)
    # Parallel workers each own one session file. The sequential per-region sweep
    # instead pools EVERY spotify_session*.json token in its single process: on a
    # 429 daily.py rotates to the next account instead of pausing ~2 min.
    env["SPOTIFY_CHARTS_SINGLE_SESSION"] = "1" if single_session else "0"
    env["SPOTIFY_CHARTS_BEARER_CACHE_FILE"] = str(session_file.with_name(f"bearer_cache_{session_file.stem}.json"))
    env["SPOTIFY_SKIP_LATEST_FALLBACK_ON_404"] = "1"
    env["SPOTIFY_WORLDWIDE_SEMAPHORE"] = str(per_worker_semaphore)
    # daily.py serialises EVERY request through one global RequestPacer, so the
    # interval — not the semaphore — sets throughput (~1 req / interval / worker,
    # ~68 regions per date). Default 2.0s in daily.py = ~2.5 min/date floor;
    # override here so a multi-hundred-date backfill is not a multi-day job.
    env["SPOTIFY_WORLDWIDE_REQUEST_INTERVAL_SECONDS"] = str(request_interval)
    # daily.py defaults FETCH_MAX_ATTEMPTS to 0 (unlimited) for the live run so it
    # never skips real data; for an unattended backfill an unbounded retry on one
    # stuck region (sustained 429 / WARP wobble) freezes the whole date silently.
    env["SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS"] = str(fetch_max_attempts)
    # GlobalPause backoff on 429 is multiplicative (20s, 40s, 60s...) capped at
    # SPOTIFY_WORLDWIDE_RATE_LIMIT_MAX_SECONDS (300s in daily.py). During a pause
    # nothing prints — a 2-5 min pause reads as a freeze. Cap it low so there is
    # a log line at least every rate_limit_max seconds, and the pause never grows
    # into the "ça n'affiche rien" territory.
    env["SPOTIFY_WORLDWIDE_RATE_LIMIT_MAX_SECONDS"] = str(rate_limit_max)
    env.setdefault("SPOTIFY_WORLDWIDE_RATE_LIMIT_MIN_SECONDS", "12")
    dates_file = Path(
        tempfile.mkstemp(prefix=f"spotify_backfill_{session_file.stem}_", suffix=".txt")[1]
    )
    try:
        dates_file.write_text("\n".join(chart_dates) + "\n", encoding="utf-8")
        cmd = [
            sys.executable,
            str(WORLDWIDE_DAILY),
            "--dates-file",
            str(dates_file),
            "--no-post",
            "--backfill-mode",
        ]
        if force:
            cmd.append("--force")
        if regions:
            cmd += ["--regions", *regions]
        if exclude_regions:
            cmd += ["--exclude-regions", *exclude_regions]
        started = time.perf_counter()
        rc = _run(cmd, dry_run=dry_run, env=env, label=label or ",".join(regions or []) or session_file.stem)
        elapsed = time.perf_counter() - started
    finally:
        try:
            dates_file.unlink(missing_ok=True)
        except OSError:
            pass

    return chart_dates, rc, elapsed, session_file.name


def _run_worker(
    chart_dates: list[str],
    *,
    session_file: Path,
    force: bool,
    dry_run: bool,
    per_worker_semaphore: int,
    request_interval: float,
    fetch_max_attempts: int,
    rate_limit_max: int,
    regions: list[str] | None,
    exclude_regions: list[str] | None,
    skip_discontinued: bool,
) -> tuple[list[str], int, float, str]:
    """One worker's batch. When --regions is not used and the discontinued-region
    filter is on, the batch is split at DISCONTINUED_REGION_CUTOFF so the ~31
    dead regionals are only requested for dates when they still existed."""
    base_exclude = list(exclude_regions or [])
    if regions or not skip_discontinued:
        return _run_chunk(
            chart_dates, session_file=session_file, force=force, dry_run=dry_run,
            per_worker_semaphore=per_worker_semaphore, request_interval=request_interval,
            fetch_max_attempts=fetch_max_attempts, rate_limit_max=rate_limit_max,
            regions=regions, exclude_regions=base_exclude or None,
        )

    post = [d for d in chart_dates if d > DISCONTINUED_REGION_CUTOFF]
    pre = [d for d in chart_dates if d <= DISCONTINUED_REGION_CUTOFF]
    total_rc = 0
    total_elapsed = 0.0
    # Newest-first: post-cutoff dates before pre-cutoff, matching the global order.
    for subset, extra_exclude in (
        (post, list(DISCONTINUED_REGIONS)),
        (pre, []),
    ):
        if not subset:
            continue
        merged_exclude = sorted(set(base_exclude) | set(extra_exclude)) or None
        _, rc, elapsed, _ = _run_chunk(
            subset, session_file=session_file, force=force, dry_run=dry_run,
            per_worker_semaphore=per_worker_semaphore, request_interval=request_interval,
            fetch_max_attempts=fetch_max_attempts, rate_limit_max=rate_limit_max,
            regions=None, exclude_regions=merged_exclude,
        )
        total_rc = total_rc or rc
        total_elapsed += elapsed
    return chart_dates, total_rc, total_elapsed, session_file.name


def _run_per_region_sweep(args, sessions, all_dates, state, state_path) -> int:
    """One region at a time: each region's own daily.py run fetches only that
    region's CSV-gap dates. Single region per run -> one request per pacer tick,
    no parallel-429 cascade. Completion is tracked per (region, date) in
    state['region_done']; re-running resumes only the still-pending dates."""
    codes = _sweep_region_codes(args.per_region_sweep)
    present_by_code = {c: _csv_dates_present(c) for c in codes}
    gap_by_code = {c: [d for d in all_dates if d not in present_by_code[c]] for c in codes}
    for c in codes:
        if c in DISCONTINUED_REGIONS and not args.include_discontinued_regions:
            gap_by_code[c] = [d for d in gap_by_code[c] if d <= DISCONTINUED_REGION_CUTOFF]
    # `codes` is already ordered richest-history-first by _sweep_region_codes
    # (explicit lists keep their given order).

    # region_done is the source of truth; a legacy state file's swept_regions (no
    # per-date detail, and it was written even for interrupted runs) is ignored.
    region_done: dict[str, set[str]] = {
        k: set(v) for k, v in (state.get("region_done") or {}).items()
    }
    if args.refetch_done:
        region_done = {}
    # A (region, date) daily.py gave up on (max fetch attempts on 429/timeout ->
    # listed in the snapshot's skipped_regions, yet rc 0) is NOT done: purge it so
    # this run refetches it (fix 2026-10-02 — such dates were silently lost).
    skipped_cache: dict[str, set[str]] = {}

    def _skipped(chart_date: str) -> set[str]:
        if chart_date not in skipped_cache:
            skipped_cache[chart_date] = _snapshot_skipped_regions(chart_date)
        return skipped_cache[chart_date]

    purged = 0
    for code, done_dates in region_done.items():
        chart_code = _CSV_TO_CHART_CODE.get(code, code)
        bad = {d for d in done_dates & set(gap_by_code.get(code, [])) if chart_code in _skipped(d)}
        if bad:
            done_dates -= bad
            purged += len(bad)
    if purged:
        print(f"[SWEEP] {purged} (region, date) previously marked done were skipped by daily.py -> pending again")
    session_file = sessions[0]

    def _remaining(code: str) -> list[str]:
        done = region_done.get(code, set())
        return [d for d in gap_by_code[code] if d not in done]

    todo = [c for c in codes if _remaining(c)]
    print(
        f"[SWEEP] {len(todo)}/{len(codes)} region(s) to do "
        f"(range {all_dates[0]} -> {all_dates[-1]}, sessions {', '.join(p.name for p in sessions)}); "
        f"total gap date-fetches = {sum(len(_remaining(c)) for c in todo)}"
    )
    touched_dates: set[str] = set()
    incomplete: dict[str, int] = {}

    # Per-date checkpoint (2026-10-02): before, region_done was only saved when a
    # whole region finished, so a Ctrl+C mid-region re-fetched every 0-entry date
    # of that region (they leave no trace on disk). Now each (region, date) is
    # marked done as soon as daily.py has written its snapshot, saved every 30 s
    # and on Ctrl+C.
    ckpt_lock = threading.Lock()
    ckpt_dirty = threading.Event()
    current: dict[str, str | None] = {"code": None, "chart": None}

    def _on_region_fetched(region: str, chart_date: str) -> None:
        if args.dry_run or region != current["chart"]:
            return
        with ckpt_lock:
            region_done.setdefault(current["code"], set()).add(chart_date)
        ckpt_dirty.set()

    def _checkpoint() -> None:
        if args.dry_run:
            return
        with ckpt_lock:
            state["swept_regions"] = sorted(c for c in codes if not _remaining(c))
            state["region_done"] = {k: sorted(v) for k, v in region_done.items() if v}
            _save_state(state_path, state)
        ckpt_dirty.clear()

    stop_ckpt = threading.Event()

    def _ckpt_loop() -> None:
        while not stop_ckpt.wait(30):
            if ckpt_dirty.is_set():
                try:
                    _checkpoint()
                except Exception as exc:
                    print(f"[WARN] checkpoint failed: {exc!r}", flush=True)

    RUN_STATS.on_region_fetched = _on_region_fetched
    threading.Thread(target=_ckpt_loop, name="sweep-checkpoint", daemon=True).start()
    try:
        rc = 0
        if args.proven_gaps_first:
            # Phase 1 (2026-10-02): only the (region, date) pairs Spotify's own
            # counters prove (streak) or tightly bound (total_days jump) to hold
            # Taylor rows we don't have -> most of the recoverable data in hours.
            # Phase 2 then sweeps every other gap date; phase-1 dates are already
            # in region_done so nothing is fetched twice.
            proven: dict[str, set[str]] = {}
            n_exact = n_bounded = 0
            for c in todo:
                exact, bounded = _proven_gap_dates(c)
                rem = set(_remaining(c))
                exact &= rem
                bounded &= rem
                n_exact += len(exact)
                n_bounded += len(bounded)
                if exact or bounded:
                    proven[c] = exact | bounded

            def _remaining_proven(code: str) -> list[str]:
                return [d for d in _remaining(code) if d in proven.get(code, ())]

            todo1 = sorted(proven, key=lambda c: -len(proven[c]))
            print(
                f"[SWEEP] PHASE 1 trous prouves : {n_exact + n_bounded} date-fetches "
                f"({n_exact} exactes via streak, {n_bounded} encadrees via total_days) "
                f"sur {len(todo1)} region(s)",
                flush=True,
            )
            rc = _sweep_regions(
                args, todo1, _remaining_proven, region_done, current, touched_dates,
                incomplete, _checkpoint, ckpt_lock, session_file, sessions, phase="P1 ",
            )
            if rc == 0:
                incomplete.clear()
                todo = [c for c in todo if _remaining(c)]
                print(
                    f"\n[SWEEP] PHASE 2 sweep complet : {sum(len(_remaining(c)) for c in todo)} "
                    f"date-fetches sur {len(todo)} region(s)",
                    flush=True,
                )
        if rc == 0:
            rc = _sweep_regions(
                args, todo, _remaining, region_done, current, touched_dates,
                incomplete, _checkpoint, ckpt_lock, session_file, sessions,
                phase="P2 " if args.proven_gaps_first else "",
            )
    except KeyboardInterrupt:
        stop_ckpt.set()
        _checkpoint()
        print(
            "\n[SWEEP] Ctrl+C - progression sauvegardee (chaque date deja ecrite est "
            "marquee faite). Relancer la meme commande reprend ici.",
            flush=True,
        )
        return 130
    finally:
        stop_ckpt.set()
        RUN_STATS.on_region_fetched = None
    if rc != 0:
        return rc

    rc = _run_sync_pass(args, touched_dates, all_dates)
    if rc != 0:
        return rc

    print(
        f"\n[SWEEP] done - {len(todo)} region(s) run, {len(touched_dates)} distinct date(s) with data"
        + (f"; incomplete (re-run to finish): {incomplete}" if incomplete else "")
    )
    return 0 if not incomplete else 1


def _sweep_regions(
    args, todo, _remaining, region_done, current, touched_dates,
    incomplete, _checkpoint, ckpt_lock, session_file, sessions, phase: str = "",
) -> int:
    consecutive_dead = 0
    sweep_started = time.monotonic()
    for i, code in enumerate(todo, 1):
        chart_code = _CSV_TO_CHART_CODE.get(code, code)
        current["code"], current["chart"] = code, chart_code
        dates = sorted(_remaining(code), reverse=True)
        if not dates:
            continue
        print(
            f"\n[SWEEP] {phase}{i}/{len(todo)} {code}"
            + (f" (chart '{chart_code}')" if chart_code != code else "")
            + f": {len(dates)} date(s) to fetch {dates[-1]} -> {dates[0]}",
            flush=True,
        )
        left_total = sum(len(_remaining(c)) for c in todo[i - 1:])
        RUN_STATS.sweep_pos = f"{phase}{i}/{len(todo)}"
        RUN_STATS.sweep_left_at_region = left_total
        avg = RUN_STATS.avg()
        if avg:
            finish = datetime.now() + timedelta(seconds=avg * left_total)
            print(
                f"[SWEEP] ETA globale : {left_total} date-fetches restantes x {avg:.1f}s "
                f"= {_fmt_duration(avg * left_total)} -> fin ~{finish.strftime('%d/%m %H:%M')} "
                f"| sweep lance depuis {_fmt_duration(time.monotonic() - sweep_started)}, "
                f"pauses 429 : {RUN_STATS.pauses} ({_fmt_duration(RUN_STATS.pause_seconds)})",
                flush=True,
            )
        # force=False: lets daily.py's per-region already_done skip work, so a
        # resumed / re-run region only re-probes the dates it hasn't got yet.
        _, rc, elapsed, _ = _run_chunk(
            dates,
            session_file=session_file,
            force=False,
            dry_run=bool(args.dry_run),
            per_worker_semaphore=1,
            request_interval=float(args.request_interval),
            fetch_max_attempts=int(args.fetch_max_attempts),
            rate_limit_max=int(args.rate_limit_max),
            regions=[chart_code],
            exclude_regions=None,
            label=code,
            single_session=len(sessions) <= 1,
        )
        got = [d for d in dates if args.dry_run or _snapshot_has_region(d, chart_code)]
        touched_dates.update(got)
        # rc == 0 -> daily.py looped through every date (some empty, some with
        # data) -> all processed. rc != 0 -> crashed/interrupted (WARP drop etc.)
        # -> only the dates that now carry data are known-done; the rest stay
        # pending so the next run retries just those.
        newly_done = set(dates) if (rc == 0 or args.dry_run) else set(got)
        skipped_now = {d for d in newly_done if chart_code in _snapshot_skipped_regions(d)}
        if skipped_now and not args.dry_run:
            newly_done -= skipped_now
            print(
                f"[SWEEP] {code}: {len(skipped_now)} date(s) abandoned by daily.py "
                f"(max attempts) stay pending: {', '.join(sorted(skipped_now)[:10])}"
                + (" ..." if len(skipped_now) > 10 else ""),
                flush=True,
            )
        with ckpt_lock:
            region_done[code] = region_done.get(code, set()) | newly_done
            region_done[code] -= skipped_now if not args.dry_run else set()
        fully = not _remaining(code)
        if not fully:
            incomplete[code] = len(_remaining(code))
        print(
            f"[SWEEP] {phase}{i}/{len(todo)} {code}: {len(got)}/{len(dates)} date(s) got "
            f"'{chart_code}' data, {len(newly_done)} processed, {elapsed:.0f}s"
            + ("" if fully else f" — {incomplete[code]} still pending (rc={rc}), re-run to finish"),
            flush=True,
        )
        _checkpoint()

        # A region run that finished non-zero AND collected nothing usually means
        # the network / WARP dropped (not that the chart is empty). Two in a row
        # -> stop rather than grind ~30 more regions against a dead connection.
        if rc != 0 and not got and not args.dry_run:
            consecutive_dead += 1
            if consecutive_dead >= 2:
                print(
                    "[SWEEP] 2 region runs in a row collected nothing — network/WARP is "
                    "probably down. Stopping. Reconnect and re-run the same command to resume.",
                    flush=True,
                )
                return 1
        else:
            consecutive_dead = 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumable no-post Spotify Charts backfill.")
    parser.add_argument("--start", default="2017-01-01", help="Start date inclusive (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date inclusive (YYYY-MM-DD), default: yesterday")
    parser.add_argument(
        "--state",
        default=None,
        help=(
            "JSON state file storing completed dates. Default: "
            f"{DEFAULT_STATE.name}, or {DEFAULT_GAPS_STATE.name} when --gaps-from-csv is "
            "used (a fresh campaign shouldn't inherit snapshot-based done_dates that "
            "never reached the CSV)."
        ),
    )
    parser.add_argument("--force", action="store_true", default=True, help="Pass --force to the collector for pending dates")
    parser.add_argument("--no-force", action="store_false", dest="force", help="Do not pass --force to the collector")
    parser.add_argument("--refetch-done", action="store_true", help="Re-fetch dates even if they are marked done")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of dates to fetch this run")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Number of parallel worker processes (default: 1). Each worker fetches its "
            "whole date batch in a single long-running subprocess via --dates-file."
        ),
    )
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to stagger between worker chunk launches")
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=30.0,
        help=(
            "Seconds between progress checkpoints — the wrapper scans the snapshots "
            "daily.py has written and saves the resume-state file. Default 30. "
            "Lower = finer resume granularity if killed, tiny bit more disk I/O."
        ),
    )
    parser.add_argument("--skip-existing-snapshot", action="store_true", default=True)
    parser.add_argument(
        "--request-interval",
        type=float,
        default=1.0,
        help=(
            "Seconds between requests inside each worker (daily.py's global RequestPacer). "
            "This is what actually caps throughput (~1 req/interval/worker, ~68 regions/date). "
            "Default 1.0 (~1.5 min/date/worker). Raise toward 2.0 if logs show '429 - pause globale'."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=6,
        help=(
            "Regions fetched in parallel within each worker (SPOTIFY_WORLDWIDE_SEMAPHORE). "
            "Default 6. Lower = fewer simultaneous 429s = fewer pauses. Overrides the "
            "SPOTIFY_WORLDWIDE_TOTAL_CONCURRENCY env split."
        ),
    )
    parser.add_argument(
        "--fetch-max-attempts",
        type=int,
        default=8,
        help=(
            "Max fetch attempts per region before it is omitted from the date's snapshot "
            "(SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS). Default 8 — bounded so one stuck region "
            "(sustained 429 / WARP wobble) cannot freeze a date forever. 0 = unlimited."
        ),
    )
    parser.add_argument(
        "--rate-limit-max",
        type=int,
        default=30,
        help=(
            "Cap (seconds) on a single 429 GlobalPause (SPOTIFY_WORLDWIDE_RATE_LIMIT_MAX_SECONDS; "
            "daily.py's own default is 300). Keeps pauses short and visible instead of the "
            "multiplicative 20s->40s->...->300s silent backoff. Default 30."
        ),
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        metavar="CODE",
        help=(
            "Only (re)collect these region codes for each pending date, merging them "
            "back into the existing dated snapshot region by region (forwarded to "
            "worldwide/daily.py --regions). Use for a targeted fill, e.g. --regions fr."
        ),
    )
    parser.add_argument(
        "--exclude-regions",
        nargs="+",
        metavar="CODE",
        help="Collect every discovered region except these (forwarded to worldwide/daily.py).",
    )
    parser.add_argument(
        "--include-discontinued-regions",
        action="store_true",
        help=(
            f"Also request the {len(DISCONTINUED_REGIONS)} regionals Spotify dropped ~"
            f"{DISCONTINUED_REGION_CUTOFF} for dates AFTER that cutoff. Off by default: "
            "those return 404 post-cutoff and waste a pacer slot. Ignored with --regions."
        ),
    )
    parser.add_argument(
        "--gaps-from-csv",
        nargs="*",
        metavar="REGION",
        default=None,
        help=(
            "Derive the pending dates from dates ABSENT in db/charts_history_<region>.csv "
            "(the durable git-tracked store) instead of from missing local snapshot files. "
            "Bare flag uses 'global'; pass region codes to union their gaps "
            "(e.g. --gaps-from-csv global us uk). Best signal for a large historical fill."
        ),
    )
    parser.add_argument(
        "--per-region-sweep",
        nargs="*",
        metavar="CODE",
        default=None,
        help=(
            "Sequential region-by-region backfill: for each region, one daily.py run "
            "fetching only that region's own charts_history_<code>.csv gap dates in "
            "[--start,--end]. One region per run = one request per --request-interval, "
            "no parallel-429 cascade — much gentler than fetching N regions per date. "
            "Bare = every 2-letter db/charts_history_<cc>.csv (minus global), biggest "
            "gap first; or pass codes. Checkpoints after each region; resumable. "
            "uk is fetched as the 'gb' chart. Ignores --workers."
        ),
    )
    parser.add_argument(
        "--proven-gaps-first",
        action="store_true",
        help=(
            "With --per-region-sweep: first fetch only the (region, date) pairs where Spotify's "
            "own counters prove a missing Taylor row (streak = consecutive days -> exact dates; "
            "total_days jump between two rows of a song -> dense bounded window), biggest region "
            "first; then the normal full sweep for every other gap date."
        ),
    )
    parser.add_argument("--no-sync", action="store_true", help="Do not sync charts_history CSVs after collection")
    parser.add_argument(
        "--upload-r2",
        action="store_true",
        help=(
            "After sync, upload the touched chart data to R2 (scripts/r2.py --charts-only) "
            "one worldwide dated snapshot at a time, matching explicit --date runs. "
            "Ignored if --no-sync is set (nothing new to upload). Networked/production write: "
            "off by default, opt in explicitly."
        ),
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=(
            "Live log file (every line timestamped, daily.py output included). Default: "
            "runtime/logs/spotify_charts_backfill_<YYYYmmdd_HHMMSS>.log. 'none' disables it."
        ),
    )
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=60.0,
        help="Print a [HEARTBEAT] line when daily.py has been silent this many seconds (default 60).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Console shows every daily.py line (old behaviour). Default in a terminal: one "
            "animated status line + important lines only; the full detail is always in --log-file."
        ),
    )
    args = parser.parse_args()

    global HEARTBEAT_SECONDS
    HEARTBEAT_SECONDS = max(10.0, float(args.heartbeat))
    if args.log_file and args.log_file.lower() == "none":
        log_path = None
    elif args.log_file:
        log_path = Path(args.log_file)
    else:
        log_path = ROOT / "runtime" / "logs" / f"spotify_charts_backfill_{datetime.now():%Y%m%d_%H%M%S}.log"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    live = not args.verbose and not args.dry_run and sys.stdout.isatty()
    tee = _TimestampTee(sys.stdout, log_path, live=live)
    sys.stdout = tee
    if live:
        stop_anim = _start_status_animation(tee)

        def _stop_anim() -> None:
            stop_anim.set()
            tee.end_status()

        atexit.register(_stop_anim)
    print(f"[LOG] {' '.join(sys.argv)}")
    if log_path is not None:
        print(f"[LOG] live log -> {log_path}  (suivre : Get-Content -Wait -Tail 50 \"{log_path}\")")

    if args.upload_r2 and args.no_sync:
        print("[WARN] --upload-r2 ignored because --no-sync is set (snapshots were not enriched this run)")
        args.upload_r2 = False

    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else (date.today() - timedelta(days=1))
    if end < start:
        raise SystemExit("--end must be >= --start")

    csv_gap_regions: list[str] | None = None
    if args.gaps_from_csv is not None:
        csv_gap_regions = [r.strip().lower() for r in args.gaps_from_csv if r.strip()] or ["global"]

    if args.state:
        state_path = Path(args.state)
    else:
        state_path = DEFAULT_GAPS_STATE if (csv_gap_regions or args.per_region_sweep is not None) else DEFAULT_STATE
    state = _load_state(state_path)
    all_dates = _date_range(start, end)

    sessions = _session_files()
    if not sessions:
        raise SystemExit(f"No Spotify session files found in {DEFAULT_SESSION_DIR}")

    if args.per_region_sweep is not None:
        return _run_per_region_sweep(args, sessions, all_dates, state, state_path)

    if csv_gap_regions:
        # A date is pending if it is missing from ANY requested region's CSV
        # (union of gaps) -> present = dates covered by EVERY requested region.
        present: set[str] | None = None
        for region in csv_gap_regions:
            region_present = _csv_dates_present(region)
            present = region_present if present is None else (present & region_present)
        csv_pending = [d for d in all_dates if d not in (present or set())]
        print(
            f"[GAPS] {len(csv_pending)} date(s) missing from charts_history_"
            f"{{{','.join(csv_gap_regions)}}}.csv in range (durable-store signal)"
        )
        # Selection comes from the CSV, not from local snapshot presence.
        pending_pool = csv_pending
    else:
        if args.skip_existing_snapshot and not args.refetch_done:
            added = _mark_existing_snapshots(state, all_dates)
            if added:
                print(f"[STATE] {added} existing snapshot date(s) marked done")
                if not args.dry_run:
                    _save_state(state_path, state)
        pending_pool = all_dates

    done = set(state.get("done_dates") or [])
    # Backfill newest first so long historical runs publish useful recent gaps
    # before spending hours on old archive dates.
    pending = sorted(
        (d for d in pending_pool if args.refetch_done or d not in done),
        reverse=True,
    )
    if args.limit and args.limit > 0:
        pending = pending[: args.limit]

    workers = max(1, int(args.workers or 1))
    workers = min(workers, len(sessions), len(pending) or 1)
    if args.concurrency and args.concurrency > 0:
        per_worker_semaphore = int(args.concurrency)
    else:
        total_worldwide_concurrency = max(1, int(os.getenv("SPOTIFY_WORLDWIDE_TOTAL_CONCURRENCY", "1")))
        per_worker_semaphore = max(1, total_worldwide_concurrency // workers)

    skip_discontinued = not args.include_discontinued_regions and not args.regions
    n_post_cutoff = sum(1 for d in pending if d > DISCONTINUED_REGION_CUTOFF)

    chunks = _chunks(pending, workers)
    print(
        f"[PLAN] range={all_dates[0]} -> {all_dates[-1]} order=newest-first total={len(all_dates)} "
        f"pending={len(pending)} workers={len(chunks)} chunk_sizes={[len(c) for c in chunks]} "
        f"regions_in_parallel_per_worker={per_worker_semaphore} "
        f"request_interval={args.request_interval}s fetch_max_attempts={args.fetch_max_attempts} "
        f"rate_limit_max={args.rate_limit_max}s "
        f"sessions={', '.join(p.name for p in sessions[: len(chunks)])}"
    )
    if skip_discontinued and n_post_cutoff:
        print(
            f"[PLAN] {len(DISCONTINUED_REGIONS)} discontinued regionals excluded for "
            f"{n_post_cutoff} date(s) after {DISCONTINUED_REGION_CUTOFF} "
            f"(pass --include-discontinued-regions to keep them)"
        )
    failures: dict[str, str] = dict(state.get("failed_dates") or {})
    touched_dates: set[str] = set()

    # Incremental progress + resumability: one subprocess per worker can run for
    # hours over hundreds of dates, and state was previously saved only once the
    # whole chunk returned — a kill at date 600/649 lost everything. This watcher
    # scans the snapshots daily.py writes as it goes and checkpoints the state
    # file every `progress_interval` seconds.
    state_lock = threading.Lock()
    run_started = time.perf_counter()
    stop_watcher = threading.Event()
    total_pending = len(pending)
    done_at_start = len(done & set(pending))

    def _progress_watcher() -> None:
        while not stop_watcher.wait(args.progress_interval):
            if args.dry_run:
                continue
            newly = [d for d in pending if d not in done and _snapshot_is_usable(d)]
            with state_lock:
                if newly:
                    for d in newly:
                        done.add(d)
                        touched_dates.add(d)
                        failures.pop(d, None)
                    state["done_dates"] = sorted(done)
                    state["failed_dates"] = failures
                    _save_state(state_path, state)
                n_done = len(done & set(pending))
            elapsed = time.perf_counter() - run_started
            this_run = n_done - done_at_start
            rate = this_run / elapsed if elapsed > 0 else 0
            eta = f", ~{(total_pending - n_done) / rate / 3600:.1f}h left" if rate > 0 and n_done < total_pending else ""
            print(
                f"[PROGRESS] {n_done}/{total_pending} dates written "
                f"({n_done * 100 // max(total_pending, 1)}%, +{this_run} this run){eta}",
                flush=True,
            )

    watcher = threading.Thread(target=_progress_watcher, name="progress-watcher", daemon=True)
    if not args.dry_run:
        watcher.start()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunks) or 1) as executor:
        future_to_chunk = {}
        for idx, chunk in enumerate(chunks, 1):
            session_file = sessions[(idx - 1) % len(chunks)]
            print(
                f"[QUEUE] worker {idx}/{len(chunks)}: {len(chunk)} date(s) "
                f"({chunk[0]} -> {chunk[-1]}) via {session_file.name}",
                flush=True,
            )
            future = executor.submit(
                _run_worker,
                chunk,
                session_file=session_file,
                force=bool(args.force),
                dry_run=bool(args.dry_run),
                per_worker_semaphore=per_worker_semaphore,
                request_interval=float(args.request_interval),
                fetch_max_attempts=int(args.fetch_max_attempts),
                rate_limit_max=int(args.rate_limit_max),
                regions=args.regions,
                exclude_regions=args.exclude_regions,
                skip_discontinued=skip_discontinued,
            )
            future_to_chunk[future] = chunk
            if args.sleep > 0:
                time.sleep(args.sleep)

        completed_workers = 0
        for future in concurrent.futures.as_completed(future_to_chunk):
            completed_workers += 1
            chunk = future_to_chunk[future]
            try:
                chunk, rc, elapsed, session_name = future.result()
            except Exception as exc:
                with state_lock:
                    for chart_date in chunk:
                        if chart_date not in done:
                            failures[chart_date] = f"exception={exc}"
                print(f"[FAIL] worker {completed_workers}/{len(chunks)} ({len(chunk)} date(s)): exception={exc}", flush=True)
            else:
                # Evaluate each date on its own snapshot, not the chunk's aggregate rc:
                # daily.py's multi-date loop continues past a failed date instead of
                # aborting the batch, so one bad date in a chunk must not make the
                # wrapper discard every other (successfully written) date in it.
                ok_dates: list[str] = []
                bad_dates: list[str] = []
                for chart_date in chunk:
                    if args.dry_run or _snapshot_is_usable(chart_date):
                        ok_dates.append(chart_date)
                    else:
                        bad_dates.append(chart_date)
                with state_lock:
                    for chart_date in ok_dates:
                        done.add(chart_date)
                        touched_dates.add(chart_date)
                        failures.pop(chart_date, None)
                    for chart_date in bad_dates:
                        failures[chart_date] = f"rc={rc}; no usable snapshot; session={session_name}"
                print(
                    f"[ OK ] worker {completed_workers}/{len(chunks)} via {session_name}: "
                    f"{len(ok_dates)}/{len(chunk)} date(s) in {elapsed:.1f}s"
                    + (f" — {len(bad_dates)} failed" if bad_dates else ""),
                    flush=True,
                )

            with state_lock:
                state["done_dates"] = sorted(done)
                state["failed_dates"] = failures
                if not args.dry_run:
                    _save_state(state_path, state)

    stop_watcher.set()
    if watcher.is_alive():
        watcher.join(timeout=5)

    rc = _run_sync_pass(args, touched_dates, all_dates)
    if rc != 0:
        return rc

    # Summary: of the dates we wrote a snapshot for, how many actually had Taylor
    # entries vs were empty (she was out of every chart that day — expected for
    # 2018-2020). Empty snapshots still count as done; they won't be re-tried.
    with_data = empty = 0
    for chart_date in sorted(touched_dates):
        try:
            payload = json.loads(_snapshot_path(chart_date).read_text(encoding="utf-8-sig"))
            if payload.get("by_track"):
                with_data += 1
            else:
                empty += 1
        except Exception:
            pass
    print(
        f"[DONE] done={len(done)} (this run: {with_data} with TS data, {empty} empty) "
        f"failed={len(failures)} state={state_path}"
    )
    if failures:
        print(
            "[DONE] failed dates are re-tried on the next run; many are Spotify-side gaps "
            "(dates / regional charts it never published) and will keep failing fast."
        )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
