#!/usr/bin/env python3
"""
generate_albums_image.py — génère le PNG "Top Albums by Daily Streams".

Pour chaque album, compte toutes les éditions sauf "extras" / "extra".

Lit  : db/streams_history.csv + db/discography/albums/*.json
       db/discography/songs.json + db/discography/covers.json
Ecrit: snapshots/spotify_streams/YYYY/MM/YYYY-MM-DD/albums_image.png

Usage:
  python generate_albums_image.py               # dernière date dans le CSV
  python generate_albums_image.py 2026-03-15    # date spécifique
"""
import concurrent.futures
import csv
import json
import re
import sys
from datetime import date as date_cls, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent          # streams/tools/scripts/
_TOOLS       = SCRIPT_DIR.parent                        # streams/tools/
ROOT         = SCRIPT_DIR.parents[1]                    # streams/
REPO_ROOT    = SCRIPT_DIR.parents[4]                    # repo root
DB_DIR       = REPO_ROOT / "db"

sys.path.insert(0, str(ROOT.parent))         # collectors/spotify/ for core.*
sys.path.insert(0, str(ROOT.parent.parent))  # collectors/ for comp.*

from core.data_paths import first_existing_db_history, update_streams_dir  # noqa: E402
from comp.fmt import fmt_num, fmt_delta  # noqa: E402
from comp.discography import build_cover_map  # noqa: E402
from comp.tables_image import (  # noqa: E402
    download_as_data_uri, pick_header_image, get_dominant_color,
    rank_change, SPOTIFY_SVG, build_table_html,
)

HISTORY_PATH = first_existing_db_history("streams_history.csv")
COVERS_PATH  = DB_DIR / "discography" / "covers.json"
ALBUMS_DIR   = DB_DIR / "discography" / "albums"
SONGS_JSON   = DB_DIR / "discography" / "songs.json"
HEADERS_DIR  = _TOOLS / "headers"
HANDLE       = "@tsmuseum13"

# Regroupe OG + Taylor's Version sous la même ère.
ERA_MAP: dict[str, str] = {
    "Fearless (Taylor's Version)": "Fearless",
    "Speak Now (Taylor's Version)": "Speak Now",
    "Red (Taylor's Version)":      "Red",
    "1989 (Taylor's Version)":     "1989",
}

# Pour la cover, on préfère la TV quand elle existe.
ERA_COVER_PRIORITY: dict[str, list[str]] = {
    "Fearless":  ["Fearless (Taylor's Version)", "Fearless"],
    "Speak Now": ["Speak Now (Taylor's Version)", "Speak Now"],
    "Red":       ["Red (Taylor's Version)", "Red"],
    "1989":      ["1989 (Taylor's Version)", "1989"],
}

