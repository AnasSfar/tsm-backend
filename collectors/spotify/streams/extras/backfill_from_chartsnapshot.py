from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
STREAMS_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPT_DIR.parents[3]

sys.path.insert(0, str(STREAMS_DIR / "tools" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))

from core.data_paths import first_existing_db_history, update_streams_dir  # noqa: E402
from history_store import (  # noqa: E402
    HISTORY_FIELDNAMES,
    append_history_row,
    extract_track_id,
    load_history_rows,
    load_stream_discography_sections_flat,
    save_history_rows,
)

ARTIST_URI = "06HL4z0CvFAxyc27GXpf02"
CHARTSNAPSHOT_URL = "https://www.chartsnapshot.com/get_top_songs"
STRICT_PREVIOUS_DATE = date(2025, 1, 1)
SOURCE_REASON = "chartsnapshot_historical"
FETCH_ATTEMPTS = 3
FETCH_TIMEOUT_SECONDS = 30
_FETCH_CACHE: dict[str, list[dict]] = {}


def log(message: str) -> None:
    print(message, flush=True)


@dataclass
class Candidate:
    date: str
    source_track_id: str
    track_id: str
    title: str
    source_title: str
    total: int
    daily: int
    previous_total: int | None
    status: str
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill track-level stream history from ChartSnapshot.",
    )
    parser.add_argument("dates", nargs="+", help="Date(s) to import, YYYY-MM-DD")
    parser.add_argument(
        "--backward-until-empty",
        action="store_true",
        help="Treat the first date as a start date, then walk backwards until ChartSnapshot returns no rows.",
    )
    parser.add_argument(
        "--backward-to",
        default=None,
        help="Treat the first date as a start date, then walk backwards through this end date, ignoring empty dates.",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=500,
        help="Safety cap for --backward-until-empty only. Default: 500",
    )
    parser.add_argument("--apply", action="store_true", help="Write accepted rows to db/streams_history.csv")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing rows for the requested date(s). Default skips existing track/date rows.",
    )
    parser.add_argument(
        "--strict-before",
        default=STRICT_PREVIOUS_DATE.isoformat(),
        help="Dates on/after this require our previous-day total check. Default: 2025-01-01",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional JSON report path. Defaults to runtime/spotify_streams/chartsnapshot_backfill_<date>.json for one date.",
    )
    parser.add_argument(
        "--allow-partial-dates",
        action="store_true",
        help="Allow writing accepted rows for dates that still have invalid, bad-date, external, or blocked rows.",
    )
    parser.add_argument(
        "--no-skip-existing-dates",
        action="store_true",
        help="Do not skip dates that already have chartsnapshot_historical rows.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel ChartSnapshot HTTP requests for dates that are not skipped. Default: 1.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.35,
        help="Seconds to stagger parallel ChartSnapshot requests. Default: 0.35.",
    )
    return parser.parse_args()


