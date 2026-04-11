#!/usr/bin/env python3
"""
Fetch issue reports from R2 and display or save them locally.

Usage:
  python scripts/fetch_issues.py              # Print all reports to stdout
  python scripts/fetch_issues.py --save       # Save JSON files to ./issues/
  python scripts/fetch_issues.py --save --images  # Also download image attachments
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
from collectors.apple_music.core.r2 import get_r2_client, get_bucket_name


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch issue reports from R2.")
    parser.add_argument("--save", action="store_true", help="Save reports to ./issues/")
    parser.add_argument(
        "--images",
        action="store_true",
        help="Also download image attachments (requires --save)",
    )
    args = parser.parse_args()

    client = get_r2_client()
    bucket = get_bucket_name()

    paginator = client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix="report-")
    keys = [
        obj["Key"]
        for page in pages
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".json")
    ]

    print(f"Found {len(keys)} report(s).\n")

    if not keys:
        return

    out_dir: Path | None = None
    if args.save:
        out_dir = Path("issues")
        out_dir.mkdir(exist_ok=True)

    for key in sorted(keys):
        response = client.get_object(Bucket=bucket, Key=key)
        data: dict = json.loads(response["Body"].read())

        print(f"--- {key} ---")
        print(f"  Category:    {data.get('category')}")
        print(f"  Priority:    {data.get('priority')}")
        print(f"  Description: {str(data.get('description', ''))[:120]}")
        print(f"  Page:        {data.get('pageUrl')}")
        print(f"  Twitter:     {data.get('twitter') or '(none)'}")
        print(f"  Timestamp:   {data.get('timestamp')}")
        print(f"  Browser:     {str(data.get('browser', ''))[:80]}")
        if data.get("imageUrl"):
            print(f"  Image:       {data.get('imageUrl')}")
        print()

        if out_dir is not None:
            local_path = out_dir / key
            local_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            if args.images and data.get("imageUrl"):
                image_url: str = data["imageUrl"]
                # Extract the object key from the URL: everything after /{bucket}/
                img_key = image_url.split(f"/{bucket}/", 1)[-1]
                try:
                    img_resp = client.get_object(Bucket=bucket, Key=img_key)
                    img_path = out_dir / img_key
                    img_path.write_bytes(img_resp["Body"].read())
                    print(f"  [saved image: {img_path}]")
                except Exception as e:
                    print(f"  [image download failed: {e}]")


if __name__ == "__main__":
    main()
