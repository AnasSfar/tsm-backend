"""Live run log for update_streams.py (2026-10-04, modelled on
scripts/backfill_spotify_charts_history.py's live log).

- Every line printed (ours + post subprocesses piped through finalize) gets a
  wall-clock ``[HH:MM:SS]`` prefix. Installed inside core.run_logging's
  CollectorRunLog, so the per-attempt run log
  (``snapshots/spotify_streams/.../logs/<date>_spstr_..._attemptNN.txt``) gets
  the timestamps too; ``own_file=True`` adds a separate UTF-8 file under
  ``runtime/logs/`` instead.
- A status registry (``set_phase`` / ``set_progress`` / ``set_detail``) feeds:
  * in a real terminal: one status line redrawn in place (spinner, clock,
    phase, progress bar, rate, time left + finish time, elapsed);
  * everywhere (log file, Task Scheduler Tee window): a ``[STATUS]`` line every
    ``STATUS_EVERY_SECONDS`` while nothing else is printed (heartbeat), so a
    stuck run is visible in the log with its phase and how long it has waited.

Never raises into the pipeline: logging problems are swallowed.
"""
from __future__ import annotations

import atexit
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

STATUS_EVERY_SECONDS = 60.0
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = int(round(max(0.0, seconds)))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}min{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}"


class _Status:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.run_started = time.monotonic()
        self.stats_date = ""
        self.phase = "demarrage"
        self.phase_started = time.monotonic()
        self.done = 0
        self.total = 0
        self.progress_started = time.monotonic()
        self.detail = ""
        self.last_output = time.monotonic()


STATUS = _Status()


def set_stats_date(stats_date: str) -> None:
    with STATUS.lock:
        STATUS.stats_date = stats_date


def set_phase(phase: str, *, total: int = 0, detail: str = "") -> None:
    """New pipeline phase (resets progress). Also printed as a [PHASE] line."""
    now = time.monotonic()
    with STATUS.lock:
        changed = phase != STATUS.phase
        STATUS.phase = phase
        STATUS.phase_started = now
        STATUS.done = 0
        STATUS.total = max(0, int(total or 0))
        STATUS.progress_started = now
        STATUS.detail = detail
    if changed:
        elapsed = fmt_duration(now - STATUS.run_started)
        print(f"[PHASE] {phase}" + (f" — {detail}" if detail else "") + f"  (run depuis {elapsed})", flush=True)


def set_progress(done: int, total: int | None = None) -> None:
    with STATUS.lock:
        if total is not None and int(total) != STATUS.total:
            STATUS.total = max(0, int(total))
            STATUS.progress_started = time.monotonic()
        STATUS.done = max(0, int(done))


def set_detail(detail: str) -> None:
    with STATUS.lock:
        STATUS.detail = detail


def _eta(done: int, total: int, started: float) -> tuple[float | None, float | None]:
    """(rate per second, seconds left) from the current progress window."""
    elapsed = time.monotonic() - started
    if done <= 0 or elapsed <= 0 or total <= 0:
        return None, None
    rate = done / elapsed
    return rate, max(0, total - done) / rate


def status_text(frame: int | None = None, *, bar: bool = True) -> str:
    with STATUS.lock:
        phase, detail = STATUS.phase, STATUS.detail
        done, total = STATUS.done, STATUS.total
        started, phase_started, run_started = STATUS.progress_started, STATUS.phase_started, STATUS.run_started
        stats_date = STATUS.stats_date
    now = time.monotonic()
    head = (f"{_SPINNER[frame % len(_SPINNER)]} " if frame is not None else "") + _hms()
    parts = [phase]
    if total:
        pct = min(1.0, done / total)
        if bar:
            width = 16
            fill = int(round(pct * width))
            parts.append(f"{'█' * fill}{'░' * (width - fill)} {done}/{total} {pct * 100:3.0f}%")
        else:
            parts.append(f"{done}/{total} ({pct * 100:.0f}%)")
        rate, left = _eta(done, total, started)
        if rate:
            parts.append(f"{rate:.1f}/s" if rate >= 1 else f"{60 * rate:.1f}/min")
        if left is not None and done < total:
            finish = datetime.now() + timedelta(seconds=left)
            parts.append(f"reste ~{fmt_duration(left)} (fin ~{finish:%H:%M})")
    else:
        parts.append(f"depuis {fmt_duration(now - phase_started)}")
    if detail:
        parts.append(detail)
    parts.append(f"run {fmt_duration(now - run_started)}")
    prefix = f"{head}  {stats_date}  " if stats_date else f"{head}  "
    return prefix + " · ".join(parts)


