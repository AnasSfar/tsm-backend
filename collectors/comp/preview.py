#!/usr/bin/env python3
"""
Generate preview galleries for Spotify collectors.

Default mode builds collectors/comp/previews/spotify/{streams,charts}/<tool>/
with copied PNGs, HTML inspection pages, command/stdout logs, and tweet.txt.

Legacy component previews for song_card.py, chart_card.py and tables_image.py
are still available with --components.

  song_card:
    - default_not_best_short / default_not_best_long
    - best_since_solo_short / best_since_solo_long
    - best_since_combined_short / best_since_combined_long
    - best_since_album

  chart_card:
    - global Spotify Charts highlight card

  tables_image:
    - streams table (Pos / Track / Daily / Total / Vs Day / Vs Week)
    - best-day-since recap table (Pos / Track / Daily / Vs Day / Vs Week / Best Since)

Usage:
  python preview.py
  python preview.py --date 2026-07-01
  python preview.py --only song-card
  python preview.py --only chart-card
  python preview.py --only tables
"""
from __future__ import annotations

import argparse
import csv
import html
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent                       # collectors/comp/
COLLECTORS_ROOT = SCRIPT_DIR.parent                                 # collectors/
REPO_ROOT = COLLECTORS_ROOT.parent                                  # repo root
DB_ROOT = REPO_ROOT / "db"
SPOTIFY_STREAMS_DIR = COLLECTORS_ROOT / "spotify" / "streams"
SPOTIFY_STREAMS_SCRIPTS_DIR = SPOTIFY_STREAMS_DIR / "tools" / "scripts"
HEADERS_DIR = SPOTIFY_STREAMS_DIR / "tools" / "headers"
OUTPUT_DIR = SCRIPT_DIR / "previews"

for _path in (COLLECTORS_ROOT, SPOTIFY_STREAMS_DIR, SPOTIFY_STREAMS_SCRIPTS_DIR):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from comp.chart_card import render_chart_card, write_chart_card_png  # noqa: E402
from comp.song_card import render_song_card, slugify, write_song_card_png  # noqa: E402
from comp.tables_image import build_table_html, render_html_to_png, url_to_data_uri  # noqa: E402
from comp.discography import build_cover_map, _norm  # noqa: E402
from comp.fmt import fmt_streams, fmt_pct, pct_cls, get_pct  # noqa: E402
import best_day_since  # noqa: E402
import spotlight  # noqa: E402


# ---------------------------------------------------------------------------
# Shared formatting helpers
# ---------------------------------------------------------------------------

def _fmt_int(value: int | None) -> str:
    return "?" if value is None else f"{int(value):,}"


def _fmt_signed_int(value: int | None) -> str:
    return "?" if value is None else f"+{int(value):,}"


def _fmt_pct(current: int | None, previous: int | None) -> str:
    if current is None or previous is None or previous <= 0:
        return "+0.0%"
    return f"{(current - previous) / previous * 100:+.1f}%"


def _badge_class(text: str) -> str:
    if text in {"+0.0%", "0.0%"}:
        return "flat"
    if text.startswith("+"):
        return "up"
    if text.startswith("-"):
        return "down"
    return "flat"


def _latest_preview_date() -> str:
    history = best_day_since.load_history()
    latest = best_day_since.latest_history_date(history)
    if latest is None:
        raise SystemExit("No streams history date found for preview generation.")
    print(f"[preview] Using latest streams date: {latest.isoformat()}", flush=True)
    return latest.isoformat()


# ---------------------------------------------------------------------------
# song_card.py cases
# ---------------------------------------------------------------------------

def _title_case(title: str) -> str:
    return "long" if len(title or "") >= 30 else "short"


