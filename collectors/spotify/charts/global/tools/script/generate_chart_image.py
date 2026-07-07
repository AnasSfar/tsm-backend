#!/usr/bin/env python3
"""
generate_chart_image.py — génère le PNG du chart Taylor Swift Global.

Lit  : {date_dir}/ts_chart_{date}.json  +  ts_history.json
       + discography/albums/covers.json
Ecrit: {date_dir}/chart_image.png

Usage: python generate_chart_image.py YYYY-MM-DD
"""
import csv
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT    = Path(__file__).parent
_TOOLS  = Path(__file__).parent.parent          # = global/tools/
sys.path.insert(0, str(Path(__file__).parents[5]))  # collectors/
sys.path.insert(0, str(Path(__file__).parents[4]))  # collectors/spotify/

from core.data_paths import (
    first_existing, first_existing_db_history,
    legacy_spotify_chart_dir, spotify_chart_dir,
    WEB_EXPORT_DATA_DIR, LEGACY_WEBSITE_DATA_DIR,
)
from comp.fmt import load_json, nan_to_none, fmt_streams, fmt_pct, pct_cls, get_pct
from comp.discography import build_cover_map, build_track_album_map, build_track_image_map, get_album_cover
from comp.track_cover_cache import load_track_cover_cache
from comp.tables_image import (
    url_to_data_uri, pick_header_image, get_dominant_color,
    rank_change, CSS, SPOTIFY_SVG, COL_HEADS_HTML,
    build_rows_html, build_out_rows_html, render_html_to_png,
)

_DATA            = _TOOLS.parent / "history"
TS_HISTORY_PATH  = _TOOLS / "json" / "ts_history.json"
ARCHIVE_CSV      = first_existing_db_history("charts_history_global.csv")
DISCOGRAPHY_ROOT = Path(__file__).parents[6] / "db" / "discography"
COVERS_PATH      = DISCOGRAPHY_ROOT / "covers.json"
HEADERS_DIR      = _TOOLS / "headers"
HANDLE           = "@swiftiescharts"
CHART_REGION     = "global"
CHART_REGION_NAME = "Global"


def date_dir_for(chart_date: str) -> Path:
    return first_existing(
        spotify_chart_dir(CHART_REGION, chart_date),
        legacy_spotify_chart_dir(CHART_REGION, chart_date),
    )


def ref_streams_from_chart(track: str, ref_date: str, row: dict | None = None):
    json_path = date_dir_for(ref_date) / f"ts_chart_{ref_date}.json"
    if not json_path.exists():
        return None
    track_id = str((row or {}).get("track_id") or "").strip()
    try:
        for ref_row in load_json(json_path):
            ref_track_id = str(ref_row.get("track_id") or "").strip()
            if track_id and ref_track_id and ref_track_id != track_id:
                continue
            if not track_id and str(ref_row.get("track_name") or "") != track:
                continue
            streams = nan_to_none(ref_row.get("streams"))
            if streams:
                return int(streams) if streams else None
    except Exception:
        return None
    return None


_archive_rows_cache: dict[str, list[dict]] | None = None


