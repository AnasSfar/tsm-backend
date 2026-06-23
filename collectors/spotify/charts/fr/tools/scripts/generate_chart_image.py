#!/usr/bin/env python3
"""
generate_chart_image.py — génère le PNG du chart Taylor Swift France.

Lit  : {date_dir}/ts_chart_{date}.json  +  ts_history.json
       + discography/albums/covers.json
Ecrit: {date_dir}/chart_image.png

Usage: python generate_chart_image.py YYYY-MM-DD
"""
import csv
import json
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT   = Path(__file__).parent
_TOOLS = Path(__file__).parent.parent          # = fr/tools/
sys.path.insert(0, str(Path(__file__).parents[5]))  # collectors/
sys.path.insert(0, str(Path(__file__).parents[4]))  # collectors/spotify/

from core.data_paths import first_existing, legacy_spotify_chart_dir, spotify_chart_dir
from comp.fmt import load_json, nan_to_none, fmt_streams, fmt_pct, pct_cls, get_pct
from comp.discography import build_cover_map, build_track_album_map, build_track_image_map, get_album_cover
from comp.tables_image import (
    url_to_data_uri, pick_header_image, get_dominant_color,
    rank_change, CSS, SPOTIFY_SVG, COL_HEADS_HTML,
    build_rows_html, build_out_rows_html, render_html_to_png,
)

DATA_DIR         = _TOOLS.parent / "history"
TS_HISTORY_PATH  = _TOOLS / "json" / "ts_history.json"
POP_HISTORY_PATH = _TOOLS / "json" / "ts_pop_history.json"
DISCOGRAPHY_ROOT = Path(__file__).parents[6] / "db" / "discography"
COVERS_PATH      = DISCOGRAPHY_ROOT / "covers.json"
HEADERS_DIR      = _TOOLS / "headers"
HANDLE           = "@theflameofanas"


def date_dir_for(chart_date: str) -> Path:
    return first_existing(
        spotify_chart_dir("fr", chart_date),
        legacy_spotify_chart_dir("fr", chart_date),
    )


def ref_streams_from_chart(track: str, ref_date: str):
    json_path = date_dir_for(ref_date) / f"ts_chart_{ref_date}.json"
    if not json_path.exists():
        return None
    try:
        for row in load_json(json_path):
            if str(row.get("track_name") or "") == track:
                streams = nan_to_none(row.get("streams"))
                return int(streams) if streams else None
    except Exception:
        return None
    return None


def ref_streams(track_hist: dict, track: str, ref_date: str, row: dict | None = None):
    streams = (track_hist.get(ref_date) or {}).get("streams")
    if streams:
        return streams
    return ref_streams_from_chart(track, ref_date)


def enrich_pop_rows(pop_rows: list, chart_date: str) -> list:
    """Fill pop_total_days from ts_pop_history.json where the field is missing/zero."""
    if not pop_rows:
        return pop_rows
    try:
        pop_history = json.loads(POP_HISTORY_PATH.read_text(encoding="utf-8-sig")) if POP_HISTORY_PATH.exists() else {}
    except Exception:
        return pop_rows
    for row in pop_rows:
        v = row.get("pop_total_days")
        missing = v is None or v == "" or (isinstance(v, float) and math.isnan(v)) or v == 0
        if missing:
            track = str(row.get("track_name") or "")
            past = sum(1 for d in pop_history.get(track, []) if d < chart_date)
            row["pop_total_days"] = past if past > 0 else None
    return pop_rows


def get_out_songs(chart_date: str, current_rows: list[dict]) -> list[dict]:
    """Returns TS songs from yesterday's CSV that are not in today's chart."""
    date_obj  = datetime.strptime(chart_date, "%Y-%m-%d").date()
    yesterday = str(date_obj - timedelta(days=1))
    csv_path  = date_dir_for(yesterday) / "ts_all_songs.csv"
    if not csv_path.exists():
        return []
    try:
        current_names = {str(r.get("song_name", "") or r.get("track_name", "")).lower() for r in current_rows}
        out_rows = []
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = str(row.get("song_name", "") or row.get("track_name", ""))
                row["track_name"] = name
                if name.lower() not in current_names:
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


