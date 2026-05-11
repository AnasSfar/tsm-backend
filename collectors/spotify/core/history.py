#!/usr/bin/env python3
"""Gestion de ts_history.json — partagé Fr + Global."""
import json
import re
from pathlib import Path
from datetime import datetime


TS_HISTORY_FILE = "ts_history.json"


def _to_int(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value

    raw = str(value).strip()
    if not raw or raw.lower() == "nan":
        return None

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    return int(digits)


def parse_date(s: str):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except ValueError:
        return None


def load(path: Path = None) -> dict:
    p = path or Path(TS_HISTORY_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            print(f"[history] JSON invalide, fichier ignoré: {p}")
            return {}
    return {}


def save(history: dict, path: Path = None):
    p = path or Path(TS_HISTORY_FILE)
    p.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def update(history: dict, track: str, chart_date: str, rank: int, streams,
           previous_rank=None, peak_rank=None):
    if track not in history:
        history[track] = {}
    streams_int = _to_int(streams) or 0
    entry = {"rank": rank, "streams": streams_int}
    if previous_rank is not None:
        v = _to_int(previous_rank)
        if v and v > 0:
            entry["previous_rank"] = v
    if peak_rank is not None:
        v = _to_int(peak_rank)
        if v and v > 0:
            entry["peak_rank"] = v
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
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
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


def calculate_total_days(history: dict, track: str, chart_date: str) -> int:
    """Compte le nombre UNIQUE de jours où la chanson a été sur le chart.
    
    DAYS = total de tous les jours uniques où la chanson a charté,
           peu importe les interruptions/sorties/rentrées.
    """
    if track not in history:
        return 0
    entries = history[track]
    # Compte tous les jours uniques <= chart_date
    count = sum(1 for d in entries if d <= chart_date)
    return count


def calculate_streak(history: dict, track: str, chart_date: str) -> int:
    """Calcule le nombre CONSÉCUTIF de jours depuis la dernière apparition.
    
    STREAK = jours consécutifs en remontant depuis chart_date.
             Reset à 0 si la chanson n'y était pas le jour d'avant.
    """
    from datetime import timedelta
    
    if track not in history:
        return 0
    
    entries = history[track]
    current_date = parse_date(chart_date)
    
    if current_date is None:
        return 0
    
    # Si la chanson n'est pas dans le chart aujourd'hui, streak = 0
    if chart_date not in entries:
        return 0
    
    # Compter consécutif en remontant
    streak = 0
    check_date = current_date
    while True:
        check_date_str = str(check_date)
        if check_date_str in entries:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break
    
    return streak
