#!/usr/bin/env python3
"""
generate_snapshot_images.py — génère des images PNG des snapshots Apple Music
(chart standings) pour Taylor Swift, à partir des CSV produits par
country_charts.py / genre_charts.py / global.py.

Usage:
  python generate_snapshot_images.py
      -> génère les 5 images par défaut (Global, US, US Pop, US Country, US Alternative)
         pour la date du jour.

  python generate_snapshot_images.py --date 2026-07-03
      -> mêmes images par défaut, pour une date donnée.

  python generate_snapshot_images.py --region global
  python generate_snapshot_images.py --region us
  python generate_snapshot_images.py --region us --genre pop
  python generate_snapshot_images.py --region fr --genre alternative --date 2026-07-03
      -> génère uniquement l'image demandée.

  python generate_snapshot_images.py --region global --album showgirl
      -> seulement les titres de l'album (nom, slug de fichier db/discography/albums
         ou sous-chaine unique), rangs du chart d'origine conserves ; PNG suffixe
         par l'album (global_the-life-of-a-showgirl.png). Combinable avec les
         cibles par defaut.

  python generate_snapshot_images.py --list-genres
      -> liste les genres disponibles.

Chaque image affiche l'heure du snapshot et celle du snapshot de comparaison
(dernier snapshot du jour precedent, meme semantique que previous_rank des CSV),
en heure de Paris.
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import os
import re
import sys
import unicodedata
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collectors.spotify.core.data_paths import (  # noqa: E402
    apple_music_charts_dir,
    legacy_apple_music_daily_csv,
)
from collectors.spotify.core.notify import send as notify_send  # noqa: E402
# NB: collectors.comp.tables_image is imported lazily inside the rendering
# helpers below -- it pulls in playwright / Pillow, which the data-only CI
# path (--notify-global-only) has no reason to install.
from core.config import GENRES  # noqa: E402
from core.discography import (  # noqa: E402
    iter_catalog_tracks as _iter_catalog_tracks,
    load_release_dates as _load_release_dates,
    resolve_album_filter as _resolve_album_filter,
    song_key_candidates as _song_key_candidates,
    song_name_key as _song_name_key,
)

DISCOGRAPHY_DIR = REPO_ROOT / "db" / "discography"

HANDLE = "@swiftiescharts"
HEADER_BG = "linear-gradient(135deg,#fa243c 0%,#bf1d47 100%)"
LOGO_PATH = HERE / "Apple_Music_icon.svg.webp"
GENRE_NAMES = [name for _id, name in GENRES]
NTFY_TOPIC = os.getenv("NTFY_TOPIC_APPLE_MUSIC", "taylormuseum-apple-music")
GLOBAL_NOTIFY_STATE_PATH = HERE / "tools" / "json" / "global_notify_state.json"


def _apple_music_logo_html() -> str:
    try:
        data = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        return f'<img class="hdr-logo" src="data:image/webp;base64,{data}" />'
    except Exception:
        return ""


APPLE_MUSIC_LOGO_HTML = _apple_music_logo_html()

DEFAULT_TARGETS = [
    {"region": "global", "genre": None},
    {"region": "us", "genre": None},
    {"region": "us", "genre": "Pop"},
    {"region": "us", "genre": "Country"},
    {"region": "us", "genre": "Alternative"},
]


def _read_latest_rows(csv_path: Path, chart_date: str) -> tuple[list[dict], str | None]:
    """Rows of the day's last snapshot + that snapshot's scraped_at (file-level,
    so it stays known even when the region has no Taylor Swift entry)."""
    if not csv_path.exists():
        return [], None
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = [dict(row) for row in csv.DictReader(fh)]
    day_rows = [row for row in rows if str(row.get("date") or "").strip() == chart_date]
    if not day_rows:
        return [], None
    latest = max(str(row.get("scraped_at") or "") for row in day_rows)
    return [row for row in day_rows if str(row.get("scraped_at") or "") == latest], (latest or None)


def _rank_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


RELEASE_DATES = _load_release_dates()

# Apple Music charts long after most Taylor Swift songs were released, so
# without this check every song missing from yesterday's snapshot would
# incorrectly show as "NEW" instead of "RE" (kept in sync with the same
# constant in tsm-frontend/api/routes/apple_music.py).
_NEW_RELEASE_WINDOW_DAYS = 21


def _is_recent_release(release_date: str | None, reference_date: str | None) -> bool:
    if not release_date or not reference_date:
        return False
    try:
        released = datetime.strptime(str(release_date)[:10], "%Y-%m-%d").date()
        reference = datetime.strptime(str(reference_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return -1 <= (reference - released).days <= _NEW_RELEASE_WINDOW_DAYS


def _rank_change(rank: int, previous_rank: int | None, is_new_release: bool) -> tuple[str, str]:
    """A missing previous_rank means either a genuinely new release or a
    re-entry (song already in our catalog, just off-chart before)."""
    if previous_rank is None:
        return ("NEW", "chg-new") if is_new_release else ("RE", "chg-re")
    delta = previous_rank - rank
    if delta > 0:
        return f"&#9650; {delta}", "chg-up"
    if delta < 0:
        return f"&#9660; {abs(delta)}", "chg-dn"
    return "=", "chg-eq"


def _region_csv_path(chart_date: str, region: str, genre: str | None) -> Path:
    """snapshots/apple_music_charts/<day>/ (since 2026-06-05), else the legacy
    data/<day>/apple_music/ folder (2026-03-22 -> 2026-06-04, same columns)."""
    region = region.lower().strip()
    if region == "global":
        filename = "apple_music_global.csv"
    elif genre:
        filename = "apple_music_genre_charts.csv"
    else:
        filename = "apple_music_country_charts.csv"
    path = apple_music_charts_dir(chart_date) / filename
    if not path.exists():
        legacy = legacy_apple_music_daily_csv(chart_date, filename)
        if legacy.exists():
            return legacy
    return path


def _region_row_filter(region: str, genre: str | None) -> Callable[[dict], bool]:
    region = region.lower().strip()
    if region == "global":
        # the first legacy days (2026-03-22..26) left chart_type empty; the
        # global file only ever holds the Global chart
        return lambda r: (r.get("chart_type") or "global") == "global"
    if genre:
        genre_norm = genre.strip().lower()
        return lambda r: (
            str(r.get("country") or "").lower() == region
            and str(r.get("genre_name") or "").lower() == genre_norm
        )
    return lambda r: str(r.get("country") or "").lower() == region


def get_region_rows(chart_date: str, region: str, genre: str | None) -> tuple[list[dict], str | None]:
    latest_rows, scraped_at = _read_latest_rows(_region_csv_path(chart_date, region, genre), chart_date)
    keep = _region_row_filter(region, genre)
    rows = [r for r in latest_rows if keep(r)]
    rows.sort(key=lambda r: _rank_int(r.get("rank")) or 9999)
    return rows, scraped_at


def _track_key(row: dict) -> str:
    track_id = str(row.get("apple_music_id") or "").strip()
    return track_id or str(row.get("song_name") or "").strip().lower()


PREVIOUS_SNAPSHOT_LOOKBACK_DAYS = 7


def get_previous_day_ranks(chart_date: str, region: str, genre: str | None) -> tuple[dict[str, int], str | None]:
    """Rank map from the last snapshot of the most recent previous day that has
    one (same semantics as core/csv_utils.load_previous_ranks, which fills the
    CSVs' previous_rank), plus that snapshot's scraped_at for the "vs" label."""
    day = datetime.strptime(chart_date, "%Y-%m-%d").date()
    for back in range(1, PREVIOUS_SNAPSHOT_LOOKBACK_DAYS + 1):
        rows, scraped_at = get_region_rows(str(day - timedelta(days=back)), region, genre)
        if scraped_at is None:
            continue
        mapping: dict[str, int] = {}
        for row in rows:
            rank = _rank_int(row.get("rank"))
            key = _track_key(row)
            if rank is not None and key:
                mapping[key] = rank
        return mapping, scraped_at
    return {}, None


def make_prev_rank_resolver(
    chart_date: str, region: str, genre: str | None,
) -> tuple[Callable[[dict], int | None], str | None]:
    """Every chart compares vs the previous day's last snapshot (Global only
    updates once/day; country/genre previous_rank already means "vs
    yesterday"). Computed here rather than read from previous_rank so the
    deltas and the "vs <hour>" label come from the very same snapshot."""
    previous_day_ranks, previous_scraped_at = get_previous_day_ranks(chart_date, region, genre)
    return (lambda row: previous_day_ranks.get(_track_key(row))), previous_scraped_at


def _filter_album(rows: list[dict], album_keys: set[str] | None) -> list[dict]:
    if album_keys is None:
        return rows
    return [
        r for r in rows
        if any(key in album_keys for key in _song_key_candidates(r.get("song_name")))
    ]


def _format_snapshot_time(scraped_at: str | None) -> str:
    """'Sep 24, 2026 · 11:00 AM CEST' in Europe/Paris (a PNG has no viewer tz)."""
    if not scraped_at:
        return ""
    try:
        dt = datetime.fromisoformat(str(scraped_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Europe/Paris"))
        dt = dt.astimezone(ZoneInfo("Europe/Paris"))
    except ValueError:
        return str(scraped_at)
    clock = f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'} {dt.tzname() or ''}".strip()
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} · {clock}"


def _release_date_for(row: dict) -> str | None:
    song = str(row.get("song_name") or "").strip()
    release = next(
        (RELEASE_DATES[key] for key in _song_key_candidates(song) if key in RELEASE_DATES),
        None,
    )
    # catalog values are ISO timestamps ("2026-09-25T00:00:00Z") -> date part
    return (str(release or row.get("release_date") or "").strip()[:10]) or None


# First day of Apple Music chart history we have (legacy data/ folder, then
# snapshots/ from 2026-06-05). A song released earlier only gets a
# "peak since <first day>".
APPLE_MUSIC_HISTORY_START = date(2026, 3, 22)

# All-time Global peaks of songs released before APPLE_MUSIC_HISTORY_START
# (owner-provided reference, 2026-09-25 — see the file's _source). Covers the
# part of their run we never collected, so the Global card can show a real
# all-time peak instead of "best in 2026".
GLOBAL_ALLTIME_PEAKS_PATH = REPO_ROOT / "db" / "apple_music_global_alltime_peaks.json"
# Per chart/day (scraped_at, song, rank) of past days for the Peak column
# (gitignored, rebuildable: delete it and it refills from the CSVs).
PEAK_ROWS_CACHE_DIR = HERE / "tools" / "cache" / "peak_rows"


def _global_alltime_peaks() -> dict[str, int]:
    try:
        payload = json.loads(GLOBAL_ALLTIME_PEAKS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    peaks: dict[str, int] = {}
    for song in payload.get("songs") or []:
        try:
            peak = int(song["peak"])
        except (KeyError, TypeError, ValueError):
            continue
        for key in _song_key_candidates(song.get("song_name")):
            peaks[key] = min(peak, peaks.get(key, peak))
    return peaks


def make_peak_resolver(
    chart_date: str, region: str, genre: str | None, current_scraped_at: str | None,
) -> Callable[[dict, int], tuple[int | None, str, str]]:
    """(peak, badge, since) per row for the album card's Peak column: best
    rank on this chart across EVERY collected cycle since the song's release
    day (same semantics as post_new_release_progression's country cards).
    badge = "NEW" (first appearance since release), "NEW PEAK" (beats every
    earlier cycle), "RE-PEAK" (equals the peak again after being lower on the
    previous cycle — owner 2026-09-25), or "".
    Song released before our history (APPLE_MUSIC_HISTORY_START) or whose
    release day has no snapshot: we only know part of its run -> peak over
    the collected days only, labelled "best in 2026" (owner
    2026-09-24; from 2027 on it becomes "since Mar 22, 2026", our history
    no longer covering the whole year; a gap on a later release day also
    gets "since <first collected day>"), and never a NEW / NEW PEAK badge (it may have debuted or
    peaked higher before we collected). No collected day at all -> dash."""
    keep = _region_row_filter(region, genre)
    cache: dict[str, list[dict]] = {}
    alltime_peaks = _global_alltime_peaks() if region == "global" and not genre else {}

    chart_slug = f"{region}_{(genre or 'all').strip().lower().replace('/', '-').replace(' ', '-')}"

    def day_rows(day: str) -> list[dict]:
        if day not in cache:
            # Past days never change: their (scraped_at, song, rank) rows are
            # cached on disk, else the Peak column re-parsed ~20 MB of genre
            # CSV per day since Mar 22 on every card (35s per Pop card,
            # 2026-09-25). Today is always re-read.
            disk = PEAK_ROWS_CACHE_DIR / chart_slug / f"{day}.json"
            if day < chart_date and disk.exists():
                try:
                    cache[day] = json.loads(disk.read_text(encoding="utf-8"))
                    return cache[day]
                except Exception:
                    pass
            path = _region_csv_path(day, region, genre)
            rows: list[dict] = []
            if path.exists():
                with path.open("r", encoding="utf-8-sig", newline="") as fh:
                    rows = [
                        {"scraped_at": r.get("scraped_at") or "", "song_name": r.get("song_name") or "",
                         "rank": r.get("rank") or ""}
                        for r in csv.DictReader(fh)
                        if str(r.get("date") or "").strip() == day and keep(r)
                    ]
            if day < chart_date and path.exists():
                try:
                    disk.parent.mkdir(parents=True, exist_ok=True)
                    disk.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
            cache[day] = rows
        return cache[day]

    def resolve(row: dict, rank: int) -> tuple[int | None, str, str]:
        release = _release_date_for(row)
        try:
            released = datetime.strptime(release, "%Y-%m-%d").date() if release else None
        except ValueError:
            released = None
        end = datetime.strptime(chart_date, "%Y-%m-%d").date()
        if released is not None and released > end:
            return None, "", ""
        start = max(released, APPLE_MUSIC_HISTORY_START) if released else APPLE_MUSIC_HISTORY_START
        complete = released is not None and released >= APPLE_MUSIC_HISTORY_START \
            and _region_csv_path(released.isoformat(), region, genre).exists()
        first_day = start
        while first_day <= end and not _region_csv_path(first_day.isoformat(), region, genre).exists():
            first_day += timedelta(days=1)
        if first_day > end:
            return None, "", ""
        keys = set(_song_key_candidates(row.get("song_name")))
        prior: int | None = None
        last_seen: tuple[str, int] | None = None  # (scraped_at, rank) of the previous cycle
        day = first_day
        while day <= end:
            for r in day_rows(day.isoformat()):
                if current_scraped_at and str(r.get("scraped_at") or "") == current_scraped_at:
                    continue
                if not keys.intersection(_song_key_candidates(r.get("song_name"))):
                    continue
                r_rank = _rank_int(r.get("rank"))
                if r_rank is not None and (prior is None or r_rank < prior):
                    prior = r_rank
                r_at = str(r.get("scraped_at") or "")
                if r_rank is not None and (last_seen is None or r_at >= last_seen[0]):
                    last_seen = (r_at, r_rank)
            day += timedelta(days=1)

        def peak_badge(best_before: int) -> str:
            """NEW PEAK = beats the peak; RE-PEAK = back exactly at the peak
            after having been lower on the previous cycle (owner 2026-09-25)."""
            if rank < best_before:
                return "NEW PEAK"
            if rank == best_before and last_seen is not None and last_seen[1] > rank:
                return "RE-PEAK"
            return ""

        peak = rank if prior is None else min(rank, prior)
        if not complete and alltime_peaks:
            ref = next((alltime_peaks[k] for k in keys if k in alltime_peaks), None)
            if ref is not None:
                # reference covers the uncollected start of the run -> all-time
                # peak, no "best in" label; NEW PEAK / RE-PEAK vs both.
                before = ref if prior is None else min(ref, prior)
                return min(peak, ref), peak_badge(before), ""
        if not complete:
            # Partial run: never NEW / NEW PEAK (it may have peaked higher before
            # we collected). Beating its best of the collected days = "BEST IN
            # 2026" (owner 2026-09-25, region cards), a true claim.
            best_badge = f"BEST IN {end.year}" if prior is not None and rank < prior else ""
            if released is not None and released < APPLE_MUSIC_HISTORY_START                     and end.year == APPLE_MUSIC_HISTORY_START.year:
                # owner 2026-09-24: released before our first data -> "best in 2026"
                return peak, best_badge, f"best in {end.year}"
            since = f"since {first_day.strftime('%b')} {first_day.day}, {first_day.year}"
            return peak, ("BEST " + since.upper()) if best_badge else "", since
        if prior is None:
            return peak, "NEW", ""
        return peak, peak_badge(prior), ""

    return resolve


def _compute_entries(
    rows: list[dict], prev_rank_fn: Callable[[dict], int | None], chart_date: str,
    peak_fn: Callable[[dict, int], tuple[int | None, str, str]] | None = None,
) -> list[dict]:
    entries: list[dict] = []
    for row in rows:
        rank = _rank_int(row.get("rank"))
        if rank is None:
            continue
        prev_rank = prev_rank_fn(row)
        song = str(row.get("song_name") or "").strip()
        release_date = next(
            (RELEASE_DATES[key] for key in _song_key_candidates(song) if key in RELEASE_DATES),
            None,
        )
        is_new_release = _is_recent_release(release_date, chart_date)
        chg_text, chg_css = _rank_change(rank, prev_rank, is_new_release)
        artist = str(row.get("artist_name") or "Taylor Swift").strip()
        album = str(row.get("album_name") or "").strip()
        peak, peak_badge, peak_since = peak_fn(row, rank) if peak_fn else (None, "", "")
        entries.append({
            "rank": rank,
            "chg_text": chg_text,
            "chg_css": chg_css,
            "song": song,
            "artist": artist,
            "album": album,
            "image_url": str(row.get("image_url") or ""),
            "peak": peak,
            "peak_badge": peak_badge,
            "peak_since": peak_since,
        })
    return entries


def _peak_cell_html(entry: dict) -> str:
    if entry.get("peak") is None:
        return '<div class="col-peak peak-unknown">&ndash;</div>'
    if entry.get("peak_since"):
        badge = f'<span class="peak-badge">{entry["peak_badge"]}</span>' if entry.get("peak_badge") else ""
        return (
            f'<div class="col-peak peak-partial"><span>#{entry["peak"]}{badge}</span>'
            f'<span class="peak-since">{entry["peak_since"]}</span></div>'
        )
    badge = f'<span class="peak-badge">{entry["peak_badge"]}</span>' if entry.get("peak_badge") else ""
    return f'<div class="col-peak">#{entry["peak"]}{badge}</div>'


# Peak column (album cards only): same pill as the debut country cards'
# NEW / NEW PEAK badge, in the Apple Music accent.
PEAK_CSS = """
.col-peak{
  font-size:17px;font-weight:800;color:#0b1f44;letter-spacing:-.02em;
  display:flex;align-items:center;justify-content:flex-end;gap:8px;
  font-variant-numeric:tabular-nums;white-space:nowrap;
}
.col-peak.peak-unknown{color:#98a2b3;font-weight:600}
.col-peak.peak-partial{flex-direction:column;align-items:flex-end;gap:2px}
.col-peak.peak-partial > span:first-child{display:flex;align-items:center;gap:8px}
.peak-since{font-size:11px;font-weight:600;color:#667085;letter-spacing:0}
.peak-badge{
  padding:3px 8px;border-radius:999px;background:#fa243c;color:#fff;
  font-size:10px;font-weight:800;letter-spacing:.04em;line-height:1;
}
"""


def _rows_html(entries: list[dict], with_peak: bool = False) -> str:
    if not entries:
        return (
            '<div class="data-row"><div class="col-entity" '
            'style="grid-column:1/-1;justify-content:center;color:#667085;padding:22px 0;">'
            "No Taylor Swift entries currently charting</div></div>"
        )

    from collectors.comp.tables_image import url_to_data_uri

    parts: list[str] = []
    for i, entry in enumerate(entries):
        subtitle = f"{entry['artist']} · {entry['album']}" if entry["album"] else entry["artist"]

        cover_url = url_to_data_uri(entry["image_url"])
        art_html = f'<img class="art" src="{cover_url}" />' if cover_url else '<div class="art-ph"></div>'

        card_cls = "data-row"
        if entry["rank"] == 1:
            card_cls += " row-gold"
        elif i % 2 != 0:
            card_cls += " row-odd"

        parts.append(f"""<div class="{card_cls}">
  <div class="col-rank">#{entry['rank']}</div>
  <div class="col-chg {entry['chg_css']}">{entry['chg_text']}</div>
  <div class="col-entity">
    {art_html}
    <div class="entity-info">
      <div class="entity-name">{html.escape(entry['song'])}</div>
      <div class="entity-sub">{html.escape(subtitle)}</div>
    </div>
  </div>
  {_peak_cell_html(entry) if with_peak else ""}
</div>
""")
    return "".join(parts)


def _notify_text(entries: list[dict]) -> str:
    if not entries:
        return "No Taylor Swift entries currently charting"
    return "\n".join(f"#{e['rank']} ({e['chg_text']}) {e['song']}" for e in entries)


def _global_signature(entries: list[dict]) -> dict[str, int]:
    return {e["song"]: e["rank"] for e in entries}


def maybe_notify_global_update(entries: list[dict], date_fmt: str) -> None:
    """Sends an ntfy notification only when the Global chart standings
    actually changed since the last time we notified (Global only updates
    once/day, but this runs every few hours -> avoid duplicate notifications)."""
    signature = _global_signature(entries)
    last_signature: dict = {}
    if GLOBAL_NOTIFY_STATE_PATH.exists():
        try:
            last_signature = json.loads(GLOBAL_NOTIFY_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            last_signature = {}

    if signature == last_signature:
        return

    try:
        notify_send(
            NTFY_TOPIC,
            _notify_text(entries),
            title="Taylor Swift · Global Apple Music",
            tags="musical_note,earth_africa",
        )
    except Exception as exc:
        print(f"[WARN] ntfy notification failed (non-blocking): {exc}")

    GLOBAL_NOTIFY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_NOTIFY_STATE_PATH.write_text(json.dumps(signature, ensure_ascii=False), encoding="utf-8")


def _label_for(region: str, genre: str | None) -> str:
    if region.lower() == "global":
        return "Global"
    label = region.upper()
    if genre:
        label += f" {genre.strip().title()}"
    return label


def _filename_for(region: str, genre: str | None, album: str | None = None) -> str:
    name = region.lower()
    if genre:
        name += f"_{genre.strip().lower().replace('/', '-').replace(' ', '-')}"
    if album:
        name += "_" + re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", album).lower()).strip("-")
    return name


def notify_global_for_date(chart_date: str) -> None:
    rows, _scraped_at = get_region_rows(chart_date, "global", None)
    prev_rank_fn, _previous_scraped_at = make_prev_rank_resolver(chart_date, "global", None)
    entries = _compute_entries(rows, prev_rank_fn, chart_date)
    date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    maybe_notify_global_update(entries, date_fmt)


def generate(
    chart_date: str,
    region: str,
    genre: str | None,
    out_dir: Path,
    album: tuple[str, set[str]] | None = None,
) -> Path:
    from collectors.comp.discography import display_title_for_album
    from collectors.comp.tables_image import build_table_html, render_html_to_png

    album_name, album_keys = album if album else (None, None)
    rows, scraped_at = get_region_rows(chart_date, region, genre)
    rows = _filter_album(rows, album_keys)
    prev_rank_fn, previous_scraped_at = make_prev_rank_resolver(chart_date, region, genre)
    # Peak column only on album cards (debut/deluxe follow-up): a full-chart
    # card would be mostly "since Mar 22" peaks (history starts 2026-03-22).
    # Peak column on every card since 2026-09-25 (owner: NEW PEAK / RE-PEAK /
    # BEST IN 2026 badges on the region cards too, not only the album ones).
    with_peak = True
    peak_fn = make_peak_resolver(chart_date, region, genre, scraped_at) if with_peak else None
    entries = _compute_entries(rows, prev_rank_fn, chart_date, peak_fn)
    label = _label_for(region, genre)

    date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    heading = display_title_for_album(album_name) if album_name else "Taylor Swift"
    title = f"{heading} · {label} Apple Music"
    rows_html = _rows_html(entries, with_peak=with_peak)

    snapshot_label = _format_snapshot_time(scraped_at) or date_fmt
    subtitle = f"Chart Snapshot · {snapshot_label}"
    if previous_scraped_at:
        subtitle += f" · vs {_format_snapshot_time(previous_scraped_at)}"

    html_doc = build_table_html(
        title=title,
        subtitle=subtitle,
        col_heads=[("Pos", False), ("+/-", False), ("Track", False)] + ([("Peak", True)] if with_peak else []),
        grid_cols="52px 64px minmax(240px,1fr)" + (" 150px" if with_peak else ""),
        extra_css=PEAK_CSS if with_peak else "",
        rows_html=rows_html,
        handle=HANDLE,
        date_str=snapshot_label,
        headers_dir=HERE,
        header_background=HEADER_BG,
        logo_svg=APPLE_MUSIC_LOGO_HTML,
    )

    filename = _filename_for(region, genre, album_name)
    out_path = out_dir / f"{filename}.png"
    render_html_to_png(html_doc, out_path, out_dir / f"_{filename}_tmp.html")
    print(f"[OK] {label}: {len(rows)} entrée(s) -> {out_path}")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Apple Music snapshot chart images for Taylor Swift.")
    parser.add_argument("--date", dest="chart_date", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--region", default=None, help="Country code (e.g. us, fr, gb) or 'global'")
    parser.add_argument("--genre", default=None, help="Genre name (e.g. Pop, Country, Alternative); requires --region")
    parser.add_argument(
        "--album",
        default=None,
        help="Keep only this album's songs (name, db/discography/albums file slug or unique substring, "
        "e.g. 'showgirl'); chart ranks are kept as-is and the PNG name gets an album suffix",
    )
    parser.add_argument("--list-genres", action="store_true", help="List available genre names and exit")
    parser.add_argument("--out-dir", default=None, help="Override output directory")
    parser.add_argument(
        "--notify-global-only",
        action="store_true",
        help="Send the Global Apple Music update notification for the selected date without rendering images.",
    )
    args = parser.parse_args()

    if args.list_genres:
        for name in GENRE_NAMES:
            print(name)
        return 0

    raw_date = args.chart_date or date.today().isoformat()
    try:
        chart_date = datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
    except ValueError:
        print(f"[ERROR] Invalid date: {raw_date!r}")
        return 1

    if args.notify_global_only:
        notify_global_for_date(chart_date)
        return 0

    if args.genre and not args.region:
        print("[ERROR] --genre requires --region")
        return 1

    out_dir = Path(args.out_dir) if args.out_dir else apple_music_charts_dir(chart_date) / "snapshot_images"
    out_dir.mkdir(parents=True, exist_ok=True)

    album = None
    if args.album:
        try:
            album = _resolve_album_filter(args.album)
        except ValueError as exc:
            print(f"[ERROR] --album: {exc}")
            return 1

    targets = [{"region": args.region, "genre": args.genre}] if args.region else DEFAULT_TARGETS

    failures = 0
    for target in targets:
        try:
            generate(chart_date, target["region"], target["genre"], out_dir, album)
        except Exception as exc:
            failures += 1
            print(f"[ERROR] {target['region']} {target['genre'] or ''}: {exc}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
