"""Shared guards for the hourly local collectors (2026-09-25): phone alert +
single-instance lock.

Task Scheduler cannot prevent overlapping runs here: the .vbs launches the
.bat without waiting (shell.Run ..., 0, False), so IgnoreNew / time limits
never apply. A runner that finds the previous run still ALIVE skips its hour
(EXIT_SKIPPED, checked by the .bat). Liveness is checked by PID, never by
age: a run suspended by laptop sleep stays the owner when it wakes up.

Used by collectors/itunes/run_itunes.py and collectors/apple_music/run_apple_music.py.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

EXIT_SKIPPED = 75
# Failed, but the runner already sent the phone alert (the .bat alerts for any
# OTHER non-zero code: a crash before Python could alert).
EXIT_ALERTED = 3


def retry_step(label: str, fn, *, attempts: int | None = None, waits: tuple[int, ...] = (20, 60)) -> int:
    """Run fn() -> exit code until it returns 0, up to `attempts` times
    (RUN_RETRY_ATTEMPTS, default 3), sleeping waits[i] seconds between tries
    (owner 2026-09-25: "si quelque chose échoue on réessaie toujours"). Returns
    the last code; the caller alerts only if it is still non-zero."""
    import time

    attempts = attempts or int(os.getenv("RUN_RETRY_ATTEMPTS", "3"))
    code = 1
    for i in range(attempts):
        code = fn()
        if code == 0:
            if i:
                print(f"[retry] {label}: OK on attempt {i + 1}/{attempts}", flush=True)
            return 0
        if i < attempts - 1:
            wait = waits[min(i, len(waits) - 1)]
            print(f"[retry] {label}: failed (code {code}), attempt {i + 1}/{attempts} — retrying in {wait}s", flush=True)
            time.sleep(wait)
    print(f"[retry] {label}: still failing after {attempts} attempts (code {code})", flush=True)
    return code


def alert(topic: str, title: str, message: str, priority: str = "high") -> None:
    """ntfy phone alert, never raises (ntfy.sh is excluded from WARP)."""
    print(f"[{title}] ALERT: {message}", flush=True)
    try:
        from collectors.spotify.core.notify import send

        send(topic, message, title=title, tags="warning", priority=priority)
    except Exception as exc:
        print(f"[{title}] WARN: alert failed: {exc}", flush=True)


def pid_alive(pid: int) -> bool:
    """Windows-safe liveness check (os.kill(pid, 0) would TERMINATE the
    process on Windows)."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def acquire_lock(lock_path: Path) -> bool:
    """False = a previous run is still alive. A lock left by a dead process
    is taken over."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid = int((lock_path.read_text(encoding="utf-8").split() or ["0"])[0])
            except (OSError, ValueError):
                pid = 0
            if pid_alive(pid):
                return False
            print(f"[run_guard] Stale lock from dead pid {pid} — taking over ({lock_path.name})", flush=True)
            try:
                lock_path.unlink()
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}")
        return True
    return False


def release_lock(lock_path: Path) -> None:
    try:
        if lock_path.read_text(encoding="utf-8").split()[0] == str(os.getpid()):
            lock_path.unlink()
    except (OSError, IndexError):
        pass