def build_pop_rows_html(
    pop_rows,
    history: dict,
    chart_date: str,
    track_album_map: dict,
    cover_map: dict,
    track_image_map: dict,
) -> str:
    date_obj  = datetime.strptime(chart_date, "%Y-%m-%d").date()
    yesterday = str(date_obj - timedelta(days=1))
    week_ago  = str(date_obj - timedelta(days=7))

    html = ""
    for i, row in enumerate(pop_rows):
        track       = str(row.get("track_name") or "")
        artist      = str(row.get("artist_names") or "")
        pop_rank    = nan_to_none(row.get("pop_rank"))
        prev_pop    = nan_to_none(row.get("previous_pop_rank"))
        streams     = nan_to_none(row.get("streams"))
        streak      = nan_to_none(row.get("streak"))
        pop_total   = nan_to_none(row.get("pop_total_days"))
        scraped_img = row.get("image_url") or ""

        if pop_rank is None:
            continue
        pop_rank = int(pop_rank)

        chg_text, chg_css = rank_change(pop_rank, int(prev_pop) if prev_pop else None, pop_total)
        cover_url = url_to_data_uri(get_album_cover(track, track_album_map, cover_map, track_image_map, scraped_img))

        track_hist   = history.get(track, {})
        prev_streams = ref_streams(track_hist, track, yesterday)
        week_streams = ref_streams(track_hist, track, week_ago)
        streams_int  = int(streams) if streams else None

        daily_pct  = get_pct(streams_int, prev_streams)
        weekly_pct = get_pct(streams_int, week_streams)

        streams_fmt = fmt_streams(streams_int)
        daily_txt   = fmt_pct(daily_pct)
        weekly_txt  = fmt_pct(weekly_pct)
        consec_txt  = str(int(streak)) + "d" if streak else "—"
        pop_tot_txt = str(int(pop_total)) + "d" if pop_total else "—"

        art_html = (
            f'<img class="art" src="{cover_url}" />'
            if cover_url
            else '<div class="art-ph"></div>'
        )

        card_cls = "song-card"
        if pop_rank == 1:
            card_cls += " row-gold"
        elif i % 2 != 0:
            card_cls += " row-odd"

        html += f"""<div class="{card_cls}">
  <div class="col-rank">#{pop_rank}</div>
  <div class="col-chg {chg_css}">{chg_text}</div>
  <div class="col-song">
    {art_html}
    <div class="song-info">
      <div class="song-title">{track}</div>
      <div class="song-artist">{artist}</div>
    </div>
  </div>
  <div class="col-num">{streams_fmt}</div>
  <div class="col-num {pct_cls(daily_pct)}">{daily_txt}</div>
  <div class="col-num {pct_cls(weekly_pct)}">{weekly_txt}</div>
  <div class="col-num">{consec_txt}</div>
  <div class="col-num">{pop_tot_txt}</div>
</div>
"""
    return html


def build_html(
    rows,
    history: dict,
    chart_date: str,
    track_album_map: dict,
    cover_map: dict,
    track_image_map: dict,
    header_img: Path | None = None,
    out_songs: list | None = None,
) -> str:
    date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")

    if header_img is None:
        header_img = pick_header_image(HEADERS_DIR)
    handle_color = "#1db954"

    if header_img:
        handle_color = get_dominant_color(header_img)
        img_url   = header_img.as_posix()
        hdr_style = (
            f'style="background-image: linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45)),'
            f'url(\'file:///{img_url}\'); background-size:100% 100%;"'
        )
    else:
        hdr_style = 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"'

    sec_style = f'style="background:{handle_color};border-top:2px solid {handle_color};"'

    rows_html  = build_rows_html(rows, history, chart_date, track_album_map, cover_map, track_image_map, ref_streams)
    rows_html += build_out_rows_html(out_songs or [], track_album_map, cover_map, track_image_map, chart_date)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