def validate_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise SystemExit(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc


def parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return number


def build_track_maps() -> tuple[dict[str, dict], dict[str, str]]:
    tracks_by_id: dict[str, dict] = {}
    id_map: dict[str, str] = {}

    for section in load_stream_discography_sections_flat():
        for track in section.get("tracks", []):
            if track.get("exclude_from_stream_collection"):
                continue
            track_id = extract_track_id(track.get("url") or track.get("spotify_url") or "")
            if not track_id:
                continue
            title = str(track.get("title") or "").strip() or track_id
            tracks_by_id.setdefault(track_id, {"track_id": track_id, "title": title})
            id_map[track_id] = track_id
            for historical_id in track.get("historical_track_ids") or []:
                historical_id = str(historical_id).strip()
                if historical_id:
                    id_map[historical_id] = track_id

    return tracks_by_id, id_map


def fetch_chartsnapshot_rows(stats_date: str) -> list[dict]:
    if stats_date in _FETCH_CACHE:
        return _FETCH_CACHE[stats_date]
    last_error: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(
                CHARTSNAPSHOT_URL,
                params={"artist_uri": ARTIST_URI, "date": stats_date},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=FETCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= FETCH_ATTEMPTS:
                raise RuntimeError(
                    f"ChartSnapshot fetch failed for {stats_date} after {FETCH_ATTEMPTS} attempt(s): {exc}"
                ) from exc
            log(f"{stats_date}: ChartSnapshot fetch failed ({exc}); retry {attempt + 1}/{FETCH_ATTEMPTS}")
            time.sleep(min(2 * attempt, 5))
        finally:
            session.close()
    else:
        raise RuntimeError(f"ChartSnapshot fetch failed for {stats_date}: {last_error}")
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected ChartSnapshot payload for {stats_date}: {payload!r}")
    _FETCH_CACHE[stats_date] = payload
    return payload


def expand_requested_dates(args: argparse.Namespace) -> list[str]:
    raw_dates = [validate_date(value) for value in args.dates]
    if args.backward_to:
        if len(raw_dates) != 1:
            raise SystemExit("--backward-to expects exactly one start date")
        start = date.fromisoformat(raw_dates[0])
        end = date.fromisoformat(validate_date(args.backward_to))
        if end > start:
            raise SystemExit("--backward-to must be on or before the start date")
        dates: list[str] = []
        current = start
        while current >= end:
            current_str = current.isoformat()
            dates.append(current_str)
            log(f"{current_str}: queued")
            current -= timedelta(days=1)
        return dates

    if not args.backward_until_empty:
        return raw_dates
    if len(raw_dates) != 1:
        raise SystemExit("--backward-until-empty expects exactly one start date")
    if args.max_days <= 0:
        raise SystemExit("--max-days must be positive")

    start = date.fromisoformat(raw_dates[0])
    dates: list[str] = []
    for offset in range(args.max_days):
        current = (start - timedelta(days=offset)).isoformat()
        rows = fetch_chartsnapshot_rows(current)
        if not rows:
            log(f"{current}: source=0 rows; stopping backward scan.")
            break
        dates.append(current)
        log(f"{current}: source={len(rows)} rows; queued")
    else:
        log(f"Reached --max-days={args.max_days}; stopping backward scan.")
    return dates


def history_indexes(rows: list[dict]) -> tuple[dict[tuple[str, str], dict], dict[tuple[str, str], int]]:
    row_by_date_track: dict[tuple[str, str], dict] = {}
    total_by_date_track: dict[tuple[str, str], int] = {}
    for row in rows:
        day = str(row.get("date") or "").strip()
        track_id = str(row.get("track_id") or "").strip()
        if not day or not track_id:
            continue
        row_by_date_track[(day, track_id)] = row
        total = parse_int(row.get("streams"))
        if total is not None:
            total_by_date_track[(day, track_id)] = total
    return row_by_date_track, total_by_date_track


def date_has_chartsnapshot_rows(existing_rows: list[dict], stats_date: str) -> bool:
    return any(
        str(row.get("date") or "").strip() == stats_date
        and str(row.get("estimated_reason") or "").strip() == SOURCE_REASON
        for row in existing_rows
    )


def chartsnapshot_dates(existing_rows: list[dict]) -> set[str]:
    return {
        str(row.get("date") or "").strip()
        for row in existing_rows
        if str(row.get("estimated_reason") or "").strip() == SOURCE_REASON
        and str(row.get("date") or "").strip()
    }


def skipped_existing_date_report(stats_date: str) -> dict:
    return {
        "date": stats_date,
        "strict_previous_check": False,
        "source_rows": 0,
        "source_unique_track_ids": 0,
        "source_duplicate_track_ids": [],
        "accepted": 0,
        "rejected": 0,
        "external_unmapped": 0,
        "bad_date_rows": 0,
        "invalid_rows": 0,
        "accepted_daily_sum": 0,
        "accepted_total_sum": 0,
        "source_daily_sum": 0,
        "source_total_sum": 0,
        "rejected_by_reason": {},
        "sample_accepted": [],
        "sample_rejected": [],
        "sample_external_unmapped": [],
        "external_unmapped_rows": [],
        "blocked_date": False,
        "skipped_existing_date": True,
        "written": 0,
    }


def fetch_error_report(stats_date: str, exc: Exception) -> dict:
    return {
        "date": stats_date,
        "strict_previous_check": False,
        "source_rows": 0,
        "source_unique_track_ids": 0,
        "source_duplicate_track_ids": [],
        "accepted": 0,
        "rejected": 0,
        "external_unmapped": 0,
        "bad_date_rows": 0,
        "invalid_rows": 0,
        "accepted_daily_sum": 0,
        "accepted_total_sum": 0,
        "source_daily_sum": 0,
        "source_total_sum": 0,
        "rejected_by_reason": {"fetch_error": 1},
        "sample_accepted": [],
        "sample_rejected": [],
        "sample_external_unmapped": [],
        "external_unmapped_rows": [],
        "fetch_error": True,
        "fetch_error_message": str(exc),
        "blocked_date": True,
        "blocked_reason": "chartsnapshot_fetch_error",
        "written": 0,
    }


def analyze_date(
    stats_date: str,
    *,
    strict_before: date,
    tracks_by_id: dict[str, dict],
    id_map: dict[str, str],
    row_by_date_track: dict[tuple[str, str], dict],
    total_by_date_track: dict[tuple[str, str], int],
    replace: bool,
    request_delay_seconds: float = 0.0,
) -> tuple[list[Candidate], dict]:
    log(f"{stats_date}: fetching ChartSnapshot")
    if request_delay_seconds > 0:
        time.sleep(request_delay_seconds)
    source_rows = fetch_chartsnapshot_rows(stats_date)
    previous_date = (date.fromisoformat(stats_date) - timedelta(days=1)).isoformat()
    strict = date.fromisoformat(stats_date) >= strict_before

    accepted: list[Candidate] = []
    rejected: list[Candidate] = []
    external_rows: list[dict] = []
    bad_date_rows: list[dict] = []
    invalid_rows: list[dict] = []
    duplicate_source_ids: set[str] = set()
    seen_source_ids: set[str] = set()

    for row in source_rows:
        source_track_id = str(row.get("track_uri") or "").strip()
        source_title = str(row.get("name") or "").strip()
        if source_track_id in seen_source_ids:
            duplicate_source_ids.add(source_track_id)
        seen_source_ids.add(source_track_id)

        if str(row.get("date") or "").strip() != stats_date:
            bad_date_rows.append(row)
            continue

        total = parse_int(row.get("total_streams"))
        daily = parse_int(row.get("daily_streams"))
        if not source_track_id or total is None or daily is None or daily < 0:
            invalid_rows.append(row)
            continue

        track_id = id_map.get(source_track_id)
        if not track_id or track_id not in tracks_by_id:
            external_rows.append(row)
            continue

        title = tracks_by_id[track_id]["title"]
        previous_total = total_by_date_track.get((previous_date, track_id))
        existing_current = row_by_date_track.get((stats_date, track_id))
        if existing_current and not replace:
            candidate = Candidate(
                stats_date,
                source_track_id,
                track_id,
                title,
                source_title,
                total,
                daily,
                previous_total,
                "skipped",
                "already_exists",
            )
            rejected.append(candidate)
            continue

        if strict:
            if previous_total is None:
                reason = "missing_previous_total"
            elif total - daily != previous_total:
                reason = "previous_total_mismatch"
            else:
                reason = SOURCE_REASON
        else:
            reason = SOURCE_REASON

        candidate = Candidate(
            stats_date,
            source_track_id,
            track_id,
            title,
            source_title,
            total,
            daily,
            previous_total,
            "accepted" if reason == SOURCE_REASON else "blocked",
            reason,
        )
        if candidate.status == "accepted":
            accepted.append(candidate)
        else:
            rejected.append(candidate)

    report = {
        "date": stats_date,
        "strict_previous_check": strict,
        "source_rows": len(source_rows),
        "source_unique_track_ids": len(seen_source_ids),
        "source_duplicate_track_ids": sorted(duplicate_source_ids),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "external_unmapped": len(external_rows),
        "bad_date_rows": len(bad_date_rows),
        "invalid_rows": len(invalid_rows),
        "accepted_daily_sum": sum(item.daily for item in accepted),
        "accepted_total_sum": sum(item.total for item in accepted),
        "source_daily_sum": sum(parse_int(row.get("daily_streams")) or 0 for row in source_rows),
        "source_total_sum": sum(parse_int(row.get("total_streams")) or 0 for row in source_rows),
        "rejected_by_reason": {},
        "sample_accepted": [item.__dict__ for item in accepted[:15]],
        "sample_rejected": [item.__dict__ for item in rejected[:15]],
        "sample_external_unmapped": [
            {
                "track_uri": row.get("track_uri"),
                "name": row.get("name"),
                "album_name": row.get("album_name"),
                "daily_streams": row.get("daily_streams"),
                "total_streams": row.get("total_streams"),
            }
            for row in external_rows[:25]
        ],
        "external_unmapped_rows": [
            {
                "track_uri": row.get("track_uri"),
                "name": row.get("name"),
                "album_name": row.get("album_name"),
                "daily_streams": row.get("daily_streams"),
                "total_streams": row.get("total_streams"),
            }
            for row in external_rows
        ],
    }
    for item in rejected:
        report["rejected_by_reason"][item.reason] = report["rejected_by_reason"].get(item.reason, 0) + 1
    return accepted, report


def write_rows(existing_rows: list[dict], candidates: list[Candidate], *, replace_dates: set[str]) -> int:
    if not replace_dates:
        written = 0
        seen_candidate_keys: set[tuple[str, str]] = set()
        for item in candidates:
            key = (item.date, item.track_id)
            if key in seen_candidate_keys:
                continue
            seen_candidate_keys.add(key)
            append_history_row(
                [
                    item.date,
                    item.track_id,
                    str(item.total),
                    str(item.daily),
                    "",
                    SOURCE_REASON,
                ]
            )
            written += 1
        return written

    fieldnames = list(HISTORY_FIELDNAMES)
    for row in existing_rows:
        for key in row:
            if key and key not in fieldnames:
                fieldnames.append(key)

    candidate_keys = {(item.date, item.track_id) for item in candidates}
    rows = [
        row
        for row in existing_rows
        if (str(row.get("date") or ""), str(row.get("track_id") or "")) not in candidate_keys
        and (str(row.get("date") or "") not in replace_dates or (str(row.get("date") or ""), str(row.get("track_id") or "")) not in candidate_keys)
    ]
    for item in candidates:
        rows.append(
            {
                "date": item.date,
                "track_id": item.track_id,
                "streams": str(item.total),
                "daily_streams": str(item.daily),
                "estimated": "",
                "estimated_reason": SOURCE_REASON,
            }
        )

    rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("track_id") or "")))
    save_history_rows([{field: row.get(field, "") for field in fieldnames} for row in rows])

    by_date: dict[str, list[dict]] = {}
    for row in rows:
        day = str(row.get("date") or "")
        if day in replace_dates or any(item.date == day and item.track_id == row.get("track_id") for item in candidates):
            by_date.setdefault(day, []).append(row)

    for day, day_rows in by_date.items():
        daily_path = update_streams_dir(day) / "streams_history.csv"
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        with daily_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in sorted(day_rows, key=lambda r: str(r.get("track_id") or "")):
                writer.writerow({field: row.get(field, "") for field in fieldnames})

    return len(candidates)


