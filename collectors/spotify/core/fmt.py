#!/usr/bin/env python3
"""Fonctions de formatage partagées Fr + Global."""
from datetime import timedelta
from core.history import get_best_day, parse_date


def fmt_streams(s) -> str:
    try:
        return f"{int(s):,}".replace(",", " ")
    except Exception:
        return "—"


def fmt_delta(rank, previous_rank, peak_rank=None, total_days=None) -> str:
    """Retourne NEW / RE / +3 / -2 / 0"""
    try:
        prev = int(previous_rank)
    except (TypeError, ValueError):
        prev = 0
    if prev <= 0:
        try:
            if total_days and int(total_days) > 0:
                return "RE"
        except (TypeError, ValueError):
            pass
        try:
            if int(peak_rank) > 0 and int(peak_rank) != int(rank):
                return "RE"
        except (TypeError, ValueError):
            pass
        return "NEW"
    d = prev - int(rank)
    if d > 0:
        return f"+{d}"
    if d < 0:
        return f"-{abs(d)}"
    return "0"


def fmt_streams_delta(track_name: str, current_streams, chart_date: str, ts_history: dict) -> str:
    try:
        entries = ts_history.get(track_name, {})
        yesterday = str(parse_date(chart_date) - timedelta(days=1))
        prev = entries.get(yesterday, {}).get("streams")

        if not prev or not current_streams or prev == 0:
            return ""

        current = int(current_streams)
        previous = int(prev)

        pct = (current - previous) / previous * 100

        return f"{pct:+.2f}%"

    except Exception:
        return ""

def fmt_best_inline(track_name: str, current_streams, chart_date: str, ts_history: dict) -> str:
    br_date, br_val, bs_date, bs_val = get_best_day(ts_history, track_name, chart_date)
    parts = []

    def older_than_week(d):
        try:
            return (parse_date(chart_date) - parse_date(d)).days > 7
        except Exception:
            return False

    if bs_val is not None:
        if bs_date == chart_date:
            parts.append("best streams 🏆")
        elif older_than_week(bs_date):
            parts.append(f"best streams since {bs_date} ({fmt_streams(bs_val)})")

    if br_val is not None:
        if br_date == chart_date:
            parts.append("best position 🏆")
        elif older_than_week(br_date):
            parts.append(f"best position since {br_date} (#{br_val})")

    return " | ".join(parts) if parts else ""
