from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR   = Path(__file__).resolve().parent
_REPO_ROOT    = _SCRIPT_DIR.parents[2]
ROOT          = _REPO_ROOT / "website"

EXPORT_SCRIPT    = _SCRIPT_DIR / "export_for_web.py"
FORECAST_SCRIPT  = _SCRIPT_DIR / "forecast_milestones.py"
FILL_IMG_SCRIPT  = _REPO_ROOT / "scripts" / "fill_images.py"
BILLBOARD_SCRIPT = _REPO_ROOT / "collectors" / "billboard" / "scrape_billboard.py"


def run(script: Path, label: str, cwd: Path = _REPO_ROOT) -> None:
    print(f"\n-- {label} --")
    result = subprocess.run([sys.executable, str(script)], cwd=str(cwd))
    if result.returncode != 0:
        raise SystemExit(f"{label} failed (exit {result.returncode}).")


def main() -> None:
    print("=" * 50)
    print("  Full site rebuild")
    print("=" * 50)

    run(EXPORT_SCRIPT,    "Export songs.json / albums.json",  cwd=ROOT)
    run(FORECAST_SCRIPT,  "Forecast milestones")
    run(FILL_IMG_SCRIPT,  "Fill image URLs + track_covers.json")
    run(BILLBOARD_SCRIPT, "Scrape Billboard charts")

    print("\nDone - all files up to date.")


if __name__ == "__main__":
    main()