def candidate_to_history_row(item: Candidate) -> dict:
    return {
        "date": item.date,
        "track_id": item.track_id,
        "streams": str(item.total),
        "daily_streams": str(item.daily),
        "estimated": "",
        "estimated_reason": SOURCE_REASON,
    }


def default_report_path(dates: list[str]) -> Path:
    suffix = dates[0] if len(dates) == 1 else f"{dates[0]}_{dates[-1]}_{len(dates)}dates"
    return REPO_ROOT / "runtime" / "spotify_streams" / f"chartsnapshot_backfill_{suffix}.json"


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def report_has_blockers(report: dict, *, allow_partial_dates: bool) -> bool:
    if report.get("fetch_error"):
        return True
    date_blockers = (
        report["external_unmapped"]
        + report["bad_date_rows"]
        + report["invalid_rows"]
        + sum(
            count
            for reason, count in report["rejected_by_reason"].items()
            if reason != "already_exists"
        )
    )
    return bool(date_blockers and not allow_partial_dates)


def write_per_date_report(
    stats_date: str,
    report: dict,
    *,
    args: argparse.Namespace,
    strict_before: date,
    candidates: list[Candidate],
) -> None:
    if len(args.dates) <= 1 and not args.backward_to and not args.backward_until_empty:
        return
    per_date_payload = {
        "source": "chartsnapshot",
        "artist_uri": ARTIST_URI,
        "apply": bool(args.apply),
        "replace": bool(args.replace),
        "strict_before": strict_before.isoformat(),
        "dates": [report],
        "accepted_total": 0 if report.get("blocked_date") else len(candidates),
        "accepted_daily_sum": 0 if report.get("blocked_date") else sum(item.daily for item in candidates),
        "written_total": report["written"],
    }
    write_report(default_report_path([stats_date]), per_date_payload)