# Albums-specific CSS overrides applied on top of STREAMS_TABLE_CSS.
_ALBUMS_EXTRA_CSS = """
.art,.art-ph{width:38px;height:38px;border-radius:6px}
.data-row{height:48px;padding:0 18px}
.col-chg{font-size:11px;font-weight:800}
.col-rank{font-size:17px}
.entity-name{font-size:13.5px;line-height:1.15}
.col-num{font-size:12.5px}
.col-num.daily-val{color:#101828;font-weight:800}
.delta-wrap{gap:1px}
.delta-num{font-size:12px;font-weight:700}
.delta-pct{font-size:10px}
"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _is_unranked_era(name: str) -> bool:
    norm = _norm(name)
    parts = set(norm.split("_"))
    if "misc" in parts or "standalone" in parts:
        return True
    return norm in {
        "miscellaneous",
        "standalone_extras",
        "standalone_and_extras",
    }


# Compatibility alias: load_covers used by generate_weekend_streams_image.py
def load_covers() -> dict:
    return build_cover_map(COVERS_PATH)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_album_track_map() -> dict[str, dict]:
    """
    Returns {track_id: {album, edition, image_url}}
    Only from albums/*.json + songs.json.
    Compte toutes les éditions sauf "extras" / "extra".
    """
    result = {}

    def _as_bool(value) -> bool | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return None

    def _is_chart_extra(section: dict, track: dict) -> bool:
        track_flag = _as_bool(track.get("chart_extra"))
        if track_flag is not None:
            return track_flag
        section_flag = _as_bool(section.get("chart_extra"))
        return bool(section_flag) if section_flag is not None else False

    def _consume_sections(
        sections: list[dict],
        album_name_fallback: str = "",
    ) -> None:
        for section in sections:
            album_name = section.get("album") or album_name_fallback
            for track in section.get("tracks", []):
                url = (track.get("url") or track.get("spotify_url") or "").strip()
                m = re.search(r"track/([A-Za-z0-9]+)", url)
                if not m:
                    continue
                track_id = m.group(1)
                if track_id not in result:
                    result[track_id] = {
                        "album":       album_name,
                        "image_url":   (track.get("image_url") or "").strip(),
                        "chart_extra": _is_chart_extra(section, track),
                    }

    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
                if not isinstance(payload, dict):
                    continue
                _consume_sections(
                    payload.get("sections", []),
                    payload.get("album", ""),
                )
            except Exception as e:
                print(f"Erreur {album_file.name}: {e}")

    if SONGS_JSON.exists():
        try:
            _consume_sections(json.loads(SONGS_JSON.read_text(encoding="utf-8-sig")))
        except Exception as e:
            print(f"Erreur {SONGS_JSON.name}: {e}")
    return result


def load_history(target_date: str) -> tuple[dict, dict, dict]:
    """Returns today, yesterday, and same weekday last week track history maps."""
    target_day = date_cls.fromisoformat(target_date)
    yesterday = str(target_day - timedelta(days=1))
    day_before = str(target_day - timedelta(days=2))
    last_week = str(target_day - timedelta(days=7))
    last_week_prev = str(target_day - timedelta(days=8))
    today: dict[str, dict] = {}
    yest:  dict[str, dict] = {}
    before: dict[str, dict] = {}
    week: dict[str, dict] = {}
    week_before: dict[str, dict] = {}

    def _parse_optional_int(raw: str | None) -> int | None:
        s = (raw or "").strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None

    with open(HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            d = row["date"]
            if d not in (target_date, yesterday, day_before, last_week, last_week_prev):
                continue
            entry = {
                "streams":       int(row["streams"] or 0),
                "daily_streams": _parse_optional_int(row.get("daily_streams")),
            }
            if d == target_date:
                today[row["track_id"]] = entry
            elif d == last_week:
                week[row["track_id"]] = entry
            elif d == last_week_prev:
                week_before[row["track_id"]] = entry
            else:
                if d == yesterday:
                    yest[row["track_id"]] = entry
                else:
                    before[row["track_id"]] = entry

    def _fill_missing_daily(cur: dict[str, dict], prev: dict[str, dict]) -> None:
        for tid, e in cur.items():
            if e.get("daily_streams") is not None:
                continue
            p = prev.get(tid)
            if not p:
                continue
            diff = e.get("streams", 0) - p.get("streams", 0)
            if diff >= 0:
                e["daily_streams"] = diff

    _fill_missing_daily(today, yest)
    _fill_missing_daily(yest, before)
    _fill_missing_daily(week, week_before)

    return today, yest, week


def get_latest_date() -> str:
    latest = ""
    with open(HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["date"] > latest:
                latest = row["date"]
    if not latest:
        raise ValueError("streams_history.csv est vide")
    return latest


def build_album_rows(
    today: dict,
    yest: dict,
    week: dict,
    track_map: dict,
    covers: dict,
    *,
    merge_eras: bool = True,
) -> list[dict]:
    """
    Agrège les streams par album (éditions incluses seulement).
    Retourne une liste triée par daily_streams desc.
    """
    albums: dict[str, dict] = {}

    tracks_by_album: dict[str, list[tuple[str, dict]]] = {}
    for track_id, info in track_map.items():
        album = info.get("album") or ""
        if not album:
            continue
        tracks_by_album.setdefault(album, []).append((track_id, info))

    for album, album_tracks in tracks_by_album.items():
        cover_url = covers.get(_norm(album), "")
        if not cover_url:
            for _, info in album_tracks:
                if info.get("image_url"):
                    cover_url = info["image_url"]
                    break

        albums[album] = {
            "album":         album,
            "streams":       0,
            "daily_streams": 0,
            "yest_daily":    0,
            "week_daily":    0,
            "cover_url":     cover_url,
        }

        for track_id, info in album_tracks:
            if not merge_eras and info.get("chart_extra"):
                continue
            t = today.get(track_id)
            if t is None:
                continue
            y = yest.get(track_id, {})
            w = week.get(track_id, {})
            albums[album]["streams"]       += t["streams"]
            albums[album]["daily_streams"] += (t.get("daily_streams") or 0)
            albums[album]["yest_daily"] += (y.get("daily_streams") or 0)
            albums[album]["week_daily"] += (w.get("daily_streams") or 0)

    if not merge_eras:
        yest_ranked = sorted(
            [r for r in albums.values() if r.get("yest_daily")],
            key=lambda r: r["yest_daily"],
            reverse=True,
        )
        yest_rank_by_album = {r["album"]: i + 1 for i, r in enumerate(yest_ranked)}
        rows = sorted(albums.values(), key=lambda r: r["daily_streams"], reverse=True)
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
            row["prev_rank"] = yest_rank_by_album.get(row["album"])
        return rows

    eras: dict[str, dict] = {}
    for album_name, album_data in albums.items():
        era_name = ERA_MAP.get(album_name, album_name)
        if era_name not in eras:
            priority = ERA_COVER_PRIORITY.get(era_name, [era_name])
            cover_url = ""
            for prio_album in priority:
                cover_url = covers.get(_norm(prio_album), "")
                if cover_url:
                    break
            if not cover_url:
                cover_url = album_data["cover_url"]
            eras[era_name] = {
                "album":         era_name,
                "streams":       0,
                "daily_streams": 0,
                "yest_daily":    0,
                "week_daily":    0,
                "cover_url":     cover_url,
            }
        eras[era_name]["streams"]       += album_data["streams"]
        eras[era_name]["daily_streams"] += album_data["daily_streams"]
        eras[era_name]["yest_daily"]    += album_data["yest_daily"]
        eras[era_name]["week_daily"]    += album_data["week_daily"]

    yest_ranked = sorted(
        [r for r in eras.values() if r.get("yest_daily") and not _is_unranked_era(r["album"])],
        key=lambda r: r["yest_daily"],
        reverse=True,
    )
    yest_rank_by_album = {r["album"]: i + 1 for i, r in enumerate(yest_ranked)}

    rows = sorted(
        eras.values(),
        key=lambda r: (_is_unranked_era(r["album"]), -int(r["daily_streams"] or 0), r["album"]),
    )
    rank = 0
    for row in rows:
        if _is_unranked_era(row["album"]):
            row["rank"] = None
            row["prev_rank"] = None
            continue
        rank += 1
        row["rank"] = rank
        row["prev_rank"] = yest_rank_by_album.get(row["album"])
    return rows


def prefetch_covers(rows: list[dict]) -> dict[str, str]:
    urls = {r["cover_url"] for r in rows if r["cover_url"]}
    result: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(download_as_data_uri, u): u for u in urls}
        for fut, url in futures.items():
            data_uri = fut.result()
            if data_uri:
                result[url] = data_uri
    return result


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def build_rows_html(rows: list[dict], image_cache: dict[str, str]) -> str:
    html = ""
    for i, row in enumerate(rows):
        rank = row.get("rank")
        album = row["album"]
        daily = row["daily_streams"]
        total = row["streams"]
        yest  = row["yest_daily"]
        week  = row["week_daily"]
        cover = row["cover_url"]

        cover_uri = image_cache.get(cover, cover) if cover else ""
        art_html = (
            f'<img class="art" src="{cover_uri}" />'
            if cover_uri else '<div class="art-ph"></div>'
        )

        delta_num, delta_pct, delta_cls = fmt_delta(daily, yest)
        week_num, week_pct, week_cls = fmt_delta(daily, week)
        rank_label = f"#{rank}" if rank else ""
        chg_text, chg_css = rank_change(rank, row.get("prev_rank")) if rank else ("", "neutral")

        card_cls = "data-row"
        if rank == 1:
            card_cls += " row-gold"
        elif i % 2 != 0:
            card_cls += " row-odd"

        html += f"""<div class="{card_cls}">
  <div class="col-rank">{rank_label}</div>
  <div class="col-chg {chg_css}">{chg_text}</div>
  <div class="col-entity">
    {art_html}
    <div class="entity-name">{album}</div>
  </div>
  <div class="col-num daily-val">{fmt_num(daily)}</div>
  <div class="col-num {delta_cls}">
    <div class="delta-wrap">
      <span class="delta-num">{delta_num}</span>
      {f'<span class="delta-pct">{delta_pct}</span>' if delta_pct else ''}
    </div>
  </div>
  <div class="col-num {week_cls}">
    <div class="delta-wrap">
      <span class="delta-num">{week_num}</span>
      {f'<span class="delta-pct">{week_pct}</span>' if week_pct else ''}
    </div>
  </div>
  <div class="col-num">{fmt_num(total)}</div>
</div>
"""
    return html


def build_html(rows: list[dict], target_date: str, image_cache: dict[str, str]) -> str:
    from datetime import datetime
    date_fmt  = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows_html = build_rows_html(rows, image_cache)

    return build_table_html(
        title="Taylor Swift · Eras on Spotify",
        subtitle=f"Daily Streams · {date_fmt}",
        col_heads=[
            ("#", False), ("+/-", False), ("Album", False),
            ("Daily Streams", True), ("vs Yesterday", True), ("vs Last Week", True), ("Total", True),
        ],
        grid_cols="48px 46px minmax(220px,1fr) 138px 132px 132px 128px",
        rows_html=rows_html,
        handle=HANDLE,
        date_str=date_fmt,
        headers_dir=HEADERS_DIR,
        body_width=1000,
        art_size=38,
        col_gap=10,
        extra_css=_ALBUMS_EXTRA_CSS,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate(target_date: str | None = None) -> Path:
    if target_date is None:
        target_date = get_latest_date()
    print(f"[albums_image] Date: {target_date}")

    covers        = load_covers()
    track_map     = load_album_track_map()
    today, yest, week = load_history(target_date)

    if not today:
        raise ValueError(f"Aucune donnée pour {target_date}")

    rows = build_album_rows(today, yest, week, track_map, covers)
    print(f"[albums_image] {len(rows)} albums")
    for r in rows:
        rank_label = f"#{int(r['rank']):2d}" if r.get("rank") else "-- "
        print(f"  {rank_label} {r['album']:<45} daily={r['daily_streams']:>12,}  total={r['streams']:>15,}")

    print("[albums_image] Téléchargement des covers...")
    image_cache = prefetch_covers(rows)
    print(f"  {len(image_cache)} images téléchargées")

    html = build_html(rows, target_date, image_cache)

    out_dir  = update_streams_dir(target_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "albums_image.png"
    tmp_html = out_dir / "_albums_tmp.html"
    tmp_html.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 1000, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()

    print(f"[albums_image] Image générée : {out_path}")
    return out_path


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    generate(date_arg)
