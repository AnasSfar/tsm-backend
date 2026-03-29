#!/usr/bin/env python3
"""
spotlight.py — scrape une chanson et génère une image "spotlight" carrée.

Usage:
  python spotlight.py "Cruel Summer"
  python spotlight.py "Cruel Summer" 2026-03-21
  python spotlight.py --url https://open.spotify.com/track/1BxfuPKGuaTgP7aM0Bbdwr
  python spotlight.py "Cruel Summer" --post
  python spotlight.py "Cruel Summer" --no-scrape   # utilise l'historique uniquement
"""
from __future__ import annotations

import argparse
import base64
import colorsys
import csv
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import date as date_cls, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

try:
    from PIL import Image as _PilImage
    _PIL = True
except ImportError:
    _PIL = False

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR      = Path(__file__).resolve().parent          # streams/
REPO_ROOT       = SCRIPT_DIR.parents[2]                    # repo root
DB_DIR          = REPO_ROOT / "db"
HISTORY_PATH    = DB_DIR / "streams_history.csv"
SONGS_JSON      = DB_DIR / "discography" / "songs.json"
ALBUMS_DIR      = DB_DIR / "discography" / "albums"
COVERS_PATH     = DB_DIR / "discography" / "covers.json"
OUT_DIR         = SCRIPT_DIR / "history" / "spotlight"
TWITTER_SESSION = SCRIPT_DIR.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(SCRIPT_DIR.parent))  # collectors/spotify/ for core.*

HANDLE          = "@swiftiescharts"
PAGE_TIMEOUT_MS = 20_000

# ── Helpers ───────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _extract_track_id(url: str) -> str | None:
    m = re.search(r"track/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def _fmt(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", "\u202f")


def _cover_palette(img_bytes: bytes) -> tuple[str, str]:
    """Extract cover colors spatially and return (gradient_css, accent_hex).

    Projects every pixel onto the 135° gradient axis (top-left → bottom-right),
    divides into N strips, averages each strip's non-neutral pixels, and builds
    a gradient whose stops match the real spatial layout of the cover.
    """
    _FALLBACK = ("#1db954", "#1db954")
    if not _PIL or not img_bytes:
        return _FALLBACK
    try:
        from io import BytesIO

        SIZE = 100
        img = _PilImage.open(BytesIO(img_bytes)).convert("RGB").resize((SIZE, SIZE), _PilImage.LANCZOS)
        pixels = list(img.getdata())

        N_STRIPS = 6
        strips: list[list[tuple[int, int, int]]] = [[] for _ in range(N_STRIPS)]

        for idx, (r, g, b) in enumerate(pixels):
            x = idx % SIZE
            y = idx // SIZE
            # Project onto 135° axis: 0 = top-left, 1 = bottom-right
            t = (x + y) / (2 * (SIZE - 1))
            strips[min(int(t * N_STRIPS), N_STRIPS - 1)].append((r, g, b))

        def _strip_color(px: list[tuple[int, int, int]]) -> tuple[str, float] | None:
            """Average non-neutral pixels in a strip, boost saturation."""
            non_neutral = [
                p for p in px
                if not (p[0] > 210 and p[1] > 210 and p[2] > 210)
                and not (p[0] < 35  and p[1] < 35  and p[2] < 35)
            ]
            pool = non_neutral or px
            if not pool:
                return None
            avg_r = sum(p[0] for p in pool) / len(pool)
            avg_g = sum(p[1] for p in pool) / len(pool)
            avg_b = sum(p[2] for p in pool) / len(pool)
            h, s, v = colorsys.rgb_to_hsv(avg_r / 255, avg_g / 255, avg_b / 255)
            s = min(1.0, s * 1.25)
            v = min(1.0, max(0.28, v))
            r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
            return f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}", s

        strip_results = [_strip_color(s) for s in strips]

        # Build gradient stops: strip i covers [i/N, (i+1)/N]
        hex_stops: list[tuple[str, int]] = []  # (hex, pct)
        for i, res in enumerate(strip_results):
            if res is None:
                continue
            hex_col, _ = res
            pct_start = round(i / N_STRIPS * 100)
            hex_stops.append((hex_col, pct_start))

        if not hex_stops:
            return _FALLBACK

        # Deduplicate consecutive identical colors for a cleaner CSS string
        deduped: list[tuple[str, int]] = [hex_stops[0]]
        for col, pct in hex_stops[1:]:
            if col != deduped[-1][0]:
                deduped.append((col, pct))

        if len(deduped) == 1:
            return deduped[0][0], deduped[0][0]

        stops_css = ", ".join(f"{c} {p}%" for c, p in deduped)
        gradient   = f"linear-gradient(135deg, {stops_css})"

        # accent = strip with highest saturation
        accent_hex = max(
            (r for r in strip_results if r is not None),
            key=lambda r: r[1]
        )[0]

        return gradient, accent_hex

    except Exception:
        return _FALLBACK