def aggregate_report_payload(
    *,
    args: argparse.Namespace,
    strict_before: date,
    reports: list[dict],
    all_candidates: list[Candidate],
    total_written: int,
    interrupted: bool = False,
) -> dict:
    payload = {
        "source": "chartsnapshot",
        "artist_uri": ARTIST_URI,
        "apply": bool(args.apply),
        "replace": bool(args.replace),
        "strict_before": strict_before.isoformat(),
        "dates": reports,
        "accepted_total": len(all_candidates),
        "accepted_daily_sum": sum(item.daily for item in all_candidates),
        "written_total": total_written if args.apply and not args.replace else None,
    }
    if interrupted:
        payload["interrupted"] = True
    return payload


def main() -> int:
    args = parse_args()
    dates = expand_requested_dates(args)
    if not dates:
        print("No dates to backfill.")
        return 0
    strict_before = date.fromisoformat(validate_date(args.strict_before))

    tracks_by_id, id_map = build_track_maps()
    existing_rows = load_history_rows()
    row_by_date_track, total_by_date_track = history_indexes(existing_rows)
    existing_chartsnapshot_dates = chartsnapshot_dates(existing_rows)
    workers = max(1, args.workers)

    all_candidates: list[Candidate] = []
    reports: list[dict] = []
    total_written = 0
    out_path = Path(args.out) if args.out else default_report_path(dates)

    def write_aggregate_report(*, interrupted: bool = False) -> None:
        write_report(
            out_path,
            aggregate_report_payload(
                args=args,
                strict_before=strict_before,
                reports=reports,
                all_candidates=all_candidates,
                total_written=total_written,
                interrupted=interrupted,
            ),
        )

    pending_dates: list[str] = []
    for stats_date in dates:
        if not args.replace and not args.no_skip_existing_dates and stats_date in existing_chartsnapshot_dates:
            report = skipped_existing_date_report(stats_date)
            reports.append(report)
            write_per_date_report(stats_date, report, args=args, strict_before=strict_before, candidates=[])
            write_aggregate_report()
            log(f"{stats_date}: skipped; chartsnapshot_historical rows already exist")
            continue
        pending_dates.append(stats_date)

    def process_analyzed_date(stats_date: str, candidates: list[Candidate], report: dict) -> None:
        nonlocal total_written
        report["blocked_date"] = report_has_blockers(report, allow_partial_dates=args.allow_partial_dates)
        if report["blocked_date"]:
            report["blocked_reason"] = report.get("blocked_reason") or "date_has_unresolved_rows"
        else:
            all_candidates.extend(candidates)

        if args.apply and not args.replace and not report["blocked_date"]:
            written = write_rows(existing_rows, candidates, replace_dates=set())
            total_written += written
            if written:
                for item in candidates:
                    row = candidate_to_history_row(item)
                    existing_rows.append(row)
                    row_by_date_track[(item.date, item.track_id)] = row
                    total_by_date_track[(item.date, item.track_id)] = item.total
                existing_chartsnapshot_dates.add(stats_date)
            report["written"] = written
        else:
            report["written"] = 0

        write_per_date_report(stats_date, report, args=args, strict_before=strict_before, candidates=candidates)
        reports.append(report)
        write_aggregate_report()
        if report.get("blocked_date"):
            log(
                f"{stats_date}: BLOCKED source={report['source_rows']} accepted={report['accepted']} "
                f"external={report['external_unmapped']} invalid={report['invalid_rows']} written={report['written']}"
            )
        else:
            log(
                f"{stats_date}: done source={report['source_rows']} accepted={report['accepted']} "
                f"external={report['external_unmapped']} invalid={report['invalid_rows']} written={report['written']}"
            )

    if pending_dates:
        log(f"Processing {len(pending_dates)} date(s) with workers={workers}")

    try:
        if workers == 1 or len(pending_dates) <= 1:
            for stats_date in pending_dates:
                try:
                    candidates, report = analyze_date(
                        stats_date,
                        strict_before=strict_before,
                        tracks_by_id=tracks_by_id,
                        id_map=id_map,
                        row_by_date_track=row_by_date_track,
                        total_by_date_track=total_by_date_track,
                        replace=args.replace,
                        request_delay_seconds=0.0,
                    )
                except Exception as exc:
                    candidates = []
                    report = fetch_error_report(stats_date, exc)
                process_analyzed_date(stats_date, candidates, report)
        else:
            analysis_row_by_date_track = dict(row_by_date_track)
            analysis_total_by_date_track = dict(total_by_date_track)
            executor = ThreadPoolExecutor(max_workers=workers)
            try:
                future_by_date = {
                    executor.submit(
                        analyze_date,
                        stats_date,
                        strict_before=strict_before,
                        tracks_by_id=tracks_by_id,
                        id_map=id_map,
                        row_by_date_track=analysis_row_by_date_track,
                        total_by_date_track=analysis_total_by_date_track,
                        replace=args.replace,
                        request_delay_seconds=max(0.0, args.request_delay) * (index % workers),
                    ): stats_date
                    for index, stats_date in enumerate(pending_dates)
                }
                for future in as_completed(future_by_date):
                    stats_date = future_by_date[future]
                    try:
                        candidates, report = future.result()
                    except Exception as exc:
                        candidates = []
                        report = fetch_error_report(stats_date, exc)
                    process_analyzed_date(stats_date, candidates, report)
            except KeyboardInterrupt:
                for future in future_by_date:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)
    except KeyboardInterrupt:
        write_aggregate_report(interrupted=True)
        log(f"Interrupted by Ctrl+C; partial report written to {out_path}")
        return 130

    write_aggregate_report()

    log(f"Report: {out_path}")

    if not args.apply:
        log("Dry-run only. Re-run with --apply to write accepted rows.")
        return 0

    if args.replace:
        total_written = write_rows(existing_rows, all_candidates, replace_dates=set(dates))
    log(f"Wrote {total_written} row(s) to {first_existing_db_history('streams_history.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