<div class="container">
  <div class="hdr" {hdr_style}>
    {SPOTIFY_SVG}
    <div>
      <div class="hdr-title">Taylor Swift · France Spotify</div>
      <div class="hdr-sub">Daily Chart · {date_fmt}</div>
    </div>
  </div>
  <div class="section-hdr" {sec_style}>France Chart</div>
  {COL_HEADS_HTML}
  {rows_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div>
</body></html>"""


def generate(chart_date: str, header_img: Path | None = None) -> Path:
    date_dir  = date_dir_for(chart_date)
    json_path = date_dir / f"ts_chart_{chart_date}.json"
    out_path  = date_dir / "chart_image.png"

    if not json_path.exists():
        raise FileNotFoundError(f"ts_chart_{chart_date}.json introuvable: {json_path}")

    rows    = load_json(json_path)
    history = load_json(TS_HISTORY_PATH) if TS_HISTORY_PATH.exists() else {}

    if not rows:
        raise ValueError(f"Aucune chanson TS dans {json_path}")

    cover_map       = build_cover_map(COVERS_PATH)
    track_album_map = build_track_album_map(DISCOGRAPHY_ROOT)
    track_image_map = build_track_image_map(DISCOGRAPHY_ROOT)
    out_songs       = get_out_songs(chart_date, rows)

    html = build_html(rows, history, chart_date, track_album_map, cover_map, track_image_map,
                      header_img=header_img, out_songs=out_songs)
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
    history = load_json(TS_HISTORY_PATH) if TS_HISTORY_PATH.exists() else {}

    cover_map       = build_cover_map(COVERS_PATH)
    track_album_map = build_track_album_map(DISCOGRAPHY_ROOT)
    track_image_map = build_track_image_map(DISCOGRAPHY_ROOT)
    out_songs_data  = get_out_songs(chart_date, rows)

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for img_path in imgs:
            out_path = date_dir / f"chart_image_{img_path.stem}.png"
            html     = build_html(rows, history, chart_date, track_album_map, cover_map, track_image_map,
                                  header_img=img_path, out_songs=out_songs_data)
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

    history         = load_json(TS_HISTORY_PATH) if TS_HISTORY_PATH.exists() else {}
    cover_map       = build_cover_map(COVERS_PATH)
    track_album_map = build_track_album_map(DISCOGRAPHY_ROOT)
    track_image_map = build_track_image_map(DISCOGRAPHY_ROOT)

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
        combined_rows_html += build_rows_html(rows, history, chart_date, track_album_map, cover_map, track_image_map, ref_streams)
        combined_rows_html += build_out_rows_html(get_out_songs(chart_date, rows), track_album_map, cover_map, track_image_map, chart_date)

    if not valid_dates:
        raise ValueError("Aucun JSON trouvé pour les dates fournies")

    if header_img is None:
        header_img = pick_header_image(HEADERS_DIR)
    handle_color = "#1db954"

    if header_img:
        handle_color = get_dominant_color(header_img)
        img_url   = header_img.as_posix()
        hdr_style = (
            f'style="background-image: linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45)),'
            f'url(\'file:///{img_url}\'); background-size:100% 100%;"'
        )
    else:
        hdr_style = 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"'

    sec_style = f'style="background:{handle_color};border-top:2px solid {handle_color};"'

    first_fmt = datetime.strptime(valid_dates[0],  "%Y-%m-%d").strftime("%B %d")
    last_fmt  = datetime.strptime(valid_dates[-1], "%Y-%m-%d").strftime("%B %d, %Y")
    subtitle  = f"Daily Chart · {first_fmt} – {last_fmt}"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
<div class="container">
  <div class="hdr" {hdr_style}>
    {SPOTIFY_SVG}
    <div>
      <div class="hdr-title">Taylor Swift · France Spotify</div>
      <div class="hdr-sub">{subtitle}</div>
    </div>
  </div>
  <div class="section-hdr" {sec_style}>France Chart</div>
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
    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  python generate_chart_image.py YYYY-MM-DD [YYYY-MM-DD ...]")
        print("  python generate_chart_image.py YYYY-MM-DD --all-headers")
        print("  python generate_chart_image.py YYYY-MM-DD photo.jpg")
        sys.exit(1)

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