# ── Discography ───────────────────────────────────────────────────────────────
def load_all_tracks() -> list[dict]:
    tracks = []
    seen: set[str] = set()

    all_sections = []
    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            album_name = payload.get("album", "") if isinstance(payload, dict) else ""
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                if not isinstance(section, dict):
                    continue
                item = dict(section)
                if not item.get("album"):
                    item["album"] = album_name
                all_sections.append(item)

    if SONGS_JSON.exists():
        try:
            all_sections.extend(json.loads(SONGS_JSON.read_text(encoding="utf-8")))
        except Exception:
            pass

    for section in all_sections:
        for t in section.get("tracks", []):
            url = (t.get("url") or t.get("spotify_url") or "").strip()
            tid = _extract_track_id(url)
            if not tid or tid in seen:
                continue
            seen.add(tid)
            artists = t.get("artists") or []
            tracks.append({
                "track_id":   tid,
                "title":      (t.get("title") or "").strip(),
                "artist":     t.get("primary_artist") or (artists[0] if artists else "Taylor Swift"),
                "spotify_url": f"https://open.spotify.com/track/{tid}",
                "image_url":  (t.get("image_url") or "").strip(),
                "album":      section.get("album", ""),
            })
    return tracks


def find_track(query: str, tracks: list[dict]) -> dict | None:
    tid = _extract_track_id(query)
    if tid:
        return next((t for t in tracks if t["track_id"] == tid), None)
    q = _norm(query)
    exact = next((t for t in tracks if _norm(t["title"]) == q), None)
    if exact:
        return exact
    return next((t for t in tracks if q in _norm(t["title"])), None)


def load_covers() -> dict:
    if not COVERS_PATH.exists():
        return {}
    covers = json.loads(COVERS_PATH.read_text(encoding="utf-8"))
    return {
        _norm(v.get("title", "")): v["cover_url"]
        for v in covers.values()
        if "cover_url" in v and v.get("title")
    }

# ── History ───────────────────────────────────────────────────────────────────
def load_history_for_track(track_id: str, stats_date: str) -> tuple[int | None, int | None, int | None, int | None]:
    """Returns (total_today, total_yesterday, daily_today, daily_yesterday).
    daily_* sont lus directement depuis la colonne daily_streams du CSV.
    """
    d0 = date_cls.fromisoformat(stats_date)
    dates = {str(d0): "today", str(d0 - timedelta(1)): "y1"}
    totals:  dict[str, int] = {}
    dailies: dict[str, int] = {}

    if not HISTORY_PATH.exists():
        return None, None, None, None

    with HISTORY_PATH.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("track_id") != track_id:
                continue
            d = (row.get("date") or "").strip()
            if d not in dates:
                continue
            key = dates[d]
            try:
                totals[key] = int(row["streams"] or 0)
            except ValueError:
                pass
            try:
                v = (row.get("daily_streams") or "").strip()
                if v:
                    dailies[key] = int(v)
            except ValueError:
                pass

    return totals.get("today"), totals.get("y1"), dailies.get("today"), dailies.get("y1")

# ── Scraping ──────────────────────────────────────────────────────────────────
def _block_unneeded(route) -> None:
    url = route.request.url.lower()
    if route.request.resource_type in {"media", "font", "image"} or any(
        x in url for x in ("doubleclick", "googletagmanager", "google-analytics",
                           "analytics", "facebook", "pixel", "ads")
    ):
        route.abort()
    else:
        route.continue_()


