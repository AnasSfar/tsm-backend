#!/usr/bin/env python3
"""Reconcile Spotify's delayed daily updates during a multi-day gap.

For each track that's missing one or more days, this fetches the current
live total (from our own API and, optionally, a RapidAPI cross-check) and
classifies what it represents using the track's own recent daily-average as
a plausibility check:

  - "single_day"       gap is 1 day (or less) — normal, unambiguous.
  - "fully_caught_up"   the observed delta matches ~N days at the track's
                        usual rate, where N = the full gap — safe to record
                        under the target date.
  - "partial_catchup"   the delta matches ~M days (M < N) at the usual
                        rate — only the first M missing days (in
                        chronological order) have actually landed; the rest
                        stay pending.
  - "uncertain"         the delta doesn't cleanly match any whole number of
                        days within tolerance (e.g. a real anomaly, or a
                        genuine rate change) — never auto-applied, always
                        needs manual review.

This never invents a number: every total used is either our own last known
real value or a value actually returned by a live source. It only decides
which real, already-observed total belongs to which calendar date.

Usage:
  python reconcile_gap_catchup.py 2026-07-03 --track-ids 5uPaqMMt59KGrdKIitDRqa,1BxfuPKGuaTgP7aM0Bbdwr
  python reconcile_gap_catchup.py 2026-07-03 --album Lover
  python reconcile_gap_catchup.py 2026-07-03 --all-pending
  python reconcile_gap_catchup.py 2026-07-03 --all-pending --apply   # actually write the confident rows
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(SCRIPT_DIR.parents[2]))  # collectors/spotify/

import history_store  # noqa: E402
import spotify_api  # noqa: E402

RATE_WINDOW_DAYS = 14
RATIO_TOLERANCE = 0.4  # 40% — how far a delta can be from N x the usual rate

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "spotify-statistics-and-stream-count.p.rapidapi.com"


def _rapidapi_stream_count(track_id: str) -> int | None:
    if not RAPIDAPI_KEY:
        return None
    import http.client
    import json as _json

    conn = http.client.HTTPSConnection(RAPIDAPI_HOST)
    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    try:
        conn.request("GET", f"/track/{track_id}", headers=headers)
        res = conn.getresponse()
        if res.status != 200:
            return None
        data = _json.loads(res.read().decode("utf-8", errors="replace"))
        return data.get("streamCount")
    except Exception:
        return None


def _expected_daily_rate(track_id: str, before_date: date, window: int = RATE_WINDOW_DAYS) -> float | None:
    """Median of the track's real daily_streams in the window before before_date."""
    rows = [
        r for r in history_store.load_history_rows()
        if r.get("track_id") == track_id
    ]
    values = []
    for r in rows:
        try:
            d = date.fromisoformat((r.get("date") or "").strip())
        except Exception:
            continue
        if d >= before_date or d < before_date - timedelta(days=window):
            continue
        daily_raw = (r.get("daily_streams") or "").strip()
        if not daily_raw:
            continue
        try:
            values.append(int(daily_raw))
        except Exception:
            continue
    if not values:
        return None
    return statistics.median(values)


def _real_rows_before(track_id: str, before_date: date) -> list[tuple[date, int]]:
    """(date, streams) for every row of this track with a real daily_streams,
    strictly before before_date, sorted ascending."""
    out = []
    for r in history_store.load_history_rows():
        if r.get("track_id") != track_id:
            continue
        if not (r.get("daily_streams") or "").strip():
            continue
        try:
            d = date.fromisoformat((r.get("date") or "").strip())
            streams = int(r.get("streams") or 0)
        except Exception:
            continue
        if d < before_date:
            out.append((d, streams))
    out.sort()
    return out


