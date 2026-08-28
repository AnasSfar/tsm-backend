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
    HERE / "ts_page_all.py",
    HERE / "country_all.py",
    HERE / "genre_all.py",
]

def child_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_parts = [str(REPO_ROOT), str(HERE)]
    existing = env.get("PYTHONPATH")
    if existing:
        pythonpath_parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    return env


def export_apple_music() -> int:
    """Generate JSON files from CSV data."""
    export_script = REPO_ROOT / "scripts" / "export_apple_music.py"
    if not export_script.exists():
        print(f"[Apple Music] Export script missing: {export_script}")
        return 1

    print("[Apple Music] Exporting CSV to JSON...")
    result = subprocess.run([sys.executable, str(export_script)], cwd=REPO_ROOT, env=child_env(), check=False)
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
    subprocess.run([sys.executable, str(upload_script)], cwd=REPO_ROOT, env=child_env(), check=False)


def regenerate_home_highlights_cache() -> None:
    """Best-effort refresh of the Charts Gallery highlights/version R2 cache.

    Never blocks the run: on failure, tsm-frontend/api's own cache-on-miss
    fallback recomputes it live instead.
    """
    script = REPO_ROOT / "scripts" / "generate_home_highlights.py"
    if not script.exists():
        print(f"[Apple Music] Home highlights cache script missing: {script}")
        return

    print("[Apple Music] Refreshing home highlights/version R2 cache...")
    result = subprocess.run(
        [sys.executable, str(script), "--quiet"], cwd=REPO_ROOT, env=child_env(), check=False
    )
    if result.returncode != 0:
        print(f"[Apple Music] Home highlights cache refresh exited with code {result.returncode}")


def generate_country_cards(scraped_at: str, force: bool = False) -> int:
    script = HERE / "generate_country_card_images.py"
    if not script.exists():
        print(f"[Apple Music] Card image script missing: {script}")
        return 1

    chart_date = scraped_at.split("T", 1)[0]
    args = [sys.executable, str(script), chart_date, "--min-countries", "1"]
    if force:
        args.append("--force")

    print("[Apple Music] Generating country chart card images...")
    result = subprocess.run(args, cwd=REPO_ROOT, env=child_env(), check=False)
    return result.returncode


def generate_snapshot_images(scraped_at: str) -> int:
    script = HERE / "generate_snapshot_images.py"
    if not script.exists():
        print(f"[Apple Music] Snapshot image script missing: {script}")
        return 1

    chart_date = scraped_at.split("T", 1)[0]
    args = [sys.executable, str(script), "--date", chart_date]

    print("[Apple Music] Generating snapshot images (Global, US, US Pop, US Country, US Alternative)...")
    result = subprocess.run(args, cwd=REPO_ROOT, env=child_env(), check=False)
    return result.returncode


def notify_global_update(scraped_at: str) -> int:
    script = HERE / "generate_snapshot_images.py"
    if not script.exists():
        print(f"[Apple Music] Snapshot image script missing: {script}")
        return 1

    chart_date = scraped_at.split("T", 1)[0]
    args = [sys.executable, str(script), "--date", chart_date, "--notify-global-only"]

    print("[Apple Music] Sending Global Apple Music notification after data export...")
    result = subprocess.run(args, cwd=REPO_ROOT, env=child_env(), check=False)
    return result.returncode


def run_script(script_path: Path, scraped_at: str, extra_args: list[str] | None = None) -> int:
    if not script_path.exists():
        print(f"[ERROR] Missing script: {script_path}")
        return 1

    print(f"\n{'=' * 80}")
    print(f"Running: {script_path.relative_to(REPO_ROOT)}")
    print(f"{'=' * 80}")

    cmd = [sys.executable, str(script_path), "--scraped-at", scraped_at]
    if extra_args:
        cmd.extend(extra_args)

    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=child_env(),
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
    parser.add_argument("--no-images", action="store_true", help="Skip Apple Music country chart card images.")
    parser.add_argument("--force-images", action="store_true", help="Regenerate Apple Music country chart card images.")
    args, _unknown = parser.parse_known_args()

    scraped_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[Apple Music] Starting full run - scraped_at={scraped_at}")

    os.environ["APPLE_MUSIC_SKIP_EXPORT"] = "1"

    failures: list[tuple[str, int]] = []

    for script in SCRIPTS:
        extra_args = ["--force"] if script.name == "ts_page_all.py" else None
        code = run_script(script, scraped_at, extra_args=extra_args)
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

        if not args.no_post:
            notify_code = notify_global_update(scraped_at)
            if notify_code != 0:
                print("[Apple Music] Global notification failed, skipping R2 upload")
                sys.exit(1)
        else:
            print("[Apple Music] Global notification skipped (--no-post)")

        if not args.no_images:
            cards_code = generate_country_cards(scraped_at, force=args.force_images)
            if cards_code != 0:
                print("[Apple Music] Card image generation failed, skipping R2 upload")
                sys.exit(1)

            snapshot_code = generate_snapshot_images(scraped_at)
            if snapshot_code != 0:
                print("[Apple Music] Snapshot image generation failed, skipping R2 upload")
                sys.exit(1)

        maybe_upload_to_r2()
        regenerate_home_highlights_cache()
        print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
