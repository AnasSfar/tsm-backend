#!/usr/bin/env python3
"""
generate_streams_image.py — génère le PNG des chansons les plus streamées daily (top configurable, défaut=10).

Lit  : db/streams_history.csv  +  db/discography/songs.json  +  db/discography/covers.json
Ecrit: collectors/spotify/streams/history/YYYY/MM/YYYY-MM-DD/streams_image.png

Usage:
  python generate_streams_image.py               # dernière date dans le CSV
  python generate_streams_image.py 2026-03-15    # date spécifique
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
SONGS_JSON   = DB_DIR / "discography" / "songs.json"
ALBUMS_DIR   = DB_DIR / "discography" / "albums"
HEADERS_DIR  = _TOOLS / "headers"
HANDLE       = "@swiftiescharts"

TOP_N = 15

# ---------------------------------------------------------------------------
# Header image + dominant colour (same helpers as chart image)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def load_covers() -> dict:
    """Returns {normalized_album_title → cover_url}."""
    if not COVERS_PATH.exists():
        return {}
    covers = json.loads(COVERS_PATH.read_text(encoding="utf-8"))
    result = {}
    for v in covers.values():
        key = _norm(v.get("title", ""))
        if key and "cover_url" in v:
            result[key] = v["cover_url"]
    return result


def load_track_album_map() -> dict:
    """Returns {normalized_track_title → album_title} from album files + songs.json."""
    result = {}

    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            album_name = payload.get("album", "") if isinstance(payload, dict) else ""
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                for track in section.get("tracks", []):
                    title = track.get("title", "")
                    if title:
                        result[_norm(title)] = album_name

    if SONGS_JSON.exists():
        try:
            groups = json.loads(SONGS_JSON.read_text(encoding="utf-8"))
        except Exception:
            groups = []
        for group in groups:
            album_name = group.get("album", "")
            for track in group.get("tracks", []):
                title = track.get("title", "")
                if title:
                    result[_norm(title)] = album_name
    return result


def load_song_db() -> dict:
    """Returns {track_id: {title, artist, image_url, type, single_image, song_family}} from discography JSONs."""
    import re as _re
    result = {}

    def _consume_sections(sections: list[dict], source_name: str) -> None:
        for section in sections:
            for t in section.get("tracks", []):
                url = (t.get("url") or t.get("spotify_url") or "").strip()
                m = _re.search(r"track/([A-Za-z0-9]+)", url)
                if not m:
                    continue
                track_id = m.group(1)
                if track_id in result:
                    continue
                artists = t.get("artists") or []
                result[track_id] = {
                    "title":     (t.get("title") or "").strip(),
                    "artist":    t.get("primary_artist") or (artists[0] if artists else "Taylor Swift"),
                    "image_url": (t.get("image_url") or "").strip(),
                    "type":      t.get("type", "album"),
                    "single_image": (t.get("single_image") or "").strip(),
                    "song_family": t.get("song_family", ""),
                }

    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
                _consume_sections(payload.get("sections", []) if isinstance(payload, dict) else [], album_file.name)
            except Exception as e:
                print(f"Erreur {album_file.name}: {e}")

    if (DB_DIR / "discography" / "songs.json").exists():
        try:
            _consume_sections(json.loads((DB_DIR / "discography" / "songs.json").read_text(encoding="utf-8")), "songs.json")
        except Exception as e:
            print(f"Erreur songs.json: {e}")
    return result


def load_history(target_date: str) -> tuple[list[dict], list[dict]]:
    """
    Returns (today_rows, yesterday_rows) from streams_history.csv.
    Each row: {track_id, streams, daily_streams}
    """
    yesterday = str(date_cls.fromisoformat(target_date) - timedelta(days=1))
    day_before = str(date_cls.fromisoformat(target_date) - timedelta(days=2))
    today_rows: dict[str, dict] = {}
    yesterday_rows: dict[str, dict] = {}
    before_rows: dict[str, dict] = {}

    def _parse_optional_int(raw: str | None) -> int | None:
        s = (raw or "").strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None

    with open(HISTORY_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = row["date"]
            if d not in (target_date, yesterday, day_before):
                continue
            entry = {
                "track_id": row["track_id"],
                "streams": int(row["streams"] or 0),
                # Keep None when daily_streams is missing; some days historically have blank daily values.
                "daily_streams": _parse_optional_int(row.get("daily_streams")),
            }
            if d == target_date:
                today_rows[row["track_id"]] = entry
            elif d == yesterday:
                yesterday_rows[row["track_id"]] = entry
            else:
                before_rows[row["track_id"]] = entry

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

    # If daily_streams is blank in CSV, recompute from totals using adjacent dates.
    _fill_missing_daily(today_rows, yesterday_rows)
    _fill_missing_daily(yesterday_rows, before_rows)

    return list(today_rows.values()), list(yesterday_rows.values())


def _get_song_family_single_image_map() -> dict:
    """Returns {song_family → single_image} mapping for version inheritance."""
    family_map = {}
    
    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                for t in section.get("tracks", []):
                    song_family = t.get("song_family", "")
                    single_image = (t.get("single_image") or "").strip()
                    if song_family and single_image and str(single_image).startswith("http"):
                        family_map[song_family] = single_image
    
    if (DB_DIR / "discography" / "songs.json").exists():
        try:
            groups = json.loads((DB_DIR / "discography" / "songs.json").read_text(encoding="utf-8"))
        except Exception:
            groups = []
        for group in groups:
            for t in group.get("tracks", []):
                song_family = t.get("song_family", "")
                single_image = (t.get("single_image") or "").strip()
                if song_family and single_image and str(single_image).startswith("http"):
                    family_map[song_family] = single_image
    
    return family_map


def get_cover_url(entry: dict, cover_map: dict, track_album_map: dict) -> str:
    """
    Returns cover URL for a stream entry (row from history CSV).
    
    Priority:
      - If type == "standalone" or "alternate_version":
        * single_image (from same song_family) > image_url (NEVER album cover)
      - Otherwise: covers.json (album) > image_url
    """
    track_type = entry.get("type", "album")
    track_img = entry.get("image_url", "")
    single_img = entry.get("single_image", "")
    song_family = entry.get("song_family", "")
    title = entry.get("title", "")
    
    # Singles et versions alternatives : JAMAIS d'album cover
    if track_type in ("standalone", "alternate_version"):
        # Check if this track's song_family has a single_image
        family_map = _get_song_family_single_image_map()
        if song_family and song_family in family_map:
            family_img = family_map[song_family]
            if str(family_img).startswith("http"):
                return family_img
        
        # Own single_image
        if single_img and str(single_img).startswith("http"):
            return single_img
        
        # Track image fallback
        if track_img and str(track_img).startswith("http"):
            return track_img
        
        return ""
    
    # Tracks normaux : priorité album cover → image_url
    album_name = track_album_map.get(_norm(title), "")
    if album_name:
        cover = cover_map.get(_norm(album_name), "")
        if cover and str(cover).startswith("http"):
            return cover
    
    # Track image fallback
    if track_img and str(track_img).startswith("http"):
        return track_img
    
    return ""


def get_latest_date() -> str:
    latest = ""
    with open(HISTORY_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"] > latest:
                latest = row["date"]
    if not latest:
        raise ValueError("streams_history.csv est vide")
    return latest


def _dedup_by_title(rows: list[dict], song_db: dict) -> list[dict]:
    """Deduplicate rows by normalized title, keeping the one with max daily_streams."""
    best: dict[str, dict] = {}
    for row in rows:
        tid  = row["track_id"]
        info = song_db.get(tid, {})
        title = info.get("title") or tid
        key   = _norm(title)
        existing = best.get(key)
        row_daily = row.get("daily_streams") or 0
        existing_daily = (existing or {}).get("daily_streams") or 0
        if existing is None or row_daily > existing_daily:
            best[key] = {**row, "title": title, "artist": info.get("artist", "Taylor Swift"),
                         "image_url": info.get("image_url", "")}
    return list(best.values())


def build_top_n(today_rows: list[dict], yesterday_rows: list[dict], song_db: dict, top_n: int) -> list[dict]:
    """
    Déduplique par titre, trie par daily_streams décroissant, retourne top N.
    Attache prev_rank et daily_streams_yesterday à chaque entrée.
    """
    # Build yesterday's ranking {title_key: rank}
    yest_deduped = _dedup_by_title(yesterday_rows, song_db)
    yest_sorted  = sorted(yest_deduped, key=lambda r: (r.get("daily_streams") or 0), reverse=True)
    yest_rank_by_key  = {_norm(r["title"]): i + 1 for i, r in enumerate(yest_sorted)}
    yest_daily_by_key = {_norm(r["title"]): r.get("daily_streams") for r in yest_deduped}

    today_deduped = _dedup_by_title(today_rows, song_db)
    ranked = sorted(today_deduped, key=lambda r: (r.get("daily_streams") or 0), reverse=True)
    top = ranked[:top_n]

    for entry in top:
        key = _norm(entry["title"])
        entry["daily_streams_yesterday"] = yest_daily_by_key.get(key)
        entry["prev_rank"]               = yest_rank_by_key.get(key)

    return top


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def rank_change(rank: int, prev_rank) -> tuple[str, str]:
    if prev_rank is None:
        return "NEW", "chg-new"
    delta = int(prev_rank) - rank
    if delta > 0:
        return f"▲{delta}", "chg-up"
    elif delta < 0:
        return f"▼{abs(delta)}", "chg-dn"
    return "=", "chg-eq"


def fmt_num(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", "\u202f")


def fmt_delta(today, yesterday) -> tuple[str, str, str]:
    """Returns (num_text, pct_text, css_class) for daily_streams delta."""
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
  width:800px;
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
  grid-template-columns:52px 50px minmax(160px,1fr) 130px 130px 110px;
  column-gap:8px;
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
.song-card{
  display:grid;
  grid-template-columns:52px 50px minmax(160px,1fr) 130px 130px 110px;
  column-gap:8px;
  align-items:center;
  padding:7px 18px;
  background:rgba(255,255,255,.82);
  border-bottom:1px solid rgba(16,24,40,.05);
}
.song-card.row-odd{background:rgba(248,250,251,.88)}
.song-card.row-gold{
  background:linear-gradient(90deg,#fff7d6 0%,#fffdf5 40%,rgba(255,255,255,.92) 100%);
  border-left:3px solid #ebc44c;
}
.col-rank{
  font-size:21px;font-weight:900;color:#0b1f44;
  letter-spacing:-.04em;
  display:flex;align-items:center;justify-content:center;
}
.col-song{display:flex;align-items:center;gap:12px;min-width:0}
.art{
  width:54px;height:54px;border-radius:7px;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
}
.art-ph{
  width:54px;height:54px;border-radius:7px;
  background:#dde3ea;flex-shrink:0;
}
.song-info{min-width:0}
.song-title{
  font-size:15px;font-weight:700;color:#101828;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.song-artist{font-size:13px;color:#667085;margin-top:3px}
.col-num{
  font-size:14px;color:#344054;font-weight:500;
  display:flex;align-items:center;justify-content:flex-end;
}
.col-chg{
  font-size:13px;font-weight:700;
  display:flex;align-items:center;justify-content:center;
}
.chg-up{color:#067647}
.chg-dn{color:#b42318}
.chg-eq{color:#9ca3af}
.chg-new{color:#5bbde4;font-size:11px;font-weight:800}
.pos{color:#067647;font-weight:600}
.neg{color:#b42318;font-weight:600}
.neutral{color:#667085}
.delta-wrap{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.delta-num{font-size:13px;font-weight:600}
.delta-pct{font-size:11px;font-weight:500;opacity:.85}
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


def _url_to_data_uri(url: str) -> str:
    """Download an image URL and return a base64 data URI (empty string on failure)."""
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


def prefetch_images(top_rows: list[dict], cover_map: dict, track_album_map: dict) -> dict[str, str]:
    """Resolve cover URLs for all top entries and return {url: data_uri}."""
    urls = set()
    for entry in top_rows:
        cover_url = get_cover_url(entry, cover_map, track_album_map)
        if cover_url:
            urls.add(cover_url)

    result: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(_url_to_data_uri, u): u for u in urls}
        for fut, url in futures.items():
            data_uri = fut.result()
            if data_uri:
                result[url] = data_uri
    return result


def build_rows_html(top_rows: list[dict], cover_map: dict, track_album_map: dict,
                    image_cache: dict[str, str] | None = None) -> str:
    html = ""
    for i, entry in enumerate(top_rows):
        rank    = i + 1
        title   = entry["title"]
        artist  = entry["artist"]
        daily   = entry["daily_streams"]
        total   = entry["streams"]
        yest    = entry.get("daily_streams_yesterday")
        img_url = entry.get("image_url", "")

        # Cover lookup using new priority logic
        cover_url = get_cover_url(entry, cover_map, track_album_map)

        # Use pre-fetched data URI so Playwright doesn't need network access
        if image_cache and cover_url:
            cover_url = image_cache.get(cover_url, cover_url)

        art_html = (
            f'<img class="art" src="{cover_url}" />'
            if cover_url
            else '<div class="art-ph"></div>'
        )

        delta_num, delta_pct, delta_cls = fmt_delta(daily, yest)
        chg_text, chg_css = rank_change(rank, entry.get("prev_rank"))

        card_cls = "song-card"
        if rank == 1:
            card_cls += " row-gold"
        elif i % 2 != 0:
            card_cls += " row-odd"

        html += f"""<div class="{card_cls}">
  <div class="col-rank">#{rank}</div>
  <div class="col-chg {chg_css}">{chg_text}</div>
  <div class="col-song">
    {art_html}
    <div class="song-info">
      <div class="song-title">{title}</div>
      <div class="song-artist">{artist}</div>
    </div>
  </div>
  <div class="col-num"><strong>{fmt_num(daily)}</strong></div>
  <div class="col-num {delta_cls}">
    <div class="delta-wrap">
      <span class="delta-num">{delta_num}</span>
      {f'<span class="delta-pct">{delta_pct}</span>' if delta_pct else ''}
    </div>
  </div>
  <div class="col-num">{fmt_num(total)}</div>