def _relabel_row(track_id: str, old_date: str, new_date: str, total: int, daily: int) -> None:
    """Move a mislabeled row from old_date to new_date (same total/daily —
    the value itself isn't invented, only its date is corrected)."""
    path = history_store.HISTORY_PATH
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    kept = [r for r in rows if not (r.get("track_id") == track_id and r.get("date") == old_date)]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)

    old_daily_path = history_store.update_streams_dir(old_date) / "streams_history.csv"
    if old_daily_path.exists():
        with old_daily_path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            daily_fieldnames = reader.fieldnames
            daily_rows = list(reader)
        kept_daily = [r for r in daily_rows if r.get("track_id") != track_id]
        with old_daily_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=daily_fieldnames)
            writer.writeheader()
            writer.writerows(kept_daily)

    history_store.append_history_row([new_date, track_id, total, daily, "", ""])
    print(f"[relabel] {track_id}: moved {total} (+{daily}) from {old_date} -> {new_date}")


class RapidApiBudget:
    """Caps how many RapidAPI calls a run may make — it's only meant as a
    tie-breaker for genuinely ambiguous tracks, not a per-track cross-check."""

    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def spend(self, track_id: str) -> int | None:
        if self.remaining <= 0 or not RAPIDAPI_KEY:
            return None
        self.remaining -= 1
        return _rapidapi_stream_count(track_id)


MANUAL_TRUSTED_REASON = "manual_trusted"


def _canary_confirmed_for_date(target_date: date) -> bool:
    """True only when a track has a row for target_date that WE manually
    injected from a verified trusted source (estimated_reason ==
    MANUAL_TRUSTED_REASON) — never a plain scraper-written row, since an
    ordinary row could itself be the mis-dated one this tool is meant to
    catch. A manually verified anchor is independent proof the calendar
    date is real, so other tracks' fresh totals can be trusted as
    belonging to target_date too, instead of being treated as ambiguous."""
    target_str = target_date.isoformat()
    for r in history_store.load_history_rows():
        if r.get("date") != target_str:
            continue
        if (r.get("estimated_reason") or "").strip() == MANUAL_TRUSTED_REASON:
            return True
    return False


def _classify_from_total(
    track_id: str, target_date: date, last_date: date, last_total: int, gap_days: int,
    rate: float | None, live_total: int, source: str,
    relabel_existing_to: date | None, existing_total: int | None, existing_daily: int | None,
    canary: bool = False,
) -> dict:
    base_result = {
        "last_date": last_date, "last_total": last_total, "live_total": live_total,
        "source": source,
    }
    if relabel_existing_to:
        base_result["relabel_existing_to"] = relabel_existing_to
        base_result["existing_total"] = existing_total
        base_result["existing_daily"] = existing_daily

    if gap_days <= 1:
        return {
            "track_id": track_id, "status": "single_day",
            "assumed_date": target_date, "daily": live_total - last_total, **base_result,
        }

    delta = live_total - last_total
    if not rate or rate <= 0:
        if canary:
            return {
                "track_id": track_id, "status": "canary_confirmed",
                "assumed_date": target_date, "daily": delta, **base_result,
            }
        return {"track_id": track_id, "status": "uncertain_no_rate", "delta": delta, **base_result}

    ratio = delta / rate
    n_days_guess = round(ratio)
    base_result["expected_daily_rate"] = rate
    base_result["ratio"] = ratio

    if n_days_guess < 1 or abs(ratio - n_days_guess) / n_days_guess > RATIO_TOLERANCE:
        if canary:
            return {
                "track_id": track_id, "status": "canary_confirmed",
                "assumed_date": target_date, "daily": delta, **base_result,
            }
        return {"track_id": track_id, "status": "uncertain", "delta": delta, **base_result}

    if n_days_guess >= gap_days:
        return {
            "track_id": track_id, "status": "fully_caught_up",
            "assumed_date": target_date, "daily": delta, **base_result,
        }

    assumed_date = last_date + timedelta(days=n_days_guess)
    return {
        "track_id": track_id, "status": "partial_catchup",
        "assumed_date": assumed_date, "daily": delta, **base_result,
    }


