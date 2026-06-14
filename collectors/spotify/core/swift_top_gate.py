from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from core.data_paths import tayboard_dir

READY_LOCK_NAME = "swift_you.lock"
DONE_LOCK_NAME = "swift_top_done.lock"


def _lock_dir(chart_date: date) -> Path:
    return tayboard_dir(chart_date)


def _read_ready_source(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return ""
    return str(payload.get("source") or "").strip()


def check_swift_top_gate(chart_date: date, *, source: str) -> str:
    """Return not_wednesday, done, waiting, or ready for the Swift Top rendezvous."""
    if chart_date.weekday() != 2:
        return "not_wednesday"

    source = source.strip().casefold()
    lock_dir = _lock_dir(chart_date)
    done_path = lock_dir / DONE_LOCK_NAME
    ready_path = lock_dir / READY_LOCK_NAME

    if done_path.exists():
        return "done"

    if not ready_path.exists():
        lock_dir.mkdir(parents=True, exist_ok=True)
        ready_path.write_text(
            json.dumps(
                {
                    "source": source,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "chart_date": chart_date.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return "waiting"

    first_source = _read_ready_source(ready_path)
    if first_source == source:
        return "waiting"
    return "ready"


def mark_swift_top_done(chart_date: date, *, source: str) -> None:
    lock_dir = _lock_dir(chart_date)
    lock_dir.mkdir(parents=True, exist_ok=True)
    done_path = lock_dir / DONE_LOCK_NAME
    done_path.write_text(
        json.dumps(
            {
                "source": source.strip().casefold(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "chart_date": chart_date.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    ready_path = lock_dir / READY_LOCK_NAME
    if ready_path.exists():
        ready_path.unlink()
