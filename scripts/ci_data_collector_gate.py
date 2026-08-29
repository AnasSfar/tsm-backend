#!/usr/bin/env python3
"""Decide which data-only collectors the CI workflow should run this trigger.

Writes ``apple_music`` / ``youtube`` / ``youtube_commit`` booleans to
``$GITHUB_OUTPUT`` (and echoes them to the log).

Why this exists: GitHub's ``schedule:`` trigger is unreliable -- it is delayed
at the top of every hour and silently drops runs under load (observed
2026-08-29: only 2 of ~13 expected scheduled runs fired, each ~1h late). The
workflow therefore fires a *frequent* cron and this gate keeps every run that
isn't actually needed down to a ~10s no-op:

* Apple Music -> run only when the current Europe/Paris 2h snapshot slot (same
  rounding as ``run_apple_music.build_scraped_at``) is not yet present in
  ``apple-music/db/apple_music_global.csv`` on R2. A missed slot is
  unrecoverable (charts are live state), so 3 cron firings per hour give us
  ~3 chances to land inside every slot even when GitHub drops most triggers.
* YouTube -> run once per America/New_York day, any time from 00:30 local
  onwards. ``collectors/youtube/update_youtube.py`` also self-guards via
  ``date_already_collected``; this gate just avoids spinning up the job.

On ``workflow_dispatch`` the manual inputs win outright.
"""
from __future__ import annotations

import csv
import io
import os
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
# Mirror how the Apple Music collector's own scripts import (PYTHONPATH-style)
# so build_scraped_at() stays the single source of truth for slot rounding.
sys.path.insert(0, str(ROOT / "collectors" / "apple_music"))

YOUTUBE_TZ = os.environ.get("YOUTUBE_COLLECTION_TZ", "America/New_York")
YOUTUBE_CSV = ROOT / "db" / "youtube_views_history.csv"
AM_GLOBAL_KEY = "apple-music/db/apple_music_global.csv"


def emit(**pairs: bool) -> None:
    lines = [f"{key}={'true' if value else 'false'}" for key, value in pairs.items()]
    for line in lines:
        print(f"[gate] -> {line}")
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def apple_music_slot_pending() -> bool:
    import boto3
    from run_apple_music import build_scraped_at

    current_slot = build_scraped_at()  # "YYYY-MM-DDTHH:MM:SS", Europe/Paris, rounded down

    client = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    bucket = os.environ.get("R2_BUCKET", "taylor-data")
    try:
        obj = client.get_object(Bucket=bucket, Key=AM_GLOBAL_KEY)
        body = obj["Body"].read().decode("utf-8-sig")
    except Exception as exc:  # missing file / first run -> collect
        print(f"[gate] Apple Music: could not read {AM_GLOBAL_KEY} ({exc}) -> run")
        return True

    latest = ""
    for row in csv.DictReader(io.StringIO(body)):
        value = (row.get("scraped_at") or row.get("date") or "").strip()
        if value > latest:
            latest = value

    # Both strings are "%Y-%m-%dT%H:%M:%S" -> lexicographic order == chronological.
    pending = latest < current_slot
    print(
        f"[gate] Apple Music: latest scraped_at={latest!r} current slot={current_slot!r} "
        f"-> {'run' if pending else 'skip'}"
    )
    return pending


def youtube_day_pending() -> bool:
    now = datetime.now(ZoneInfo(YOUTUBE_TZ))
    if now.time() < time(0, 30):
        print(f"[gate] YouTube: {now:%H:%M} {YOUTUBE_TZ} is before 00:30 -> skip")
        return False

    today = now.date().isoformat()
    if YOUTUBE_CSV.exists():
        with YOUTUBE_CSV.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("date") == today:
                    print(f"[gate] YouTube: {today} already in {YOUTUBE_CSV.name} -> skip")
                    return False
    print(f"[gate] YouTube: {today} not collected yet -> run")
    return True


def main() -> int:
    event = os.environ.get("GATE_EVENT", "")

    if event == "workflow_dispatch":
        collector = (os.environ.get("GATE_COLLECTOR") or "apple-music").strip()
        commit_youtube = (os.environ.get("GATE_COMMIT_YOUTUBE") or "true").strip().lower() == "true"
        print(f"[gate] workflow_dispatch: collector={collector!r} commit_youtube={commit_youtube}")
        emit(
            apple_music=collector in ("apple-music", "both"),
            youtube=collector in ("youtube", "both"),
            youtube_commit=commit_youtube,
        )
        return 0

    # schedule (or any other trigger): self-healing freshness gates.
    emit(
        apple_music=apple_music_slot_pending(),
        youtube=youtube_day_pending(),
        youtube_commit=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