def _write_song_card(
    *,
    output_dir: Path,
    keep_html: bool,
    case_slug: str,
    card_title: str,
    date_text: str,
    stats: list[dict],
    cover_url: str,
    extra: str,
    subtitle: str = "",
    best_since: bool = False,
    combined_versions: bool = False,
    badge_text: str | None = None,
) -> Path:
    html_text = render_song_card(
        title=card_title,
        eyebrow="Spotify Streams",
        subtitle=subtitle,
        stats=stats,
        cover_url=cover_url,
        footer_left="@swiftiescharts",
        footer_right=date_text,
        extra=extra,
        best_since=best_since,
        combined_versions=combined_versions,
        badge_text=badge_text,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(card_title)
    out_path = output_dir / f"song_card_{case_slug}_{slug}.png"
    html_path = output_dir / f"song_card_{case_slug}_{slug}.html"
    print(f"[preview] Writing HTML: {html_path}", flush=True)
    print(f"[preview] Rendering PNG: {out_path}", flush=True)
    return write_song_card_png(html_text, out_path, html_path, keep_html=keep_html)


def _best_since_rows_for_date(stats_date: str) -> list[dict]:
    base_tracks = best_day_since.load_tracks(include_extras=False)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    history = best_day_since.load_history()
    target = datetime.strptime(stats_date, "%Y-%m-%d").date()

    rows = []
    seen_families: set[str] = set()
    for track_id, track in base_tracks.items():
        family = (track.song_family or track_id).strip()
        if family in seen_families:
            continue
        seen_families.add(family)
        row = best_day_since.compute_best_day_since_combined(
            track,
            best_day_since.combined_tracks_for(all_tracks.get(track_id, track), all_tracks),
            history,
            target,
        )
        if row and row.get("kind") == "since" and best_day_since.passes_filters(row, min_days=1):
            rows.append(row)
    return rows


def _album_rows_for_date(stats_date: str) -> list[dict]:
    tracks = best_day_since.load_tracks(include_extras=False)
    history = best_day_since.load_history()
    target = datetime.strptime(stats_date, "%Y-%m-%d").date()
    by_album = best_day_since.load_album_track_ids(tracks)

    rows = []
    for album, track_ids in by_album.items():
        if len(track_ids) < 2:
            continue
        row = best_day_since.compute_album_best_day_since(album, track_ids, history, target)
        if row and row.get("kind") == "since" and best_day_since.passes_filters(row, min_days=1):
            rows.append(row)
    return rows


def _default_candidates_for_date(stats_date: str, best_track_ids: set[str]) -> list[dict]:
    target_day = datetime.strptime(stats_date, "%Y-%m-%d").date()
    previous_day = target_day - timedelta(days=1)
    history = best_day_since.load_history()
    tracks = spotlight.load_all_tracks()
    covers = spotlight.load_covers()
    candidates = []
    for track in tracks:
        point_by_day = {point.day: point for point in history.get(track["track_id"], [])}
        current = point_by_day.get(target_day)
        if current is None or current.total is None or current.daily is None or current.daily <= 0:
            continue
        if track["track_id"] in best_track_ids:
            continue
        previous = point_by_day.get(previous_day)
        candidates.append({
            "track": track,
            "total_today": current.total,
            "daily_today": current.daily,
            "daily_yesterday": previous.daily if previous else None,
            "cover_url": spotlight.get_cover_url(track, covers),
        })
    if not candidates and best_track_ids:
        print("[preview] No non-best-day default candidates found; allowing all real stream tracks.", flush=True)
        return _default_candidates_for_date(stats_date, set())
    return candidates


def _pick_case(candidates: list[dict], *, title_case: str) -> dict | None:
    matching = [item for item in candidates if _title_case(item["title"]) == title_case]
    return random.choice(matching) if matching else None


def _song_card_gallery(output_dir: Path, keep_html: bool, target_date: str) -> list[Path]:
    print("[preview] Building song_card gallery...", flush=True)
    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    covers = spotlight.load_covers()
    spotlight_tracks = {track["track_id"]: track for track in spotlight.load_all_tracks()}
    paths: list[Path] = []

    best_rows = _best_since_rows_for_date(target_date)
    best_track_ids = {row["track_id"] for row in best_rows}
    print(f"[preview] Found {len(best_rows)} best-day-since row(s).", flush=True)

    default_candidates = _default_candidates_for_date(target_date, best_track_ids)
    print(f"[preview] Found {len(default_candidates)} default non-best candidate(s).", flush=True)
    default_items = [
        {**candidate, "title": candidate["track"].get("title") or ""}
        for candidate in default_candidates
    ]
    for case in ("short", "long"):
        selected = _pick_case(default_items, title_case=case)
        if not selected:
            print(f"[preview] Skip default_not_best_{case}: no real candidate.", flush=True)
            continue
        track = selected["track"]
        daily = selected["daily_today"]
        pct = _fmt_pct(daily, selected["daily_yesterday"])
        paths.append(_write_song_card(
            output_dir=output_dir,
            keep_html=keep_html,
            case_slug=f"default_not_best_{case}",
            card_title=track["title"],
            date_text=date_text,
            stats=[
                {"label": "Daily Streams", "value": _fmt_signed_int(daily), "badge": pct, "badge_class": _badge_class(pct)},
                {"label": "Total Streams", "value": _fmt_int(selected["total_today"]), "badge": "Since release", "badge_class": "flat"},
            ],
            cover_url=selected["cover_url"],
            extra=track.get("album") or track.get("artist") or "",
        ))

    best_items = []
    for row in best_rows:
        track = spotlight_tracks.get(row["track_id"])
        if not track:
            continue
        title = track.get("title") or row["title"]
        best_items.append({
            "row": row,
            "track": track,
            "title": title,
            "combined": bool(row.get("combined")),
        })

    for combined in (False, True):
        combined_label = "combined" if combined else "solo"
        for case in ("short", "long"):
            matching = [
                item for item in best_items
                if item["combined"] == combined and _title_case(item["title"]) == case
            ]
            if not matching:
                print(f"[preview] Skip best_since_{combined_label}_{case}: no real candidate.", flush=True)
                continue
            item = random.choice(matching)
            row = item["row"]
            track = item["track"]
            track_ids = row.get("combined_track_ids") or [row["track_id"]]
            total_today, _total_yesterday, _daily_today, daily_yesterday, _daily_last_week = (
                spotlight.load_history_for_tracks(track_ids, target_date)
            )
            if total_today is None:
                print(f"[preview] Skip best_since_{combined_label}_{case}: missing total streams.", flush=True)
                continue
            daily = int(row["daily_streams"])
            label = best_day_since.row_label(row)
            pct = _fmt_pct(daily, daily_yesterday)
            paths.append(_write_song_card(
                output_dir=output_dir,
                keep_html=keep_html,
                case_slug=f"best_since_{combined_label}_{case}",
                card_title=item["title"],
                date_text=date_text,
                subtitle=label,
                stats=[
                    {"label": "Daily Streams", "value": _fmt_signed_int(daily), "badge": pct, "badge_class": _badge_class(pct)},
                    {"label": "Total Streams", "value": _fmt_int(total_today), "badge": "Since release", "badge_class": "flat"},
                ],
                cover_url=spotlight.get_cover_url(track, covers),
                extra=track.get("album") or row.get("album") or "",
                best_since=True,
                combined_versions=combined,
            ))

    album_rows = _album_rows_for_date(target_date)
    print(f"[preview] Found {len(album_rows)} album best-day-since row(s).", flush=True)
    if album_rows:
        row = random.choice(album_rows)
        total_today, _total_yesterday, _daily_today, daily_yesterday, _daily_last_week = (
            spotlight.load_history_for_tracks(row["track_ids"], target_date)
        )
        if total_today is None:
            print("[preview] Skip best_since_album: missing total streams.", flush=True)
        else:
            album_covers = build_cover_map(DB_ROOT / "discography" / "covers.json")
            cover_url = album_covers.get(_norm(row["album"]), "")
            daily = int(row["daily_streams"])
            label = best_day_since.row_label(row)
            pct = _fmt_pct(daily, daily_yesterday)
            paths.append(_write_song_card(
                output_dir=output_dir,
                keep_html=keep_html,
                case_slug="best_since_album",
                card_title=row["album"],
                date_text=date_text,
                subtitle=label,
                stats=[
                    {"label": "Daily Streams", "value": _fmt_signed_int(daily), "badge": pct, "badge_class": _badge_class(pct)},
                    {"label": "Total Streams", "value": _fmt_int(total_today), "badge": "Since release", "badge_class": "flat"},
                ],
                cover_url=cover_url,
                extra="",
                best_since=True,
                badge_text=f"Album - {date_text}",
            ))
    else:
        print("[preview] Skip best_since_album: no real candidate.", flush=True)

    return paths


# ---------------------------------------------------------------------------
# chart_card.py cases
# ---------------------------------------------------------------------------

CHARTS_HISTORY_GLOBAL = DB_ROOT / "charts_history_global.csv"


def _to_int(value) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _fmt_chart_int(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _fmt_chart_delta(value: int | None) -> str:
    if value is None or value == 0:
        return ""
    sign = "+" if value > 0 else "-"
    return f"{sign}{abs(value):,}"


def _fmt_chart_pct(current: int | None, previous: int | None) -> str:
    if current is None or previous is None or previous <= 0:
        return ""
    return f"{(current - previous) / previous * 100:+.1f}%"


def _delta_class(value: int | float | None) -> str:
    if value is None or value == 0:
        return "flat"
    return "up" if value > 0 else "down"


def _rank_badge_class(movement: str) -> str:
    raw = (movement or "").strip().upper()
    if raw == "NEW":
        return "new"
    if raw == "RE":
        return "re"
    if raw.startswith("+"):
        return "up"
    if raw.startswith("-"):
        return "down"
    return "flat"


def _date_pill(chart_date: str) -> str:
    dt = datetime.strptime(chart_date, "%Y-%m-%d")
    day = dt.day
    if 10 <= day % 100 <= 20:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(day % 10, "TH")
    return f"Spotify Charts · {dt.strftime('%B').upper()} {day}{suffix} {dt.year}"


def _load_global_chart_rows() -> list[dict]:
    if not CHARTS_HISTORY_GLOBAL.exists():
        print(f"[preview] Skip chart_card: missing {CHARTS_HISTORY_GLOBAL}", flush=True)
        return []
    with CHARTS_HISTORY_GLOBAL.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _same_chart_track(left: dict, right: dict) -> bool:
    left_id = str(left.get("track_id") or "").strip()
    right_id = str(right.get("track_id") or "").strip()
    if left_id and right_id:
        return left_id == right_id
    return _norm(str(left.get("song_name") or "")) == _norm(str(right.get("song_name") or ""))


def _previous_global_chart_row(row: dict, rows: list[dict]) -> dict | None:
    row_date = datetime.strptime(str(row.get("date")), "%Y-%m-%d").date()
    previous_date = (row_date - timedelta(days=1)).isoformat()
    for candidate in rows:
        if candidate.get("date") == previous_date and _same_chart_track(row, candidate):
            return candidate
    return None


def _chart_stream_delta(row: dict, previous: dict | None) -> int | None:
    streams = _to_int(row.get("streams"))
    previous_streams = _to_int(previous.get("streams")) if previous else None
    if streams is None or previous_streams is None:
        return None
    return streams - previous_streams


def _best_chart_candidate(candidates: list[tuple[dict, dict | None]]) -> tuple[dict, dict | None] | None:
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            1 if re.fullmatch(r"[A-Za-z0-9]{16,}", str(item[0].get("song_name") or "").strip()) else 0,
            0 if item[0].get("date") else 1,
            _to_int(item[0].get("rank")) or 9999,
            str(item[0].get("song_name") or ""),
        ),
    )