class _TimestampTee:
    """sys.stdout/stderr replacement: [HH:MM:SS] prefix, UTF-8 log file, and a
    live status line redrawn in place when the console is a terminal."""

    def __init__(self, stream, log_file, *, live: bool, shared_lock: threading.Lock):
        self._stream = stream
        # The live line goes to the real terminal only, never into tee'd log files.
        self._term = sys.__stdout__
        self._log = log_file
        self.live = live
        self._lock = shared_lock
        self._at_line_start = True
        self._status_len = 0

    def _console(self, text: str) -> None:
        try:
            self._stream.write(text)
        except UnicodeEncodeError:
            self._stream.write(text.encode("ascii", "replace").decode("ascii"))
        except Exception:
            return
        try:
            self._stream.flush()
        except Exception:
            pass

    def _term_write(self, text: str) -> None:
        try:
            self._term.write(text)
            self._term.flush()
        except Exception:
            pass

    def _clear_status(self) -> None:
        if self._status_len:
            self._term_write("\r" + " " * self._status_len + "\r")
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
                try:
                    self._log.write(chunk)
                except Exception:
                    pass
            if self.live:
                self._clear_status()
            self._console(chunk)
            STATUS.last_output = time.monotonic()
        return len(text)

    def status(self, text: str) -> None:
        if not self.live:
            return
        width = max(20, shutil.get_terminal_size((140, 20)).columns - 1)
        text = text[:width]
        with self._lock:
            if not self._at_line_start:
                return  # never draw over a half-written line
            pad = max(0, self._status_len - len(text))
            self._term_write("\r" + text + " " * pad)
            self._status_len = len(text)

    def end_status(self) -> None:
        with self._lock:
            self._clear_status()

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False  # children/libraries must not draw their own \r bars

    def __getattr__(self, name):
        return getattr(self._stream, name)


_INSTALLED: dict = {}


def install(name: str, stats_date: str = "", *, own_file: bool = False, log_dir: Path | None = None) -> Path | None:
    """Timestamp stdout/stderr, start the status heartbeat (+ live line in a
    terminal), optionally tee into an own log file. Idempotent."""
    if _INSTALLED:
        return _INSTALLED.get("path")
    if stats_date:
        set_stats_date(stats_date)
    path: Path | None = None
    log_file = None
    if own_file:
        repo_root = Path(__file__).resolve().parents[5]
        log_dir = log_dir or (repo_root / "runtime" / "logs")
        path = log_dir / f"{name}_{stats_date + '_' if stats_date else ''}{datetime.now():%Y%m%d_%H%M%S}.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            path = None
    for stream in (sys.stdout, sys.stderr, sys.__stdout__):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        live = bool(sys.__stdout__.isatty())
    except Exception:
        live = False
    lock = threading.Lock()
    out = _TimestampTee(sys.stdout, log_file, live=live, shared_lock=lock)
    err = _TimestampTee(sys.stderr, log_file, live=False, shared_lock=lock)
    sys.stdout, sys.stderr = out, err
    stop = threading.Event()

    def _loop() -> None:
        frame = 0
        last_status_line = time.monotonic()
        while not stop.wait(0.2 if live else 5.0):
            try:
                if live:
                    out.status(status_text(frame))
                    frame += 1
                now = time.monotonic()
                if (
                    now - STATUS.last_output >= STATUS_EVERY_SECONDS
                    and now - last_status_line >= STATUS_EVERY_SECONDS
                ):
                    last_status_line = now
                    print(f"[STATUS] {status_text(bar=False)}", flush=True)
            except Exception:
                pass
        out.end_status()

    threading.Thread(target=_loop, name="live-log-status", daemon=True).start()

    def _shutdown() -> None:
        stop.set()
        try:
            out.end_status()
        except Exception:
            pass

    atexit.register(_shutdown)
    _INSTALLED.update({"path": path, "stop": stop})
    if path is not None:
        print(f"[LOG] live log -> {path}  (suivre : Get-Content -Wait -Tail 50 \"{path}\")", flush=True)
    return path
