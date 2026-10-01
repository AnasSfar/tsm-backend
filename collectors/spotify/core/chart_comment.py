#!/usr/bin/env python3
"""Commentaire de chart partage entre les regions Spotify (Global, US, UK).

Raconte le chart du jour avec l'historique complet `db/charts_history_<chart>.csv`
au lieu de la seule veille (owner 2026-10-01 : « remains steady at » / « jumped to
the X spot » trop courts) :

- jours consecutifs / au total a #1 (« spends its 3rd consecutive day at #1 ») ;
- retour a #1, nouveau peak (avec l'ancien peak) ;
- RE raconte comme un retour : jours d'absence, derniere date/position ;
- gros bond avec la meilleure position depuis <date> ;
- paliers de jours sur le chart (100e, 365e, 1 000e...) ;
- plus gros gain de streams, sinon la plus longue serie en cours.

Chaque candidat a un score ; on garde les 2 meilleurs (2 chansons differentes).
Valeurs exactes uniquement : un fait sans donnee source n'est pas ecrit.
L'historique est relie par titre (les lignes d'avant ~2025-09 n'ont pas de
track_id, cf. skill song-posting).
"""
from __future__ import annotations

import csv
import json
import random
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

from core.data_paths import (
    first_existing,
    first_existing_db_history,
    legacy_spotify_chart_dir,
    spotify_chart_dir,
)
from core.history import load as load_ts_history

JUMP_THRESHOLD = 15
MAX_COMMENTS = 2
DAY_MILESTONES = {50, 100, 150, 200, 250, 300, 365, 400, 500, 600, 700, 730, 750, 800, 900}
CHART_LABELS = {"global": "Global", "us": "US", "uk": "UK", "fr": "France"}


