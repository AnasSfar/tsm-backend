#!/usr/bin/env python3
"""Prune orphaned Apple Music artwork from R2 (images/apple-music/).

Images are content-addressed by md5(source Apple CDN url) (see
download_apple_music_images.py::extract_filename_from_url) and deduplicated
against the CURRENT apple_music_country_charts.csv scan
(download_apple_music_images.py::load_apple_music_images). No historical
JSON object (apple-music/snapshots/*, apple-music/history-by-song/*, the
apple-music/db/*.csv daily mirrors) ever embeds our own R2-hosted image key
-- they only ever store the original Apple mzstatic.com CDN URL in their
`image_url` field (see .claude/skills/scripts-maintenance/SKILL.md, "R2 :
donnees perennes vs cache"). So an images/apple-music/*.jpg object not
referenced by the current CSV scan is a true orphan, safe to delete.

Dry-run by default -- prints what would be removed. Use --apply to delete.

    python scripts/prune_apple_music_images.py                  # report only
    python scripts/prune_apple_music_images.py --apply          # delete
    python scripts/prune_apple_music_images.py --apply --no-archive
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as _date
from pathlib import Path

from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_SCRIPT_DIR))
from download_apple_music_images import get_s3_client, load_apple_music_images  # noqa: E402
import r2_keys  # noqa: E402

load_dotenv(str(_REPO_ROOT / ".env"), override=True)

ARCHIVE_DIR = _REPO_ROOT / "snapshots" / "apple_music_charts" / "_pruned_images_archive"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune orphaned Apple Music artwork objects from R2.")
    parser.add_argument("--apply", action="store_true", help="Delete orphaned objects (default: dry-run report).")
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip writing a local manifest of deleted keys before deleting.",
    )
    parser.add_argument("--bucket", default=os.getenv("R2_BUCKET", "taylor-data").strip() or "taylor-data")
    return parser.parse_args()


def list_r2_images(client, bucket: str) -> dict[str, int]:
    """Return {key: size_bytes} for every object under the Apple Music images prefix."""
    prefix = f"{r2_keys.IMAGES_APPLE_MUSIC_PREFIX}/"
    objects: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            objects[obj["Key"]] = int(obj.get("Size") or 0)
    return objects


def archive_manifest(deleted: dict[str, int]) -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = ARCHIVE_DIR / f"{_date.today().isoformat()}.json"
    manifest_path.write_text(
        json.dumps({"deleted_keys": deleted}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def delete_objects(client, bucket: str, keys: list[str]) -> None:
    # delete_objects accepts at most 1000 keys per call.
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )


def main() -> int:
    args = parse_args()

    referenced_images, _ = load_apple_music_images()
    if not referenced_images:
        print("[prune-images] ABORT: no referenced images found (CSV missing/unreadable) -- "
              "refusing to treat an empty reference set as 'everything is orphaned'.")
        return 1

    referenced_filenames = set(referenced_images.values())
    print(f"[prune-images] {len(referenced_filenames)} filename(s) referenced by the current Apple Music CSV.")

    client = get_s3_client()
    remote_objects = list_r2_images(client, args.bucket)
    print(f"[prune-images] {len(remote_objects)} object(s) currently on R2 under "
          f"{r2_keys.IMAGES_APPLE_MUSIC_PREFIX}/.")

    orphans = {
        key: size
        for key, size in remote_objects.items()
        if Path(key).name not in referenced_filenames
    }

    if not orphans:
        print("[prune-images] nothing to prune -- every remote object is referenced.")
        return 0

    total_bytes = sum(orphans.values())
    for key in sorted(orphans):
        print(f"[{'would prune' if not args.apply else 'pruning'}] {key} ({orphans[key] / 1024:.1f} Ko)")

    print()
    print(f"[prune-images] orphaned objects: {len(orphans)}")
    print(f"[prune-images] space to reclaim: {total_bytes / (1024 * 1024):.2f} Mo")

    if not args.apply:
        print("[prune-images] DRY-RUN -- nothing deleted. Re-run with --apply to prune "
              "(deleted keys are archived to a local manifest unless --no-archive).")
        return 0

    if not args.no_archive:
        manifest_path = archive_manifest(orphans)
        print(f"[prune-images] manifest of deleted keys written to {manifest_path}")

    delete_objects(client, args.bucket, list(orphans.keys()))
    print(f"[prune-images] deleted {len(orphans)} object(s) from bucket '{args.bucket}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
