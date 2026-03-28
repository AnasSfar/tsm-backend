from __future__ import annotations

import subprocess
import sys
from pathlib import Path



def maybe_run_export(script_path: Path) -> None:
    if script_path.exists():
        subprocess.run([sys.executable, str(script_path)], check=False)