def _sentence(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return text
    if text[-1] in ".!?":
        return text
    return f"{text}."


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


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


def _fmt_day(d: date) -> str:
    """Song-posting date format: Monday (Jun 5, 2027)."""
    return f"{d.strftime('%A')} ({d.strftime('%b')} {d.day}, {d.year})"


def _title_key(title: str) -> str:
    text = unicodedata.normalize("NFKC", str(title or ""))
    text = text.replace("’", "'").replace("‘", "'")
    return " ".join(text.casefold().split())


def _is_day_milestone(days: int) -> bool:
    return days in DAY_MILESTONES or (days >= 1000 and days % 250 == 0) or (days > 365 and days % 365 == 0)


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


def _load_chart_history(chart_name: str, d: date) -> tuple[dict[str, dict[date, int]], set[date]]:
    """({title_key: {date: rank}}, every chart date present) for days strictly before d."""
    path = first_existing_db_history(f"charts_history_{chart_name}.csv")
    history: dict[str, dict[date, int]] = {}
    chart_dates: set[date] = set()
    if not path.exists():
        return history, chart_dates
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                day = datetime.strptime((row.get("date") or "").strip(), "%Y-%m-%d").date()
            except ValueError:
                continue
            rank = _to_int(row.get("rank"))
            key = _title_key(row.get("song_name") or row.get("track_name") or "")
            if day >= d or rank is None or not key:
                continue
            chart_dates.add(day)
            history.setdefault(key, {})[day] = rank
    return history, chart_dates


def _daily_streams_pct(track: str, streams, d: date, ts_history: dict) -> float | None:
    entries = ts_history.get(track, {})
    yesterday = str(d - timedelta(days=1))
    prev = _to_int(entries.get(yesterday, {}).get("streams"))
    cur = _to_int(streams)
    if not prev or not cur:
        return None
    return (cur - prev) / prev * 100


def _consecutive_days(past: dict[date, int], d: date, predicate, chart_dates: set[date]) -> int | None:
    """Days in a row ending today (today counted) where predicate(rank) holds.

    None when the run reaches a date missing from the history CSV (unknown, not
    a break) — never claim a run we cannot verify.
    """
    count = 1
    day = d - timedelta(days=1)
    while True:
        if day not in chart_dates:
            return None if chart_dates and day >= min(chart_dates) else count
        if day not in past or not predicate(past[day]):
            return count
        count += 1
        day -= timedelta(days=1)


def _history_complete(chart_dates: set[date], start: date, end: date) -> bool:
    """Every day strictly between start and end has chart rows."""
    day = start + timedelta(days=1)
    while day < end:
        if day not in chart_dates:
            return False
        day += timedelta(days=1)
    return True


def _best_since(past: dict[date, int], d: date, rank: int) -> date | None:
    """Most recent earlier day with a rank at least as good (None = never)."""
    better = [day for day, r in past.items() if r <= rank and day < d]
    return max(better) if better else None


def _top_tier_label(rank: int) -> str | None:
    if rank <= 10:
        return "top 10"
    if rank <= 50:
        return "top 50"
    if rank <= 100:
        return "top 100"
    return None


def _candidates(
    chart_name: str,
    d: date,
    rows: list[dict],
    ts_history: dict,
    history: dict,
    chart_dates: set[date],
    jump_threshold: int,
) -> list[tuple[float, str, str]]:
    chart_label = CHART_LABELS.get(chart_name, chart_name.upper())
    out: list[tuple[float, str, str]] = []
    gainers: list[tuple[float, str, int, int | None]] = []
    debuts: list[tuple[int, str, int | None]] = []
    re_entries: list[tuple[int, str]] = []

    for row in rows:
        track = str(row.get("track_name") or "").strip()
        rank = _to_int(row.get("rank"))
        if not track or rank is None:
            continue
        prev_rank = _to_int(row.get("previous_rank"))
        streams = _to_int(row.get("streams"))
        total_days = _to_int(row.get("total_days"))
        past = history.get(_title_key(track), {})
        prior_peak = min(past.values()) if past else None
        streams_txt = f" with {streams:,} streams" if streams is not None else ""
        pct = _daily_streams_pct(track, row.get("streams"), d, ts_history)

        # Entering the chart today.
        if prev_rank is None or prev_rank <= 0:
            if not past:
                debuts.append((rank, track, streams))
                continue
            last_day = max(past)
            gap = (d - last_day).days
            last_rank = past[last_day]
            tier = _top_tier_label(rank)
            where = f"the {tier}" if tier else "the chart"
            text = f'"{track}" jumps back into {where} at #{rank}{streams_txt}'
            if _history_complete(chart_dates, last_day, d):
                text += f". It last charted {gap:,} days ago, at #{last_rank} on {_fmt_day(last_day)}"
            if prior_peak is not None and rank < prior_peak:
                text += f", and it's a new peak (previously #{prior_peak})"
            re_entries.append((rank, track))
            out.append((70 + (201 - rank) / 4 + min(gap, 365) / 15, track, text))
            continue

        delta = prev_rank - rank

        # #1 storylines.
        if rank == 1:
            run = _consecutive_days(past, d, lambda r: r == 1, chart_dates)
            total_at_1 = 1 + sum(1 for r in past.values() if r == 1)
            if run is None:
                pass
            elif run >= 2:
                text = f'"{track}" spends its {_ordinal(run)} consecutive day at #1 on the {chart_label} chart'
                if total_at_1 > run:
                    text += f" ({total_at_1:,} days at #1 in total)"
                out.append((90 + run, track, text))
            elif total_at_1 > 1:
                out.append((115, track, f'"{track}" returns to #1 on the {chart_label} chart, its {_ordinal(total_at_1)} day at the top{streams_txt}'))
            else:
                out.append((180, track, f'"{track}" reaches #1 on the {chart_label} chart for the first time{streams_txt}'))
            continue

        # New peak (beats every earlier day).
        if prior_peak is not None and rank < prior_peak:
            text = f'"{track}" hits a new peak of #{rank}, up from #{prev_rank}'
            if prior_peak != prev_rank:
                text += f" (previous peak #{prior_peak})"
            out.append((95 + (201 - rank) / 4, track, text))
            continue

        # Big jump, with the best position since <date>.
        if delta >= jump_threshold:
            since = _best_since(past, d - timedelta(days=1), rank)
            pct_txt = f" [{pct:+.1f}% streams]" if pct is not None else ""
            text = f'"{track}" leaps {delta} spots from #{prev_rank} to #{rank}{pct_txt}'
            score = 50 + delta / 2
            if since is not None and (d - since).days >= 14 and _history_complete(chart_dates, since, d):
                text += f", its highest position since {_fmt_day(since)}"
                score += min((d - since).days, 365) / 6
            out.append((score, track, text))
            continue

        # Day milestones on the chart.
        if total_days is not None and _is_day_milestone(total_days):
            out.append((70 + total_days / 100, track, f'"{track}" marks its {_ordinal(total_days)} day on the {chart_label} chart, currently at #{rank}'))
            continue

        # Long runs inside the top 10.
        if rank <= 10:
            run10 = _consecutive_days(past, d, lambda r: r <= 10, chart_dates)
            if run10 is not None and run10 >= 5:
                out.append((35 + run10 / 2, track, f'"{track}" holds at #{rank}, its {_ordinal(run10)} consecutive day in the top 10'))

        if pct is not None and pct >= 5:
            gainers.append((pct, track, rank, streams))

    if debuts:
        debuts.sort()
        rank, track, streams = debuts[0]
        streams_txt = f" with {streams:,} streams" if streams is not None else ""
        if len(debuts) >= 3:
            out.append((
                200,
                track,
                f'Taylor Swift debuts {len(debuts)} new songs on the {chart_label} chart, '
                f'led by "{track}" at #{rank}{streams_txt}',
            ))
        else:
            for rank, track, streams in debuts:
                streams_txt = f" with {streams:,} streams" if streams is not None else ""
                out.append((100 + (201 - rank) / 2, track, f'"{track}" debuts on the {chart_label} chart at #{rank}{streams_txt}'))

    if len(re_entries) >= 3:
        re_entries.sort()
        rank, track = re_entries[0]
        out = [c for c in out if c[1] not in {t for _r, t in re_entries}]
        out.append((
            150,
            track,
            f'{len(re_entries)} Taylor Swift songs re-enter the {chart_label} chart, led by "{track}" at #{rank}',
        ))

    if gainers:
        pct, track, rank, streams = max(gainers)
        streams_txt = f" to {streams:,} streams" if streams is not None else ""
        out.append((30 + pct, track, f'"{track}" posts the biggest streaming gain of the day, up {pct:.1f}%{streams_txt} at #{rank}'))

    if not out:
        # Fallback: the longest current run on the chart.
        runs = []
        for row in rows:
            track = str(row.get("track_name") or "").strip()
            rank = _to_int(row.get("rank"))
            if not track or rank is None:
                continue
            run = _to_int(row.get("streak")) or _consecutive_days(
                history.get(_title_key(track), {}), d, lambda r: True, chart_dates
            )
            if run:
                runs.append((run, track, rank))
        if runs:
            run, track, rank = max(runs)
            if run >= 2:
                out.append((1, track, f'"{track}" extends its {chart_label} chart run to {run:,} consecutive days, at #{rank}'))
    return out


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
    history, chart_dates = _load_chart_history(chart_name, d)
    candidates = _candidates(chart_name, d, rows, ts_history, history, chart_dates, jump_threshold)
    if not candidates:
        return None

    random.shuffle(candidates)  # ties broken at random
    candidates.sort(key=lambda c: c[0], reverse=True)
    picked: list[str] = []
    seen_tracks: set[str] = set()
    for _score, track, text in candidates:
        if track in seen_tracks:
            continue
        seen_tracks.add(track)
        picked.append(_sentence(text))
        if len(picked) >= MAX_COMMENTS:
            break
    return "\n\n".join(picked)
