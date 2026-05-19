#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

SCRIPTS = [
    HERE / "global.py",
    HERE / "ts_page.py",
    HERE / "country_charts.py",
    HERE / "country_albums.py",
    HERE / "genre_charts.py",
    HERE / "genre_album_charts.py",
    HERE / "music_video_charts.py",
]


def export_apple_music() -> int:
    """Generate JSON files from CSV data."""
    export_script = REPO_ROOT / "scripts" / "export_apple_music.py"
    if not export_script.exists():
        print(f"[Apple Music] Export script missing: {export_script}")
        return 1

    print("[Apple Music] Exporting CSV to JSON...")
    result = subprocess.run([sys.executable, str(export_script)], cwd=REPO_ROOT, check=False)
    return result.returncode


def maybe_upload_to_r2() -> None:
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        print("[Apple Music] R2 upload skipped (UPLOAD_TO_R2 explicitly disabled)")
        return

    upload_script = REPO_ROOT / "scripts" / "upload_ap_r2.py"
    if not upload_script.exists():
        print(f"[Apple Music] R2 upload script missing: {upload_script}")
        return

    print("[Apple Music] Uploading history-by-song to R2...")
    subprocess.run([sys.executable, str(upload_script)], cwd=REPO_ROOT, check=False)

def run_script(script_path: Path, scraped_at: str) -> int:
    if not script_path.exists():
        print(f"[ERROR] Missing script: {script_path}")
        return 1

    print(f"\n{'=' * 80}")
    print(f"Running: {script_path.relative_to(REPO_ROOT)}")
    print(f"{'=' * 80}")

    result = subprocess.run(
        [sys.executable, str(script_path), "--scraped-at", scraped_at],
        cwd=REPO_ROOT,
        check=False,
    )

    if result.returncode == 0:
        print(f"[OK] {script_path.name}")
    else:
        print(f"[ERROR] {script_path.name} failed with code {result.returncode}")

    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-post", action="store_true")
    parser.parse_known_args()

    scraped_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[Apple Music] Starting full run - scraped_at={scraped_at}")

    os.environ["APPLE_MUSIC_SKIP_EXPORT"] = "1"

    failures: list[tuple[str, int]] = []

    for script in SCRIPTS:
        code = run_script(script, scraped_at)
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
        
        # Export CSV to JSON for API/website
        export_code = export_apple_music()
        if export_code != 0:
            print("[Apple Music] Export failed, skipping R2 upload")
            sys.exit(1)
        
        maybe_upload_to_r2()
        print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
