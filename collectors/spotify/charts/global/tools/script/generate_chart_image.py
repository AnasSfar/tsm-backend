#!/usr/bin/env python3
"""
generate_chart_image.py — génère le PNG du chart Taylor Swift Global.
Design adapté depuis unfiltered-charts (light glassmorphism).

Lit  : {date_dir}/ts_chart_{date}.json  +  ts_history.json
       + unfiltered-charts/discography/albums/covers.json  (couvertures albums)
Ecrit: {date_dir}/chart_image.png

Usage: python generate_chart_image.py YYYY-MM-DD
"""
import base64
import colorsys
import csv
import json
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

from playwright.sync_api import sync_playwright

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

ROOT             = Path(__file__).parent
_TOOLS           = Path(__file__).parent.parent          # = global/tools/
_DATA            = _TOOLS.parent / "history"             # = global/history/
TS_HISTORY_PATH  = _TOOLS / "json" / "ts_history.json"
DISCOGRAPHY_ROOT = Path(__file__).parents[6] / "db" / "discography"
COVERS_PATH      = DISCOGRAPHY_ROOT / "covers.json"
HEADERS_DIR      = _TOOLS / "headers"
HANDLE           = "@swiftiescharts"


# ---------------------------------------------------------------------------
# Image URL → base64 data URI (Chromium bloque les URLs externes via file://)
# ---------------------------------------------------------------------------

_img_cache: dict[str, str] = {}

def url_to_data_uri(url: str) -> str:
    """Fetches an image URL and returns a base64 data URI, or the original URL on failure."""
    if not url or not url.startswith("http"):
        return url
    if url in _img_cache:
        return _img_cache[url]
    last_exc = None
    for attempt in range(2):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=8) as resp:
                mime = resp.headers.get_content_type() or "image/jpeg"
                data = base64.b64encode(resp.read()).decode()
                result = f"data:{mime};base64,{data}"
            _img_cache[url] = result
            return result
        except Exception as e:
            last_exc = e
    print(f"[warn] url_to_data_uri: échec pour {url} ({last_exc}), URL brute utilisée (risque d'affichage vide)")
    _img_cache[url] = url
    return url


# ---------------------------------------------------------------------------
# Header image + dominant colour
# ---------------------------------------------------------------------------