def _archive_rows_by_date() -> dict[str, list[dict]]:
    global _archive_rows_cache
    if _archive_rows_cache is not None:
        return _archive_rows_cache
    rows_by_date: dict[str, list[dict]] = {}
    if ARCHIVE_CSV.exists():
        try:
            with ARCHIVE_CSV.open(newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    chart_date = (row.get("date") or "").strip()
                    if chart_date:
                        rows_by_date.setdefault(chart_date, []).append(row)
        except Exception:
            rows_by_date = {}
    _archive_rows_cache = rows_by_date
    return rows_by_date


def ref_streams_from_archive(track: str, ref_date: str):
    """Read streams from db/charts_history_global.csv when snapshots are missing."""
    if CHART_REGION != "global":
        return None
    for row in _archive_rows_by_date().get(ref_date, []):
        name = str(row.get("song_name") or row.get("track_name") or "")
        if name != track:
            continue
        streams = nan_to_none(row.get("streams"))
        try:
            return int(float(streams)) if streams not in (None, "") else None
        except (TypeError, ValueError):
            return None
    return None


_worldwide_rows_cache: dict[str, dict] = {}


def ref_streams_from_worldwide(row: dict, ref_date: str):
    track_id = str(row.get("track_id") or "").strip()
    if not track_id:
        return None
    if ref_date not in _worldwide_rows_cache:
        json_path = first_existing(
            spotify_chart_dir("worldwide", ref_date) / f"ts_worldwide_{ref_date}.json",
            legacy_spotify_chart_dir("worldwide", ref_date) / f"ts_worldwide_{ref_date}.json",
        )
        if not json_path.exists():
            _worldwide_rows_cache[ref_date] = {}
        else:
            try:
                data = load_json(json_path)
                _worldwide_rows_cache[ref_date] = data.get("by_track", {}) if isinstance(data, dict) else {}
            except Exception:
                _worldwide_rows_cache[ref_date] = {}
    for entry in _worldwide_rows_cache.get(ref_date, {}).get(track_id, []):
        if entry.get("country") == CHART_REGION:
            streams = nan_to_none(entry.get("streams"))
            return int(streams) if streams else None
    return None


def ref_streams(track_hist: dict, track: str, ref_date: str, row: dict | None = None):
    streams = (track_hist.get(ref_date) or {}).get("streams")
    if streams:
        return streams
    from_chart = ref_streams_from_chart(track, ref_date, row)
    if from_chart:
        return from_chart
    if row is not None:
        from_worldwide = ref_streams_from_worldwide(row, ref_date)
        if from_worldwide:
            return from_worldwide
    return ref_streams_from_archive(track, ref_date)


def get_out_songs(chart_date: str, current_rows: list[dict]) -> list[dict]:
    """Returns TS songs from yesterday's CSV (or archive) that are not in today's chart."""
    date_obj  = datetime.strptime(chart_date, "%Y-%m-%d").date()
    yesterday = str(date_obj - timedelta(days=1))
    yesterday_dir = date_dir_for(yesterday)
    json_path = yesterday_dir / f"ts_chart_{yesterday}.json"
    csv_path  = yesterday_dir / "ts_all_songs.csv"
    if not json_path.exists() and not csv_path.exists() and yesterday not in _archive_rows_by_date():
        return []
    try:
        current_names = {str(r.get("song_name", "") or r.get("track_name", "")).lower() for r in current_rows}
        current_ids = {str(r.get("track_id") or "").strip() for r in current_rows if str(r.get("track_id") or "").strip()}
        out_rows = []
        source_rows: list[dict]
        if json_path.exists():
            source_rows = list(load_json(json_path))
        elif csv_path.exists():
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                source_rows = list(csv.DictReader(f))
        else:
            source_rows = list(_archive_rows_by_date().get(yesterday, []))
        for row in source_rows:
            name = str(row.get("song_name", "") or row.get("track_name", ""))
            row["track_name"] = name
            track_id = str(row.get("track_id") or "").strip()
            present_today = (track_id in current_ids) if track_id and current_ids else (name.lower() in current_names)
            if not present_today:
                try:
                    row["rank"] = int(float(row["rank"])) if row.get("rank") else None
                except (ValueError, TypeError):
                    row["rank"] = None
                try:
                    row["streams"] = int(float(row["streams"])) if row.get("streams") else None
                except (ValueError, TypeError):
                    row["streams"] = None
                out_rows.append(row)
        return out_rows
    except Exception:
        return []


def build_html(
    rows,
    history: dict,
    chart_date: str,
    track_album_map: dict,
    cover_map: dict,
    track_image_map: dict,
    header_img: Path | None = None,
    out_songs: list | None = None,
    track_cover_cache: dict | None = None,
) -> str:
    date_fmt  = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows_html  = build_rows_html(rows, history, chart_date, track_album_map, cover_map, track_image_map, ref_streams, track_cover_cache)
    rows_html += build_out_rows_html(out_songs or [], track_album_map, cover_map, track_image_map, chart_date, track_cover_cache)

    if header_img is None:
        header_img = pick_header_image(HEADERS_DIR)
    handle_color = "#1db954"

    if header_img:
        handle_color = get_dominant_color(header_img)
        img_url      = header_img.as_posix()
        hdr_style    = (
            f'style="background-image: linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45)),'
            f'url(\'file:///{img_url}\'); background-size:100% 100%;"'
        )
    else:
        hdr_style = 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"'

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
<div class="container">
  <div class="hdr" {hdr_style}>
    {SPOTIFY_SVG}
    <div>
      <div class="hdr-title">Taylor Swift · {CHART_REGION_NAME} Spotify</div>
      <div class="hdr-sub">Daily Chart · {date_fmt}</div>
    </div>
  </div>
  {COL_HEADS_HTML}
  {rows_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div>
</body></html>"""


def _build_track_image_map() -> dict:
    web_songs_path = first_existing(
        WEB_EXPORT_DATA_DIR / "songs.json",
        LEGACY_WEBSITE_DATA_DIR / "songs.json",
    )
    extra = [web_songs_path] if web_songs_path else []
    return build_track_image_map(DISCOGRAPHY_ROOT, extra_song_sources=extra)


def generate(chart_date: str, header_img: Path | None = None) -> Path:
    date_dir  = date_dir_for(chart_date)
    json_path = date_dir / f"ts_chart_{chart_date}.json"
    out_path  = date_dir / "chart_image.png"

    if not json_path.exists():
        raise FileNotFoundError(f"ts_chart_{chart_date}.json introuvable: {json_path}")

    rows    = load_json(json_path)
    history = load_json(TS_HISTORY_PATH) if CHART_REGION == "global" and TS_HISTORY_PATH.exists() else {}

    if not rows:
        raise ValueError(f"Aucune chanson TS dans {json_path}")

    cover_map         = build_cover_map(COVERS_PATH)
    track_album_map   = build_track_album_map(DISCOGRAPHY_ROOT)
    track_image_map   = _build_track_image_map()
    track_cover_cache = load_track_cover_cache()
    out_songs         = get_out_songs(chart_date, rows)

    html = build_html(rows, history, chart_date, track_album_map, cover_map, track_image_map,
                      header_img=header_img, out_songs=out_songs, track_cover_cache=track_cover_cache)
    render_html_to_png(html, out_path, date_dir / "_chart_tmp.html")
    print(f"OK image: {out_path}")
    return out_path


def generate_all_headers(chart_date: str) -> list[Path]:
    """Génère une image par photo dans headers/, nommée chart_image_<photo>.png."""
    if not HEADERS_DIR.exists():
        print("Dossier headers/ introuvable")
        return []

    imgs = [p for p in sorted(HEADERS_DIR.iterdir())
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    if not imgs:
        print("Aucune photo dans headers/")
        return []

    date_dir  = date_dir_for(chart_date)
    json_path = date_dir / f"ts_chart_{chart_date}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"ts_chart_{chart_date}.json introuvable: {json_path}")

    rows    = load_json(json_path)
    history = load_json(TS_HISTORY_PATH) if CHART_REGION == "global" and TS_HISTORY_PATH.exists() else {}

    cover_map         = build_cover_map(COVERS_PATH)
    track_album_map   = build_track_album_map(DISCOGRAPHY_ROOT)
    track_image_map   = _build_track_image_map()
    track_cover_cache = load_track_cover_cache()

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for img_path in imgs:
            out_path = date_dir / f"chart_image_{img_path.stem}.png"
            html     = build_html(rows, history, chart_date, track_album_map, cover_map, track_image_map,
                                  header_img=img_path, track_cover_cache=track_cover_cache)
            html_tmp = date_dir / "_chart_tmp.html"
            html_tmp.write_text(html, encoding="utf-8")
            try:
                page = browser.new_page(viewport={"width": 860, "height": 200}, device_scale_factor=2)
                page.goto(f"file:///{html_tmp.as_posix()}", wait_until="load")
                page.wait_for_load_state("networkidle", timeout=3000)
                try:
                    full_h = page.evaluate("() => document.body.scrollHeight")
                    full_h = int(full_h) if full_h else 200
                    full_h = max(200, min(full_h, 6000))
                    page.set_viewport_size({"width": 860, "height": full_h})
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                page.locator("body").screenshot(path=str(out_path))
                page.close()
                print(f"OK: {out_path.name}")
                results.append(out_path)
            finally:
                if html_tmp.exists():
                    html_tmp.unlink()
        browser.close()

    print(f"\n{len(results)} images générées dans {date_dir}/")
    return results


def generate_multi(chart_dates: list[str], header_img: Path | None = None) -> Path:
    """Génère une seule image PNG combinant plusieurs dates (séparées par un bandeau)."""
    out_path = ROOT / "chart_image_multi.png"

    history           = load_json(TS_HISTORY_PATH) if CHART_REGION == "global" and TS_HISTORY_PATH.exists() else {}
    cover_map         = build_cover_map(COVERS_PATH)
    track_album_map   = build_track_album_map(DISCOGRAPHY_ROOT)
    track_image_map   = _build_track_image_map()
    track_cover_cache = load_track_cover_cache()

    combined_rows_html = ""
    valid_dates = []
    for chart_date in chart_dates:
        date_dir  = date_dir_for(chart_date)
        json_path = date_dir / f"ts_chart_{chart_date}.json"
        if not json_path.exists():
            print(f"  JSON introuvable pour {chart_date}, ignoré")
            continue
        rows = load_json(json_path)
        if not rows:
            continue
        valid_dates.append(chart_date)
        date_label = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
        combined_rows_html += f'<div class="day-hdr">{date_label}</div>\n'
        combined_rows_html += build_rows_html(rows, history, chart_date, track_album_map, cover_map, track_image_map, ref_streams, track_cover_cache)
        combined_rows_html += build_out_rows_html(get_out_songs(chart_date, rows), track_album_map, cover_map, track_image_map, chart_date, track_cover_cache)

    if not valid_dates:
        raise ValueError("Aucun JSON trouvé pour les dates fournies")

    if header_img is None:
        header_img = pick_header_image(HEADERS_DIR)
    handle_color = "#1db954"

    first_fmt = datetime.strptime(valid_dates[0],  "%Y-%m-%d").strftime("%B %d")
    last_fmt  = datetime.strptime(valid_dates[-1], "%Y-%m-%d").strftime("%B %d, %Y")
    subtitle  = f"Daily Chart · {first_fmt} – {last_fmt}"

    if header_img:
        handle_color = get_dominant_color(header_img)
        img_url      = header_img.as_posix()
        hdr_style    = (
            f'style="background-image: linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45)),'
            f'url(\'file:///{img_url}\'); background-size:100% 100%;"'
        )
    else:
        hdr_style = 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"'

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
<div class="container">
  <div class="hdr" {hdr_style}>
    {SPOTIFY_SVG}
    <div>
      <div class="hdr-title">Taylor Swift · {CHART_REGION_NAME} Spotify</div>
      <div class="hdr-sub">{subtitle}</div>
    </div>
  </div>
  {COL_HEADS_HTML}
  {combined_rows_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{last_fmt}</span>
  </div>
</div>
</body></html>"""

    render_html_to_png(html, out_path, ROOT / "_chart_tmp.html")
    print(f"OK image multi: {out_path}")
    return out_path


def main():
    global CHART_REGION, CHART_REGION_NAME
    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  python generate_chart_image.py YYYY-MM-DD [YYYY-MM-DD ...]")
        print("  python generate_chart_image.py YYYY-MM-DD --all-headers")
        print("  python generate_chart_image.py YYYY-MM-DD photo.jpg")
        print("  python generate_chart_image.py YYYY-MM-DD --region REGION --region-name NAME")
        sys.exit(1)

    cleaned_args: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--region" and i + 1 < len(args):
            CHART_REGION = args[i + 1].strip().lower()
            i += 2
            continue
        if arg == "--region-name" and i + 1 < len(args):
            CHART_REGION_NAME = args[i + 1].strip() or CHART_REGION_NAME
            i += 2
            continue
        cleaned_args.append(arg)
        i += 1
    args = cleaned_args

    dates     = [a for a in args if re.match(r"^\d{4}-\d{2}-\d{2}$", a)]
    non_dates = [a for a in args if not re.match(r"^\d{4}-\d{2}-\d{2}$", a)]

    if len(dates) > 1:
        generate_multi(dates)
    elif "--all-headers" in non_dates:
        generate_all_headers(dates[0])
    elif non_dates and not non_dates[0].startswith("--"):
        header_path = Path(non_dates[0])
        if not header_path.is_absolute():
            header_path = ROOT / "headers" / header_path
        generate(dates[0], header_img=header_path)
    else:
        generate(dates[0])


if __name__ == "__main__":
    main()
