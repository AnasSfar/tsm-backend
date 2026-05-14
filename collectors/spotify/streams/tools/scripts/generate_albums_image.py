#!/usr/bin/env python3
"""
generate_albums_image.py — génère le PNG "Top Albums by Daily Streams".

Pour chaque album, compte toutes les éditions sauf "extras" / "extra".

Lit  : db/streams_history.csv + db/discography/albums/*.json
       db/discography/songs.json + db/discography/covers.json
Ecrit: collectors/spotify/streams/history/YYYY/MM/YYYY-MM-DD/albums_image.png

Usage:
  python generate_albums_image.py               # dernière date dans le CSV
  python generate_albums_image.py 2026-03-15    # date spécifique
"""
import base64
import colorsys
import concurrent.futures
import csv
import json
import re
import sys
import urllib.request
from datetime import date as date_cls, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent          # streams/tools/scripts/
_TOOLS       = SCRIPT_DIR.parent                        # streams/tools/
ROOT         = SCRIPT_DIR.parents[1]                    # streams/
REPO_ROOT    = SCRIPT_DIR.parents[4]                    # repo root
DB_DIR       = REPO_ROOT / "db"
HISTORY_PATH = DB_DIR / "streams_history.csv"
COVERS_PATH  = DB_DIR / "discography" / "covers.json"
ALBUMS_DIR   = DB_DIR / "discography" / "albums"
SONGS_JSON   = DB_DIR / "discography" / "songs.json"
HEADERS_DIR  = _TOOLS / "headers"
HANDLE       = "@swiftiescharts"

EXCLUDED_EDITIONS = {"extras", "extra"}
EXCLUDED_DISPLAY_SECTIONS = {"extras", "extra"}

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

