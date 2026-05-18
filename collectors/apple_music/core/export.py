from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def maybe_run_export(script_path: Path) -> None:
    if os.getenv("APPLE_MUSIC_SKIP_EXPORT", "").strip().lower() in ("1", "true", "yes"):
        return
    if script_path.exists():
        subprocess.run([sys.executable, str(script_path)], check=False)