def classify_track(track_id: str, target_date: date, rapidapi_budget: "RapidApiBudget", canary: bool = False) -> dict:
    prior_rows = _real_rows_before(track_id, target_date)
    if not prior_rows:
        return {"track_id": track_id, "status": "no_history"}
    last_date, last_total = prior_rows[-1]
    gap_days = (target_date - last_date).days

    existing_total = history_store.get_history_total_for_date(track_id, target_date.isoformat())
    existing_daily = None
    if existing_total is not None:
        for r in history_store.load_history_rows():
            if r.get("track_id") == track_id and r.get("date") == target_date.isoformat():
                raw = (r.get("daily_streams") or "").strip()
                existing_daily = int(raw) if raw else None
                break

    rate = _expected_daily_rate(track_id, last_date) if gap_days > 1 else None

    relabel_existing_to: date | None = None
    if existing_total is not None and existing_daily is not None:
        if gap_days <= 1:
            return {"track_id": track_id, "status": "already_done_correct", "last_date": last_date}
        if rate and rate > 0:
            existing_ratio = existing_daily / rate
            n_days_existing = round(existing_ratio)
            if n_days_existing >= gap_days and abs(existing_ratio - n_days_existing) / n_days_existing <= RATIO_TOLERANCE:
                return {"track_id": track_id, "status": "already_done_correct", "last_date": last_date}
            if n_days_existing >= 1:
                relabel_existing_to = last_date + timedelta(days=n_days_existing)
        # Existing row for target_date looks like it only covers an earlier
        # day within the gap — it needs to be moved there, and target_date
        # itself is still genuinely open. Fall through to a fresh live check.

    compare_base_total = existing_total if existing_total is not None else last_total

    # Phase 1: our own API only — free, no quota concern.
    live_own = spotify_api.fetch_playcount_api(track_id, _TOKEN_MGR, _SESSION)
    if live_own is None or live_own <= compare_base_total:
        result = {
            "track_id": track_id, "status": "no_movement", "last_date": last_date,
            "last_total": last_total, "live_own": live_own,
        }
        if relabel_existing_to:
            result["relabel_existing_to"] = relabel_existing_to
            result["existing_total"] = existing_total
            result["existing_daily"] = existing_daily
        return result

    result = _classify_from_total(
        track_id, target_date, last_date, last_total, gap_days, rate,
        live_own, "own_api", relabel_existing_to, existing_total, existing_daily, canary,
    )

    # Phase 2: only spend RapidAPI budget when our own API alone left the
    # classification ambiguous — it's a tie-breaker, not a routine check.
    # (canary already resolved it above, so this won't even trigger then.)
    if result["status"] in ("uncertain", "uncertain_no_rate") and rapidapi_budget.remaining > 0:
        live_rapidapi = rapidapi_budget.spend(track_id)
        if live_rapidapi is not None and live_rapidapi > compare_base_total:
            best_total = max(live_own, live_rapidapi)
            result = _classify_from_total(
                track_id, target_date, last_date, last_total, gap_days, rate,
                best_total, "own_api+rapidapi", relabel_existing_to, existing_total, existing_daily, canary,
            )
            result["live_rapidapi"] = live_rapidapi

    return result


def _track_ids_for_album(album_name: str) -> list[str]:
    return sorted(history_store.load_album_track_ids_for_album(album_name))


def _track_labels() -> dict[str, str]:
    """track_id -> 'Song Name (Album)' for readable console/CSV output."""
    labels: dict[str, str] = {}
    for section in history_store.load_album_sections_flat():
        album = section.get("album", "")
        for track in section.get("tracks", []):
            tid = history_store.extract_track_id(track.get("url") or track.get("spotify_url") or "")
            if tid and tid not in labels:
                name = track.get("title_clean") or track.get("title") or ""
                labels[tid] = f"{name} ({album})" if album else name
    return labels