def pick_header_image() -> Path | None:
    """Returns a random image from the headers folder (should be 860×80px)."""
    if not HEADERS_DIR.exists():
        return None
    imgs = [p for p in HEADERS_DIR.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    return random.choice(imgs) if imgs else None


def get_dominant_color(img_path: Path) -> str:
    """Returns a vibrant hex colour extracted from the image."""
    if not _PIL:
        return "#1db954"
    try:
        img = Image.open(img_path).convert("RGB").resize((60, 60), Image.LANCZOS)
        pixels = list(img.getdata())
        # Ignore near-white and near-black
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
        # Boost saturation so the handle pops
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        s = min(1.0, s * 1.8)
        v = min(1.0, max(0.55, v))
        r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
        return f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"
    except Exception:
        return "#1db954"


# ---------------------------------------------------------------------------
# Album cover lookup from discography
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Normalize album/track name to a simple key for matching."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def build_cover_map() -> dict:
    """Returns {normalized_album_title → cover_url} from covers.json."""
    if not COVERS_PATH.exists():
        return {}
    covers = json.loads(COVERS_PATH.read_text(encoding="utf-8"))
    result = {}
    for v in covers.values():
        key = _norm(v.get("title", ""))
        if key and "cover_url" in v:
            result[key] = v["cover_url"]
    return result


def build_track_album_map() -> dict:
    """Reads album files + songs.json → {normalized_track_title → album_title}."""
    result = {}

    albums_dir = DISCOGRAPHY_ROOT / "albums"
    if albums_dir.exists():
        for album_file in sorted(albums_dir.glob("*.json"), key=lambda p: p.name.casefold()):
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

    songs_file = DISCOGRAPHY_ROOT / "songs.json"
    if songs_file.exists():
        try:
            sections = json.loads(songs_file.read_text(encoding="utf-8"))
        except Exception:
            sections = []
        for section in sections:
            album_name = section.get("album", "")
            if not album_name:
                continue
            for track in section.get("tracks", []):
                title = track.get("title", "")
                if title:
                    result[_norm(title)] = album_name
    return result


_track_image_map: dict | None = None

def _get_track_image_map() -> dict:
    """Returns {normalized_track_title → image_url} from album files + songs.json (lazy, cached)."""
    global _track_image_map
    if _track_image_map is None:
        _track_image_map = {}
        albums_dir = DISCOGRAPHY_ROOT / "albums"
        if albums_dir.exists():
            for album_file in sorted(albums_dir.glob("*.json"), key=lambda p: p.name.casefold()):
                try:
                    payload = json.loads(album_file.read_text(encoding="utf-8"))
                    for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                        for track in section.get("tracks", []):
                            title = track.get("title", "")
                            img = (track.get("image_url") or "").strip()
                            if title and img:
                                _track_image_map[_norm(title)] = img
                except Exception:
                    pass
        # Also read songs.json for standalone/non-album tracks
        songs_path = DISCOGRAPHY_ROOT / "songs.json"
        if songs_path.exists():
            try:
                songs = json.loads(songs_path.read_text(encoding="utf-8"))
                for track in songs:
                    title = track.get("title", "")
                    img = (track.get("image_url") or "").strip()
                    if title and img:
                        _track_image_map.setdefault(_norm(title), img)
            except Exception:
                pass
    return _track_image_map


_track_image_map: dict | None = None
_track_metadata_map: dict | None = None
_song_family_single_image_map: dict | None = None

def _get_track_image_map() -> dict:
    """Returns {normalized_track_title → image_url} from album files + songs.json (lazy, cached)."""
    global _track_image_map
    if _track_image_map is None:
        _track_image_map = {}
        albums_dir = DISCOGRAPHY_ROOT / "albums"
        if albums_dir.exists():
            for album_file in sorted(albums_dir.glob("*.json"), key=lambda p: p.name.casefold()):
                try:
                    payload = json.loads(album_file.read_text(encoding="utf-8"))
                    for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                        for track in section.get("tracks", []):
                            title = track.get("title", "")
                            img = (track.get("image_url") or "").strip()
                            if title and img:
                                _track_image_map[_norm(title)] = img
                except Exception:
                    pass
        songs_path = DISCOGRAPHY_ROOT / "songs.json"
        if songs_path.exists():
            try:
                songs = json.loads(songs_path.read_text(encoding="utf-8"))
                for track in songs:
                    title = track.get("title", "")
                    img = (track.get("image_url") or "").strip()
                    if title and img:
                        _track_image_map.setdefault(_norm(title), img)
            except Exception:
                pass
    return _track_image_map


def _get_song_family_single_image_map() -> dict:
    """Returns {song_family → single_image} for tracks with single_image."""
    global _song_family_single_image_map
    if _song_family_single_image_map is None:
        _song_family_single_image_map = {}
        albums_dir = DISCOGRAPHY_ROOT / "albums"
        if albums_dir.exists():
            for album_file in sorted(albums_dir.glob("*.json"), key=lambda p: p.name.casefold()):
                try:
                    payload = json.loads(album_file.read_text(encoding="utf-8"))
                    for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                        for track in section.get("tracks", []):
                            song_family = track.get("song_family", "")
                            single_img = (track.get("single_image") or "").strip()
                            if song_family and single_img and str(single_img).startswith("http"):
                                _song_family_single_image_map.setdefault(song_family, single_img)
                except Exception:
                    pass
    return _song_family_single_image_map


def _get_track_metadata_map() -> dict:
    """Returns {normalized_track_title → {type, image_url, single_image, song_family, album}} for priority logic."""
    global _track_metadata_map
    if _track_metadata_map is None:
        _track_metadata_map = {}
        albums_dir = DISCOGRAPHY_ROOT / "albums"
        if albums_dir.exists():
            for album_file in sorted(albums_dir.glob("*.json"), key=lambda p: p.name.casefold()):
                try:
                    payload = json.loads(album_file.read_text(encoding="utf-8"))
                    for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                        for track in section.get("tracks", []):
                            title = track.get("title", "")
                            if title:
                                _track_metadata_map[_norm(title)] = {
                                    "type": track.get("type", "album"),
                                    "image_url": (track.get("image_url") or "").strip(),
                                    "single_image": (track.get("single_image") or "").strip(),
                                    "song_family": track.get("song_family", ""),
                                    "album": track.get("album", ""),
                                }
                except Exception:
                    pass
    return _track_metadata_map


def get_album_cover(
    track_name: str,
    track_album_map: dict,
    cover_map: dict,
    fallback_url: str = "",
) -> str:
    """
    Returns cover URL for a track.
    
    Priority:
      - If type == "standalone" or "alternate_version": 
        * single_image (from same song_family) > image_url > fallback_url (NEVER album cover)
      - Otherwise: covers.json (album) > image_url > fallback_url
    """
    norm_track = _norm(track_name)
    metadata = _get_track_metadata_map().get(norm_track, {})
    track_type = metadata.get("type", "album")
    track_img = metadata.get("image_url", "")
    single_img = metadata.get("single_image", "")
    song_family = metadata.get("song_family", "")

    # Singles et versions alternatives : JAMAIS d'album cover
    if track_type in ("standalone", "alternate_version"):
        # Check if this track's song_family has a single_image
        family_single_img_map = _get_song_family_single_image_map()
        if song_family and song_family in family_single_img_map:
            family_single_img = family_single_img_map[song_family]
            if str(family_single_img).startswith("http"):
                return family_single_img
        
        # Own single_image
        if single_img and str(single_img).startswith("http"):
            return single_img
        
        # Track image fallback
        if track_img and str(track_img).startswith("http"):
            return track_img
        
        return fallback_url or ""

    # Tracks normaux : priorité album cover → image_url → fallback
    album_name = track_album_map.get(norm_track, "")
    if album_name:
        cover = cover_map.get(_norm(album_name), "")
        if cover and str(cover).startswith("http"):
            return cover

    # Track image fallback
    if track_img and str(track_img).startswith("http"):
        return track_img

    # Fallback URL scrapée depuis Spotify ou vide
    return fallback_url or ""

    return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fmt_streams(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", "\u202f")   # narrow no-break space


def fmt_pct(pct) -> str:
    if pct is None:
        return "--"
    sign = "+" if pct >= 0 else ""
    formatted = f"{sign}{pct:.1f}%"
    if formatted == "-0.0%":
        return "+0.0%"
    return formatted


def pct_cls(pct) -> str:
    if pct is None:
        return "neutral"
    return "pos" if pct >= 0 else "neg"


def get_pct(today, ref):
    if not today or not ref or ref == 0:
        return None
    return (today - ref) / ref * 100


def rank_change(rank, previous_rank, total_days=None):
    if previous_rank is None:
        if total_days and int(total_days) > 0:
            return "RE-ENTRY", "chg-re"
        return "NEW", "chg-new"
    delta = int(previous_rank) - int(rank)
    if delta > 0:
        return f"▲{delta}", "chg-up"
    elif delta < 0:
        return f"▼{abs(delta)}", "chg-dn"
    return "=", "chg-eq"


def nan_to_none(v):
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    return v



# ---------------------------------------------------------------------------
# HTML / CSS
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
  padding:16px;
  color:#101828;
}
.container{
  border-radius:18px;
  overflow:hidden;
  box-shadow:0 14px 40px rgba(16,24,40,.10),0 2px 8px rgba(16,24,40,.06);
}
/* Header */
.hdr{
  padding:49px 22px;
  display:flex;align-items:center;gap:16px;
}
.hdr-logo{width:52px;height:52px;flex-shrink:0}
.hdr-title{color:#fff;font-size:22px;font-weight:800;letter-spacing:-.3px}
.hdr-sub{color:rgba(255,255,255,.85);font-size:13px;margin-top:4px}
/* Column headers */
.col-heads{
  display:grid;
  grid-template-columns:52px 60px minmax(180px,1fr) 112px 74px 74px 50px 60px;
  column-gap:8px;
  padding:7px 14px;
  background:rgba(241,245,246,.95);
  border-bottom:1px solid rgba(16,24,40,.07);
}
.col-heads span{
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#667085;
  display:flex;align-items:center;
}
.col-heads .right{justify-content:flex-end}
/* Song cards */
.song-card{
  display:grid;
  grid-template-columns:52px 60px minmax(180px,1fr) 112px 74px 74px 50px 60px;
  column-gap:8px;
  align-items:center;
  padding:9px 14px;
  background:rgba(255,255,255,.82);
  border-bottom:1px solid rgba(16,24,40,.05);
}
.song-card.row-odd{background:rgba(248,250,251,.88)}
.song-card.row-gold{
  background:linear-gradient(90deg,#fff7d6 0%,#fffdf5 40%,rgba(255,255,255,.92) 100%);
  border-left:3px solid #ebc44c;
}
/* Rank */
.col-rank{
  font-size:17px;font-weight:900;color:#0b1f44;
  letter-spacing:-.04em;
  display:flex;align-items:center;justify-content:center;
}
/* Change */
.col-chg{
  font-size:11px;font-weight:700;
  display:flex;align-items:center;justify-content:center;
}
.chg-up{color:#067647}
.chg-dn{color:#b42318}
.chg-eq{color:#9ca3af}
.chg-new{color:#5bbde4;font-size:10px;font-weight:800}
.chg-re{color:#5bbde4;font-size:10px;font-weight:800}
/* Song */
.col-song{display:flex;align-items:center;gap:10px;min-width:0}
.art{
  width:42px;height:42px;border-radius:6px;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
}
.art-ph{
  width:42px;height:42px;border-radius:6px;
  background:#dde3ea;flex-shrink:0;
}
.song-info{min-width:0}
.song-title{
  font-size:13px;font-weight:700;color:#101828;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.song-artist{font-size:11px;color:#667085;margin-top:2px}
/* Numeric columns */
.col-num{
  font-size:12px;color:#344054;font-weight:500;
  display:flex;align-items:center;justify-content:flex-end;
}
.pos{color:#067647;font-weight:600}
.neg{color:#b42318;font-weight:600}
.neutral{color:#667085}
/* Footer */
.ftr{
  background:rgba(241,245,246,.96);
  padding:8px 16px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid rgba(16,24,40,.07);
}
.ftr-handle{font-size:11px;color:#1db954;font-weight:700}
.ftr-date{font-size:11px;color:#667085;font-weight:500}
/* Day separator (multi-date) */
.day-hdr{
  padding:6px 14px;
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#344054;
  background:rgba(29,185,84,.06);
  border-top:2px solid rgba(29,185,84,.20);
  border-bottom:1px solid rgba(16,24,40,.07);
}
/* OUT section */
.out-hdr{
  padding:6px 14px;
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#b42318;
  background:rgba(180,35,24,.05);
  border-top:2px solid rgba(180,35,24,.25);
  border-bottom:1px solid rgba(180,35,24,.10);
}
.out-card{
  display:grid;
  grid-template-columns:52px 60px minmax(180px,1fr);
  column-gap:8px;
  align-items:center;
  padding:9px 14px;
  background:rgba(240,240,240,.60);
  border-bottom:1px solid rgba(16,24,40,.05);
  opacity:0.80;
}
.col-out-badge{
  font-size:10px;font-weight:800;color:#fff;
  display:flex;align-items:center;justify-content:center;
  background:#b42318;border-radius:4px;padding:3px 6px;
  width:fit-content;margin:auto;
}
.col-out-last{
  font-size:12px;font-weight:600;color:#9ca3af;
  display:flex;align-items:center;justify-content:center;
}
"""

SPOTIFY_SVG = """<svg class="hdr-logo" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
</svg>"""


def get_out_songs(chart_date: str, current_rows: list[dict]) -> list[dict]:
    """Returns TS songs from yesterday's CSV that are not in today's chart."""
    date_obj  = datetime.strptime(chart_date, "%Y-%m-%d").date()
    yesterday = str(date_obj - timedelta(days=1))
    csv_path  = ROOT / yesterday[:4] / yesterday[5:7] / yesterday / "ts_all_songs.csv"
    if not csv_path.exists():
        return []
    try:
        current_names = {str(r.get("song_name", "") or r.get("track_name", "")).lower() for r in current_rows}
        out_rows = []
        with open(csv_path, newline="", encoding="utf-8") as f:
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


def build_out_rows_html(
    out_songs: list[dict],
    track_album_map: dict,
    cover_map: dict,
    chart_date: str,
) -> str:
    if not out_songs:
        return ""
    date_obj  = datetime.strptime(chart_date, "%Y-%m-%d").date()
    yesterday = str(date_obj - timedelta(days=1))
    html = ""
    for row in out_songs:
        track      = str(row.get("track_name") or "")
        artist     = str(row.get("artist_names") or "")
        rank       = row.get("rank")
        scraped_img = row.get("image_url") or ""
        cover_url  = url_to_data_uri(get_album_cover(track, track_album_map, cover_map, scraped_img))
        art_html   = (
            f'<img class="art" src="{cover_url}" />'
            if cover_url
            else '<div class="art-ph"></div>'
        )
        rank_txt = f"#{int(rank)}" if rank else "—"
        html += f"""<div class="out-card">
  <div class="col-out-badge">OUT</div>
  <div class="col-out-last">{rank_txt}</div>
  <div class="col-song">
    {art_html}
    <div class="song-info">
      <div class="song-title">{track}</div>
      <div class="song-artist">{artist} · last: {yesterday}</div>
    </div>
  </div>
</div>
"""
    return html


def build_rows_html(
    rows,
    history,
    chart_date: str,
    track_album_map: dict,
    cover_map: dict,
) -> str:
    date_obj  = datetime.strptime(chart_date, "%Y-%m-%d").date()
    yesterday = str(date_obj - timedelta(days=1))
    week_ago  = str(date_obj - timedelta(days=7))

    html = ""
    for i, row in enumerate(rows):
        track      = str(row.get("track_name") or "")
        artist     = str(row.get("artist_names") or "")
        rank        = nan_to_none(row.get("rank"))
        prev_rank   = nan_to_none(row.get("previous_rank"))
        streams     = nan_to_none(row.get("streams"))
        streak      = nan_to_none(row.get("streak"))
        total_days  = nan_to_none(row.get("total_days"))
        scraped_img = row.get("image_url") or ""

        if rank is None:
            continue
        rank = int(rank)

        chg_text, chg_css = rank_change(rank, int(prev_rank) if prev_rank else None, total_days)

        # Album cover: discography lookup → fallback to scraped CDN URL
        cover_url = url_to_data_uri(get_album_cover(track, track_album_map, cover_map, scraped_img))

        # Daily / weekly % from ts_history
        track_hist   = history.get(track, {})
        prev_streams = (track_hist.get(yesterday) or {}).get("streams")
        week_streams = (track_hist.get(week_ago)  or {}).get("streams")
        streams_int  = int(streams) if streams else None

        daily_pct  = get_pct(streams_int, prev_streams)
        weekly_pct = get_pct(streams_int, week_streams)

        streams_fmt = fmt_streams(streams_int)
        daily_txt   = fmt_pct(daily_pct)
        weekly_txt  = fmt_pct(weekly_pct)
        # streak from Spotify row = consecutive days in current run
        consec_txt     = str(int(streak)) + "d" if streak else "—"
        # total_days scraped from Spotify expanded detail
        total_days_txt = str(int(total_days)) + "d" if total_days else "—"

        art_html = (
            f'<img class="art" src="{cover_url}" />'
            if cover_url
            else '<div class="art-ph"></div>'
        )

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
      <div class="song-title">{track}</div>
      <div class="song-artist">{artist}</div>
    </div>
  </div>
  <div class="col-num">{streams_fmt}</div>
  <div class="col-num {pct_cls(daily_pct)}">{daily_txt}</div>
  <div class="col-num {pct_cls(weekly_pct)}">{weekly_txt}</div>
  <div class="col-num">{consec_txt}</div>
  <div class="col-num">{total_days_txt}</div>
</div>
"""
    return html


def build_html(
    rows,
    history,
    chart_date: str,
    track_album_map: dict,
    cover_map: dict,
    header_img: Path | None = None,
    out_songs: list | None = None,
) -> str:
    date_fmt  = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows_html = build_rows_html(rows, history, chart_date, track_album_map, cover_map)
    rows_html += build_out_rows_html(out_songs or [], track_album_map, cover_map, chart_date)

    if header_img is None:
        header_img = pick_header_image()
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
      <div class="hdr-title">Taylor Swift · Global Spotify</div>
      <div class="hdr-sub">Daily Chart · {date_fmt}</div>
    </div>
  </div>
  <div class="col-heads">
    <span>Pos</span>
    <span>Chg</span>
    <span>Track</span>
    <span class="right">Streams</span>
    <span class="right">Daily</span>
    <span class="right">Weekly</span>
    <span class="right">Streak</span>
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

def generate(chart_date: str, header_img: Path | None = None) -> Path:
    date_dir  = _DATA / chart_date[:4] / chart_date[5:7] / chart_date
    json_path = date_dir / f"ts_chart_{chart_date}.json"
    out_path  = date_dir / "chart_image.png"

    if not json_path.exists():
        raise FileNotFoundError(f"ts_chart_{chart_date}.json introuvable: {json_path}")

    rows    = load_json(json_path)
    history = load_json(TS_HISTORY_PATH) if TS_HISTORY_PATH.exists() else {}

    if not rows:
        raise ValueError(f"Aucune chanson TS dans {json_path}")

    cover_map       = build_cover_map()
    track_album_map = build_track_album_map()
    out_songs       = get_out_songs(chart_date, rows)

    html     = build_html(rows, history, chart_date, track_album_map, cover_map, header_img=header_img, out_songs=out_songs)
    html_tmp = date_dir / "_chart_tmp.html"
    html_tmp.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 800, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{html_tmp.as_posix()}", wait_until="load")
            page.wait_for_load_state("networkidle", timeout=3000)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if html_tmp.exists():
            html_tmp.unlink()

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

    date_dir  = _DATA / chart_date[:4] / chart_date[5:7] / chart_date
    json_path = date_dir / f"ts_chart_{chart_date}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"ts_chart_{chart_date}.json introuvable: {json_path}")

    rows    = load_json(json_path)
    history = load_json(TS_HISTORY_PATH) if TS_HISTORY_PATH.exists() else {}
    cover_map       = build_cover_map()
    track_album_map = build_track_album_map()

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for img_path in imgs:
            out_path = date_dir / f"chart_image_{img_path.stem}.png"
            html     = build_html(rows, history, chart_date, track_album_map, cover_map, header_img=img_path)
            html_tmp = date_dir / "_chart_tmp.html"
            html_tmp.write_text(html, encoding="utf-8")
            try:
                page = browser.new_page(viewport={"width": 860, "height": 200}, device_scale_factor=2)
                page.goto(f"file:///{html_tmp.as_posix()}", wait_until="load")
                page.wait_for_load_state("networkidle", timeout=3000)
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
    cover_map       = build_cover_map()
    track_album_map = build_track_album_map()

    combined_rows_html = ""
    valid_dates = []
    for chart_date in chart_dates:
        date_dir  = _DATA / chart_date[:4] / chart_date[5:7] / chart_date
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
        combined_rows_html += build_rows_html(rows, history, chart_date, track_album_map, cover_map)
        combined_rows_html += build_out_rows_html(get_out_songs(chart_date, rows), track_album_map, cover_map, chart_date)

    if not valid_dates:
        raise ValueError("Aucun JSON trouvé pour les dates fournies")

    if header_img is None:
        header_img = pick_header_image()
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
      <div class="hdr-title">Taylor Swift · Global Spotify</div>
      <div class="hdr-sub">{subtitle}</div>
    </div>
  </div>
  <div class="col-heads">
    <span>Pos</span>
    <span>Chg</span>
    <span>Track</span>
    <span class="right">Streams</span>
    <span class="right">Daily</span>
    <span class="right">Weekly</span>
    <span class="right">Streak</span>
    <span class="right">Total</span>
  </div>
  {combined_rows_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{last_fmt}</span>
  </div>
</div>
</body></html>"""

    html_tmp = ROOT / "_chart_tmp.html"
    html_tmp.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 800, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{html_tmp.as_posix()}", wait_until="load")
            page.wait_for_load_state("networkidle", timeout=3000)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if html_tmp.exists():
            html_tmp.unlink()

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
