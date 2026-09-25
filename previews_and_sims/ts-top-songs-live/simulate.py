"""Sim for collectors/apple_music/ts_top_songs_live.py + export's ts_top_songs_live.

Reads REAL raw cycles (read-only) and writes only inside this folder:
1. Identity check: a finished day's live ranking over ALL its cycles must equal
   the real finalized canonical CSV (same function, same inputs).
2. Progression: today's live ranking after 1, 2, ... N cycles (top 10 each).
3. Export shape: build_top_songs_live() fed from a live CSV written here.

Usage: python previews_and_sims/ts-top-songs-live/simulate.py [--done-day YYYY-MM-DD] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from collectors.apple_music import ts_top_songs_live as live  # noqa: E402
from collectors.apple_music.core.csv_utils import write_csv_rows  # noqa: E402
from collectors.apple_music.finalize_ts_top_songs_daily import FILENAME, RAW_FILENAME, _previous_final_rows, _read_day_rows  # noqa: E402
from collectors.spotify.core.data_paths import apple_music_charts_dir  # noqa: E402

OUT = HERE / "out"


def identity_check(day: str) -> bool:
    cycles = _read_day_rows(apple_music_charts_dir(day) / RAW_FILENAME)
    final = _read_day_rows(apple_music_charts_dir(day) / FILENAME)
    if not cycles or not final:
        print(f"[identity] {day}: missing raw ({len(cycles)}) or final ({len(final)}) rows, skipped")
        return True
    rows = live.build_live_rows(day, cycles, _previous_final_rows(day))
    fields = [f for f in live.LIVE_FIELDNAMES if f not in ("live_cycles", "compared_to_day")]
    a = [{f: str(r.get(f, "")) for f in fields} for r in rows]
    b = [{f: str(r.get(f, "")) for f in fields} for r in final]
    ok = a == b
    print(f"[identity] {day}: live over {rows[0]['live_cycles']} cycles vs final ({len(final)} rows): {'IDENTICAL' if ok else 'DIFF'}")
    if not ok:
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                print("  first diff at", i, {k: (x[k], y[k]) for k in fields if x[k] != y[k]})
                break
    return ok


def progression(day: str) -> Path | None:
    cycles = _read_day_rows(apple_music_charts_dir(day) / RAW_FILENAME)
    if not cycles:
        print(f"[progression] {day}: no raw cycles")
        return None
    prev = _previous_final_rows(day)
    stamps = sorted({r["scraped_at"] for r in cycles if r.get("scraped_at")})
    lines = []
    rows = []
    for n in range(1, len(stamps) + 1):
        subset = [r for r in cycles if r.get("scraped_at") in set(stamps[:n])]
        rows = live.build_live_rows(day, subset, prev)
        top = " | ".join(f"{r['rank']}. {r['song_name']} ({r['previous_rank'] or 'new'})" for r in rows[:10])
        lines.append(f"after {n:>2} cycle(s) [{stamps[n-1][11:16]}]: {top}")
    (OUT / "progression.log").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:3] + ["..."] + lines[-2:]))
    live_path = OUT / day / live.LIVE_FILENAME
    write_csv_rows(live_path, live.LIVE_FIELDNAMES, rows)
    return live_path


def export_shape(day: str) -> None:
    spec = importlib.util.spec_from_file_location("exp", ROOT / "scripts" / "export_apple_music.py")
    exp = importlib.util.module_from_spec(spec)
    sys.argv = ["export_apple_music.py"]
    spec.loader.exec_module(exp)
    exp.apple_music_charts_dir = lambda d: OUT / str(d)[:10]  # sim folder only
    top_rows = exp.read_csv_rows(exp.TOP_SONGS_CSV)
    _cur, top_history, top_dates = exp.build_top_songs(top_rows)
    payload = exp.build_top_songs_live(day, top_history, top_dates)
    (OUT / "ts_top_songs_live.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if payload:
        e = payload["entries"]
        print(f"[export] date={payload['date']} scraped_at={payload['scraped_at']} cycles={payload['cycles']} "
              f"vs={payload['compared_to_day']} final_date={payload['final_date']} entries={len(e)} new={sum(1 for x in e if x.get('previous_rank') is None and not x.get('is_reentry'))} "
              f"reentry={sum(1 for x in e if x.get('is_reentry'))}")
        print("  first entry keys:", sorted(e[0].keys()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--done-day", default=None)
    args = ap.parse_args()
    done_day = args.done_day or (date.fromisoformat(args.today) - timedelta(days=1)).isoformat()
    OUT.mkdir(exist_ok=True)
    identity_check(done_day)
    if progression(args.today):
        export_shape(args.today)


if __name__ == "__main__":
    main()
