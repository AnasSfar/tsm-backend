#!/usr/bin/env python3
"""Backfill Spotify Charts snapshots safely, with a resumable done-state file.

This wrapper splits the pending date range into `--workers` contiguous chunks
and runs the worldwide collector once per chunk (via --dates-file), with
--no-post. Each worker is a single long-running subprocess that loops over its
whole batch of dates internally, so the Python/import/Playwright/bearer-token
startup cost is paid once per worker instead of once per date. After each
worker finishes, every date in its chunk is checked against the worldwide
snapshot on disk and recorded in a JSON state file. Re-running the command
skips completed dates unless --refetch-done is passed.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORLDWIDE_DAILY = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "daily.py"
SYNC_COUNTRY_CSVS = ROOT / "scripts" / "sync_spotify_country_charts_from_worldwide.py"
BACKFILL_TRACK_IDS = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "backfill_charts_history_track_ids.py"
BACKFILL_TOTAL_DAYS = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "backfill_total_days.py"
ENRICH_WORLDWIDE_SNAPSHOTS = ROOT / "scripts" / "enrich_spotify_worldwide_snapshots.py"
UPLOAD_R2 = ROOT / "scripts" / "r2.py"
DEFAULT_STATE = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "run_all_backfill_done.json"
DEFAULT_GAPS_STATE = DEFAULT_STATE.with_name("run_all_backfill_gaps_done.json")
DEFAULT_SESSION_DIR = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json"

# Spotify stopped publishing these ~31 regional daily charts around 2019-08-24
# (all our charts_history_<region>.csv freeze on that exact date — it was the end
# of an old TSM backfill batch, and regular collection resumed later with only
# ~37 "live" regions). Requesting them for a later date just returns 404, which
# still costs a global-pacer slot. They ARE fetched for dates on/before the
# cutoff (they were tracked then). Disable with --include-discontinued-regions.
DISCONTINUED_REGION_CUTOFF = "2019-08-24"
DISCONTINUED_REGIONS = tuple(
    "ar bg bo cl co cr do ec eg es fi gr gt hn in is it jp ma mx "
    "ni pa pe py ro sv th tr uy vn za".split()
)


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


def _snapshot_is_usable(chart_date: str) -> bool:
    path = _snapshot_path(chart_date)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    by_track = payload.get("by_track")
    skipped_regions = payload.get("skipped_regions") or []
    # A no-Taylor day can be an exact empty snapshot. An empty snapshot where
    # regions were skipped is incomplete and must not be treated as done.
    if isinstance(by_track, dict) and not by_track and skipped_regions:
        return False
    return isinstance(by_track, dict)


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
        if chart_date not in done and _snapshot_is_usable(chart_date):
            done.add(chart_date)
            added += 1
    state["done_dates"] = sorted(done)
    return added


def _session_files() -> list[Path]:
    return sorted(DEFAULT_SESSION_DIR.glob("spotify_session*.json"))


def _csv_dates_present(region: str) -> set[str]:
    """Distinct chart dates already recorded in db/charts_history_<region>.csv.

    This is the durable, git-tracked store the site reads from — unlike the local
    snapshots/ tree, which is scratch and often incomplete on a given machine.
    Taylor charts every day globally, so a date missing from charts_history_global
    is a date we never collected worldwide.
    """
    path = ROOT / "db" / f"charts_history_{region}.csv"
    out: set[str] = set()
    if not path.exists():
        print(f"[WARN] --gaps-from-csv: {path} not found; treating every date as missing for {region}")
        return out
    with path.open(encoding="utf-8-sig") as f:
        next(f, None)  # header
        for line in f:
            cell = line.split(",", 1)[0].strip()
            if len(cell) == 10 and cell[4] == "-" and cell[7] == "-":
                out.add(cell)
    return out


def _chunks(items: list[str], n: int) -> list[list[str]]:
    """Split items into n contiguous chunks (as even as possible), dropping empty ones."""
    if n <= 0:
        return [items] if items else []
    size, extra = divmod(len(items), n)
    out: list[list[str]] = []
    start = 0
    for i in range(n):
        this_size = size + (1 if i < extra else 0)
        if this_size == 0:
            continue
        out.append(items[start : start + this_size])
        start += this_size
    return out


def _run_chunk(
    chart_dates: list[str],
    *,
    session_file: Path,
    force: bool,
    dry_run: bool,
    per_worker_semaphore: int,
    request_interval: float,
    fetch_max_attempts: int,
    regions: list[str] | None = None,
    exclude_regions: list[str] | None = None,
) -> tuple[list[str], int, float, str]:
    """Fetch a whole batch of dates in a single subprocess (one process per worker,
    not one per date), so Python/import/Playwright/bearer-token/region-discovery
    startup cost is paid once per worker instead of once per date."""
    env = os.environ.copy()
    env["SPOTIFY_CHARTS_SESSION_FILE"] = str(session_file)
    env["SPOTIFY_CHARTS_SINGLE_SESSION"] = "1"
    env["SPOTIFY_CHARTS_BEARER_CACHE_FILE"] = str(session_file.with_name(f"bearer_cache_{session_file.stem}.json"))
    env["SPOTIFY_SKIP_LATEST_FALLBACK_ON_404"] = "1"
    env["SPOTIFY_WORLDWIDE_SEMAPHORE"] = str(per_worker_semaphore)
    # daily.py serialises EVERY request through one global RequestPacer, so the
    # interval — not the semaphore — sets throughput (~1 req / interval / worker,
    # ~68 regions per date). Default 2.0s in daily.py = ~2.5 min/date floor;
    # override here so a multi-hundred-date backfill is not a multi-day job.
    env["SPOTIFY_WORLDWIDE_REQUEST_INTERVAL_SECONDS"] = str(request_interval)
    # daily.py defaults FETCH_MAX_ATTEMPTS to 0 (unlimited) for the live run so it
    # never skips real data; for an unattended backfill an unbounded retry on one
    # stuck region (sustained 429 / WARP wobble) freezes the whole date silently.
    env["SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS"] = str(fetch_max_attempts)
    dates_file = Path(
        tempfile.mkstemp(prefix=f"spotify_backfill_{session_file.stem}_", suffix=".txt")[1]
    )
    try:
        dates_file.write_text("\n".join(chart_dates) + "\n", encoding="utf-8")
        cmd = [
            sys.executable,
            str(WORLDWIDE_DAILY),
            "--dates-file",
            str(dates_file),
            "--no-post",
            "--backfill-mode",
        ]
        if force:
            cmd.append("--force")
        if regions:
            cmd += ["--regions", *regions]
        if exclude_regions:
            cmd += ["--exclude-regions", *exclude_regions]
        started = time.perf_counter()
        rc = _run(cmd, dry_run=dry_run, env=env)
        elapsed = time.perf_counter() - started
    finally:
        try:
            dates_file.unlink(missing_ok=True)
        except OSError:
            pass

    return chart_dates, rc, elapsed, session_file.name


def _run_worker(
    chart_dates: list[str],
    *,
    session_file: Path,
    force: bool,
    dry_run: bool,
    per_worker_semaphore: int,
    request_interval: float,
    fetch_max_attempts: int,
    regions: list[str] | None,
    exclude_regions: list[str] | None,
    skip_discontinued: bool,
) -> tuple[list[str], int, float, str]:
    """One worker's batch. When --regions is not used and the discontinued-region
    filter is on, the batch is split at DISCONTINUED_REGION_CUTOFF so the ~31
    dead regionals are only requested for dates when they still existed."""
    base_exclude = list(exclude_regions or [])
    if regions or not skip_discontinued:
        return _run_chunk(
            chart_dates, session_file=session_file, force=force, dry_run=dry_run,
            per_worker_semaphore=per_worker_semaphore, request_interval=request_interval,
            fetch_max_attempts=fetch_max_attempts, regions=regions, exclude_regions=base_exclude or None,
        )

    post = [d for d in chart_dates if d > DISCONTINUED_REGION_CUTOFF]
    pre = [d for d in chart_dates if d <= DISCONTINUED_REGION_CUTOFF]
    total_rc = 0
    total_elapsed = 0.0
    # Newest-first: post-cutoff dates before pre-cutoff, matching the global order.
    for subset, extra_exclude in (
        (post, list(DISCONTINUED_REGIONS)),
        (pre, []),
    ):
        if not subset:
            continue
        merged_exclude = sorted(set(base_exclude) | set(extra_exclude)) or None
        _, rc, elapsed, _ = _run_chunk(
            subset, session_file=session_file, force=force, dry_run=dry_run,
            per_worker_semaphore=per_worker_semaphore, request_interval=request_interval,
            fetch_max_attempts=fetch_max_attempts, regions=None, exclude_regions=merged_exclude,
        )
        total_rc = total_rc or rc
        total_elapsed += elapsed
    return chart_dates, total_rc, total_elapsed, session_file.name


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumable no-post Spotify Charts backfill.")
    parser.add_argument("--start", default="2017-01-01", help="Start date inclusive (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date inclusive (YYYY-MM-DD), default: yesterday")
    parser.add_argument(
        "--state",
        default=None,
        help=(
            "JSON state file storing completed dates. Default: "
            f"{DEFAULT_STATE.name}, or {DEFAULT_GAPS_STATE.name} when --gaps-from-csv is "
            "used (a fresh campaign shouldn't inherit snapshot-based done_dates that "
            "never reached the CSV)."
        ),
    )
    parser.add_argument("--force", action="store_true", default=True, help="Pass --force to the collector for pending dates")
    parser.add_argument("--no-force", action="store_false", dest="force", help="Do not pass --force to the collector")
    parser.add_argument("--refetch-done", action="store_true", help="Re-fetch dates even if they are marked done")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of dates to fetch this run")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Number of parallel worker processes (default: 1). Each worker fetches its "
            "whole date batch in a single long-running subprocess via --dates-file."
        ),
    )
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to stagger between worker chunk launches")
    parser.add_argument("--skip-existing-snapshot", action="store_true", default=True)
    parser.add_argument(
        "--request-interval",
        type=float,
        default=1.0,
        help=(
            "Seconds between requests inside each worker (daily.py's global RequestPacer). "
            "This is what actually caps throughput (~1 req/interval/worker, ~68 regions/date). "
            "Default 1.0 (~1.5 min/date/worker). Raise toward 2.0 if logs show '429 - pause globale'."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help=(
            "Regions fetched in parallel within each worker (SPOTIFY_WORLDWIDE_SEMAPHORE). "
            "Default 8. Overrides the SPOTIFY_WORLDWIDE_TOTAL_CONCURRENCY env split."
        ),
    )
    parser.add_argument(
        "--fetch-max-attempts",
        type=int,
        default=8,
        help=(
            "Max fetch attempts per region before it is omitted from the date's snapshot "
            "(SPOTIFY_WORLDWIDE_FETCH_MAX_ATTEMPTS). Default 8 — bounded so one stuck region "
            "(sustained 429 / WARP wobble) cannot freeze a date forever. 0 = unlimited."
        ),
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        metavar="CODE",
        help=(
            "Only (re)collect these region codes for each pending date, merging them "
            "back into the existing dated snapshot region by region (forwarded to "
            "worldwide/daily.py --regions). Use for a targeted fill, e.g. --regions fr."
        ),
    )
    parser.add_argument(
        "--exclude-regions",
        nargs="+",
        metavar="CODE",
        help="Collect every discovered region except these (forwarded to worldwide/daily.py).",
    )
    parser.add_argument(
        "--include-discontinued-regions",
        action="store_true",
        help=(
            f"Also request the {len(DISCONTINUED_REGIONS)} regionals Spotify dropped ~"
            f"{DISCONTINUED_REGION_CUTOFF} for dates AFTER that cutoff. Off by default: "
            "those return 404 post-cutoff and waste a pacer slot. Ignored with --regions."
        ),
    )
    parser.add_argument(
        "--gaps-from-csv",
        nargs="*",
        metavar="REGION",
        default=None,
        help=(
            "Derive the pending dates from dates ABSENT in db/charts_history_<region>.csv "
            "(the durable git-tracked store) instead of from missing local snapshot files. "
            "Bare flag uses 'global'; pass region codes to union their gaps "
            "(e.g. --gaps-from-csv global us uk). Best signal for a large historical fill."
        ),
    )
    parser.add_argument("--no-sync", action="store_true", help="Do not sync charts_history CSVs after collection")
    parser.add_argument(
        "--upload-r2",
        action="store_true",
        help=(
            "After sync, upload the touched chart data to R2 (scripts/r2.py --charts-only) "
            "one worldwide dated snapshot at a time, matching explicit --date runs. "
            "Ignored if --no-sync is set (nothing new to upload). Networked/production write: "
            "off by default, opt in explicitly."
        ),
    )
    args = parser.parse_args()
    if args.upload_r2 and args.no_sync:
        print("[WARN] --upload-r2 ignored because --no-sync is set (snapshots were not enriched this run)")
        args.upload_r2 = False

    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else (date.today() - timedelta(days=1))
    if end < start:
        raise SystemExit("--end must be >= --start")

    csv_gap_regions: list[str] | None = None
    if args.gaps_from_csv is not None:
        csv_gap_regions = [r.strip().lower() for r in args.gaps_from_csv if r.strip()] or ["global"]

    if args.state:
        state_path = Path(args.state)
    else:
        state_path = DEFAULT_GAPS_STATE if csv_gap_regions else DEFAULT_STATE
    state = _load_state(state_path)
    all_dates = _date_range(start, end)

    if csv_gap_regions:
        # A date is pending if it is missing from ANY requested region's CSV
        # (union of gaps) -> present = dates covered by EVERY requested region.
        present: set[str] | None = None
        for region in csv_gap_regions:
            region_present = _csv_dates_present(region)
            present = region_present if present is None else (present & region_present)
        csv_pending = [d for d in all_dates if d not in (present or set())]
        print(
            f"[GAPS] {len(csv_pending)} date(s) missing from charts_history_"
            f"{{{','.join(csv_gap_regions)}}}.csv in range (durable-store signal)"
        )
        # Selection comes from the CSV, not from local snapshot presence.
        pending_pool = csv_pending
    else:
        if args.skip_existing_snapshot and not args.refetch_done:
            added = _mark_existing_snapshots(state, all_dates)
            if added:
                print(f"[STATE] {added} existing snapshot date(s) marked done")
                if not args.dry_run:
                    _save_state(state_path, state)
        pending_pool = all_dates

    done = set(state.get("done_dates") or [])
    # Backfill newest first so long historical runs publish useful recent gaps
    # before spending hours on old archive dates.
    pending = sorted(
        (d for d in pending_pool if args.refetch_done or d not in done),
        reverse=True,
    )
    if args.limit and args.limit > 0:
        pending = pending[: args.limit]

    sessions = _session_files()
    if not sessions:
        raise SystemExit(f"No Spotify session files found in {DEFAULT_SESSION_DIR}")
    workers = max(1, int(args.workers or 1))
    workers = min(workers, len(sessions), len(pending) or 1)
    if args.concurrency and args.concurrency > 0:
        per_worker_semaphore = int(args.concurrency)
    else:
        total_worldwide_concurrency = max(1, int(os.getenv("SPOTIFY_WORLDWIDE_TOTAL_CONCURRENCY", "1")))
        per_worker_semaphore = max(1, total_worldwide_concurrency // workers)

    skip_discontinued = not args.include_discontinued_regions and not args.regions
    n_post_cutoff = sum(1 for d in pending if d > DISCONTINUED_REGION_CUTOFF)

    chunks = _chunks(pending, workers)
    print(
        f"[PLAN] range={all_dates[0]} -> {all_dates[-1]} order=newest-first total={len(all_dates)} "
        f"pending={len(pending)} workers={len(chunks)} chunk_sizes={[len(c) for c in chunks]} "
        f"regions_in_parallel_per_worker={per_worker_semaphore} "
        f"request_interval={args.request_interval}s fetch_max_attempts={args.fetch_max_attempts} "
        f"sessions={', '.join(p.name for p in sessions[: len(chunks)])}"
    )
    if skip_discontinued and n_post_cutoff:
        print(
            f"[PLAN] {len(DISCONTINUED_REGIONS)} discontinued regionals excluded for "
            f"{n_post_cutoff} date(s) after {DISCONTINUED_REGION_CUTOFF} "
            f"(pass --include-discontinued-regions to keep them)"
        )
    failures: dict[str, str] = dict(state.get("failed_dates") or {})
    touched_dates: set[str] = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunks) or 1) as executor:
        future_to_chunk = {}
        for idx, chunk in enumerate(chunks, 1):
            session_file = sessions[(idx - 1) % len(chunks)]
            print(
                f"[QUEUE] worker {idx}/{len(chunks)}: {len(chunk)} date(s) "
                f"({chunk[0]} -> {chunk[-1]}) via {session_file.name}",
                flush=True,
            )
            future = executor.submit(
                _run_worker,
                chunk,
                session_file=session_file,
                force=bool(args.force),
                dry_run=bool(args.dry_run),
                per_worker_semaphore=per_worker_semaphore,
                request_interval=float(args.request_interval),
                fetch_max_attempts=int(args.fetch_max_attempts),
                regions=args.regions,
                exclude_regions=args.exclude_regions,
                skip_discontinued=skip_discontinued,
            )
            future_to_chunk[future] = chunk
            if args.sleep > 0:
                time.sleep(args.sleep)

        completed_workers = 0
        for future in concurrent.futures.as_completed(future_to_chunk):
            completed_workers += 1
            chunk = future_to_chunk[future]
            try:
                chunk, rc, elapsed, session_name = future.result()
            except Exception as exc:
                for chart_date in chunk:
                    failures[chart_date] = f"exception={exc}"
                print(f"[FAIL] worker {completed_workers}/{len(chunks)} ({len(chunk)} date(s)): exception={exc}", flush=True)
            else:
                # Evaluate each date on its own snapshot, not the chunk's aggregate rc:
                # daily.py's multi-date loop continues past a failed date instead of
                # aborting the batch, so one bad date in a chunk must not make the
                # wrapper discard every other (successfully written) date in it.
                ok_dates: list[str] = []
                bad_dates: list[tuple[str, bool]] = []
                for chart_date in chunk:
                    snapshot_usable = args.dry_run or _snapshot_is_usable(chart_date)
                    if snapshot_usable:
                        ok_dates.append(chart_date)
                    else:
                        bad_dates.append((chart_date, snapshot_usable))
                for chart_date in ok_dates:
                    done.add(chart_date)
                    touched_dates.add(chart_date)
                    failures.pop(chart_date, None)
                for chart_date, snapshot_usable in bad_dates:
                    failures[chart_date] = f"rc={rc}; snapshot_usable={snapshot_usable}; session={session_name}"
                print(
                    f"[ OK ] worker {completed_workers}/{len(chunks)} via {session_name}: "
                    f"{len(ok_dates)}/{len(chunk)} date(s) in {elapsed:.1f}s"
                    + (f" — {len(bad_dates)} failed" if bad_dates else ""),
                    flush=True,
                )

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

        if args.upload_r2:
            for chart_date in sorted(touched_dates):
                rc = _run(
                    [
                        sys.executable, str(UPLOAD_R2),
                        "--charts-only",
                        "--worldwide-snapshot-only",
                        "--skip-history-upload",
                        "--skip-db-upload",
                        "--skip-images-upload",
                        "--new-date",
                        chart_date,
                    ],
                    dry_run=args.dry_run,
                )
                if rc != 0:
                    return rc

    print(f"[DONE] done={len(done)} failed={len(failures)} state={state_path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
