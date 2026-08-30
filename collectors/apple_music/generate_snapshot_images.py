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

  python generate_snapshot_images.py --list-genres
      -> liste les genres disponibles.
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

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collectors.spotify.core.data_paths import (  # noqa: E402
    apple_music_charts_dir,
)
from collectors.spotify.core.notify import send as notify_send  # noqa: E402
# NB: collectors.comp.tables_image is imported lazily inside the rendering
# helpers below -- it pulls in playwright / Pillow, which the data-only CI
# path (--notify-global-only) has no reason to install.
from core.config import GENRES  # noqa: E402

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


def _read_latest_rows(csv_path: Path, chart_date: str) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = [dict(row) for row in csv.DictReader(fh)]
    day_rows = [row for row in rows if str(row.get("date") or "").strip() == chart_date]
    if not day_rows:
        return []
    latest = max(str(row.get("scraped_at") or "") for row in day_rows)
    return [row for row in day_rows if str(row.get("scraped_at") or "") == latest]


def _rank_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _song_name_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text)
    return text


def _song_key_candidates(value: object) -> list[str]:
    """Apple sometimes retitles a track with a soundtrack/feature suffix
    (e.g. 'Song (From "Movie")') that our catalog doesn't have -> also try
    the key with that trailing parenthetical stripped."""
    key = _song_name_key(value)
    candidates = [key]
    stripped = re.sub(r"\s*\(from\b[^)]*\)\s*$", "", key).strip()
    if stripped and stripped != key:
        candidates.append(stripped)
    return candidates


