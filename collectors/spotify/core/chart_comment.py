#!/usr/bin/env python3
"""Commentaire de chart partage entre toutes les regions Spotify (Global, FR, US, UK, ...).

Choisit un seul commentaire pour accompagner le tweet quotidien, par ordre de priorite :
1. une chanson entre ou revient dans le chart (NEW / RE-ENTRY)
2. une chanson fait un gros bond de classement (delta >= jump_threshold)
3. le plus gros gain de streams (%) du jour (biggest gainer)
4. une chanson au classement inchange (la plus stable)

En cas d'egalite dans une meme categorie, le choix se fait au hasard parmi les candidats.
"""
from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from core.data_paths import first_existing, legacy_spotify_chart_dir, spotify_chart_dir
from core.history import load as load_ts_history

JUMP_THRESHOLD = 15


def _sentence(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return text
    if text[-1] in ".!?":
        return text
    return f"{text}."


def _streams_suffix(streams) -> str:
    value = _to_int(streams)
    if value is None:
        return ""
    return f" with {value:,} streams"


def _to_int(value) -> int | None:
    try:
        if value is None:
            return None
        s = str(value).strip()
        if not s or s.lower() == "nan":
            return None
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _chart_csv_path(chart_name: str, d: date) -> Path:
    return first_existing(
        spotify_chart_dir(chart_name, d) / "ts_all_songs.csv",
        legacy_spotify_chart_dir(chart_name, d) / "ts_all_songs.csv",
    )


def _chart_json_path(chart_name: str, d: date) -> Path:
    return first_existing(
        spotify_chart_dir(chart_name, d) / f"ts_chart_{d}.json",
        legacy_spotify_chart_dir(chart_name, d) / f"ts_chart_{d}.json",
    )


def _load_ts_rows(chart_name: str, d: date) -> list[dict]:
    path = _chart_csv_path(chart_name, d)
    if path.exists():
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    # En mode --post-only, les regions n'ont pas de ts_all_songs.csv :
    # les donnees viennent de worldwide via ts_chart_{date}.json (memes champs).
    json_path = _chart_json_path(chart_name, d)
    if json_path.exists():
        try:
            rows = json.loads(json_path.read_text(encoding="utf-8-sig"))
            return [row for row in rows if isinstance(row, dict)]
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _daily_streams_pct(track: str, streams, d: date, ts_history: dict) -> float | None:
    entries = ts_history.get(track, {})
    yesterday = str(d - timedelta(days=1))
    prev = _to_int(entries.get(yesterday, {}).get("streams"))
    cur = _to_int(streams)
    if not prev or not cur:
        return None
    return (cur - prev) / prev * 100


def _top_tier_label(rank: int) -> str | None:
    if rank <= 10:
        return "top 10"
    if rank <= 50:
        return "top 50"
    if rank <= 100:
        return "top 100"
    return None


def build_chart_comment(
    chart_name: str,
    d: date,
    ts_history_path: Path,
    *,
    jump_threshold: int = JUMP_THRESHOLD,
) -> str | None:
    rows = _load_ts_rows(chart_name, d)
    if not rows:
        return None

    ts_history = load_ts_history(ts_history_path)

    new_entries: list[tuple[str, int, int | None]] = []
    re_entries: list[tuple[str, int, int | None]] = []
    jumps: list[tuple[str, int, int, float | None]] = []
    gainers: list[tuple[str, int, float]] = []
    stable: list[tuple[str, int, int]] = []

    for row in rows:
        track = str(row.get("track_name") or "").strip()
        rank = _to_int(row.get("rank"))
        if not track or rank is None:
            continue

        prev_rank = _to_int(row.get("previous_rank"))
        streams = _to_int(row.get("streams"))
        total_days = _to_int(row.get("total_days")) or 0
        pct = _daily_streams_pct(track, row.get("streams"), d, ts_history)

        if prev_rank is None or prev_rank <= 0:
            if total_days > 1:
                re_entries.append((track, rank, streams))
            else:
                new_entries.append((track, rank, streams))
            continue

        delta = prev_rank - rank
        if delta >= jump_threshold:
            jumps.append((track, rank, delta, pct))
        if pct is not None and pct > 0:
            gainers.append((track, rank, pct))
        if delta == 0:
            stable.append((track, rank, total_days))

    if re_entries or new_entries:
        track, rank, streams = random.choice(re_entries + new_entries)
        if (track, rank, streams) in re_entries:
            tier = _top_tier_label(rank)
            if tier:
                return _sentence(f'"{track}" re-enters the {tier} at #{rank}{_streams_suffix(streams)}')
            return _sentence(f'"{track}" re-enters the chart at #{rank}{_streams_suffix(streams)}')
        return _sentence(f'"{track}" joins the chart at #{rank}{_streams_suffix(streams)}')

    if jumps:
        track, rank, _delta, pct = max(jumps, key=lambda x: x[2])
        if pct is not None:
            return _sentence(f'"{track}" jumped to the #{rank} spot, up {pct:+.1f}%')
        return _sentence(f'"{track}" jumped to the #{rank} spot')

    if gainers:
        track, rank, pct = max(gainers, key=lambda x: x[2])
        return _sentence(f'"{track}" is the biggest gainer today, up {pct:+.1f}% in streams')

    if stable:
        track, rank, _total_days = max(stable, key=lambda x: x[2])
        return _sentence(f'"{track}" remains steady at #{rank}')

    return None
