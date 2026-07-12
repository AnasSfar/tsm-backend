from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

from collectors.spotify.core.data_paths import SNAPSHOTS_ROOT, date_key


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_name(value: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", value.strip()).strip("._")
    return cleaned or "run"


def terminal_log_path(command: str, value: str | None = None) -> Path:
    key = date_key(value or datetime.now().date())
    stamp = datetime.now().strftime("%H%M%S")
    name = _safe_name(command)
    return SNAPSHOTS_ROOT / "run_logs" / key[:4] / key[5:7] / key / f"{stamp}_{name}.txt"


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


class TerminalLog:
    def __init__(self, path: Path):
        self.path = path
        self._fh = None
        self._stdout = None
        self._stderr = None

    def __enter__(self) -> "TerminalLog":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("ab")
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = TeeTextIO(sys.stdout, self._fh)
        sys.stderr = TeeTextIO(sys.stderr, self._fh)
        print(f"[LOG] Terminal output: {self.path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._stdout is not None:
            sys.stdout = self._stdout
        if self._stderr is not None:
            sys.stderr = self._stderr
        if self._fh is not None:
            self._fh.close()