</div>
"""
    return html


def build_html(top_rows: list[dict], target_date: str, cover_map: dict, track_album_map: dict,
               top_n: int,
               image_cache: dict[str, str] | None = None) -> str:
    from datetime import datetime
    date_fmt   = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows_html  = build_rows_html(top_rows, cover_map, track_album_map, image_cache)

    header_img   = _pick_header_image()
    handle_color = "#1db954"

    if header_img:
        handle_color = _dominant_color(header_img)
        img_url      = header_img.as_posix()
        hdr_style    = (
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
      <div class="hdr-title">Taylor Swift · Daily Streams</div>
            <div class="hdr-sub">Taylor Swift's top {top_n} most streamed songs · {date_fmt}</div>
    </div>
  </div>
  <div class="col-heads">
    <span>Rank</span>
    <span>+/-</span>
    <span>Track</span>
    <span class="right">Daily</span>
    <span class="right">vs Yesterday</span>
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

def generate(target_date: str | None = None, *, top_n: int | None = None) -> Path:
    if target_date is None:
        target_date = get_latest_date()
    print(f"Date: {target_date}")

    top_n_final = TOP_N if top_n is None else int(top_n)
    if top_n_final <= 0:
        raise ValueError("top_n must be > 0")

    song_db         = load_song_db()
    cover_map       = load_covers()
    track_album_map = load_track_album_map()

    today_rows, yesterday_rows = load_history(target_date)
    if not today_rows:
        raise ValueError(f"Aucune donnée pour {target_date} dans {HISTORY_PATH}")

    top_rows = build_top_n(today_rows, yesterday_rows, song_db, top_n_final)
    print(f"Top {top_n_final} construit ({len(top_rows)} chansons)")
    for i, e in enumerate(top_rows, 1):
        daily_fmt = f"{e['daily_streams']:,}"
        print(f"  #{i:2d} {e['title']:<40} {daily_fmt} streams/day")

    print("Téléchargement des images...")
    image_cache = prefetch_images(top_rows, cover_map, track_album_map)
    print(f"  {len(image_cache)} images téléchargées")

    html = build_html(top_rows, target_date, cover_map, track_album_map, top_n_final, image_cache)

    # Output to history/YYYY/MM/YYYY-MM-DD/
    out_dir  = ROOT / "history" / target_date[:4] / target_date[5:7] / target_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "streams_image.png"
    tmp_html = out_dir / "_streams_tmp.html"
    tmp_html.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 800, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)  # images are base64-embedded, no network needed
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()

    print(f"\nImage générée : {out_path}")
    return out_path


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    generate(date_arg)
