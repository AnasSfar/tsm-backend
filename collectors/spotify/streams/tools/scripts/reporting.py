from __future__ import annotations

import time

from run_logs import save_failed_rows, save_last_successful_updates_json, save_last_unfinished_updates_json, save_pending_debug_rows


def format_int(value: int | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,}".replace(",", " ")

class ProgressLogger:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.start = time.perf_counter()
        self.done = 0
        self.counts = {
            "updated": 0,
            "pending": 0,
            "skipped": 0,
            "timeout": 0,
            "error": 0,
            "not_found": 0,
        }
        self._last_summary_t = 0.0

    def _eta(self, total: int) -> str:
        elapsed = time.perf_counter() - self.start
        rate = (self.done / elapsed) if elapsed > 0 and self.done > 0 else 0
        remaining = (total - self.done) / rate if rate > 0 else 0
        return f"{int(remaining // 60)}m {int(remaining % 60)}s"

    def __call__(self, i: int, total: int, title: str, result: dict | None):
        # Called twice per track (start + finish). Keep output compact.
        if result is None:
            if self.mode == "verbose":
                print(f"[{i}/{total}] {title} ... scraping | ETA {self._eta(total)}")
            return

        status = (result.get("status") or "").strip()
        self.done += 1
        if status in self.counts:
            self.counts[status] += 1

        eta = self._eta(total)
        prefix = f"[{self.done}/{total}]"

        if self.mode == "verbose":
            # Keep the previous detailed format.
            if status == "updated":
                print(
                    f"[{i}/{total}] {title} OK | total={format_int(result.get('streams'))} | "
                    f"daily={format_int(result.get('daily_streams'))} | ETA {eta}"
                )
            elif status == "pending":
                print(
                    f"[{i}/{total}] {title} PENDING | total={format_int(result.get('streams'))} | "
                    f"prev={format_int(result.get('previous_streams'))} | "
                    f"delta={format_int(result.get('delta'))} | "
                    f"reason={result.get('reason')} | ETA {eta}"
                )
            else:
                print(f"[{i}/{total}] {title} {status.upper()} | ETA {eta}")
            return

        # quiet/normal: only print errors + periodic summaries
        important = status in {"timeout", "error", "not_found"}
        now = time.perf_counter()
        should_summary = (
            self.done == total
            or important
            or (now - self._last_summary_t) >= (60 if self.mode == "quiet" else 30)
            or (self.done % (50 if self.mode == "quiet" else 25) == 0)
        )

        if status == "pending":
            print(
                f"{prefix} PENDING | {title} | "
                f"total={format_int(result.get('streams'))} prev={format_int(result.get('previous_streams'))} "
                f"reason={result.get('reason')}"
            )

        if important:
            print(f"{prefix} {status.upper()} | {title} | ETA {eta}")

        if should_summary:
            self._last_summary_t = now
            print(
                f"{prefix} ETA {eta} | "
                f"updated={self.counts['updated']} pending={self.counts['pending']} "
                f"nf={self.counts['not_found']} to={self.counts['timeout']} err={self.counts['error']}"
            )

def print_remaining_details(summary: dict) -> None:
    print()
    print("Tracks still not done for this stats date:")

    remaining_details = []

    for r in summary["results"]:
        if r["status"] == "pending":
            remaining_details.append(
                f"PENDING | {r['title']} | {r.get('track_id', '')} | "
                f"total={format_int(r.get('streams'))} | "
                f"prev={format_int(r.get('previous_streams'))} | "
                f"delta={format_int(r.get('delta'))} | "
                f"reason={r.get('reason')}"
            )

    for r in summary["failed_results"]:
        if r["status"] in {"not_found", "timeout", "error"}:
            remaining_details.append(
                f"{r['status'].upper()} | {r['title']} | "
                f"{r.get('track_id', '')} | {r.get('spotify_url', '')}"
            )

    if remaining_details:
        for line in remaining_details:
            print(line)
    else:
        print("None.")

def print_summary_block(summary: dict) -> None:
    print()
    print("=" * 70)
    print(f"Progress {summary['stats_date']}")
    print("=" * 70)
    print(f"  Total tracks:      {summary['total_tracks']}")
    print(f"  Updated this run:  {summary['updated_this_run']}")
    print(f"  Pending:           {summary['pending_this_run']}")
    print(f"  Not found:         {summary['not_found_this_run']}")
    print(f"  Timeout:           {summary['timeout_this_run']}")
    print(f"  Error:             {summary['error_this_run']}")
    print("=" * 70)
    print()

def update_json_logs_from_summary(summary: dict) -> None:
    updated_results = [r for r in summary.get("results", []) if r and r.get("status") == "updated"]
    save_last_successful_updates_json(summary["stats_date"], updated_results)
    save_last_unfinished_updates_json(summary["stats_date"], summary.get("results", []), summary.get("failed_results", []))

    pending_debug_rows = [
        {
            "title": r.get("title"),
            "track_id": r.get("track_id"),
            "spotify_url": r.get("spotify_url"),
            "previous_streams": r.get("previous_streams"),
            "new_streams": r.get("streams"),
            "delta": r.get("delta"),
            "reason": r.get("reason"),
            "raw": r.get("raw"),
        }
        for r in summary.get("results", [])
        if r and r.get("status") == "pending"
    ]
    save_pending_debug_rows(pending_debug_rows)
    save_failed_rows(summary.get("failed_results", []))