def _extract_playcount(page) -> int | None:
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None

    lines = [l.replace("\u202f", " ").replace("\xa0", " ").strip()
             for l in body.splitlines() if l.strip()]

    start = next((i for i, l in enumerate(lines) if l.strip().lower() in ("titre", "title")), None)
    if start is None:
        return None

    end_markers = {"connectez-vous", "se connecter", "artiste", "recommandes",
                   "recommandés", "basees sur ce titre", "basées sur ce titre",
                   "titres populaires par", "sorties populaires par taylor swift"}

    block: list[str] = []
    for l in lines[start + 1:]:
        if _norm(l) in end_markers:
            break
        block.append(l)

    def _is_large(t: str) -> bool:
        c = t.strip().replace("\u202f", " ").replace("\xa0", " ")
        if not re.fullmatch(r"[\d\s,.\']+", c):
            return False
        try:
            return int(re.sub(r"[^\d]", "", c)) >= 1000
        except ValueError:
            return False

    for i, l in enumerate(block):
        if re.fullmatch(r"\d{1,2}:\d{2}", l.strip()):
            for j in range(i + 1, min(i + 6, len(block))):
                c = block[j].strip()
                if c in {"•", "-", ""}:
                    continue
                if _is_large(c):
                    try:
                        return int(re.sub(r"[^\d]", "", c))
                    except ValueError:
                        pass

    # JS fallback
    try:
        r = page.evaluate("""() => {
            const cs = [];
            document.querySelectorAll('[data-testid], span, div').forEach(el => {
                const t = (el.innerText||'').trim();
                if (/^[\\d\\u202f\\u00a0\\s,.']+$/.test(t)) {
                    const n = parseInt(t.replace(/[^\\d]/g,''));
                    if (!isNaN(n) && n >= 10000) cs.push(n);
                }
            });
            return cs.length === 1 ? cs[0] : null;
        }""")
        if r is not None:
            return int(r)
    except Exception:
        pass
    return None


def scrape_track(track: dict) -> int | None:
    print(f"Scraping : {track['title']} …")
    attempt = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx  = browser.new_context(locale="fr-FR")
        page = ctx.new_page()
        page.route("**/*", _block_unneeded)
        try:
            while True:
                attempt += 1
                try:
                    page.goto(track["spotify_url"], wait_until="commit", timeout=PAGE_TIMEOUT_MS)
                    try:
                        page.wait_for_function(
                            "() => { for (const el of document.querySelectorAll('[data-testid], span, div')) {"
                            "  const n = parseInt((el.innerText||'').replace(/[^\\d]/g,''));"
                            "  if (!isNaN(n) && n >= 100000) return true; } return false; }",
                            timeout=8000,
                        )
                    except Exception:
                        pass
                    total = _extract_playcount(page)
                    if total is not None:
                        print(f"  → {total:,} streams")
                        return total
                    for wait_ms in (1000, 2500):
                        page.wait_for_timeout(wait_ms)
                        total = _extract_playcount(page)
                        if total is not None:
                            print(f"  → {total:,} streams")
                            return total
                    print(f"  Not found (attempt {attempt}), retrying …")
                    page.wait_for_timeout(3000)
                except PlaywrightTimeoutError:
                    print(f"  Timeout (attempt {attempt}), retrying …")
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"  Erreur (attempt {attempt}): {e}, retrying …")
                    page.wait_for_timeout(3000)
        finally:
            browser.close()

# ── Image ─────────────────────────────────────────────────────────────────────
_CSS = """
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
  display:flex;
  flex-direction:column;
}
.main-row{
  display:flex;
  flex-direction:row;
  background:#fff;
}
.cover-col{
  flex:0 0 280px;
  padding:24px 20px 24px 24px;
  display:flex;align-items:center;justify-content:center;
  border-right:1px solid rgba(16,24,40,.07);
}
.cover-art{
  width:232px;height:232px;
  border-radius:12px;object-fit:cover;
  box-shadow:0 16px 40px rgba(0,0,0,.22),0 4px 12px rgba(0,0,0,.12);
}
.cover-ph{
  width:232px;height:232px;border-radius:12px;
  background:#dde3ea;
  display:flex;align-items:center;justify-content:center;
  font-size:56px;
}
.info-col{
  flex:1;
  display:flex;flex-direction:column;
  padding:20px 22px 20px 20px;
  gap:14px;
}
.song-name{
  font-size:24px;font-weight:900;color:#101828;
  letter-spacing:-.3px;line-height:1.15;
}
.song-artist{font-size:12px;color:#667085;margin-top:2px;}
.song-date{font-size:13px;font-weight:600;color:#344054;margin-top:1px;}
.daily-block{
  border-radius:12px;
  padding:14px 18px 10px;
  text-align:center;
}
.daily-num{
  color:#fff;
  font-size:56px;font-weight:900;
  letter-spacing:-.04em;line-height:1;
}
.stat-row{
  display:grid;grid-template-columns:1fr 1fr;gap:10px;
}
.stat-card{
  background:rgba(241,245,246,.96);
  border-radius:10px;padding:12px 14px;
  border:1px solid rgba(16,24,40,.07);
}
.stat-label{
  font-size:10px;font-weight:700;
  text-transform:uppercase;letter-spacing:.08em;
  color:#667085;margin-bottom:5px;
}
.stat-val{font-size:24px;font-weight:800;color:#101828;letter-spacing:-.02em;}
.stat-sub{font-size:10px;font-weight:500;color:#667085;margin-top:2px;}
.pos{color:#067647}
.neg{color:#b42318}
.neutral{color:#667085}
.ftr{
  background:rgba(241,245,246,.96);
  padding:7px 16px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid rgba(16,24,40,.07);
}
.ftr-handle{font-size:11px;font-weight:700}
.ftr-date{font-size:11px;color:#667085;font-weight:500}
.stat-card-gold{
  background:linear-gradient(135deg,#7a5800,#c8920a,#f5c518,#c8920a,#7a5800);
  border:none;
}
.stat-card-gold .stat-label{color:rgba(255,255,255,.80);}
.stat-card-gold .stat-val{color:#fff;}
.stat-card-gold .stat-sub{color:rgba(255,255,255,.75);font-weight:700;}
"""