def _iter_catalog_tracks() -> list[dict]:
    """Every track dict from the discography source-of-truth files
    (db/discography/, committed to git — unlike the runtime web export,
    which only exists on hosts that also ran the Spotify streams export,
    not the case on the Apple Music-only VPS)."""
    tracks: list[dict] = []

    def _collect(sections: object) -> None:
        if not isinstance(sections, list):
            return
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_tracks = section.get("tracks")
            if isinstance(section_tracks, list):
                tracks.extend(t for t in section_tracks if isinstance(t, dict))

    albums_dir = DISCOGRAPHY_DIR / "albums"
    if albums_dir.is_dir():
        for path in sorted(albums_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if isinstance(payload, dict):
                _collect(payload.get("sections"))

    for name in ("songs.json", "features.json", "misc.json"):
        path = DISCOGRAPHY_DIR / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(payload, list):
            _collect(payload)

    return tracks


def _load_release_dates() -> dict[str, str]:
    """Map every known song-title key -> its catalog release_date, so a
    missing previous_rank can be told apart between a genuine new release
    and a re-entry (data-rules: never infer NEW for an already-released
    song)."""
    dates: dict[str, str] = {}
    for track in _iter_catalog_tracks():
        release_date = str(track.get("release_date") or "").strip()
        if not release_date:
            continue
        for field in ("title", "base_title"):
            key = _song_name_key(track.get(field))
            if key and key not in dates:
                dates[key] = release_date
    return dates


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
        return f"▲ {delta}", "chg-up"
    if delta < 0:
        return f"▼ {abs(delta)}", "chg-dn"
    return "=", "chg-eq"


def get_region_rows(chart_date: str, region: str, genre: str | None) -> list[dict]:
    region = region.lower().strip()
    if region == "global":
        path = apple_music_charts_dir(chart_date) / "apple_music_global.csv"
        rows = [r for r in _read_latest_rows(path, chart_date) if r.get("chart_type") == "global"]
    elif genre:
        path = apple_music_charts_dir(chart_date) / "apple_music_genre_charts.csv"
        genre_norm = genre.strip().lower()
        rows = [
            r for r in _read_latest_rows(path, chart_date)
            if str(r.get("country") or "").lower() == region
            and str(r.get("genre_name") or "").lower() == genre_norm
        ]
    else:
        path = apple_music_charts_dir(chart_date) / "apple_music_country_charts.csv"
        rows = [r for r in _read_latest_rows(path, chart_date) if str(r.get("country") or "").lower() == region]

    rows.sort(key=lambda r: _rank_int(r.get("rank")) or 9999)
    return rows


def _track_key(row: dict) -> str:
    track_id = str(row.get("apple_music_id") or "").strip()
    return track_id or str(row.get("song_name") or "").strip().lower()


def get_previous_day_ranks(chart_date: str, region: str, genre: str | None) -> dict[str, int]:
    """Rank map from yesterday's last snapshot, for day-over-day deltas."""
    yesterday = str(datetime.strptime(chart_date, "%Y-%m-%d").date() - timedelta(days=1))
    mapping: dict[str, int] = {}
    for row in get_region_rows(yesterday, region, genre):
        rank = _rank_int(row.get("rank"))
        key = _track_key(row)
        if rank is not None and key:
            mapping[key] = rank
    return mapping


def make_prev_rank_resolver(chart_date: str, region: str, genre: str | None) -> Callable[[dict], int | None]:
    """Global only updates once/day, so a run-over-run diff sits on "=" for
    hours -> compare vs yesterday. US/genre charts update near-hourly, so
    run-over-run (the CSV's own previous_rank) shows the real movement."""
    if region.lower().strip() == "global":
        previous_day_ranks = get_previous_day_ranks(chart_date, region, genre)
        return lambda row: previous_day_ranks.get(_track_key(row))
    return lambda row: _rank_int(row.get("previous_rank"))


def _compute_entries(
    rows: list[dict], prev_rank_fn: Callable[[dict], int | None], chart_date: str,
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
        entries.append({
            "rank": rank,
            "chg_text": chg_text,
            "chg_css": chg_css,
            "song": song,
            "artist": artist,
            "album": album,
            "image_url": str(row.get("image_url") or ""),
        })
    return entries


def _rows_html(entries: list[dict]) -> str:
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


def _filename_for(region: str, genre: str | None) -> str:
    name = region.lower()
    if genre:
        name += f"_{genre.strip().lower().replace('/', '-').replace(' ', '-')}"
    return name


def notify_global_for_date(chart_date: str) -> None:
    rows = get_region_rows(chart_date, "global", None)
    prev_rank_fn = make_prev_rank_resolver(chart_date, "global", None)
    entries = _compute_entries(rows, prev_rank_fn, chart_date)
    date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    maybe_notify_global_update(entries, date_fmt)


def generate(chart_date: str, region: str, genre: str | None, out_dir: Path) -> Path:
    from collectors.comp.tables_image import build_table_html, render_html_to_png

    rows = get_region_rows(chart_date, region, genre)
    prev_rank_fn = make_prev_rank_resolver(chart_date, region, genre)
    entries = _compute_entries(rows, prev_rank_fn, chart_date)
    label = _label_for(region, genre)

    date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    title = f"Taylor Swift · {label} Apple Music"
    rows_html = _rows_html(entries)

    html_doc = build_table_html(
        title=title,
        subtitle=f"Chart Snapshot · {date_fmt}",
        col_heads=[("Pos", False), ("+/-", False), ("Track", False)],
        grid_cols="52px 64px minmax(240px,1fr)",
        rows_html=rows_html,
        handle=HANDLE,
        date_str=date_fmt,
        headers_dir=HERE,
        header_background=HEADER_BG,
        logo_svg=APPLE_MUSIC_LOGO_HTML,
    )

    filename = _filename_for(region, genre)
    out_path = out_dir / f"{filename}.png"
    render_html_to_png(html_doc, out_path, out_dir / f"_{filename}_tmp.html")
    print(f"[OK] {label}: {len(rows)} entrée(s) -> {out_path}")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Apple Music snapshot chart images for Taylor Swift.")
    parser.add_argument("--date", dest="chart_date", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--region", default=None, help="Country code (e.g. us, fr, gb) or 'global'")
    parser.add_argument("--genre", default=None, help="Genre name (e.g. Pop, Country, Alternative); requires --region")
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

    targets = [{"region": args.region, "genre": args.genre}] if args.region else DEFAULT_TARGETS

    failures = 0
    for target in targets:
        try:
            generate(chart_date, target["region"], target["genre"], out_dir)
        except Exception as exc:
            failures += 1
            print(f"[ERROR] {target['region']} {target['genre'] or ''}: {exc}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
