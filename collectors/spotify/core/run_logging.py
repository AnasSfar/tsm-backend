from __future__ import annotations

import re
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import TextIO

from .data_paths import collector_data_dir, date_key


_SAFE_CODE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_code(value: str) -> str:
    cleaned = _SAFE_CODE_RE.sub("_", value.strip()).strip("._")
    return cleaned or "collector"


def _next_attempt(logs_dir: Path, stats_date: str, collector_code: str) -> int:
    prefix = f"{stats_date}_{collector_code}_"
    highest = 0
    if logs_dir.exists():
        for path in logs_dir.glob(f"{prefix}*_attempt*.txt"):
            match = re.search(r"_attempt(\d+)\.txt$", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def collector_run_log_path(
    collector_name: str,
    collector_code: str,
    value: date | datetime | str,
) -> tuple[Path, str]:
    stats_date = date_key(value)
    code = _safe_code(collector_code)
    logs_dir = collector_data_dir(collector_name, stats_date) / "logs"
    attempt = _next_attempt(logs_dir, stats_date, code)
    stamp = datetime.now().strftime("%H%M%S")
    run_id = f"{stats_date}_{code}_{stamp}_attempt{attempt:02d}"
    return logs_dir / f"{run_id}.txt", run_id


class TeeTextIO:
    def __init__(self, stream: TextIO, log_file):
        self._stream = stream
        self._log_file = log_file
        self.encoding = getattr(stream, "encoding", None)
        self.errors = getattr(stream, "errors", None)
        self.buffer = TeeBuffer(getattr(stream, "buffer", None), log_file)

    def write(self, text: str) -> int:
        written = self._stream.write(text)
        self._stream.flush()
        data = text.encode(self.encoding or "utf-8", errors="replace")
        self._log_file.write(data)
        self._log_file.flush()
        return written

    def flush(self) -> None:
        self._stream.flush()
        self._log_file.flush()

    def isatty(self) -> bool:
        return self._stream.isatty()

    def fileno(self) -> int:
        return self._stream.fileno()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


class TeeBuffer:
    def __init__(self, buffer, log_file):
        self._buffer = buffer
        self._log_file = log_file

    def write(self, data: bytes) -> int:
        if self._buffer is not None:
            written = self._buffer.write(data)
            self._buffer.flush()
        else:
            text = data.decode("utf-8", errors="replace")
            written = len(data)
            sys.__stdout__.write(text)
            sys.__stdout__.flush()
        self._log_file.write(data)
        self._log_file.flush()
        return written

    def flush(self) -> None:
        if self._buffer is not None:
            self._buffer.flush()
        self._log_file.flush()

    def __getattr__(self, name: str):
        return getattr(self._buffer, name)


class CollectorRunLog:
    def __init__(self, collector_name: str, collector_code: str, value: date | datetime | str):
        self.path, self.run_id = collector_run_log_path(collector_name, collector_code, value)
        self._fh = None
        self._stdout = None
        self._stderr = None

    def __enter__(self) -> "CollectorRunLog":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("ab")
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = TeeTextIO(sys.stdout, self._fh)
        sys.stderr = TeeTextIO(sys.stderr, self._fh)
        (self.path.parent / ".latest.txt").write_text(str(self.path), encoding="utf-8")
        print(f"[LOG] Collector run id: {self.run_id}")
        print(f"[LOG] Collector output: {self.path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None and self._fh is not None:
            if exc_type is KeyboardInterrupt:
                self._fh.write("\n[LOG] Interrupted by user before log close.\n".encode("utf-8"))
            elif exc_type is not SystemExit:
                self._fh.write("\n[LOG] Unhandled exception before log close:\n".encode("utf-8"))
                for line in traceback.format_exception(exc_type, exc, tb):
                    self._fh.write(line.encode("utf-8", errors="replace"))
            self._fh.flush()
        if self._stdout is not None:
            sys.stdout = self._stdout
        if self._stderr is not None:
            sys.stderr = self._stderr
        if self._fh is not None:
            self._fh.close()