# ---------------------------------------------------------------------------
# Helpers (copiés depuis generate_streams_image.py)
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _pick_header_image() -> Path | None:
    if not HEADERS_DIR.exists():
        return None
    import random
    imgs = [p for p in HEADERS_DIR.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    return random.choice(imgs) if imgs else None


def _dominant_color(img_path: Path) -> str:
    if not _PIL:
        return "#1db954"
    try:
        img = Image.open(img_path).convert("RGB").resize((60, 60), Image.LANCZOS)
        pixels = list(img.getdata())
        filtered = [
            (r, g, b) for r, g, b in pixels
            if not (r > 210 and g > 210 and b > 210)
            and not (r < 40  and g < 40  and b < 40)
        ]
        if not filtered:
            filtered = pixels
        r = sum(p[0] for p in filtered) // len(filtered)
        g = sum(p[1] for p in filtered) // len(filtered)
        b = sum(p[2] for p in filtered) // len(filtered)
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        s = min(1.0, s * 1.8)
        v = min(1.0, max(0.55, v))
        r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
        return f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"
    except Exception:
        return "#1db954"


def _url_to_data_uri(url: str) -> str:
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            return f"data:{ct};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return ""


def fmt_num(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", "\u202f")


def fmt_delta(today, yesterday) -> tuple[str, str, str]:
    if today is None or yesterday is None or yesterday == 0:
        return "—", "", "neutral"
    delta = today - yesterday
    pct   = delta / yesterday * 100
    pct_s = f"{pct:+.1f}%"
    if pct_s == "-0.0%":
        pct_s = "+0.0%"
    if delta > 0:
        return f"+{fmt_num(delta)}", pct_s, "pos"
    elif delta < 0:
        return f"−{fmt_num(abs(delta))}", pct_s, "neg"
    return "=", pct_s, "neutral"


def rank_change(rank: int, prev_rank) -> tuple[str, str]:
    if prev_rank is None:
        return "NEW", "chg-new"
    delta = int(prev_rank) - rank
    if delta > 0:
        return f"▲{delta}", "chg-up"
    if delta < 0:
        return f"▼{abs(delta)}", "chg-dn"
    return "=", "chg-eq"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_covers() -> dict:
    """Returns {normalized_album_title → cover_url}."""
    if not COVERS_PATH.exists():
        return {}
    covers = json.loads(COVERS_PATH.read_text(encoding="utf-8-sig"))
    result = {}
    for v in covers.values():
        key = _norm(v.get("title", ""))
        if key and "cover_url" in v:
            result[key] = v["cover_url"]
    return result


def load_album_track_map() -> dict[str, dict]:
    """
    Returns {track_id: {album, edition, image_url}}
    Only from albums/*.json + songs.json.
    Compte toutes les éditions sauf "extras" / "extra".
    """
    result = {}

    def _to_int(raw, fallback: int) -> int:
        try:
            return int(raw)
        except Exception:
            try:
                return int(float(raw))
            except Exception:
                return fallback

    def _consume_sections(
        sections: list[dict],
        album_name_fallback: str = "",
        *,
        allow_all_non_extras: bool = True,
    ) -> None:
        for section in sections:
            album_name = section.get("album") or album_name_fallback
            for track in section.get("tracks", []):
                edition = (track.get("edition") or "").strip().lower()
                display_section = (track.get("display_section") or "").strip().lower()

                if allow_all_non_extras:
                    if edition in EXCLUDED_EDITIONS:
                        continue
                    if display_section in EXCLUDED_DISPLAY_SECTIONS:
                        continue

                url = (track.get("url") or track.get("spotify_url") or "").strip()
                m = re.search(r"track/([A-Za-z0-9]+)", url)
                if not m:
                    continue
                track_id = m.group(1)
                if track_id not in result:
                    result[track_id] = {
                        "album":     album_name,
                        "edition":   edition,
                        "image_url": (track.get("image_url") or "").strip(),
                        "song_family": (track.get("song_family") or "").strip(),
                        "display_section": (track.get("display_section") or "").strip(),
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
                    allow_all_non_extras=True,
                )
            except Exception as e:
                print(f"Erreur {album_file.name}: {e}")

    if SONGS_JSON.exists():
        try:
            _consume_sections(json.loads(SONGS_JSON.read_text(encoding="utf-8-sig")), allow_all_non_extras=True)
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


def build_album_rows(today: dict, yest: dict, week: dict, track_map: dict, covers: dict) -> list[dict]:
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

    def _best_key(day_entry: dict | None) -> tuple[int, int]:
        if not day_entry:
            return (-1, -1)
        return (int(day_entry.get("daily_streams") or 0), int(day_entry.get("streams") or 0))

    for album, album_tracks in tracks_by_album.items():
        # Dédoublonnage uniquement *dans une même section d'affichage*.
        # Ex: folklore contient des pistes Standard Edition en double avec deux IDs.
        dedupe = True

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

        if not dedupe:
            for track_id, _info in album_tracks:
                t = today.get(track_id)
                if t is None:
                    continue
                y = yest.get(track_id, {})
                w = week.get(track_id, {})
                albums[album]["streams"]       += t["streams"]
                albums[album]["daily_streams"] += (t.get("daily_streams") or 0)
                albums[album]["yest_daily"]    += (y.get("daily_streams") or 0)
                albums[album]["week_daily"]    += (w.get("daily_streams") or 0)
            continue

        best_today: dict[tuple[str, str], str] = {}
        best_yest: dict[tuple[str, str], str] = {}
        best_week: dict[tuple[str, str], str] = {}

        for track_id, info in album_tracks:
            fam = (info.get("song_family") or "").strip() or track_id
            sec = (info.get("display_section") or "").strip().lower()
            key = (fam, sec)

            t = today.get(track_id)
            if t is not None:
                prev_id = best_today.get(key)
                if prev_id is None or _best_key(t) > _best_key(today.get(prev_id)):
                    best_today[key] = track_id

            y = yest.get(track_id)
            if y is not None:
                prev_id = best_yest.get(key)
                if prev_id is None or _best_key(y) > _best_key(yest.get(prev_id)):
                    best_yest[key] = track_id

            w = week.get(track_id)
            if w is not None:
                prev_id = best_week.get(key)
                if prev_id is None or _best_key(w) > _best_key(week.get(prev_id)):
                    best_week[key] = track_id

        for track_id in best_today.values():
            t = today.get(track_id)
            if t is None:
                continue
            albums[album]["streams"]       += t["streams"]
            albums[album]["daily_streams"] += (t.get("daily_streams") or 0)

        for track_id in best_yest.values():
            y = yest.get(track_id)
            if y is None:
                continue
            albums[album]["yest_daily"] += (y.get("daily_streams") or 0)

        for track_id in best_week.values():
            w = week.get(track_id)
            if w is None:
                continue
            albums[album]["week_daily"] += (w.get("daily_streams") or 0)

    # Passe 2 : merge des albums en ères (OG + TV → une seule ligne).
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
        [r for r in eras.values() if r.get("yest_daily")],
        key=lambda r: r["yest_daily"],
        reverse=True,
    )
    yest_rank_by_album = {r["album"]: i + 1 for i, r in enumerate(yest_ranked)}

    rows = sorted(eras.values(), key=lambda r: r["daily_streams"], reverse=True)
    for row in rows:
        row["prev_rank"] = yest_rank_by_album.get(row["album"])
    return rows


def prefetch_covers(rows: list[dict]) -> dict[str, str]:
    urls = {r["cover_url"] for r in rows if r["cover_url"]}
    result: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(_url_to_data_uri, u): u for u in urls}
        for fut, url in futures.items():
            data_uri = fut.result()
            if data_uri:
                result[url] = data_uri
    return result