def _chart_preview_cases(rows: list[dict]) -> list[tuple[str, str, dict, dict | None, str | None]]:
    enriched = [(row, _previous_global_chart_row(row, rows)) for row in rows if row.get("date")]

    def movement_is(value: str) -> list[tuple[dict, dict | None]]:
        wanted = value.upper()
        return [
            item for item in enriched
            if str(item[0].get("movement") or "").strip().upper() == wanted
        ]

    def movement_prefix(prefix: str) -> list[tuple[dict, dict | None]]:
        return [
            item for item in enriched
            if str(item[0].get("movement") or "").strip().startswith(prefix)
        ]

    case_specs: list[tuple[str, str, list[tuple[dict, dict | None]], str | None]] = [
        ("new", "NEW debut", movement_is("NEW"), "NEW"),
        ("re", "RE entry", movement_is("RE"), "RE"),
        ("rank_up", "Rank up", movement_prefix("+"), None),
        ("rank_down", "Rank down", movement_prefix("-"), None),
        ("rank_flat", "Rank flat", movement_is("0"), None),
        (
            "streams_up",
            "Streams up",
            [item for item in enriched if (_chart_stream_delta(item[0], item[1]) or 0) > 0],
            None,
        ),
        (
            "streams_down",
            "Streams down",
            [item for item in enriched if (_chart_stream_delta(item[0], item[1]) or 0) < 0],
            None,
        ),
    ]

    cases: list[tuple[str, str, dict, dict | None, str | None]] = []
    used_keys: set[tuple[str, str]] = set()
    for slug, label, candidates, forced_movement in case_specs:
        selected = _best_chart_candidate([
            item for item in candidates
            if (str(item[0].get("date") or ""), str(item[0].get("song_name") or "")) not in used_keys
        ])
        if selected is None:
            print(f"[preview] Skip chart_card_{slug}: no exact Global chart row.", flush=True)
            continue
        row, previous = selected
        used_keys.add((str(row.get("date") or ""), str(row.get("song_name") or "")))
        cases.append((slug, label, row, previous, forced_movement))
    return cases


def _chart_track_meta(row: dict) -> tuple[dict, str]:
    tracks = spotlight.load_all_tracks()
    covers = spotlight.load_covers()
    track_id = str(row.get("track_id") or "").strip()
    title = str(row.get("song_name") or "").strip()
    for track in tracks:
        if track_id and str(track.get("track_id") or "") == track_id:
            return track, spotlight.get_cover_url(track, covers)
    for track in tracks:
        if _norm(str(track.get("title") or "")) == _norm(title):
            return track, spotlight.get_cover_url(track, covers)
    return {"title": title, "album": ""}, ""


