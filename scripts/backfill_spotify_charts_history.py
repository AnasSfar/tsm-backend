#!/usr/bin/env python3
"""Backfill Spotify Charts snapshots safely, with a resumable done-state file.

This wrapper runs the worldwide collector one date at a time with --no-post.
After each successful date, it verifies that the worldwide snapshot exists and
records the date in a JSON state file. Re-running the command skips completed
dates unless --refetch-done is passed.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORLDWIDE_DAILY = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "daily.py"
SYNC_COUNTRY_CSVS = ROOT / "scripts" / "sync_spotify_country_charts_from_worldwide.py"
BACKFILL_TRACK_IDS = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "backfill_charts_history_track_ids.py"
BACKFILL_TOTAL_DAYS = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "backfill_total_days.py"
ENRICH_WORLDWIDE_SNAPSHOTS = ROOT / "scripts" / "enrich_spotify_worldwide_snapshots.py"
DEFAULT_STATE = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "run_all_backfill_done.json"
DEFAULT_SESSION_DIR = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json"


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _date_range(start: date, end: date) -> list[str]:
    out: list[str] = []
    cur = start
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _snapshot_path(chart_date: str) -> Path:
    return (
        ROOT
        / "snapshots"
        / "spotify_charts"
        / chart_date[:4]
        / chart_date[5:7]
        / chart_date
        / "worldwide"
        / f"ts_worldwide_{chart_date}.json"
    )


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"done_dates": [], "failed_dates": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"done_dates": [], "failed_dates": {}}
    if not isinstance(payload, dict):
        return {"done_dates": [], "failed_dates": {}}
    payload.setdefault("done_dates", [])
    payload.setdefault("failed_dates", {})
    return payload


def _save_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["done_dates"] = sorted(set(payload.get("done_dates") or []))
    payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _run(cmd: list[str], *, dry_run: bool, env: dict[str, str] | None = None) -> int:
    print("[RUN] " + " ".join(cmd), flush=True)
    if dry_run:
        return 0
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


def _mark_existing_snapshots(state: dict, dates: list[str]) -> int:
    done = set(state.get("done_dates") or [])
    added = 0
    for chart_date in dates:
        if chart_date not in done and _snapshot_path(chart_date).exists():
            done.add(chart_date)
            added += 1
    state["done_dates"] = sorted(done)
    return added


def _session_files() -> list[Path]:
    return sorted(DEFAULT_SESSION_DIR.glob("spotify_session*.json"))


def _run_date(chart_date: str, *, session_file: Path, force: bool, dry_run: bool) -> tuple[str, int, float, bool, str]:
    cmd = [sys.executable, str(WORLDWIDE_DAILY), chart_date, "--no-post", "--backfill-mode"]
    if force:
        cmd.append("--force")
    env = os.environ.copy()
    env["SPOTIFY_CHARTS_SESSION_FILE"] = str(session_file)
    env["SPOTIFY_CHARTS_SINGLE_SESSION"] = "1"
    env["SPOTIFY_CHARTS_BEARER_CACHE_FILE"] = str(session_file.with_name(f"bearer_cache_{session_file.stem}.json"))
    env["SPOTIFY_SKIP_LATEST_FALLBACK_ON_404"] = "1"
    started = time.perf_counter()
    rc = _run(cmd, dry_run=dry_run, env=env)
    elapsed = time.perf_counter() - started
    snapshot_exists = _snapshot_path(chart_date).exists()
    return chart_date, rc, elapsed, snapshot_exists, session_file.name


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumable no-post Spotify Charts backfill.")
    parser.add_argument("--start", default="2017-01-01", help="Start date inclusive (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date inclusive (YYYY-MM-DD), default: yesterday")
    parser.add_argument("--state", default=str(DEFAULT_STATE), help="JSON state file storing completed dates")
    parser.add_argument("--force", action="store_true", default=True, help="Pass --force to the collector for pending dates")
    parser.add_argument("--no-force", action="store_false", dest="force", help="Do not pass --force to the collector")
    parser.add_argument("--refetch-done", action="store_true", help="Re-fetch dates even if they are marked done")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of dates to fetch this run")
    parser.add_argument("--workers", type=int, default=2, help="Number of dates to fetch in parallel")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between dates")
    parser.add_argument("--skip-existing-snapshot", action="store_true", default=True)
    parser.add_argument("--no-sync", action="store_true", help="Do not sync charts_history CSVs after collection")
    args = parser.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else (date.today() - timedelta(days=1))
    if end < start:
        raise SystemExit("--end must be >= --start")

    state_path = Path(args.state)
    state = _load_state(state_path)
    all_dates = _date_range(start, end)
    if args.skip_existing_snapshot and not args.refetch_done:
        added = _mark_existing_snapshots(state, all_dates)
        if added:
            print(f"[STATE] {added} existing snapshot date(s) marked done")
            if not args.dry_run:
                _save_state(state_path, state)

    done = set(state.get("done_dates") or [])
    pending = [d for d in all_dates if args.refetch_done or d not in done]
    if args.limit and args.limit > 0:
        pending = pending[: args.limit]

    sessions = _session_files()
    if not sessions:
        raise SystemExit(f"No Spotify session files found in {DEFAULT_SESSION_DIR}")
    workers = max(1, int(args.workers or 1))
    workers = min(workers, len(sessions), len(pending) or 1)

    print(
        f"[PLAN] range={all_dates[0]} -> {all_dates[-1]} total={len(all_dates)} "
        f"pending={len(pending)} workers={workers} sessions={', '.join(p.name for p in sessions[:workers])}"
    )
    failures: dict[str, str] = dict(state.get("failed_dates") or {})

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_date = {}
        for idx, chart_date in enumerate(pending, 1):
            session_file = sessions[(idx - 1) % workers]
            print(f"[QUEUE] {idx}/{len(pending)} {chart_date} via {session_file.name}", flush=True)
            future = executor.submit(
                _run_date,
                chart_date,
                session_file=session_file,
                force=bool(args.force),
                dry_run=bool(args.dry_run),
            )
            future_to_date[future] = chart_date
            if args.sleep > 0:
                time.sleep(args.sleep)

        completed = 0
        for future in concurrent.futures.as_completed(future_to_date):
            completed += 1
            chart_date = future_to_date[future]
            try:
                chart_date, rc, elapsed, snapshot_exists, session_name = future.result()
            except Exception as exc:
                failures[chart_date] = f"exception={exc}"
                print(f"[FAIL] {chart_date}: {failures[chart_date]}", flush=True)
            else:
                if rc == 0 and (args.dry_run or snapshot_exists):
                    done.add(chart_date)
                    failures.pop(chart_date, None)
                    print(f"[ OK ] {completed}/{len(pending)} {chart_date} via {session_name} in {elapsed:.1f}s", flush=True)
                else:
                    failures[chart_date] = f"rc={rc}; snapshot_exists={snapshot_exists}; session={session_name}"
                    print(f"[FAIL] {chart_date}: {failures[chart_date]}", flush=True)

            state["done_dates"] = sorted(done)
            state["failed_dates"] = failures
            if not args.dry_run:
                _save_state(state_path, state)

    if not args.no_sync:
        rc = _run([sys.executable, str(SYNC_COUNTRY_CSVS)], dry_run=args.dry_run)
        if rc != 0:
            return rc
        rc = _run([sys.executable, str(BACKFILL_TRACK_IDS), "--rebuild-ts-history"], dry_run=args.dry_run)
        if rc != 0:
            return rc
        rc = _run([sys.executable, str(ENRICH_WORLDWIDE_SNAPSHOTS), "--start", all_dates[0], "--end", all_dates[-1]], dry_run=args.dry_run)
        if rc != 0:
            return rc
        rc = _run([sys.executable, str(BACKFILL_TOTAL_DAYS)], dry_run=args.dry_run)
        if rc != 0:
            return rc

    print(f"[DONE] done={len(done)} failed={len(failures)} state={state_path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