# ---------------------------------------------------------------------------
# CSS / HTML
# ---------------------------------------------------------------------------

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  background:
    radial-gradient(circle at 12% 18%, rgba(29,185,84,.13), transparent 30%),
    radial-gradient(circle at 84% 16%, rgba(126,87,255,.10), transparent 32%),
    linear-gradient(180deg,#f4f7f8 0%,#edf3f4 100%);
  width:1000px;
  padding:0;
  color:#101828;
}
.container{
  overflow:hidden;
}
.hdr{
  padding:22px 26px;
  display:flex;align-items:center;gap:18px;
}
.hdr-logo{width:64px;height:64px;flex-shrink:0}
.hdr-title{color:#fff;font-size:26px;font-weight:800;letter-spacing:-.3px}
.hdr-sub{color:rgba(255,255,255,.85);font-size:15px;margin-top:5px}
.col-heads{
  display:grid;
  grid-template-columns:48px 46px minmax(220px,1fr) 138px 132px 132px 128px;
  column-gap:10px;
  padding:9px 18px;
  background:rgba(241,245,246,.95);
  border-bottom:1px solid rgba(16,24,40,.07);
}
.col-heads span{
  font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#667085;
  display:flex;align-items:center;
}
.col-heads .right{justify-content:flex-end}
.album-card{
  display:grid;
  grid-template-columns:48px 46px minmax(220px,1fr) 138px 132px 132px 128px;
  column-gap:10px;
  align-items:center;
  height:48px;
  padding:0 18px;
  background:rgba(255,255,255,.82);
  border-bottom:1px solid rgba(16,24,40,.05);
}
.album-card.row-odd{background:rgba(248,250,251,.88)}
.album-card.row-gold{
  background:linear-gradient(90deg,#fff7d6 0%,#fffdf5 40%,rgba(255,255,255,.92) 100%);
  border-left:3px solid #ebc44c;
}
.col-rank{
  font-size:17px;font-weight:900;color:#0b1f44;
  letter-spacing:-.04em;
  display:flex;align-items:center;justify-content:center;
}
.col-chg{
  font-size:11px;font-weight:800;
  display:flex;align-items:center;justify-content:center;
}
.chg-up{color:#067647}
.chg-dn{color:#b42318}
.chg-eq{color:#9ca3af}
.chg-new{color:#5bbde4;font-size:10px;font-weight:800}
.col-album{display:flex;align-items:center;gap:9px;min-width:0}
.art{
  width:38px;height:38px;border-radius:6px;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
}
.art-ph{
  width:38px;height:38px;border-radius:6px;
  background:#dde3ea;flex-shrink:0;
}
.album-name{
  font-size:13.5px;font-weight:700;color:#101828;
  line-height:1.15;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.col-num{
  font-size:12.5px;color:#344054;font-weight:500;
  display:flex;align-items:center;justify-content:flex-end;
}
.col-num.daily-val{
  color:#101828;
  font-weight:800;
}
.pos{color:#067647;font-weight:600}
.neg{color:#b42318;font-weight:600}
.neutral{color:#667085}
.delta-wrap{display:flex;flex-direction:column;align-items:flex-end;gap:1px}
.delta-num{font-size:12px;font-weight:700}
.delta-pct{font-size:10px;font-weight:500;opacity:.85}
.ftr{
  background:rgba(241,245,246,.96);
  padding:11px 20px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid rgba(16,24,40,.07);
}
.ftr-handle{font-size:13px;color:#1db954;font-weight:700}
.ftr-date{font-size:13px;color:#667085;font-weight:500}
"""

SPOTIFY_SVG = """<svg class="hdr-logo" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
</svg>"""


def build_rows_html(rows: list[dict], image_cache: dict[str, str]) -> str:
    html = ""
    for i, row in enumerate(rows):
        rank  = i + 1
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
        chg_text, chg_css = rank_change(rank, row.get("prev_rank"))

        card_cls = "album-card"
        if rank == 1:
            card_cls += " row-gold"
        elif i % 2 != 0:
            card_cls += " row-odd"

        html += f"""<div class="{card_cls}">
  <div class="col-rank">#{rank}</div>
  <div class="col-chg {chg_css}">{chg_text}</div>
  <div class="col-album">
    {art_html}
    <div class="album-name">{album}</div>
  </div>
  <div class="col-num daily-val">+{fmt_num(daily)}</div>
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

    header_img   = _pick_header_image()
    handle_color = "#1db954"

    if header_img:
        handle_color = _dominant_color(header_img)
        img_url   = header_img.as_posix()
        hdr_style = (
            f'style="background-image: linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45)),'
            f'url(\'file:///{img_url}\'); background-size:cover; background-position:center;"'
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
      <div class="hdr-title">Taylor Swift · Eras on Spotify</div>
      <div class="hdr-sub">Daily Streams · {date_fmt}</div>
    </div>
  </div>
  <div class="col-heads">
    <span>#</span>
    <span>+/-</span>
    <span>Album</span>
    <span class="right">Daily Streams</span>
    <span class="right">vs Yesterday</span>
    <span class="right">vs Last Week</span>
    <span class="right">Total</span>
  </div>
  {rows_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div>
</body></html>"""


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
    for i, r in enumerate(rows, 1):
        print(f"  #{i:2d} {r['album']:<45} daily={r['daily_streams']:>12,}  total={r['streams']:>15,}")

    print("[albums_image] Téléchargement des covers...")
    image_cache = prefetch_covers(rows)
    print(f"  {len(image_cache)} images téléchargées")

    html = build_html(rows, target_date, image_cache)

    out_dir  = ROOT / "history" / target_date[:4] / target_date[5:7] / target_date
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
