from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import requests


DEFAULT_CACHE = Path("collectors/spotify/charts/global/tools/json/bearer_cache.json")
DEFAULT_URL = (
    "https://charts-spotify-com-service.spotify.com/auth/v0/charts/"
    "regional-global-daily/2026-07-10"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe how long a cached Spotify charts Bearer stays valid."
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--interval-min", type=float, default=5.0)
    parser.add_argument("--max-min", type=float, default=150.0)
    parser.add_argument("--out", type=Path, default=Path("logs/spotify_token_lifetime_probe.csv"))
    args = parser.parse_args()

    data = json.loads(args.cache.read_text(encoding="utf-8-sig"))
    token = data.get("token")
    saved_at = float(data.get("ts") or time.time())
    if not token:
        raise SystemExit(f"No token found in {args.cache}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    is_new = not args.out.exists()

    session = requests.Session()
    session.trust_env = False

    deadline = time.time() + args.max_min * 60
    with args.out.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["checked_at", "age_min", "status_code", "elapsed_ms", "body_prefix"],
        )
        if is_new:
            writer.writeheader()

        while True:
            started = time.time()
            status_code = ""
            body_prefix = ""
            try:
                resp = session.get(
                    args.url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "Mozilla/5.0",
                    },
                    timeout=20,
                )
                status_code = str(resp.status_code)
                body_prefix = resp.text[:120].replace("\n", " ")
            except Exception as exc:
                status_code = "ERR"
                body_prefix = repr(exc)[:120]

            now = time.time()
            writer.writerow(
                {
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                    "age_min": f"{(now - saved_at) / 60:.2f}",
                    "status_code": status_code,
                    "elapsed_ms": f"{(now - started) * 1000:.0f}",
                    "body_prefix": body_prefix,
                }
            )
            fh.flush()

            if status_code == "401" or now >= deadline:
                break
            time.sleep(args.interval_min * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
