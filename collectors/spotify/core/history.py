#!/usr/bin/env python3
"""Gestion de ts_history.json — partagé Fr + Global."""
import json
from pathlib import Path
from datetime import datetime


TS_HISTORY_FILE = "ts_history.json"


def parse_date(s: str):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except Exception:
        return None


def load(path: Path = None) -> dict:
    p = path or Path(TS_HISTORY_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save(history: dict, path: Path = None):
    p = path or Path(TS_HISTORY_FILE)
    p.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def update(history: dict, track: str, chart_date: str, rank: int, streams,
           previous_rank=None, peak_rank=None):
    if track not in history:
        history[track] = {}
    try:
        streams_int = int(streams)
    except (TypeError, ValueError):
        streams_int = 0
    entry = {"rank": rank, "streams": streams_int}
    if previous_rank is not None:
        try:
            v = int(float(previous_rank))
            if v > 0:
                entry["previous_rank"] = v
        except (TypeError, ValueError):
            pass
    if peak_rank is not None:
        try:
            v = int(float(peak_rank))
            if v > 0:
                entry["peak_rank"] = v
        except (TypeError, ValueError):
            pass
    history[track][chart_date] = entry


def get_best_day(history: dict, track: str, current_date: str):
    entries = history.get(track, {})
    if not entries:
        return None, None, None, None

    current_entry = entries.get(current_date, {})
    current_streams = current_entry.get("streams", 0)
    current_rank = current_entry.get("rank")
    past_dates = sorted([d for d in entries if d < current_date], reverse=True)

    best_streams_date = best_streams = None
    for d in past_dates:
        s = entries[d].get("streams", 0)
        if s > current_streams:
            best_streams_date, best_streams = d, s
            break
    if best_streams_date is None and current_streams:
        best_streams_date, best_streams = current_date, current_streams

    best_rank_date = best_rank = None
    if current_rank is not None:
        for d in past_dates:
            r = entries[d].get("rank")
            if r is not None and r < current_rank:
                best_rank_date, best_rank = d, r
                break
    if best_rank_date is None and current_rank is not None:
        best_rank_date, best_rank = current_date, current_rank

    return best_rank_date, best_rank, best_streams_date, best_streams


def rebuild_from_csvs(root: Path, chart_id_prefix: str) -> dict:
    """Reconstruit ts_history depuis tous les ts_all_songs.csv dans root."""
    import csv
    history = {}
    files = sorted(root.rglob("ts_all_songs.csv"))
    print(f"Trouvé {len(files)} fichiers ts_all_songs.csv")

    for csv_path in files:
        chart_date = csv_path.parent.name
        if not parse_date(chart_date):
            print(f"  ⚠  Date invalide : {csv_path.parent} — ignoré")
            continue
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception as e:
            print(f"  ✗ {csv_path} : {e}")
            continue

        for row in rows:
            if "Taylor Swift" not in row.get("artist_names", ""):
                continue
            track = row.get("track_name", "").strip()
            if not track:
                continue
            try:
                rank = int(row.get("rank", 0))
            except (ValueError, TypeError):
                continue
            update(
                history, track, chart_date, rank,
                row.get("streams"),
                previous_rank=row.get("previous_rank"),
                peak_rank=row.get("peak_rank"),
            )

    return history
