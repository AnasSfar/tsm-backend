"""Shared best-effort trigger for the TayBoard live projection.

Product decision (2026-09-23): swift_top_100_live.py no longer runs on its
own fixed daily schedule. Instead each of the three collectors that feed its
scoring (Apple Music, Spotify streams, YouTube) calls trigger_live_projection()
at the end of its own successful run, so the live projection always reflects
whatever fresh data that collector just produced plus whatever the other two
already have. See .claude/skills/collector-billboard/CONTEXTE.md "Live
projection" section.

This module intentionally has zero project imports so it can be dropped into
any collector's success path without pulling in unrelated dependencies.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[1]
_LIVE_SCRIPTS = (
    _SCRIPT_DIR / "swift_top_100_live.py",
    _SCRIPT_DIR / "swift_top_albums_live.py",
)


def _run_live_script(script: Path, *, log) -> None:
    """Best-effort, non-blocking: run one live-projection script.

    Runs it (real run: no --dry-run, no --skip-r2) as a subprocess so it
    writes fresh live history/export files and uploads them to R2. Never
    raises — a failure here must never affect the calling collector's own
    success/exit code.
    """
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(_REPO_ROOT),
            check=False,
            capture_output=True,
            text=True,
            # Callers run on an hourly schedule; a hang here must not stall them.
            timeout=600,
        )
        if result.returncode == 0:
            log(f"live projection : {script.name} OK")
        else:
            stderr_tail = "\n".join((result.stderr or "").strip().splitlines()[-10:])
            log(
                f"⚠ live projection : {script.name} exited with code "
                f"{result.returncode} — {stderr_tail}"
            )
    except Exception as exc:
        log(f"⚠ live projection : failed to run {script.name} — {exc}")


def trigger_live_projection(*, log=print) -> None:
    """Best-effort, non-blocking: regenerate + upload every live projection.

    Runs swift_top_100_live.py (songs) and swift_top_albums_live.py
    (albums/eras, added 2026-09-23 — it internally reuses swift_top_100_live's
    not-combined projection machinery, see that script's own docstring) as
    two independent subprocess calls, each with its own best-effort
    try/except so a failure in one never blocks the other or the caller.
    """
    for script in _LIVE_SCRIPTS:
        _run_live_script(script, log=log)