_MILESTONES = [
    100_000_000, 200_000_000, 300_000_000, 400_000_000, 500_000_000,
    600_000_000, 700_000_000, 800_000_000, 900_000_000,
    1_000_000_000, 1_500_000_000, 2_000_000_000, 2_500_000_000,
    3_000_000_000, 3_500_000_000, 4_000_000_000, 5_000_000_000,
]


def _just_crossed_milestone(total: int, total_yesterday: int | None) -> int | None:
    if total_yesterday is None:
        return None
    for m in _MILESTONES:
        if total_yesterday < m <= total:
            return m
    return None


def _fmt_milestone(m: int) -> str:
    if m >= 1_000_000_000 and m % 1_000_000_000 == 0:
        return f"{m // 1_000_000_000}B"
    if m >= 1_000_000_000:
        return f"{m / 1_000_000_000:.1f}B"
    return f"{m // 1_000_000}M"


def _fetch_image(url: str) -> tuple[str, bytes]:
    if not url:
        return "", b""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
            ct   = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            return f"data:{ct};base64,{base64.b64encode(data).decode()}", data
    except Exception:
        return "", b""


def _build_html(
    title: str,
    artist: str,
    daily: int | None,
    daily_yesterday: int | None,
    total: int,
    cover_uri: str,
    gradient: str,
    accent_hex: str,
    date_fmt: str,
    milestone: int | None = None,
) -> str:
    has_daily    = daily is not None and daily >= 0
    daily_fmt    = _fmt(daily) if has_daily else "—"
    daily_prefix = "+" if has_daily else ""
    daily_prefix = "+" if has_daily else ""
    total_fmt = _fmt(total)

    # vs Yesterday = variation de daily (today_daily - yesterday_daily)
    if daily is not None and daily_yesterday is not None and daily_yesterday > 0:
        delta     = daily - daily_yesterday
        pct       = delta / daily_yesterday * 100
        sign      = "+" if delta >= 0 else "−"
        vs_str    = f"{sign}{_fmt(abs(delta))}"
        pct_str   = f"{pct:+.1f}%"
        vs_cls    = "pos" if delta >= 0 else "neg"
    else:
        vs_str    = "—"
        pct_str   = ""
        vs_cls    = "neutral"

    cover_html = (
        f'<img class="cover-art" src="{cover_uri}" />'
        if cover_uri else
        '<div class="cover-ph">🎵</div>'
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>
<div class="container">
  <div class="main-row">
    <div class="cover-col">
      {cover_html}
    </div>
    <div class="info-col">
      <div>
        <div class="song-name">{title}</div>
        <div class="song-artist">{artist}</div>
        <div class="song-date">{date_fmt}</div>
      </div>
      <div class="daily-block" style="background:{gradient}">
        <div class="daily-num">{daily_prefix}{daily_fmt}</div>
      </div>
      <div class="stat-row">
        <div class="stat-card">
          <div class="stat-label">vs Yesterday</div>
          <div class="stat-val {vs_cls}">{vs_str}</div>
          <div class="stat-sub">{pct_str}</div>
        </div>
        <div class="stat-card{' stat-card-gold' if milestone else ''}">
          <div class="stat-label">Total Streams</div>
          <div class="stat-val">{total_fmt}</div>
          <div class="stat-sub">{'🏆 ' + _fmt_milestone(milestone) + ' MILESTONE' if milestone else 'SINCE RELEASE'}</div>
        </div>
      </div>
    </div>
  </div>
  <div class="ftr">
    <span class="ftr-handle" style="color:{accent_hex}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div>
</body></html>"""


def generate_spotlight_image(
    track: dict,
    total_scraped: int,
    total_yesterday: int | None,
    daily_yesterday: int | None,
    cover_url: str,
    stats_date: str,
) -> Path:
    from datetime import datetime
    date_fmt = datetime.strptime(stats_date, "%Y-%m-%d").strftime("%B %d, %Y")

    print("Téléchargement de la cover …")
    cover_uri, cover_bytes = _fetch_image(cover_url)

    gradient, accent_hex = _cover_palette(cover_bytes) if cover_bytes else ("#1db954", "#1db954")
    print(f"Gradient : {gradient}")
    print(f"Accent   : {accent_hex}")

    daily     = (total_scraped - total_yesterday) if total_yesterday is not None else None
    milestone = _just_crossed_milestone(total_scraped, total_yesterday)
    if milestone:
        print(f"Milestone atteint : {_fmt_milestone(milestone)}")

    html = _build_html(
        title           = track["title"],
        artist          = track.get("artist", "Taylor Swift"),
        daily           = daily,
        daily_yesterday = daily_yesterday,
        total           = total_scraped,
        cover_uri       = cover_uri,
        gradient        = gradient,
        accent_hex      = accent_hex,
        date_fmt        = date_fmt,
        milestone       = milestone,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tid      = track["track_id"]
    out_path = OUT_DIR / f"{stats_date}_{tid}.png"
    tmp_html = OUT_DIR / f"_spotlight_{tid}.html"
    tmp_html.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 800, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()

    print(f"Image générée : {out_path}")
    return out_path

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Spotlight image for one Taylor Swift track.")
    parser.add_argument("title", nargs="?", help="Track title (or Spotify URL)")
    parser.add_argument("date",  nargs="?", help="Stats date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--url",       help="Spotify track URL (alternative to title positional arg)")
    parser.add_argument("--post",      action="store_true", help="Post to Twitter after generating")
    parser.add_argument("--no-post",   action="store_true", help="Generate image but skip Twitter posting")
    parser.add_argument("--no-scrape", action="store_true", help="Use history CSV total, skip live scraping")
    args = parser.parse_args()

    query = args.url or args.title
    if not query:
        parser.print_help()
        sys.exit(1)

    stats_date = args.date or str(date_cls.today() - timedelta(days=1))

    tracks = load_all_tracks()
    track  = find_track(query, tracks)
    if not track:
        print(f"Track not found in discography: {query!r}")
        sys.exit(1)

    print(f"Track      : {track['title']} [{track['track_id']}]")
    print(f"Stats date : {stats_date}")

    total_today_hist, total_yesterday, daily_today_hist, daily_yesterday = load_history_for_track(track["track_id"], stats_date)
    print(f"History    : today={total_today_hist}, yesterday={total_yesterday}, daily_today={daily_today_hist}, daily_yesterday={daily_yesterday}")

    if total_today_hist is not None:
        total_scraped = total_today_hist
        print(f"Data already in history, skipping scrape : {total_scraped:,}")
    elif args.no_scrape:
        print("No history data available and --no-scrape specified. Aborting.")
        sys.exit(1)
    else:
        total_scraped = scrape_track(track)
        if total_scraped is None:
            print("Scrape failed and no history available. Aborting.")
            sys.exit(1)

    covers    = load_covers()
    cover_url = covers.get(_norm(track["album"]), "") or track.get("image_url", "")
    if not cover_url:
        print("Warning: no cover found.")

    img_path = generate_spotlight_image(
        track           = track,
        total_scraped   = total_scraped,
        total_yesterday = total_yesterday,
        daily_yesterday = daily_yesterday,
        cover_url       = cover_url,
        stats_date      = stats_date,
    )

    post_requested = args.post and not args.no_post
    if post_requested:
        if not TWITTER_SESSION.exists():
            print(f"Twitter session not found: {TWITTER_SESSION}")
            sys.exit(1)
        from core.twitter import post_with_image
        daily     = (total_scraped - total_yesterday) if total_yesterday else None
        daily_fmt = _fmt(daily) if daily and daily >= 0 else "?"
        tweet     = f"✨ {track['title']}\n{daily_fmt} streams yesterday"
        print(f"Tweet : {tweet}")
        success = post_with_image(tweet, img_path, TWITTER_SESSION)
        if success:
            print("Posté avec succès.")
        else:
            print("Échec du post.")
            sys.exit(1)
    elif args.no_post:
        print("Twitter post skipped (--no-post).")


if __name__ == "__main__":
    main()
