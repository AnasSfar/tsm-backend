#!/usr/bin/env python3
"""Warn through ntfy when R2 bucket storage crosses configured soft limits."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.spotify.core.notify import send as notify

load_dotenv()

GRAPHQL_ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"
GRAPHQL_QUERY = """
query R2StorageLatest(
  $accountTag: string!
  $startDate: Time
  $endDate: Time
  $bucketName: string
) {
  viewer {
    accounts(filter: { accountTag: $accountTag }) {
      r2StorageAdaptiveGroups(
        limit: 1
        filter: {
          datetime_geq: $startDate
          datetime_leq: $endDate
          bucketName: $bucketName
        }
        orderBy: [datetime_DESC]
      ) {
        max {
          objectCount
          payloadSize
          metadataSize
        }
        dimensions {
          datetime
        }
      }
    }
  }
}
""".strip()
SIZE_RE = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTPE]?i?B)?\s*$", re.I)
DECIMAL_UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
BINARY_UNITS = {"KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}


@dataclass(frozen=True)
class BucketLimit:
    name: str
    limit_bytes: int


@dataclass(frozen=True)
class BucketMetric:
    name: str
    timestamp: str
    payload_bytes: int
    metadata_bytes: int
    object_count: int

    @property
    def used_bytes(self) -> int:
        return self.payload_bytes + self.metadata_bytes


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def parse_size(raw: str) -> int:
    match = SIZE_RE.match(raw)
    if not match:
        raise ValueError(f"Invalid size '{raw}'. Use values like 10GB or 750MiB.")

    unit = (match.group("unit") or "B").upper()
    multiplier = DECIMAL_UNITS.get(unit) or BINARY_UNITS.get(unit)
    if multiplier is None:
        raise ValueError(f"Unsupported size unit '{unit}' in '{raw}'.")
    return int(float(match.group("value")) * multiplier)


def format_size(size_bytes: int) -> str:
    if size_bytes < 1000:
        return f"{size_bytes} B"
    for unit, multiplier in (("TB", 1000**4), ("GB", 1000**3), ("MB", 1000**2), ("KB", 1000)):
        if size_bytes >= multiplier:
            return f"{size_bytes / multiplier:.2f} {unit}"
    return f"{size_bytes} B"


def default_bucket_names() -> list[str]:
    names = []
    for env_name in ("R2_BUCKET", "R2_APP_BUCKET"):
        value = os.getenv(env_name, "").strip()
        if value and value not in names:
            names.append(value)
    return names


def parse_bucket_limits(raw: str, default_limit: str) -> list[BucketLimit]:
    if raw.strip():
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        limits = []
        for part in parts:
            if "=" not in part:
                raise ValueError(
                    "R2_STORAGE_BUCKET_LIMITS must use bucket=size entries, "
                    f"for example taylor-data=8GB. Invalid entry: '{part}'."
                )
            name, size = (value.strip() for value in part.split("=", 1))
            if not name:
                raise ValueError(f"Missing bucket name in '{part}'.")
            limits.append(BucketLimit(name=name, limit_bytes=parse_size(size)))
        return limits

    names = default_bucket_names()
    if not names:
        raise RuntimeError(
            "No R2 buckets to monitor. Set R2_STORAGE_BUCKET_LIMITS or "
            "configure R2_BUCKET/R2_APP_BUCKET."
        )
    limit_bytes = parse_size(default_limit)
    return [BucketLimit(name=name, limit_bytes=limit_bytes) for name in names]


def post_graphql(*, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        GRAPHQL_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloudflare GraphQL returned HTTP {exc.code}: {detail}") from exc


def fetch_bucket_metric(*, account_id: str, token: str, bucket_name: str) -> BucketMetric | None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=3)
    payload = {
        "query": GRAPHQL_QUERY,
        "variables": {
            "accountTag": account_id,
            "bucketName": bucket_name,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
        },
    }
    response = post_graphql(token=token, payload=payload)
    errors = response.get("errors") or []
    if errors:
        messages = "; ".join(str(error.get("message") or error) for error in errors)
        raise RuntimeError(f"Cloudflare GraphQL query failed for {bucket_name}: {messages}")

    accounts = (((response.get("data") or {}).get("viewer") or {}).get("accounts") or [])
    groups = accounts[0].get("r2StorageAdaptiveGroups", []) if accounts else []
    if not groups:
        return None

    group = groups[0]
    max_values = group.get("max") or {}
    dimensions = group.get("dimensions") or {}
    return BucketMetric(
        name=bucket_name,
        timestamp=str(dimensions.get("datetime") or "unknown"),
        payload_bytes=int(max_values.get("payloadSize") or 0),
        metadata_bytes=int(max_values.get("metadataSize") or 0),
        object_count=int(max_values.get("objectCount") or 0),
    )


def warning_percent(raw: str) -> float:
    value = float(raw)
    if value <= 0 or value > 100:
        raise ValueError("R2_STORAGE_WARNING_PERCENT must be within 0-100.")
    return value


def alert_title(metric: BucketMetric, percent: float) -> tuple[str, str, str]:
    if percent >= 100:
        return ("R2 storage soft limit reached", "urgent", "rotating_light,cloud")
    return ("R2 storage warning", "high", "warning,cloud")


def alert_message(metric: BucketMetric, bucket_limit: BucketLimit, percent: float) -> str:
    return "\n".join(
        [
            f"Bucket: {metric.name}",
            f"Usage: {format_size(metric.used_bytes)} / {format_size(bucket_limit.limit_bytes)} ({percent:.1f}%)",
            f"Payload: {format_size(metric.payload_bytes)}",
            f"Metadata: {format_size(metric.metadata_bytes)}",
            f"Objects: {metric.object_count:,}",
            f"Metric timestamp: {metric.timestamp}",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print alerts without sending ntfy.")
    parser.add_argument(
        "--bucket-limits",
        default=os.getenv("R2_STORAGE_BUCKET_LIMITS", ""),
        help="Comma-separated bucket=size entries. Defaults to R2_BUCKET and R2_APP_BUCKET.",
    )
    parser.add_argument(
        "--default-bucket-limit",
        default=os.getenv("R2_STORAGE_DEFAULT_BUCKET_LIMIT", "").strip() or "10GB",
        help="Soft limit used when --bucket-limits is omitted.",
    )
    parser.add_argument(
        "--warning-percent",
        default=os.getenv("R2_STORAGE_WARNING_PERCENT", "").strip() or "80",
        help="Notify when usage reaches this percentage of the bucket soft limit.",
    )
    parser.add_argument(
        "--topic",
        default=os.getenv("NTFY_TOPIC_R2_STORAGE", "").strip() or "taylormuseum-r2",
        help="ntfy topic for R2 storage alerts.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    account_id = get_env("R2_ACCOUNT_ID")
    token = get_env("CLOUDFLARE_ANALYTICS_API_TOKEN")
    bucket_limits = parse_bucket_limits(args.bucket_limits, args.default_bucket_limit)
    threshold_percent = warning_percent(args.warning_percent)
    alerts = 0

    print(f"Checking {len(bucket_limits)} R2 bucket(s); warning threshold {threshold_percent:.1f}%.")
    for bucket_limit in bucket_limits:
        metric = fetch_bucket_metric(account_id=account_id, token=token, bucket_name=bucket_limit.name)
        if metric is None:
            print(f"[warn] No Cloudflare storage metric found for {bucket_limit.name}.")
            continue

        percent = (metric.used_bytes / bucket_limit.limit_bytes) * 100 if bucket_limit.limit_bytes else 0
        print(
            f"[ok] {metric.name}: {format_size(metric.used_bytes)} / "
            f"{format_size(bucket_limit.limit_bytes)} ({percent:.1f}%)"
        )
        if percent < threshold_percent:
            continue

        title, priority, tags = alert_title(metric, percent)
        message = alert_message(metric, bucket_limit, percent)
        alerts += 1
        if args.dry_run:
            print(f"[dry-run alert] {title}\n{message}")
            continue
        notify(args.topic, message, title=title, tags=tags, priority=priority)
        print(f"[alert] ntfy sent for {metric.name} to {args.topic}.")

    print(f"Done: {alerts} alert(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