# Statuses that need no action and clutter the console when scanning the
# whole catalog — still included in the CSV report and the summary counts.
_QUIET_STATUSES = {"already_done_correct", "no_movement", "no_history"}


def _typed_row_from_csv(row: dict) -> dict:
    """Re-parse a row read back from the report CSV (all strings) into the
    same shape classify_track() returns, so resumed rows can feed --apply /
    --relabel-track exactly like freshly computed ones."""
    out: dict = {"track_id": row["track_id"], "status": row["status"]}
    for key in ("assumed_date", "last_date", "relabel_existing_to"):
        val = (row.get(key) or "").strip()
        if val:
            out[key] = date.fromisoformat(val)
    for key in ("last_total", "live_total", "daily", "existing_total", "existing_daily"):
        val = (row.get(key) or "").strip()
        if val:
            out[key] = int(val)
    for key in ("expected_daily_rate", "ratio"):
        val = (row.get(key) or "").strip()
        if val:
            out[key] = float(val)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile delayed Spotify catch-up per track.")
    parser.add_argument("date", help="Target stats date YYYY-MM-DD.")
    parser.add_argument("--track-ids", help="Comma-separated track ids to check.")
    parser.add_argument("--album", help="Check every track in this album.")
    parser.add_argument("--all-pending", action="store_true", help="Check every catalog track missing this date.")
    parser.add_argument("--apply", action="store_true", help="Write confident rows (not 'uncertain') to history.")
    parser.add_argument(
        "--rapidapi-budget", type=int, default=500,
        help="Max RapidAPI calls this run may spend as a tie-breaker for ambiguous tracks (default: 500).",
    )
    parser.add_argument(
        "--out", default=None,
        help="CSV report path (default: reconcile_report_<date>.csv next to this script).",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Also print already_done_correct/no_movement/no_history rows to the console.",
    )
    parser.add_argument(
        "--relabel-track",
        help="Move ONE specific track's existing row to its flagged date. Never done in bulk or "
             "automatically — you must name the exact track id after reviewing the report.",
    )
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date)

    if args.track_ids:
        track_ids = [t.strip() for t in args.track_ids.split(",") if t.strip()]
    elif args.album:
        track_ids = _track_ids_for_album(args.album)
    elif args.all_pending:
        # Check every catalog track, not just ones missing target_date — a
        # track can already have a (mislabeled) row for it that still needs
        # validating against its own recent rate.
        track_ids = sorted(history_store.load_album_track_ids())
    else:
        print("Specify --track-ids, --album, or --all-pending.")
        sys.exit(2)

    if not track_ids:
        print("No tracks to check.")
        return

    global _TOKEN_MGR, _SESSION
    import requests
    _TOKEN_MGR = spotify_api.TokenManager()
    _TOKEN_MGR.capture()
    _SESSION = requests.Session()

    if not RAPIDAPI_KEY:
        print("[warn] RAPIDAPI_KEY not set — ambiguous tracks won't get a tie-breaker check.")

    canary = _canary_confirmed_for_date(target_date)
    print(f"Canary check for {args.date}: "
          f"{'a confirmed real update exists (trusted anchor active)' if canary else 'no confirmed real update on record yet'}")

    out_path = Path(args.out) if args.out else SCRIPT_DIR / f"reconcile_report_{args.date}.csv"
    report_fields = [
        "track_id", "name", "status", "last_date", "last_total", "live_total",
        "assumed_date", "daily", "relabel_existing_to", "existing_total", "existing_daily",
        "expected_daily_rate", "ratio", "source",
    ]

    # Resume support: skip tracks already written to out_path so re-running
    # after an interruption doesn't repeat work (and doesn't re-spend budget).
    already_done: dict[str, dict] = {}
    if out_path.exists():
        with out_path.open("r", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                already_done[row["track_id"]] = row
        print(f"Resuming: {len(already_done)} track(s) already in {out_path}, skipping those.")

    write_header = not out_path.exists()
    out_file = out_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_file, fieldnames=report_fields, extrasaction="ignore")
    if write_header:
        writer.writeheader()

    labels = _track_labels()
    budget = RapidApiBudget(args.rapidapi_budget)
    results = []
    by_status: dict[str, int] = {}
    total = len(track_ids)
    try:
        for i, track_id in enumerate(track_ids, 1):
            if track_id in already_done:
                by_status[already_done[track_id]["status"]] = by_status.get(already_done[track_id]["status"], 0) + 1
                continue

            r = classify_track(track_id, target_date, budget, canary)
            results.append(r)
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1

            name = labels.get(track_id, track_id)
            row = dict(r)
            row["name"] = name
            writer.writerow(row)
            out_file.flush()

            if r["status"] in _QUIET_STATUSES:
                if args.verbose:
                    print(f"[{i}/{total}] {name}: {r['status']}")
                continue
            extra = ""
            if "assumed_date" in r:
                extra = f" -> {r['assumed_date']} ({r.get('daily', '?'):+})" if isinstance(r.get("daily"), int) else f" -> {r['assumed_date']}"
            relabel = f"  [relabel existing -> {r['relabel_existing_to']}]" if r.get("relabel_existing_to") else ""
            print(f"[{i}/{total}] {name}: {r['status']}{extra}{relabel}")
    finally:
        out_file.close()

    print()
    print("Summary:", by_status)
    print(f"RapidAPI calls spent: {args.rapidapi_budget - budget.remaining}/{args.rapidapi_budget}")
    print(f"Report written incrementally to {out_path}")

    # Combine this run's results with rows resumed from a prior run so
    # --apply / --relabel-track / the relabel-candidate listing below see
    # the full picture, not just what was (re-)computed just now.
    all_rows: list[dict] = list(results)
    for track_id, row in already_done.items():
        if track_id in {r["track_id"] for r in results}:
            continue
        all_rows.append(_typed_row_from_csv(row))

    relabel_candidates = [r for r in all_rows if r.get("relabel_existing_to")]
    if relabel_candidates:
        print(f"\n{len(relabel_candidates)} existing row(s) look mis-dated but were NOT touched "
              f"(relabeling is never automatic — a real value already on record, including anything "
              f"manually injected, is never moved or overwritten without explicit review):")
        for r in relabel_candidates:
            name = labels.get(r["track_id"], r["track_id"])
            print(f"  - {name}: {args.date} total {r['existing_total']} looks like it belongs to "
                  f"{r['relabel_existing_to']} instead (use --relabel-track {r['track_id']} to move it).")

    if args.relabel_track:
        match = next((r for r in all_rows if r["track_id"] == args.relabel_track), None)
        if match and match.get("relabel_existing_to"):
            _relabel_row(
                match["track_id"], args.date, match["relabel_existing_to"].isoformat(),
                match["existing_total"], match["existing_daily"],
            )
        else:
            print(f"[warn] {args.relabel_track} has no pending relabel candidate — nothing done.")

    if args.apply:
        applied = 0
        for r in all_rows:
            if r["status"] not in ("single_day", "fully_caught_up", "partial_catchup", "canary_confirmed"):
                continue
            history_store.append_history_row([
                r["assumed_date"].isoformat(), r["track_id"], r["live_total"], r["daily"], "", "",
            ])
            applied += 1
        print(f"Applied {applied} new row(s). No existing row was modified or moved.")

    if args.apply:
        applied = 0
        for r in results:
            if r["status"] not in ("single_day", "fully_caught_up", "partial_catchup"):
                continue
            history_store.append_history_row([
                r["assumed_date"].isoformat(), r["track_id"], r["live_total"], r["daily"], "", "",
            ])
            applied += 1
        print(f"Applied {applied} new row(s). No existing row was modified or moved.")


if __name__ == "__main__":
    main()
