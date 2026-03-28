#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

EXPORT_SCRIPT = REPO_ROOT / "scripts" / "upload_apple_music_to_r2.py"

SCRIPTS = [
    HERE / "ts_page.py",
    HERE / "global.py",
    HERE / "genre_charts.py",
    HERE / "country_charts.py",
]

def run_script(script_path: Path) -> int:
    if not script_path.exists():
        print(f"[ERROR] Missing script: {script_path}")
        return 1

    print(f"\n{'=' * 80}")
    print(f"Running: {script_path.relative_to(REPO_ROOT)}")
    print(f"{'=' * 80}")

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=REPO_ROOT,
        check=False,
    )

    if result.returncode == 0:
        print(f"[OK] {script_path.name}")
    else:
        print(f"[ERROR] {script_path.name} failed with code {result.returncode}")

    return result.returncode


def main() -> None:
    print("[Apple Music] Starting full run")

    failures: list[tuple[str, int]] = []

    for script in SCRIPTS:
        code = run_script(script)
        if code != 0:
            failures.append((script.name, code))

    print(f"\n{'=' * 80}")
    if failures:
        print("[Apple Music] Finished with errors:")
        for name, code in failures:
            print(f" - {name}: {code}")
        sys.exit(1)
    else:
        print("[Apple Music] All scripts completed successfully")
        print(f"{'=' * 80}")


if __name__ == "__main__":
    main()