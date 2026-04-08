#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
from pathlib import Path

import boto3
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
DB_HISTORY = REPO_ROOT / "db" / "swift_top_100_history.csv"
SITE_SNAPSHOT = REPO_ROOT / "website" / "site" / "data" / "swift_top_100.json"
SITE_SONGS_DIR = REPO_ROOT / "website" / "site" / "data" / "swift_top_100_songs"
R2_REQUIRED_ENV_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")

load_dotenv(str(REPO_ROOT / ".env"), override=True)


def _collect_targets() -> list[Path]:
    targets: list[Path] = [DB_HISTORY, SITE_SNAPSHOT]
    if SITE_SONGS_DIR.exists():
        targets.extend(sorted(SITE_SONGS_DIR.glob("*.json")))
    return targets


def _delete_target(path: Path, *, dry_run: bool) -> bool:
    if not path.exists():
        return False

    if dry_run:
        return True

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def _get_missing_env_vars(names: tuple[str, ...]) -> list[str]:
    return [name for name in names if not os.getenv(name, "").strip()]


def _get_s3_client():
    account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
    access_key_id = os.getenv("R2_ACCESS_KEY_ID", "").strip()
    secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
    if not account_id or not access_key_id or not secret_access_key:
        raise RuntimeError("Missing R2 credentials")

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )


def _delete_r2_keys(*, bucket: str, keys: list[str], dry_run: bool) -> int:
    existing_keys = [key for key in keys if key]
    if not existing_keys:
        return 0

    if dry_run:
        return len(existing_keys)

    client = _get_s3_client()

    def _delete_one(key: str) -> bool:
        try:
            client.delete_object(Bucket=bucket, Key=key)
            return True
        except Exception as exc:
            print(f"[WARN] Failed to delete R2 object {key}: {exc}")
            return False

    deleted = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_delete_one, key) for key in existing_keys]
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                deleted += 1
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Delete every Swift Top 100 history artifact so the next export starts from a blank state."
        )
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete the files. Without this flag the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--remove-bonuses",
        action="store_true",
        help="Also delete db/swift_top_100_bonuses.json.",
    )
    parser.add_argument(
        "--skip-r2",
        action="store_true",
        help="Do not delete R2 Swift Top 100 objects.",
    )

    args = parser.parse_args()
    dry_run = not args.yes

    targets = _collect_targets()
    if args.remove_bonuses:
        targets.append(REPO_ROOT / "db" / "swift_top_100_bonuses.json")

    existing_targets = [path for path in targets if path.exists()]
    existing_song_files = [path for path in existing_targets if path.parent == SITE_SONGS_DIR and path.suffix == ".json"]

    if not existing_targets:
        print("No Swift Top 100 history files found.")
    else:
        mode = "DRY RUN" if dry_run else "DELETE"
        print(f"[{mode}] Swift Top 100 targets:")
        for path in existing_targets:
            print(f" - {path}")

    if dry_run:
        if not args.skip_r2:
            missing_vars = _get_missing_env_vars(R2_REQUIRED_ENV_VARS)
            if missing_vars:
                print(f"[DRY RUN] R2 purge unavailable: missing {', '.join(missing_vars)}")
            else:
                bucket = os.getenv("R2_BUCKET", "").strip()
                r2_keys = ["data/swift_top_100.json", "data/swift_top_100.png"]
                r2_keys.extend(f"history-by-track/{path.stem}.json" for path in existing_song_files)
                print(f"[DRY RUN] Would delete {len(r2_keys)} R2 object(s) from bucket {bucket}:")
                for key in r2_keys:
                    print(f" - {key}")
        print("\nRe-run with --yes to delete these files.")
        return 0

    deleted = 0
    for path in existing_targets:
        if _delete_target(path, dry_run=False):
            deleted += 1

    if SITE_SONGS_DIR.exists() and not any(SITE_SONGS_DIR.iterdir()):
        SITE_SONGS_DIR.rmdir()

    r2_deleted = 0
    if not args.skip_r2:
        missing_vars = _get_missing_env_vars(R2_REQUIRED_ENV_VARS)
        if missing_vars:
            print(f"[WARN] Skipping R2 purge; missing env vars: {', '.join(missing_vars)}")
        else:
            bucket = os.getenv("R2_BUCKET", "").strip()
            r2_keys = ["data/swift_top_100.json", "data/swift_top_100.png"]
            r2_keys.extend(f"history-by-track/{path.stem}.json" for path in existing_song_files)
            r2_deleted = _delete_r2_keys(bucket=bucket, keys=r2_keys, dry_run=False)

    print(f"\nDeleted {deleted} file(s).")
    if not args.skip_r2:
        print(f"Deleted {r2_deleted} R2 object(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())