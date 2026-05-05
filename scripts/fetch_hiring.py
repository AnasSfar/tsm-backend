#!/usr/bin/env python3
"""
Fetch and display all hiring applications stored in R2 under the 'hiring/' prefix.

Usage:
    python3 scripts/fetch_hiring.py
    python3 scripts/fetch_hiring.py --json        # raw JSON output
    python3 scripts/fetch_hiring.py --role "Frontend Developer"
"""

import argparse
import json
import os
import sys
from datetime import datetime

import boto3
from dotenv import load_dotenv

load_dotenv()

R2_ACCOUNT_ID = os.environ["R2_ACCOUNT_ID"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ.get("R2_BUCKET", "taylor-data")

HIRING_PREFIX = "hiring/"


def get_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def list_applications(client) -> list[str]:
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=R2_BUCKET, Prefix=HIRING_PREFIX):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return sorted(keys)


def fetch_application(client, key: str) -> dict:
    response = client.get_object(Bucket=R2_BUCKET, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def print_application(app: dict, index: int):
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  #{index}  {app.get('name', '?')}  —  {app.get('role', '?')}")
    print(sep)
    print(f"  Email      : {app.get('email', '—')}")
    print(f"  Submitted  : {app.get('submitted_at', '—')}")
    if app.get("portfolio"):
        print(f"  Portfolio  : {app['portfolio']}")
    if app.get("experience"):
        print(f"\n  Experience :\n    {app['experience'].replace(chr(10), chr(10) + '    ')}")
    if app.get("motivation"):
        print(f"\n  Why TSM?   :\n    {app['motivation'].replace(chr(10), chr(10) + '    ')}")


def main():
    parser = argparse.ArgumentParser(description="Fetch TSM hiring applications from R2")
    parser.add_argument("--json", action="store_true", help="Output raw JSON array")
    parser.add_argument("--role", help="Filter by role (case-insensitive substring)")
    args = parser.parse_args()

    client = get_client()
    keys = list_applications(client)

    if not keys:
        print("No applications found.")
        return

    applications = []
    for key in keys:
        try:
            app = fetch_application(client, key)
            app["_key"] = key
            applications.append(app)
        except Exception as e:
            print(f"[warn] Could not read {key}: {e}", file=sys.stderr)

    if args.role:
        needle = args.role.lower()
        applications = [a for a in applications if needle in a.get("role", "").lower()]

    if args.json:
        print(json.dumps(applications, ensure_ascii=False, indent=2))
        return

    print(f"\n{'═' * 60}")
    print(f"  TSM HIRING APPLICATIONS  —  {len(applications)} found")
    print(f"{'═' * 60}")

    for i, app in enumerate(applications, start=1):
        print_application(app, i)

    print(f"\n{'─' * 60}\n  Total: {len(applications)} application(s)\n{'─' * 60}\n")


if __name__ == "__main__":
    main()