def _write_chart_card_case(
    *,
    output_dir: Path,
    keep_html: bool,
    case_slug: str,
    _case_label: str,
    row: dict,
    previous: dict | None,
    forced_movement: str | None = None,
    layout: str = "wide",
) -> Path:
    chart_date = str(row.get("date") or "")
    track, cover_url = _chart_track_meta(row)
    title = str(track.get("title") or row.get("song_name") or "Chart highlight")
    album = str(track.get("album") or "").strip()
    rank = _to_int(row.get("rank"))
    streams = _to_int(row.get("streams"))
    previous_streams = _to_int(previous.get("streams")) if previous else None
    streams_delta = _chart_stream_delta(row, previous)
    movement = forced_movement or str(row.get("movement") or "").strip()
    if not movement:
        previous_rank = _to_int(row.get("previous_rank"))
        movement = "NEW" if previous_rank in (None, -1) else ""

    date_text = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    html_text = render_chart_card(
        title=title,
        eyebrow="Spotify Charts",
        subtitle=album or "Global Spotify Charts",
        stats=[
            {
                "label": "Rank",
                "value": f"#{rank}" if rank is not None else "#-",
                "badge": movement,
                "badge_class": _rank_badge_class(movement),
            },
            {
                "label": "Streams",
                "value": _fmt_chart_int(streams),
                "badge": _fmt_chart_pct(streams, previous_streams),
                "badge_class": _delta_class(streams_delta),
                "delta": _fmt_chart_delta(streams_delta),
                "delta_class": _delta_class(streams_delta),
            },
        ],
        cover_url=cover_url,
        footer_left="@swiftiescharts",
        footer_right=date_text,
        badge_text=_date_pill(chart_date),
        layout=layout,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = (slugify(title)[:80].rstrip("_") or "chart")
    out_path = output_dir / f"chart_card_{case_slug}_{layout}_{chart_date}_{slug}.png"
    html_path = output_dir / f"chart_card_{case_slug}_{layout}_{chart_date}_{slug}.html"
    print(f"[preview] Writing HTML: {html_path}", flush=True)
    print(f"[preview] Rendering PNG: {out_path}", flush=True)
    if layout == "square":
        return write_chart_card_png(html_text, out_path, html_path, keep_html=keep_html, width=1080, height=1080)
    return write_chart_card_png(html_text, out_path, html_path, keep_html=keep_html)


def _chart_card_preview(output_dir: Path, _target_date: str, keep_html: bool) -> list[Path]:
    print("[preview] Building chart_card preview gallery...", flush=True)
    rows = _load_global_chart_rows()
    if not rows:
        return []
    paths: list[Path] = []
    for case_slug, case_label, row, previous, forced_movement in _chart_preview_cases(rows):
        paths.append(_write_chart_card_case(
            output_dir=output_dir,
            keep_html=keep_html,
            case_slug=case_slug,
            _case_label=case_label,
            row=row,
            previous=previous,
            forced_movement=forced_movement,
        ))
        if case_slug == "streams_down":
            paths.append(_write_chart_card_case(
                output_dir=output_dir,
                keep_html=keep_html,
                case_slug="square_trial",
                _case_label=case_label,
                row=row,
                previous=previous,
                forced_movement=forced_movement,
                layout="square",
            ))
    return paths


# ---------------------------------------------------------------------------
# tables_image.py cases
# ---------------------------------------------------------------------------

def _streams_table_rows(stats_date: str, limit: int) -> list[dict]:
    target_day = datetime.strptime(stats_date, "%Y-%m-%d").date()
    previous_day = target_day - timedelta(days=1)
    week_day = target_day - timedelta(days=7)
    history = best_day_since.load_history()
    tracks = spotlight.load_all_tracks()
    covers = spotlight.load_covers()
    print(f"[preview] Loaded {len(tracks)} track(s) and {len(covers)} cover fallback(s).", flush=True)

    candidates = []
    for track in tracks:
        point_by_day = {point.day: point for point in history.get(track["track_id"], [])}
        current = point_by_day.get(target_day)
        if current is None or current.total is None or current.daily is None or current.daily <= 0:
            continue
        previous = point_by_day.get(previous_day)
        week = point_by_day.get(week_day)
        candidates.append({
            "track": track,
            "total": current.total,
            "daily": current.daily,
            "previous_daily": previous.daily if previous else None,
            "week_daily": week.daily if week else None,
            "cover_url": spotlight.get_cover_url(track, covers),
        })

    if not candidates:
        print(f"[preview] Skip streams table: no stream rows found for {stats_date}.", flush=True)
        return []
    sample_size = min(max(limit, 1), len(candidates))
    rows = random.sample(candidates, sample_size)
    rows.sort(key=lambda row: row["daily"], reverse=True)
    print(f"[preview] Selected {len(rows)} real stream row(s).", flush=True)
    return rows


def _streams_table_rows_html(rows: list[dict]) -> str:
    output = []
    for index, row in enumerate(rows, 1):
        track = row["track"]
        daily_pct = get_pct(row["daily"], row["previous_daily"])
        weekly_pct = get_pct(row["daily"], row["week_daily"])
        cover = url_to_data_uri(row["cover_url"])
        art_html = f'<img class="art" src="{cover}" />' if cover else '<div class="art-ph"></div>'
        row_class = "data-row row-gold" if index == 1 else ("data-row row-odd" if index % 2 else "data-row")
        output.append(f"""<div class="{row_class}">
  <div class="col-rank">#{index}</div>
  <div class="col-entity">
    {art_html}
    <div class="entity-info">
      <div class="entity-name">{html.escape(track.get("title") or "")}</div>
      <div class="entity-sub">{html.escape(track.get("album") or track.get("artist") or "")}</div>
    </div>
  </div>
  <div class="col-num">{fmt_streams(row["daily"])}</div>
  <div class="col-num">{fmt_streams(row["total"])}</div>
  <div class="col-num {pct_cls(daily_pct)}">{fmt_pct(daily_pct)}</div>
  <div class="col-num {pct_cls(weekly_pct)}">{fmt_pct(weekly_pct)}</div>
</div>""")
    return "\n".join(output)


def _streams_table_preview(output_dir: Path, target_date: str, limit: int, keep_html: bool) -> Path | None:
    print("[preview] Building streams table preview...", flush=True)
    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows = _streams_table_rows(target_date, limit)
    if not rows:
        return None
    html_text = build_table_html(
        title="Spotify Streams Preview",
        subtitle=f"Real stream rows - {date_text}",
        col_heads=[
            ("Pos", False),
            ("Track", False),
            ("Daily", True),
            ("Total", True),
            ("Vs Day", True),
            ("Vs Week", True),
        ],
        grid_cols="52px minmax(260px,1fr) 108px 118px 82px 82px",
        rows_html=_streams_table_rows_html(rows),
        handle="@swiftiescharts",
        date_str=date_text,
        headers_dir=HEADERS_DIR,
        body_width=920,
        art_size=48,
        header_background="linear-gradient(135deg,#1db954 0%,#0f5132 100%)",
    )
    out_path = output_dir / f"tables_image_streams_{target_date}.png"
    html_path = output_dir / f"tables_image_streams_{target_date}.html"
    print(f"[preview] Writing HTML: {html_path}", flush=True)
    print(f"[preview] Rendering PNG: {out_path}", flush=True)
    return render_html_to_png(html_text, out_path, html_path, width=920, keep_html=keep_html)


def _recap_rows_for_date(stats_date: str, min_days: int) -> list[dict]:
    rows = _best_since_rows_for_date(stats_date)
    rows = [row for row in rows if best_day_since.passes_filters(row, min_days=min_days)]
    rows.sort(key=lambda row: row["best_day_since"])
    return rows


def _recap_row_html(index: int, row: dict, track: dict, cover_url: str, daily: int, daily_pct: float | None, weekly_pct: float | None) -> str:
    art_html = f'<img class="art" src="{cover_url}" />' if cover_url else '<div class="art-ph"></div>'
    row_class = "data-row row-gold" if index == 1 else ("data-row row-odd" if index % 2 else "data-row")
    since_txt = best_day_since.format_long_date(row["best_day_since"])
    title = html.escape(track.get("title") or row["title"])
    subtitle = html.escape(track.get("album") or row.get("album") or "")
    return f"""<div class="{row_class}">
  <div class="col-rank">#{index}</div>
  <div class="col-entity">
    {art_html}
    <div class="entity-info">
      <div class="entity-name">{title}</div>
      <div class="entity-sub">{subtitle}</div>
    </div>
  </div>
  <div class="col-num">{fmt_streams(daily)}</div>
  <div class="col-num {pct_cls(daily_pct)}">{fmt_pct(daily_pct)}</div>
  <div class="col-num {pct_cls(weekly_pct)}">{fmt_pct(weekly_pct)}</div>
  <div class="col-num">{html.escape(since_txt)}</div>
</div>"""


def _recap_table_preview(output_dir: Path, target_date: str, keep_html: bool, min_days: int) -> Path | None:
    print("[preview] Building best-day-since recap table preview...", flush=True)
    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows = _recap_rows_for_date(target_date, min_days)
    print(f"[preview] Found {len(rows)} best-day-since recap row(s).", flush=True)
    if not rows:
        print("[preview] Skip recap table: no qualifying rows.", flush=True)
        return None

    tracks_by_id = {track["track_id"]: track for track in spotlight.load_all_tracks()}
    covers = spotlight.load_covers()
    rows_html = []
    for index, row in enumerate(rows, 1):
        track = tracks_by_id.get(row["track_id"])
        if not track:
            continue
        track_ids = row.get("combined_track_ids") or [row["track_id"]]
        _total_today, _total_yesterday, daily_today, daily_yesterday, daily_last_week = (
            spotlight.load_history_for_tracks(track_ids, target_date)
        )
        if daily_today is None:
            continue
        cover_url = url_to_data_uri(spotlight.get_cover_url(track, covers))
        daily_pct = get_pct(daily_today, daily_yesterday)
        weekly_pct = get_pct(daily_today, daily_last_week)
        rows_html.append(_recap_row_html(index, row, track, cover_url, daily_today, daily_pct, weekly_pct))

    if not rows_html:
        print("[preview] Skip recap table: no row had complete comparison history.", flush=True)
        return None

    html_text = build_table_html(
        title="Best Day Since — Full Recap",
        subtitle=f"Every song that hit a best-day-since record - {date_text}",
        col_heads=[
            ("Pos", False),
            ("Track", False),
            ("Daily", True),
            ("Vs Day", True),
            ("Vs Week", True),
            ("Best Since", True),
        ],
        grid_cols="48px minmax(220px,1fr) 100px 76px 76px 130px",
        rows_html="\n".join(rows_html),
        handle="@swiftiescharts",
        date_str=date_text,
        headers_dir=HEADERS_DIR,
        body_width=960,
        art_size=48,
    )
    out_path = output_dir / f"tables_image_recap_{target_date}.png"
    html_path = output_dir / f"tables_image_recap_{target_date}.html"
    print(f"[preview] Writing HTML: {html_path}", flush=True)
    print(f"[preview] Rendering PNG: {out_path}", flush=True)
    return render_html_to_png(html_text, out_path, html_path, width=960, keep_html=keep_html)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _clear_previous_previews(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    removed = 0
    for pattern in (
        "song_card_*.png",
        "song_card_*.html",
        "chart_card_*.png",
        "chart_card_*.html",
        "tables_image_*.png",
        "tables_image_*.html",
    ):
        for path in output_dir.glob(pattern):
            if not path.is_file():
                continue
            path.unlink()
            removed += 1
    if removed:
        print(f"[preview] Deleted {removed} previous preview file(s).", flush=True)


def _component_main() -> None:
    parser = argparse.ArgumentParser(description="Generate previews for song_card.py, chart_card.py and tables_image.py.")
    parser.add_argument("--date", help="Stats date YYYY-MM-DD. Defaults to latest date in streams history.")
    parser.add_argument("--only", choices=["song-card", "chart-card", "tables"], help="Restrict to one family of previews.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for generated previews.")
    parser.add_argument("--limit", type=int, default=10, help="Number of rows for the streams table preview.")
    parser.add_argument("--min-days", type=int, default=1, help="Minimum days filter for best-day-since rows.")
    parser.add_argument("--keep-html", action="store_true", default=True, help="Keep the generated HTML preview.")
    parser.add_argument("--no-keep-html", action="store_false", dest="keep_html", help="Delete the temporary HTML.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    print(f"[preview] Output dir: {output_dir}", flush=True)
    _clear_previous_previews(output_dir)

    target_date = args.date or _latest_preview_date()

    paths: list[Path] = []
    if args.only in (None, "song-card"):
        paths.extend(_song_card_gallery(output_dir, args.keep_html, target_date))
    if args.only in (None, "chart-card"):
        paths.extend(_chart_card_preview(output_dir, target_date, args.keep_html))
    if args.only in (None, "tables"):
        streams_path = _streams_table_preview(output_dir, target_date, args.limit, args.keep_html)
        if streams_path:
            paths.append(streams_path)
        recap_path = _recap_table_preview(output_dir, target_date, args.keep_html, args.min_days)
        if recap_path:
            paths.append(recap_path)

    print(f"[preview] Generated {len(paths)} preview file(s).", flush=True)
    for path in paths:
        print(f"Generated preview: {path}", flush=True)



# ---------------------------------------------------------------------------
# Spotify collector preview gallery
# ---------------------------------------------------------------------------

def _slug_text(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return value or "preview"


def _latest_charts_date() -> str | None:
    roots = [REPO_ROOT / "snapshots" / "spotify_charts", REPO_ROOT / "data"]
    dates: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("ts_chart_*.json"):
            match = re.search(r"ts_chart_(\d{4}-\d{2}-\d{2})\.json$", path.name)
            if match:
                dates.append(match.group(1))
    return max(dates) if dates else None


def _collector_preview_root(output_dir: Path) -> Path:
    return output_dir / "spotify"


def _clear_collector_previews(output_dir: Path) -> None:
    root = _collector_preview_root(output_dir)
    if root.exists():
        import shutil
        shutil.rmtree(root)
    (root / "streams").mkdir(parents=True, exist_ok=True)
    (root / "charts").mkdir(parents=True, exist_ok=True)




def _history_dates() -> list[str]:
    history_path = DB_ROOT / "streams_history.csv"
    if not history_path.exists():
        return []
    dates: set[str] = set()
    with history_path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw = (row.get("date") or "").strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
                dates.add(raw)
    return sorted(dates)


def _latest_streams_weekday_date() -> str | None:
    for raw in reversed(_history_dates()):
        try:
            if datetime.strptime(raw, "%Y-%m-%d").weekday() < 5:
                return raw
        except ValueError:
            continue
    return None


def _chart_image_path(chart_date: str, region: str) -> Path:
    return REPO_ROOT / "snapshots" / "spotify_charts" / chart_date[:4] / chart_date[5:7] / chart_date / region / "chart_image.png"


def _latest_existing_chart_image(region: str) -> Path | None:
    root = REPO_ROOT / "snapshots" / "spotify_charts"
    if not root.exists():
        return None
    candidates = [
        path for path in root.rglob("chart_image.png")
        if path.parent.name.lower() == region.lower()
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime_ns) if candidates else None


def _worldwide_cards_dirs(chart_date: str) -> list[Path]:
    return [
        REPO_ROOT / "snapshots" / "spotify_charts" / chart_date[:4] / chart_date[5:7] / chart_date / "worldwide" / "cards",
        REPO_ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "history" / chart_date[:4] / chart_date[5:7] / chart_date / "cards",
        REPO_ROOT / "data" / chart_date[:4] / chart_date[5:7] / chart_date / "run_all_charts" / "spotify" / "worldwide" / "cards",
    ]


def _latest_worldwide_cards_dir(pattern: str = "*.png") -> Path | None:
    roots = [
        REPO_ROOT / "snapshots" / "spotify_charts",
        REPO_ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "history",
        REPO_ROOT / "data",
    ]
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("cards"):
            if path.is_dir() and any(path.glob(pattern)):
                candidates.append(path)
    return max(candidates, key=lambda p: p.stat().st_mtime_ns) if candidates else None


def _worldwide_cards_globs(chart_date: str) -> list[str]:
    globs = [str(path / "*.png") for path in _worldwide_cards_dirs(chart_date)]
    latest_cards = _latest_worldwide_cards_dir("*.png")
    if latest_cards:
        globs.append(str(latest_cards / "*.png"))
    latest_regional_cards = _latest_worldwide_cards_dir("*_chart_card.png")
    if latest_regional_cards:
        globs.append(str(latest_regional_cards / "*_chart_card.png"))
    return globs
def _find_chart_json(chart_date: str, region: str) -> Path | None:
    candidates = [
        REPO_ROOT / "snapshots" / "spotify_charts" / chart_date[:4] / chart_date[5:7] / chart_date / region / f"ts_chart_{chart_date}.json",
        REPO_ROOT / "data" / chart_date[:4] / chart_date[5:7] / chart_date / "run_all_charts" / "spotify" / region / f"ts_chart_{chart_date}.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _chart_tweet_hint(chart_date: str | None, region: str, region_label: str) -> str:
    if not chart_date:
        return ""
    json_path = _find_chart_json(chart_date, region)
    if not json_path:
        return f"Taylor Swift on Spotify {region_label} Charts on {chart_date}."
    try:
        import json
        rows = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except Exception:
        rows = []
    try:
        date_label = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        date_label = chart_date
    lines = [f"Taylor Swift on Spotify {region_label} Charts on {date_label}:"]
    for row in rows[:10] if isinstance(rows, list) else []:
        title = row.get("song_name") or row.get("track_name") or row.get("title") or "Unknown"
        rank = row.get("rank") or "-"
        streams = row.get("streams")
        streams_text = f" | {int(streams):,}" if str(streams or "").strip().isdigit() else ""
        lines.append(f"- #{rank} {title}{streams_text}")
    return "\n".join(lines)
def _preview_specs(streams_date: str, charts_date: str | None) -> list[dict]:
    py = sys.executable
    s = SPOTIFY_STREAMS_SCRIPTS_DIR
    chart_global = COLLECTORS_ROOT / "spotify" / "charts" / "global" / "tools" / "script" / "generate_chart_image.py"
    chart_us = COLLECTORS_ROOT / "spotify" / "charts" / "us" / "tools" / "scripts" / "generate_chart_image.py"
    chart_uk = COLLECTORS_ROOT / "spotify" / "charts" / "uk" / "tools" / "scripts" / "generate_chart_image.py"
    chart_worldwide = COLLECTORS_ROOT / "spotify" / "charts" / "worldwide" / "tools" / "scripts" / "generate_card_images.py"
    specs = [
        {"kind": "streams", "name": "post_streams_twitter", "cmd": [py, str(s / "post_streams_twitter.py"), streams_date, "--no-post", "--top-n", "20"]},
        {"kind": "streams", "name": "post_weekend_streams_twitter", "cmd": [py, str(s / "post_weekend_streams_twitter.py"), streams_date, "--no-post", "--force-weekday"]},
        {"kind": "streams", "name": "post_albums_twitter", "cmd": [py, str(s / "post_albums_twitter.py"), streams_date, "--no-post"], "fallback_cmds": ([[py, str(s / "post_albums_twitter.py"), _latest_streams_weekday_date(), "--no-post"]] if _latest_streams_weekday_date() and _latest_streams_weekday_date() != streams_date else [])},
        {"kind": "streams", "name": "post_best_day_since_twitter", "cmd": [py, str(s / "post_best_day_since_twitter.py"), streams_date, "--no-post", "--limit", "1", "--min-days", "1"]},
        {"kind": "streams", "name": "post_gainer_thread_daily", "cmd": [py, str(s / "post_gainer_thread.py"), streams_date, "--period", "daily", "--limit", "1", "--no-post"]},
        {"kind": "streams", "name": "post_gainer_thread_weekly", "cmd": [py, str(s / "post_gainer_thread.py"), streams_date, "--period", "weekly", "--limit", "1", "--no-post"]},
        {"kind": "streams", "name": "post_stream_highlights_thread", "cmd": [py, str(s / "post_stream_highlights_thread.py"), streams_date, "--limit", "5", "--best-limit", "3", "--min-days", "1", "--no-post"]},
        {"kind": "streams", "name": "post_weekend_song_gainers", "cmd": [py, str(s / "post_weekend_song_gainers.py"), streams_date, "--min-pct", "5", "--no-post"]},
        {"kind": "streams", "name": "post_song_overtakes", "cmd": [py, str(s / "post_song_overtakes.py"), streams_date, "--no-post", "--limit", "5", "--force"]},
        {"kind": "streams", "name": "generate_album_update_image_ttpd", "cmd": [py, str(s / "generate_album_update_image.py"), "THE TORTURED POETS DEPARTMENT", streams_date], "tweet_hint": "Album update preview generated only. Real tweet text is built inside generate_album_update_image.post()."},
    ]
    if charts_date:
        specs.extend([
            {"kind": "charts", "name": "global_generate_chart_image", "cmd": [py, str(chart_global), charts_date], "tweet_hint": _chart_tweet_hint(charts_date, "global", "Global"), "output_globs": [str(_chart_image_path(charts_date, "global"))]},
            {"kind": "charts", "name": "us_generate_chart_image", "cmd": [py, str(chart_us), charts_date], "tweet_hint": _chart_tweet_hint(charts_date, "us", "US"), "output_globs": [str(_chart_image_path(charts_date, "us")), *([str(_latest_existing_chart_image("us"))] if _latest_existing_chart_image("us") else [])]},
            {"kind": "charts", "name": "uk_generate_chart_image", "cmd": [py, str(chart_uk), charts_date], "tweet_hint": _chart_tweet_hint(charts_date, "uk", "UK"), "output_globs": [str(_chart_image_path(charts_date, "uk")), *([str(_latest_existing_chart_image("uk"))] if _latest_existing_chart_image("uk") else [])]},
            {"kind": "charts", "name": "worldwide_generate_card_images", "cmd": [py, str(chart_worldwide), charts_date, "--force", "--min-countries", "1"], "output_globs": _worldwide_cards_globs(charts_date)},
        ])
        if not _chart_image_path(charts_date, "uk").exists() and _latest_existing_chart_image("uk") is None:
            specs = [spec for spec in specs if spec.get("name") != "uk_generate_chart_image"]
    return specs


def _extract_png_paths(stdout: str) -> list[Path]:
    found: list[Path] = []
    for pattern in (r"[A-Za-z]:\\[^\r\n,]+?\.png", r"/[A-Za-z0-9_./\\ -]+?\.png"):
        for match in re.finditer(pattern, stdout):
            raw = match.group(0).strip().strip('"').strip("'")
            path = Path(raw)
            if path.exists() and path.is_file() and path not in found:
                found.append(path)
    return found



def _paths_from_output_globs(spec: dict) -> list[Path]:
    import glob
    found: list[Path] = []
    for pattern in spec.get("output_globs") or []:
        if not pattern:
            continue
        for raw in glob.glob(str(pattern)):
            path = Path(raw)
            if path.exists() and path.is_file() and path.suffix.lower() == ".png" and path not in found:
                found.append(path)
    return found
def _extract_tweets(stdout: str) -> list[str]:
    tweets: list[str] = []
    markers = ["Tweet", "Recap tweet", "Combined gainers table", "Post"]
    lines = stdout.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not any(marker in line for marker in markers) or ":" not in line:
            i += 1
            continue
        after = line.split(":", 1)[1].strip()
        block: list[str] = []
        if after:
            block.append(after)
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if "Image:" in nxt or "Images:" in nxt or "Twitter post skipped" in nxt or "OK image" in nxt:
                break
            if re.match(r"^\[[^\]]+\] [A-Z][A-Za-z_ -]+:", nxt) and block:
                break
            block.append(nxt)
            i += 1
        tweet = "\n".join(block).strip()
        if tweet and tweet not in tweets:
            tweets.append(tweet)
    return tweets


def _write_preview_html(*, html_path: Path, png_name: str | None, tweet_text: str, spec_name: str, command: list[str], returncode: int) -> None:
    image_html = f'<img src="{html.escape(png_name or "")}" alt="preview" />' if png_name else '<div class="missing">No PNG generated for this date.</div>'
    html_path.write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(spec_name)} preview</title>
<style>
body{{font-family:Inter,Arial,sans-serif;margin:24px;background:#f6f8fa;color:#101828}}
img{{max-width:100%;height:auto;display:block;border:1px solid #d0d5dd;background:white}}
pre{{white-space:pre-wrap;background:white;border:1px solid #d0d5dd;padding:16px;border-radius:6px}}
.meta{{color:#667085;font-size:13px;margin:12px 0}}
.missing{{padding:24px;background:white;border:1px solid #d0d5dd;border-radius:6px}}
</style></head><body>
<h1>{html.escape(spec_name)}</h1>
<div class="meta">exit {returncode} - {' '.join(html.escape(part) for part in command)}</div>
{image_html}
<h2>Tweet text</h2>
<pre>{html.escape(tweet_text or 'No tweet text captured.')}</pre>
</body></html>""", encoding="utf-8")


def _copy_collector_outputs(spec: dict, stdout: str, stderr: str, returncode: int, output_dir: Path) -> list[Path]:
    import shutil
    dest = _collector_preview_root(output_dir) / spec["kind"] / spec["name"]
    dest.mkdir(parents=True, exist_ok=True)
    command = [str(part) for part in spec["cmd"]]
    (dest / "command.txt").write_text(" ".join(command), encoding="utf-8")
    (dest / "stdout.txt").write_text(stdout, encoding="utf-8")
    if stderr.strip():
        (dest / "stderr.txt").write_text(stderr, encoding="utf-8")

    tweets = _extract_tweets(stdout)
    if spec.get("tweet_hint") and not tweets:
        tweets = [str(spec["tweet_hint"])]
    (dest / "tweet.txt").write_text("\n\n---\n\n".join(tweets), encoding="utf-8")

    copied: list[Path] = []
    sources = []
    for source in [*_extract_png_paths(stdout), *_paths_from_output_globs(spec)]:
        if source not in sources:
            sources.append(source)
    for idx, source in enumerate(sources, 1):
        target = dest / f"{idx:02d}_{_slug_text(source.stem)}.png"
        try:
            shutil.copy2(source, target)
        except Exception as exc:
            print(f"[preview] Could not copy {source}: {exc}", flush=True)
            continue
        copied.append(target)
        tweet = tweets[min(idx - 1, len(tweets) - 1)] if tweets else ""
        _write_preview_html(html_path=dest / f"{idx:02d}_{_slug_text(source.stem)}.html", png_name=target.name, tweet_text=tweet, spec_name=spec["name"], command=command, returncode=returncode)
    if not copied:
        _write_preview_html(html_path=dest / "preview.html", png_name=None, tweet_text=tweets[0] if tweets else "", spec_name=spec["name"], command=command, returncode=returncode)
    return copied


def _run_collector_spec(spec: dict, output_dir: Path) -> list[Path]:
    import os
    import subprocess
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    print(f"[preview] Running {spec['kind']}/{spec['name']}...", flush=True)
    result = subprocess.run([str(part) for part in spec["cmd"]], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        print(f"[preview] {spec['name']} exited {result.returncode}; keeping stdout/stderr preview.", flush=True)
    copied = _copy_collector_outputs(spec, result.stdout, result.stderr, result.returncode, output_dir)
    if not copied and spec.get("fallback_cmds"):
        for fallback_cmd in spec.get("fallback_cmds") or []:
            print(f"[preview] {spec['name']}: no PNG for primary date, trying latest available data...", flush=True)
            fallback_spec = {**spec, "cmd": fallback_cmd, "fallback_cmds": []}
            fallback_result = subprocess.run([str(part) for part in fallback_cmd], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            copied = _copy_collector_outputs(fallback_spec, fallback_result.stdout, fallback_result.stderr, fallback_result.returncode, output_dir)
            if copied:
                break
    print(f"[preview] {spec['name']}: {len(copied)} PNG(s) copied.", flush=True)
    return copied



def _latest_worldwide_chart_card_paths(limit: int = 6) -> list[Path]:
    roots = [
        REPO_ROOT / "snapshots" / "spotify_charts",
        REPO_ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "history",
        REPO_ROOT / "data",
    ]
    found: list[Path] = []
    for root in roots:
        if root.exists():
            found.extend(path for path in root.rglob("*_chart_card.png") if path.is_file())
    found = sorted({path.resolve(): path for path in found}.values(), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    return found[:limit]


def _copy_regional_chart_card_cases(folder: Path) -> list[Path]:
    import shutil
    folder.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    sources = _latest_worldwide_chart_card_paths()
    tweet = (
        "Regional Spotify chart-card visual states. These are exact PNGs generated by "
        "worldwide_generate_card_images when a Taylor Swift song charts in a single region."
    )
    (folder / "tweet.txt").write_text(tweet, encoding="utf-8")
    (folder / "command.txt").write_text("python collectors\\comp\\preview.py --components --only regional-chart-card", encoding="utf-8")
    for idx, source in enumerate(sources, 1):
        target = folder / f"{idx:02d}_{_slug_text(source.stem)}.png"
        shutil.copy2(source, target)
        copied.append(target)
        _write_preview_html(
            html_path=folder / f"{idx:02d}_{_slug_text(source.stem)}.html",
            png_name=target.name,
            tweet_text=tweet,
            spec_name="component_regional_chart_card_cases",
            command=["component", "regional-chart-card"],
            returncode=0,
        )
    if not copied:
        _write_preview_html(
            html_path=folder / "preview.html",
            png_name=None,
            tweet_text=tweet,
            spec_name="component_regional_chart_card_cases",
            command=["component", "regional-chart-card"],
            returncode=0,
        )
    return copied

def _write_component_tweet_placeholder(folder: Path, label: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tweet.txt").write_text(
        f"{label} component visual states. These previews are template/state coverage, not one specific Twitter post.",
        encoding="utf-8",
    )


def _run_component_previews_for_collectors(output_dir: Path, streams_date: str) -> list[Path]:
    paths: list[Path] = []
    streams_root = _collector_preview_root(output_dir) / "streams"
    charts_root = _collector_preview_root(output_dir) / "charts"

    song_dir = streams_root / "component_song_card_cases"
    try:
        song_paths = _song_card_gallery(song_dir, True, streams_date)
        paths.extend(song_paths)
        _write_component_tweet_placeholder(song_dir, "song_card")
        (song_dir / "command.txt").write_text("python collectors\\comp\\preview.py --components --only song-card", encoding="utf-8")
    except Exception as exc:
        song_dir.mkdir(parents=True, exist_ok=True)
        (song_dir / "stderr.txt").write_text(str(exc), encoding="utf-8")
        _write_preview_html(html_path=song_dir / "preview.html", png_name=None, tweet_text="", spec_name="component_song_card_cases", command=["component", "song-card"], returncode=1)
        print(f"[preview] component_song_card_cases failed: {exc}", flush=True)

    tables_dir = streams_root / "component_tables_image_cases"
    try:
        streams_path = _streams_table_preview(tables_dir, streams_date, 10, True)
        recap_path = _recap_table_preview(tables_dir, streams_date, True, 1)
        for item in (streams_path, recap_path):
            if item:
                paths.append(item)
        _write_component_tweet_placeholder(tables_dir, "tables_image")
        (tables_dir / "command.txt").write_text("python collectors\\comp\\preview.py --components --only tables", encoding="utf-8")
    except Exception as exc:
        tables_dir.mkdir(parents=True, exist_ok=True)
        (tables_dir / "stderr.txt").write_text(str(exc), encoding="utf-8")
        _write_preview_html(html_path=tables_dir / "preview.html", png_name=None, tweet_text="", spec_name="component_tables_image_cases", command=["component", "tables"], returncode=1)
        print(f"[preview] component_tables_image_cases failed: {exc}", flush=True)

    chart_dir = charts_root / "component_chart_card_cases"
    try:
        chart_paths = _chart_card_preview(chart_dir, streams_date, True)
        paths.extend(chart_paths)
        _write_component_tweet_placeholder(chart_dir, "chart_card")
        (chart_dir / "command.txt").write_text("python collectors\\comp\\preview.py --components --only chart-card", encoding="utf-8")
    except Exception as exc:
        chart_dir.mkdir(parents=True, exist_ok=True)
        (chart_dir / "stderr.txt").write_text(str(exc), encoding="utf-8")
        _write_preview_html(html_path=chart_dir / "preview.html", png_name=None, tweet_text="", spec_name="component_chart_card_cases", command=["component", "chart-card"], returncode=1)
        print(f"[preview] component_chart_card_cases failed: {exc}", flush=True)

    regional_chart_dir = charts_root / "component_regional_chart_card_cases"
    try:
        paths.extend(_copy_regional_chart_card_cases(regional_chart_dir))
    except Exception as exc:
        regional_chart_dir.mkdir(parents=True, exist_ok=True)
        (regional_chart_dir / "stderr.txt").write_text(str(exc), encoding="utf-8")
        _write_preview_html(html_path=regional_chart_dir / "preview.html", png_name=None, tweet_text="", spec_name="component_regional_chart_card_cases", command=["component", "regional-chart-card"], returncode=1)
        print(f"[preview] component_regional_chart_card_cases failed: {exc}", flush=True)

    return paths
def _collector_main(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    only_wants_charts = args.only_collector and _slug_text(args.only_collector) == "charts"
    streams_date = args.date or ("1970-01-01" if args.skip_streams or only_wants_charts else _latest_preview_date())
    charts_date = args.charts_date or _latest_charts_date()
    print(f"[preview] Output dir: {output_dir}", flush=True)
    print(f"[preview] Streams date: {streams_date}", flush=True)
    print(f"[preview] Charts date: {charts_date or 'none found'}", flush=True)
    _clear_collector_previews(output_dir)

    specs = _preview_specs(streams_date, charts_date)
    if args.only_collector:
        wanted = {_slug_text(item.strip()) for item in args.only_collector.split(",") if item.strip()}
        specs = [spec for spec in specs if _slug_text(spec["name"]) in wanted or _slug_text(spec["kind"]) in wanted]
    if args.skip_charts:
        specs = [spec for spec in specs if spec["kind"] != "charts"]
    if args.skip_streams:
        specs = [spec for spec in specs if spec["kind"] != "streams"]

    paths: list[Path] = []
    for spec in specs:
        paths.extend(_run_collector_spec(spec, output_dir))

    if not args.no_component_cases and not args.only_collector:
        paths.extend(_run_component_previews_for_collectors(output_dir, streams_date))

    print(f"[preview] Collector gallery generated {len(paths)} PNG file(s).", flush=True)
    for path in paths:
        print(f"Generated preview: {path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Spotify collector preview galleries.")
    parser.add_argument("--date", help="Streams stats date YYYY-MM-DD. Defaults to latest streams history date.")
    parser.add_argument("--charts-date", help="Spotify charts date YYYY-MM-DD. Defaults to latest local chart JSON date.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for generated previews.")
    parser.add_argument("--components", action="store_true", help="Run the older component preview gallery instead.")
    parser.add_argument("--skip-streams", action="store_true", help="Skip Spotify Streams collector previews.")
    parser.add_argument("--skip-charts", action="store_true", help="Skip Spotify Charts collector previews.")
    parser.add_argument("--only-collector", help="Comma-separated collector/tool names or kind: streams, charts.")
    parser.add_argument("--no-component-cases", action="store_true", help="Skip nested component state previews.")
    parser.add_argument("--only", choices=["song-card", "chart-card", "tables"], help=argparse.SUPPRESS)
    parser.add_argument("--limit", type=int, default=10, help=argparse.SUPPRESS)
    parser.add_argument("--min-days", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--keep-html", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--no-keep-html", action="store_false", dest="keep_html", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.components:
        component_args = [sys.argv[0]]
        skip_next = False
        for index, part in enumerate(sys.argv[1:]):
            if skip_next:
                skip_next = False
                continue
            if part == "--components" or part in {"--skip-streams", "--skip-charts"}:
                continue
            if part in {"--charts-date", "--only-collector"}:
                skip_next = True
                continue
            if part.startswith("--charts-date=") or part.startswith("--only-collector="):
                continue
            component_args.append(part)
        sys.argv = component_args
        _component_main()
        return
    _collector_main(args)

if __name__ == "__main__":
    main()
