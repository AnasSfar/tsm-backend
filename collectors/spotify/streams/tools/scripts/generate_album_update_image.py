#!/usr/bin/env python3
"""
generate_album_update_image.py — génère le PNG "Album Daily Update" pour un album donné.

Pour chaque section (Standard Edition, Acoustic Edition, etc.), liste les chansons
avec rang, titre, daily streams, changement vs hier (abs + %), total streams.
Affiche les totaux par section et un grand total.

Usage:
  python generate_album_update_image.py "The Life of a Showgirl"
  python generate_album_update_image.py "The Life of a Showgirl" 2026-03-25
  python generate_album_update_image.py "The Life of a Showgirl" --post
  python generate_album_update_image.py "The Life of a Showgirl" 2026-03-25 --post
"""
from __future__ import annotations

import base64
import colorsys
import csv
import html
import io
import json
import random
import re
import sys
import urllib.request
from datetime import date as date_cls, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from PIL import Image
    from PIL import ImageEnhance
    from PIL import ImageFilter
    _PIL = True
except ImportError:
    _PIL = False

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR      = Path(__file__).resolve().parent          # streams/tools/scripts/
_TOOLS          = SCRIPT_DIR.parent                        # streams/tools/
ROOT            = SCRIPT_DIR.parents[1]                    # streams/
REPO_ROOT       = SCRIPT_DIR.parents[4]                    # repo root
DB_DIR          = REPO_ROOT / "db"

sys.path.insert(0, str(REPO_ROOT / "collectors"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))   # collectors/spotify/ for core.*

from core.data_paths import first_existing_db_history, update_streams_dir
from twitter.links import streams_latest_url
from twitter.prefixes import DEFAULT_POST_PREFIX
import history_store
import best_day_since

HISTORY_PATH    = first_existing_db_history("streams_history.csv")
ALBUMS_DIR      = DB_DIR / "discography" / "albums"
COVERS_PATH     = DB_DIR / "discography" / "covers.json"
HEADERS_DIR     = DB_DIR / "discography" / "headers"
PREFERRED_HEADERS_PATH = HEADERS_DIR / "preferences.json"
CHARTS_GLOBAL_HISTORY_DIR = ROOT.parent / "charts" / "global" / "history"
TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"

HANDLE          = "@swiftiescharts"
TWEET_CHAR_LIMIT = 280
HOLIDAY_COLLECTION_ALBUM = "The Taylor Swift Holiday Collection"
HOLIDAY_COLLECTION_MIN_DAILY_STREAMS_TO_POST = 100_000
HOLIDAY_COLLECTION_SEASON_START = (11, 25)
HOLIDAY_COLLECTION_SEASON_END = (1, 7)

# Nouveau : logo à gauche du handle
HANDLE_ICON_PATH = Path(r"C:\Users\sfara\Documents\GitHub\tsm-frontend\icons\logo.gif")

ENABLE_FILTERED_CHARTS = False

BODY_WIDTH_CSS = 880
BODY_PADDING_CSS = 12
HEADER_HEIGHT_CSS = 110
RENDER_DPR = 4

# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def is_holiday_collection_album(album_name: str) -> bool:
    return _norm(album_name) == _norm(HOLIDAY_COLLECTION_ALBUM)


def _is_holiday_collection_season(target_date: str) -> bool:
    day = date_cls.fromisoformat(target_date)
    month_day = (day.month, day.day)
    return month_day >= HOLIDAY_COLLECTION_SEASON_START or month_day <= HOLIDAY_COLLECTION_SEASON_END


def holiday_collection_post_block_reason(album_name: str, target_date: str) -> str | None:
    if not is_holiday_collection_album(album_name):
        return None
    if not _is_holiday_collection_season(target_date):
        return (
            f"{HOLIDAY_COLLECTION_ALBUM} is outside Christmas posting season "
            f"(Nov 25-Jan 7)."
        )

    sections, _canonical_name = load_album_sections(album_name, target_date)
    if not sections:
        return f"{HOLIDAY_COLLECTION_ALBUM} sections unavailable."
    hist = load_history_for_album(sections, target_date)
    daily_total = sum(
        int(hist.get(track["track_id"], {}).get("daily") or 0)
        for section in sections
        for track in section.get("tracks", [])
    )
    if daily_total < HOLIDAY_COLLECTION_MIN_DAILY_STREAMS_TO_POST:
        return (
            f"{HOLIDAY_COLLECTION_ALBUM} daily total is {daily_total:,}, below "
            f"{HOLIDAY_COLLECTION_MIN_DAILY_STREAMS_TO_POST:,}."
        )
    return None


def _shorten_title(t: str) -> str:
    t = re.sub(r"\(feat\.\s*", "(ft. ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bDressing Room\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\bRehearsal\b", "Reh.", t, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", t).strip()


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _dominant_color(img_path: Path) -> str:
    if not _PIL:
        return "#1db954"
    try:
        img = Image.open(img_path).convert("RGB")
        return _dominant_color_from_pil(img)
    except Exception:
        return "#1db954"


# Albums with an established black-and-white / monochrome brand identity.
# Photo-based dominant-color extraction always finds *some* hue (compression
# noise, a warm-lit patch, skin tone) even in a near-grayscale header/cover,
# and the extraction helpers below deliberately boost saturation to stay
# "vivid" — so these albums would otherwise render with an arbitrary pink/tan
# tint instead of the neutral gray their branding actually uses (matches the
# gray accent the frontend hardcodes for these same themes, see
# tsm-frontend anniversaries.js / themes.css theme-folklore & theme-reputation).
MONOCHROME_ALBUM_ACCENTS = {
    "folklore": "#6b6b6b",
    "reputation": "#6b6b6b",
}

TABLE_DARK_DEFAULT_ALBUMS = {
    "reputation",
    "the life of a showgirl",
}


def _dominant_color_from_url(url: str) -> str:
    if not _PIL or not url:
        return "#1db954"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return _dominant_color_from_pil(img)
    except Exception:
        return "#1db954"


def _dominant_color_from_pil(img: Image.Image) -> str:
    # Pick a vivid representative color from the album cover instead of averaging all pixels.
    img = img.resize((160, 160), Image.LANCZOS)
    pal = img.quantize(colors=32, method=Image.MEDIANCUT).convert("RGB")
    colors = pal.getcolors(maxcolors=160 * 160) or []

    best = None
    best_score = -1.0
    for count, (r, g, b) in colors:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        # Ignore near-white, near-black and very gray tones.
        if v > 0.96 or v < 0.10 or s < 0.18:
            continue
        # Weight saturation heavily so vivid colors win over muted dominant ones.
        score = float(count) ** 0.4 * (s ** 1.5) * (0.3 + 0.70 * v)
        if score > best_score:
            best_score = score
            best = (h, s, v)

    if best is None:
        # Fallback to average if palette filtering removed everything.
        pixels = list(img.getdata())
        r = sum(p[0] for p in pixels) // len(pixels)
        g = sum(p[1] for p in pixels) // len(pixels)
        b = sum(p[2] for p in pixels) // len(pixels)
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    else:
        h, s, v = best

    s = min(1.0, max(0.42, s * 1.12))
    v = min(0.88, max(0.46, v))
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return f"#{int(r2 * 255):02x}{int(g2 * 255):02x}{int(b2 * 255):02x}"


def _header_accent_color(img_path: Path) -> str:
    """More faithful accent extraction for header visuals."""
    if not _PIL:
        return "#1db954"
    try:
        img = Image.open(img_path).convert("RGB")
        img = img.resize((320, 140), Image.LANCZOS)
        # Bias sampling toward the left side where the title area sits.
        crop = img.crop((0, 0, int(img.width * 0.72), img.height))
        pixels = list(crop.getdata())
        if not pixels:
            return "#1db954"

        # Drop extreme highlights/shadows to avoid washed or muddy accents.
        filtered = [p for p in pixels if 16 < max(p) < 245]
        source = filtered or pixels
        r = sum(p[0] for p in source) // len(source)
        g = sum(p[1] for p in source) // len(source)
        b = sum(p[2] for p in source) // len(source)

        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        s = min(0.82, max(0.28, s * 1.08))
        v = min(0.84, max(0.40, v * 0.96))
        r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
        return f"#{int(r2 * 255):02x}{int(g2 * 255):02x}{int(b2 * 255):02x}"
    except Exception:
        return "#1db954"


def _section_palette_colors(img_path: Path, max_colors: int = 6) -> list[str]:
    """Extract ranked dominant colors for section total rows."""
    if not _PIL or not img_path or not img_path.exists():
        return []
    try:
        with Image.open(img_path) as img:
            img = img.convert("RGB").resize((300, 160), Image.LANCZOS)
            pal = img.quantize(colors=24, method=Image.MEDIANCUT).convert("RGB")
            colors = pal.getcolors(maxcolors=300 * 160) or []

        ranked = sorted(colors, key=lambda x: x[0], reverse=True)
        result = []
        kept_hues = []

        def hue_dist(a: float, b: float) -> float:
            d = abs(a - b)
            return min(d, 1.0 - d)

        for count, (r, g, b) in ranked:
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            # Skip near-white/near-black/gray colors.
            if v > 0.96 or v < 0.12 or s < 0.18:
                continue

            # Force color-family diversity: avoid adjacent hues (e.g. orange vs orange-red).
            if any(hue_dist(h, hk) < 0.12 for hk in kept_hues):
                continue

            # Normalize slightly so accents remain vivid and readable.
            s = min(0.82, max(0.34, s * 1.06))
            v = min(0.86, max(0.38, v * 0.97))
            rr, gg, bb = colorsys.hsv_to_rgb(h, s, v)
            hex_color = f"#{int(rr * 255):02x}{int(gg * 255):02x}{int(bb * 255):02x}"

            # Keep only visually distinct colors.
            keep = True
            for existing in result:
                er, eg, eb = int(existing[1:3], 16), int(existing[3:5], 16), int(existing[5:7], 16)
                if (int(rr * 255) - er) ** 2 + (int(gg * 255) - eg) ** 2 + (int(bb * 255) - eb) ** 2 < 42 ** 2:
                    keep = False
                    break
            if keep:
                result.append(hex_color)
                kept_hues.append(h)
            if len(result) >= max_colors:
                break

        return result
    except Exception:
        return []


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


def _file_to_data_uri(path: Path) -> str:
    if not path or not path.exists():
        return ""
    try:
        ext = path.suffix.lower().lstrip(".")
        ct = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
            "gif": "image/gif",
        }.get(ext, "image/png")
        return f"data:{ct};base64,{base64.b64encode(path.read_bytes()).decode()}"
    except Exception:
        return ""


def _enhanced_header_file_to_data_uri(path: Path) -> str:
    """Enhance header image before embedding to preserve detail in final render."""
    if not path or not path.exists():
        return ""
    if not _PIL:
        return _file_to_data_uri(path)
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            # Keep processing subtle to avoid zoom artifacts.
            img = img.filter(ImageFilter.UnsharpMask(radius=0.9, percent=85, threshold=3))
            img = ImageEnhance.Contrast(img).enhance(1.03)
            img = ImageEnhance.Sharpness(img).enhance(1.03)

            buf = io.BytesIO()
            # Lossless embed: avoids extra JPEG artifacts in the final header.
            img.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
            return f"data:image/png;base64,{base64.b64encode(data).decode()}"
    except Exception:
        return _file_to_data_uri(path)


def _prepare_header_for_render(path: Path, target_w: int, target_h: int) -> str:
    """Crop+resize header to exact output pixel size to avoid runtime scaling blur."""
    if not path or not path.exists() or target_w <= 0 or target_h <= 0:
        return ""
    if not _PIL:
        return _file_to_data_uri(path)

    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            src_w, src_h = img.size

            # Cover-crop in PIL (same visual behavior as CSS cover) then exact resize.
            target_ratio = target_w / target_h
            src_ratio = src_w / src_h if src_h else target_ratio

            if src_ratio > target_ratio:
                # Source is wider: crop left/right.
                new_w = int(src_h * target_ratio)
                left = max(0, (src_w - new_w) // 2)
                img = img.crop((left, 0, left + new_w, src_h))
            elif src_ratio < target_ratio:
                # Source is taller: crop top/bottom.
                new_h = int(src_w / target_ratio)
                top = max(0, (src_h - new_h) // 2)
                img = img.crop((0, top, src_w, top + new_h))

            img = img.resize((target_w, target_h), Image.LANCZOS)
            img = img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=75, threshold=3))

            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
            return f"data:image/png;base64,{base64.b64encode(data).decode()}"
    except Exception:
        return _enhanced_header_file_to_data_uri(path)


def _pick_random_best_quality(images: list[Path]) -> Path:
    """Pick randomly among the highest-resolution images to keep quality consistent."""
    if not images:
        return None
    if not _PIL:
        return random.choice(images)

    scored = []
    for p in images:
        try:
            with Image.open(p) as im:
                w, h = im.size
            scored.append((w * h, p))
        except Exception:
            scored.append((0, p))

    if not scored:
        return random.choice(images)

    best_area = max(area for area, _ in scored)
    threshold = int(best_area * 0.80)
    pool = [p for area, p in scored if area >= threshold]
    return random.choice(pool or images)


def fmt_num(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", "\u202f")


def fmt_comma_num(n) -> str:
    if n is None:
        return "-"
    return f"{int(n):,}"


def fmt_signed(n) -> tuple[str, str]:
    """Returns (display_text, css_class) for a signed daily figure.

    Mirrors fmt_chg's sign convention so a track/section/era with an
    admin-forced negative daily (Spotify-side merge/split correction) shows
    a real minus sign instead of a broken "+-" and renders in red."""
    if n is None:
        return "—", ""
    if n < 0:
        return "−" + fmt_num(abs(n)), "neg"
    return "+" + fmt_num(n), ""


def fmt_chg(change, pct) -> tuple[str, str, str]:
    """Returns (change_str, pct_str, css_class)."""
    if change is None:
        return "—", "", "neutral"
    cls = "pos" if change >= 0 else "neg"
    chg_s = ("+" if change >= 0 else "−") + fmt_num(abs(change))
    pct_s = ""
    if pct is not None:
        sign = "+" if pct >= 0 else "−"
        pct_s = f"{sign}{abs(pct):.1f}%"
    return chg_s, pct_s, cls


def fmt_rate(rate) -> str:
    if rate is None:
        return ""
    return f"{rate:.1f}%"


def fmt_optional_num(n) -> str:
    if n is None:
        return ""
    return fmt_num(n)


# ── Data loading ───────────────────────────────────────────────────────────────

def load_album_sections(album_name: str, target_date: str | None = None) -> list[dict]:
    """
    Returns list of sections for the given album, each with:
      {name, tracks: [{track_id, title, title_clean, version_tag, display_order, image_url}]}
    Includes every track whose section/track is not marked chart_extra.
    Tracks sorted by display_order.

    If target_date is given, sections not yet released as of that date
    (release_date after target_date) are excluded — an edition/section
    must not appear before it actually existed.
    """
    if not ALBUMS_DIR.exists():
        return []

    target_payload = None
    for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(payload, dict) and (payload.get("album") or "").lower() == album_name.lower():
            target_payload = payload
            break

    if target_payload is None:
        return [], album_name

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
        if section_flag is not None:
            return bool(section_flag)

        edition = (track.get("edition") or "").strip().casefold()
        display_section = (
            track.get("display_section")
            or section.get("display_section")
            or section.get("title")
            or section.get("name")
            or ""
        ).strip().casefold()
        section_name = (section.get("section") or "").strip().casefold()
        album = (section.get("album") or track.get("album") or "").strip().casefold()
        track_type = (track.get("type") or "").strip().casefold()
        version_tag = (track.get("version_tag") or "").strip().casefold()
        haystack = " ".join(
            part
            for part in (edition, display_section, section_name, album, track_type, version_tag)
            if part
        )
        return any(
            token in haystack
            for token in (
                "standalone & extras",
                "extra",
                "kworb",
                "live",
                "karaoke",
                "acoustic",
                "remix",
                "track by track",
                "music video",
                "voice memo",
            )
        )

    canonical_name = target_payload.get("album") or album_name
    sections = []
    for sec in target_payload.get("sections", []):
        tracks = []
        seen_track_ids = set()
        for t in sec.get("tracks", []):
            if _is_chart_extra(sec, t):
                continue
            url = (t.get("url") or t.get("spotify_url") or "").strip()
            m = re.search(r"track/([A-Za-z0-9]+)", url)
            if not m:
                continue
            track_id = m.group(1)

            # Some album JSONs can contain accidental duplicate rows for a section.
            # Keep distinct versions in the same song family, e.g. All Too Well
            # and All Too Well (10 Minute Version), and only drop exact track dupes.
            if track_id in seen_track_ids:
                continue
            seen_track_ids.add(track_id)

            try:
                display_order = int(t.get("display_order") or 9999)
            except Exception:
                display_order = 9999

            tracks.append({
                "track_id":     track_id,
                "title":        (t.get("title") or t.get("title_clean") or "").strip(),
                "title_clean":  (t.get("title_clean") or t.get("title") or "").strip(),
                "release_date":  (t.get("release_date") or "").strip(),
                "version_tag":  (t.get("version_tag") or "").strip(),
                "display_order": display_order,
                "image_url":    (t.get("image_url") or "").strip(),
            })
        if not tracks:
            continue
        tracks.sort(key=lambda x: (x["display_order"], x["title_clean"].casefold()))
        name = (
            sec.get("display_section")
            or sec.get("section", "").replace("_", " ").title()
        )
        release_dates = [
            str(t.get("release_date") or "")[:10]
            for t in tracks
            if re.match(r"\d{4}-\d{2}-\d{2}", str(t.get("release_date") or ""))
        ]
        sections.append({
            "name": name,
            "tracks": tracks,
            "release_date": min(release_dates) if release_dates else "",
            "source_order": len(sections),
        })

    # Keep album update sections in release-date order; preserve DB order for ties.
    def sort_key(sec):
        return (sec.get("release_date") or "9999-12-31", sec.get("source_order", 9999))
    sections.sort(key=sort_key)

    if target_date:
        sections = [
            sec for sec in sections
            if not sec.get("release_date") or sec["release_date"] <= target_date
        ]

    return sections, canonical_name


def load_history_for_album(
    sections: list[dict], target_date: str
) -> dict[str, dict]:
    """
    Returns {track_id: {streams, daily, change, pct}} for target_date.
    change = daily_today - daily_yesterday
    pct    = change / daily_yesterday * 100  (None if yest == 0)
    """
    yesterday = str(date_cls.fromisoformat(target_date) - timedelta(days=1))
    day_before = str(date_cls.fromisoformat(target_date) - timedelta(days=2))
    all_ids = {t["track_id"] for sec in sections for t in sec["tracks"]}
    release_dates = {
        t["track_id"]: (t.get("release_date") or "")[:10]
        for sec in sections for t in sec["tracks"]
    }

    today_data: dict[str, dict] = {}
    yest_data: dict[str, dict] = {}
    before_data: dict[str, dict] = {}

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
            tid = row.get("track_id") or ""
            if tid not in all_ids:
                continue

            d = row.get("date") or ""
            if d not in (target_date, yesterday, day_before):
                continue

            entry = {
                "streams": int(row.get("streams") or 0),
                "daily_streams": _parse_optional_int(row.get("daily_streams")),
                "estimated_reason": (row.get("estimated_reason") or "").strip(),
            }
            if d == target_date:
                today_data[tid] = entry
            elif d == yesterday:
                yest_data[tid] = entry
            else:
                before_data[tid] = entry

    def _fill_missing_daily(cur: dict[str, dict], prev: dict[str, dict]) -> None:
        for tid, e in cur.items():
            if e.get("daily_streams") is not None:
                continue
            reason = e.get("estimated_reason") or ""
            if reason == "manual_trusted" or reason.startswith("collection_incident_"):
                continue
            p = prev.get(tid)
            if not p:
                continue
            diff = e.get("streams", 0) - p.get("streams", 0)
            if diff >= 0:
                e["daily_streams"] = diff

    _fill_missing_daily(today_data, yest_data)
    _fill_missing_daily(yest_data, before_data)

    def _is_release_day(tid: str) -> bool:
        # The catalogue release_date is the ground truth for "NEW" (shown on
        # the exact release day), not CSV history: a track's first CSV row
        # can predate or postdate its real release (backfill baseline rows,
        # or a track added to collection scope later than it was released).
        return release_dates.get(tid) == target_date

    result = {}
    for tid in all_ids:
        t = today_data.get(tid)
        if t is None:
            result[tid] = {
                "streams": None,
                "daily": None,
                "change": None,
                "pct": None,
                "ever_seen": not _is_release_day(tid),
            }
            continue
        y = yest_data.get(tid)
        daily = t.get("daily_streams")
        streams = t.get("streams")
        yest_d = (y or {}).get("daily_streams")
        change = (daily - yest_d) if (daily is not None and yest_d is not None) else None
        pct = (change / yest_d * 100) if (change is not None and yest_d not in (None, 0)) else None
        result[tid] = {
            "streams": streams,
            "daily":   daily,
            "change":  change,
            "pct":     pct,
            "ever_seen": not _is_release_day(tid),
        }
    return result


def sort_album_sections_by_daily_streams(sections: list[dict], hist: dict[str, dict]) -> None:
    """Sort tracks inside each album section by daily streams descending."""
    for sec in sections:
        sec["tracks"].sort(
            key=lambda t: (
                -(hist.get(t["track_id"], {}).get("daily") or 0),
                -(hist.get(t["track_id"], {}).get("streams") or 0),
                t.get("title_clean", "").casefold(),
            )
        )


def load_global_chart_filtered_for_album(sections: list[dict], target_date: str) -> tuple[dict[str, dict], bool]:
    """
    Returns ({track_id: {filtered_streams, filter_rate}}, chart_available).
    chart_available is True only when same-date global chart JSON exists and is readable.
    """
    json_path = CHARTS_GLOBAL_HISTORY_DIR / target_date[:4] / target_date[5:7] / target_date / f"ts_chart_{target_date}.json"
    if not json_path.exists():
        return {}, False

    try:
        entries = json.loads(json_path.read_text(encoding="utf-8-sig"))
        if not isinstance(entries, list):
            return {}, False
    except Exception:
        return {}, False

    chart_by_title: dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        title = (e.get("track_name") or "").strip()
        streams = e.get("streams")
        if not title:
            continue
        try:
            streams_i = int(streams)
        except Exception:
            continue
        chart_by_title[_norm(title)] = streams_i

    if not chart_by_title:
        return {}, False

    result: dict[str, dict] = {}
    matched_any = False
    # Daily streams for filter rate denominator (same day only)
    daily_map: dict[str, int] = {}
    all_ids = {t["track_id"] for sec in sections for t in sec["tracks"]}
    with open(HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("date") != target_date:
                continue
            tid = row.get("track_id")
            if tid not in all_ids:
                continue
            try:
                daily_map[tid] = int(row.get("daily_streams") or 0)
            except Exception:
                daily_map[tid] = 0

    for sec in sections:
        for t in sec["tracks"]:
            tid = t["track_id"]
            t_norm = _norm(t.get("title_clean") or "")
            f_streams = chart_by_title.get(t_norm)
            if f_streams is None:
                result[tid] = {"filtered_streams": None, "filter_rate": None}
                continue
            matched_any = True
            daily = daily_map.get(tid, 0)
            rate = (100 - (f_streams / daily * 100)) if daily > 0 else None
            result[tid] = {"filtered_streams": f_streams, "filter_rate": rate}

    # Keep layout compact when chart exists but no track could be matched.
    return result, matched_any


def load_cover_url(album_name: str) -> str:
    # 1) Primary source: covers.json
    try:
        if COVERS_PATH.exists():
            covers = json.loads(COVERS_PATH.read_text(encoding="utf-8-sig"))
            for v in covers.values():
                if (v.get("title") or "").lower() == album_name.lower():
                    url = v.get("cover_url", "")
                    if url:
                        return url
    except Exception:
        pass

    # 2) Fallback: album files track image_url (fixes missing entries like Holiday Collection)
    try:
        if ALBUMS_DIR.exists():
            for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
                if not isinstance(payload, dict) or (payload.get("album") or "").lower() != album_name.lower():
                    continue
                for section in payload.get("sections", []):
                    for tr in section.get("tracks", []):
                        url = tr.get("image_url", "")
                        if url:
                            return url
    except Exception:
        pass

    return ""


def header_images_for_album(album_name: str) -> list[Path]:
    if not HEADERS_DIR.exists():
        return []

    allowed_exts = {".png", ".jpg", ".jpeg", ".webp"}
    target_raw = (album_name or "").strip().casefold()
    target_norm = _norm(album_name)
    result: list[Path] = []

    # New structure: db/discography/headers/<album_name>/*.png|jpg|jpeg|webp
    album_dirs = [p for p in HEADERS_DIR.iterdir() if p.is_dir()]
    selected_dir = None
    for d in sorted(album_dirs, key=lambda x: x.name.casefold()):
        if d.name.casefold() == target_raw:
            selected_dir = d
            break
    if selected_dir is None:
        for d in sorted(album_dirs, key=lambda x: x.name.casefold()):
            if _norm(d.name) == target_norm:
                selected_dir = d
                break

    if selected_dir is not None:
        result.extend(
            p for p in selected_dir.iterdir()
            if p.is_file() and p.suffix.lower() in allowed_exts
        )

    # Legacy fallback: db/discography/headers/<album_name>.<ext>
    flat_candidates = [
        p for p in HEADERS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in allowed_exts
    ]
    for p in sorted(flat_candidates, key=lambda x: x.name.casefold()):
        if p.stem.casefold() == target_raw:
            result.append(p)
    for p in sorted(flat_candidates, key=lambda x: x.name.casefold()):
        if _norm(p.stem) == target_norm:
            result.append(p)

    seen = set()
    deduped = []
    for path in sorted(result, key=lambda x: (x.parent.name.casefold(), x.name.casefold())):
        key = str(path.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _preferred_header_for_album(album_name: str, variant: str = "dark") -> Path | None:
    if not PREFERRED_HEADERS_PATH.exists():
        return None
    try:
        prefs = json.loads(PREFERRED_HEADERS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(prefs, dict):
        return None

    keys = [album_name, album_name.strip().casefold(), _norm(album_name)]
    pref = None
    for key in keys:
        value = prefs.get(key)
        # A value may be a plain string (used for every variant) or a mapping
        # {"dark": "...", "light": "..."} to pick a header per theme variant.
        if isinstance(value, dict):
            picked = value.get(variant) or value.get("dark") or next(
                (v for v in value.values() if isinstance(v, str) and v.strip()), None
            )
            if isinstance(picked, str) and picked.strip():
                pref = picked.strip()
                break
        elif isinstance(value, str) and value.strip():
            pref = value.strip()
            break
    if not pref:
        return None

    candidates = []
    pref_path = Path(pref)
    if pref_path.is_absolute():
        candidates.append(pref_path)
    else:
        candidates.append((HEADERS_DIR / pref_path).resolve())
        candidates.extend(path for path in header_images_for_album(album_name) if path.name == pref or path.stem == pref)

    allowed_exts = {".png", ".jpg", ".jpeg", ".webp"}
    for path in candidates:
        if path.exists() and path.is_file() and path.suffix.lower() in allowed_exts:
            return path
    return None


def pick_header_image(album_name: str, variant: str = "dark") -> Path | None:
    return _preferred_header_for_album(album_name, variant) or _pick_random_best_quality(header_images_for_album(album_name))


def resolve_header_arg(album_name: str, header_arg: str | None, variant: str = "dark") -> Path | None:
    if not header_arg:
        return pick_header_image(album_name, variant)

    raw = Path(header_arg)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append((Path.cwd() / raw).resolve())
        candidates.extend(
            path
            for path in header_images_for_album(album_name)
            if path.name == header_arg or path.stem == header_arg
        )

    for path in candidates:
        if path.exists() and path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            return path
    raise FileNotFoundError(f"Header introuvable pour {album_name!r}: {header_arg}")


def effective_album_update_style(album_name: str, style: str) -> str:
    if style == "default" and album_name.strip().casefold() in TABLE_DARK_DEFAULT_ALBUMS:
        return "table-dark"
    return style


def get_latest_date() -> str:
    latest = ""
    with open(HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["date"] > latest:
                latest = row["date"]
    if not latest:
        raise ValueError("streams_history.csv est vide")
    return latest


# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  background:#ffffff;
  width:var(--body-w, 880px);
  padding:0;
  color:#101828;
}
.container{
  width:100%;
  overflow:hidden;
  background:#ffffff;
}
/* ── header ── */
.hdr{
  height:110px;
  display:flex;align-items:center;gap:14px;
  padding:0 16px;
  position:relative;overflow:hidden;
  background:linear-gradient(135deg, rgba(29,185,84,.15) 0%, rgba(21,136,62,.08) 100%);
}
.hdr-overlay{
  position:absolute;inset:0;
  background:linear-gradient(90deg, rgba(0,0,0,0.58) 0%, rgba(0,0,0,0.34) 36%, rgba(0,0,0,0.12) 66%, rgba(0,0,0,0.0) 100%);
  pointer-events:none;
}
.hdr-cover{
  width:72px;height:72px;border-radius:10px;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 4px 14px rgba(0,0,0,.15);
  position:relative;
  z-index:1;
}
.hdr-cover-ph{
  width:72px;height:72px;border-radius:10px;
  background:linear-gradient(135deg,#e8f5ee 0%,#d4f1e0 100%);
  flex-shrink:0;
  position:relative;
  z-index:1;
}
.hdr-text{
  display:flex;flex-direction:column;gap:4px;
  min-width:0;
  max-width:calc(100% - 92px);
  position:relative;
  z-index:1;
}
.hdr-title{color:#101828;font-size:22px;font-weight:800;letter-spacing:-.4px;line-height:1.2}
.hdr-sub{color:#667085;font-size:14px;font-weight:600;line-height:1.3}
.hdr-handle{
  display:flex;
  align-items:center;
  gap:6px;
  font-size:12px;
  font-weight:700;
  line-height:1.3;
}
.hdr-handle-icon{
  width:14px;
  height:14px;
  object-fit:contain;
  flex-shrink:0;
}
.hdr-title,.hdr-sub{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hdr-sub .sep{opacity:.72;margin:0 6px}
.hdr-date-chip{
  display:inline-block;
  padding:1px 8px;
  border-radius:999px;
  font-weight:800;
  letter-spacing:.01em;
  color:var(--hdr-date-fg);
  background:var(--hdr-date-bg);
  border:1px solid var(--hdr-date-br);
}
/* ── column headers ── */
.col-heads{
  display:grid;
  grid-template-columns:var(--grid-cols);
  column-gap:8px;
  padding:6px 18px;
  background:var(--tint-bg);
  border-bottom:1px solid var(--tint-border);
}
.col-heads span{
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.08em;color:#9aa5b4;
  display:flex;align-items:center;
}
.col-heads .center{justify-content:center}
.col-heads .right{justify-content:flex-end}
/* ── song rows ── */
.song-row{
  display:grid;
  grid-template-columns:var(--grid-cols);
  column-gap:8px;
  align-items:center;
  padding:0 18px;
  height:var(--row-h);
  border-bottom:1px solid rgba(16,24,40,.04);
  background:#ffffff;
}
.song-row.alt{background:var(--alt-row)}
.col-rank{
  font-size:12px;color:#b0bac8;font-weight:600;
  text-align:center;
}
.col-song{display:flex;flex-direction:column;justify-content:center;min-width:0}
.song-title{
  font-size:13px;font-weight:600;color:#101828;
  display:block;
  text-align:center;
  white-space:nowrap;overflow:visible;text-overflow:clip;
}
.best-day-note{
  font-size:10px;
  font-weight:700;
  color:#7f8794;
}
.song-title.has-tag{font-size:12.5px}
.song-row.no-filter .col-song{grid-column:2/5}
.song-row.no-filter .song-title{padding-right:6px}
.song-row.no-filter .filtered-col,
.song-row.no-filter .rate-col{visibility:hidden}
.song-row.no-filter .daily-col{grid-column:5}
.song-row.no-filter .chg-col{grid-column:6}
.song-row.no-filter .pct-col{grid-column:7}
.song-row.no-filter .total-col{grid-column:8}
.song-ver{
  display:block;
  font-size:11px;color:#9aa5b4;font-weight:400;
  text-align:center;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.col-num{
  font-size:12px;color:#344054;font-weight:700;
  display:flex;align-items:center;justify-content:flex-end;
}
.col-num.daily-val{color:#101828;font-size:13px;font-weight:700}
.col-num.daily-val.neg{color:#b42318}
.col-chg{font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:flex-end}
.col-pct{font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:flex-end}
.pos{color:#067647}
.neg{color:#b42318}
.neutral{color:#667085}
.new{color:#5bbde4}
/* ── section total ── */
.sec-total{
  display:grid;
  grid-template-columns:var(--grid-cols);
  column-gap:8px;
  align-items:center;
  padding:6px 18px;
  height:36px;
  box-shadow:inset 5px 0 0 var(--sec-accent);
  background:var(--sec-bg);
  font-weight:700;
}
.sec-label{
  grid-column:1/3;
  font-size:12px;color:var(--sec-accent);
  padding-left:2px;
}
.sec-num{
  font-size:13px;
  display:flex;align-items:center;justify-content:flex-end;color:#101828;
  font-weight:700;
}
.sec-num.neg{color:#b42318}
.tot-chip-wrap{display:flex;align-items:center;justify-content:flex-end;width:100%}
.tot-chip{
  display:inline-flex;
  align-items:center;
  justify-content:space-between;
  width:100%;
  gap:10px;
  min-height:24px;
  padding:2px 12px;
  border-radius:8px;
  border:1px solid rgba(16,24,40,.15);
  background:rgba(255,255,255,.72);
}
.tot-chip-val{font-size:12px;font-weight:800;color:#101828;white-space:nowrap;display:inline-flex;justify-content:flex-end;min-width:0}
.tot-chip-val.chip-pos{color:#067647}
.tot-chip-val.chip-neg{color:#b42318}
.tot-chip-val.chip-neutral{color:#667085}
.tot-chip-val.chip-new{color:#5bbde4}
.sec-filter-wrap{grid-column:3/5}
.sec-main-wrap{grid-column:5/9}
.sec-total.no-filter .sec-main-wrap{grid-column:3/7}
/* ── grand total ── */
.era-total{
  display:grid;
  grid-template-columns:var(--grid-cols);
  column-gap:8px;
  align-items:center;
  padding:6px 18px;
  height:38px;
  background:linear-gradient(135deg, #0d1117 0%, #1a1f26 100%);
  border-top:2px solid var(--tint-border);
}
.era-label{
  grid-column:1/3;
  font-size:14px;font-weight:800;color:rgba(255,255,255,.95);
  padding-left:2px;
  display:flex;align-items:center;
}
.era-num{
  font-size:14px;font-weight:800;color:rgba(255,255,255,.95);
  display:flex;align-items:center;justify-content:flex-end;
}
.era-num.neg{color:#b42318}
.era-filter-wrap{grid-column:3/5}
.era-main-wrap{grid-column:5/9}
.era-total.no-filter .era-main-wrap{grid-column:3/7}
.era-total .tot-chip{
  border-color:rgba(255,255,255,.28);
  background:rgba(255,255,255,.12);
}
.era-total .tot-chip-val{color:rgba(255,255,255,.95)}
.era-total .tot-chip-val.chip-pos{color:#7ce9a4}
.era-total .tot-chip-val.chip-neg{color:#ffb0a8}
.era-total .tot-chip-val.chip-neutral{color:rgba(255,255,255,.82)}
.era-total .tot-chip-val.chip-new{color:#5bbde4}
/* ── footer ── */
.ftr{
  background:var(--tint-bg);
  padding:7px 18px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid var(--tint-border);
}
.ftr-handle{font-size:12px;font-weight:700}
.ftr-date{font-size:12px;color:#667085;font-weight:500}
"""


# ── HTML builders ──────────────────────────────────────────────────────────────

def _css_hsl(h_deg: float, s_pct: float, l_pct: float) -> str:
    return f"hsl({h_deg:.1f},{s_pct:.1f}%,{l_pct:.1f}%)"


def _estimate_title_width_px(text: str, font_size_px: float = 13.0) -> float:
    """Fast width estimate used to size the SONG column without browser measurement."""
    if not text:
        return 0.0

    text = html.unescape(re.sub(r"<[^>]+>", "", text))

    # Relative glyph width factors tuned for Inter-like sans serif fonts.
    narrow = set(" ilI'`.,:;!|()[]{}")
    wide = set("MW@#%&QGm")
    total = 0.0
    for ch in text:
        if ch in narrow:
            total += 0.28
        elif ch in wide:
            total += 0.62
        else:
            total += 0.46
    return total * font_size_px


def _compute_layout_metrics(
    sections: list[dict],
    show_filter_cols: bool,
    best_day_labels_by_track: dict[str, str] | None = None,
) -> dict:
    """Compute dynamic grid/body sizing to avoid extra whitespace in final PNG."""
    total_tracks = sum(len(s["tracks"]) for s in sections)
    row_h = max(20, min(36, 20 + (16 - total_tracks) * 2))
    best_day_labels_by_track = best_day_labels_by_track or {}
    song_header = "SONG (MM/DD/YYYY)" if best_day_labels_by_track else "SONG"

    titles = [
        _display_song_title(t, best_day_labels_by_track.get(t.get("track_id", "")))
        for s in sections
        for t in s.get("tracks", [])
    ]
    longest_title_px = max((_estimate_title_width_px(t) for t in titles), default=150.0)

    col_gap_px = 8
    # Keep enough safety margin so titles stay on a single line without clipping.
    song_buffer_px = 28
    row_padding_px = 18

    if show_filter_cols:
        cols = [36, 0, 106, 72, 106, 74, 66, 106]
        song_col_px = int(max(120, longest_title_px + song_buffer_px))
        cols[1] = song_col_px
        grid_cols = f"36px {song_col_px}px 106px 72px 106px 74px 66px 106px"
        col_heads_html = f"""<div class="col-heads">
    <span class="center">#</span>
    <span>{song_header}</span>
    <span class="right">FILTERED</span>
    <span class="right">RATE</span>
    <span class="right">DAILY</span>
    <span class="right">CHG</span>
    <span class="right">%</span>
    <span class="right">TOTAL</span>
  </div>"""
    else:
        cols = [40, 0, 120, 80, 80, 110]
        song_col_px = int(max(130, longest_title_px + song_buffer_px))
        cols[1] = song_col_px
        grid_cols = f"40px {song_col_px}px 120px 80px 80px 110px"
        col_heads_html = f"""<div class="col-heads">
    <span class="center">#</span>
    <span>{song_header}</span>
    <span class="right">DAILY</span>
    <span class="right">CHG</span>
    <span class="right">%</span>
    <span class="right">TOTAL</span>
  </div>"""

    cols_count = len(cols)
    row_content_width_px = sum(cols) + (cols_count - 1) * col_gap_px + 2 * row_padding_px
    body_width_px = row_content_width_px + 2 * BODY_PADDING_CSS

    return {
        "row_h": row_h,
        "grid_cols": grid_cols,
        "col_heads_html": col_heads_html,
        "body_width_px": body_width_px,
    }


def _edition_css(dominant_hex: str, bi: int) -> tuple[str, str]:
    """Returns (accent_css, bg_css) for section total row."""
    m = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", dominant_hex.lower())
    if not m:
        h, s, bg_l = 142.0, 60.0, 96.5
    else:
        r, g, b = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)
        h_f, l_f, s_f = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        h = h_f * 360
        s = max(40.0, min(s_f * 100, 75.0))
        bg_l = max(92.0, 96.8 - bi * 1.2)
    accent = _css_hsl(h, s, 42.0)
    bg     = _css_hsl(h, min(s, 45.0), bg_l)
    return accent, bg


def _chip_cls(state: str) -> str:
    if state == "pos":
        return "chip-pos"
    if state == "neg":
        return "chip-neg"
    if state == "neutral":
        return "chip-neutral"
    if state == "new":
        return "chip-new"
    return ""


def _build_totals_chip(items: list[tuple[str, str]]) -> str:
    vals = []
    for value, cls in items:
        class_attr = f" tot-chip-val {cls}".rstrip()
        vals.append(f'<span class="{class_attr}">{value}</span>')
    return f'<div class="tot-chip">{"".join(vals)}</div>'


def _display_song_title(track: dict, best_day_label: str | None = None) -> str:
    title = _shorten_title(track.get("title") or track.get("title_clean") or "")
    if best_day_label:
        prefix = "" if best_day_label.startswith("of ") else "since "
        return f"&#9733; {title} <span class=\"best-day-note\">&middot; {prefix}{html.escape(best_day_label)}</span>"
    return title


def build_song_row_html(
    si: int,
    track: dict,
    hdata: dict,
    alt: bool,
    show_filter_cols: bool,
    best_day_labels_by_track: dict[str, str] | None = None,
) -> str:
    best_day_labels_by_track = best_day_labels_by_track or {}
    title = _display_song_title(track, best_day_labels_by_track.get(track.get("track_id", "")))
    daily = hdata.get("daily")
    change = hdata.get("change")
    pct = hdata.get("pct")
    streams = hdata.get("streams")
    f_streams = hdata.get("filtered_streams")
    f_rate = hdata.get("filter_rate")

    daily_s, daily_cls = fmt_signed(daily)
    chg_s, pct_s, chg_cls = fmt_chg(change, pct)
    if not hdata.get("ever_seen", True):
        chg_s = "NEW"
        pct_s = "NEW"
        chg_cls = "new"

    alt_cls = " alt" if alt else ""

    if show_filter_cols:
        extra_cells = f"""
            <div class="col-num filtered-col">{fmt_optional_num(f_streams)}</div>
        <div class="col-pct neutral rate-col">{fmt_rate(f_rate)}</div>"""
    else:
        extra_cells = ""

    return f"""<div class="song-row{alt_cls}">
    <div class="col-rank">{si + 1}</div>
    <div class="col-song">
        <div class="song-title">{title}</div>
    </div>
{extra_cells}
        <div class="col-num daily-val daily-col {daily_cls}">{daily_s}</div>
        <div class="col-chg {chg_cls} chg-col">{chg_s}</div>
        <div class="col-pct {chg_cls} pct-col">{pct_s}</div>
    <div class="col-num total-col">{fmt_num(streams)}</div>
</div>
"""


def build_section_total_html(sec_name: str, tracks: list[dict],
                              hist: dict, accent: str, bg: str, show_filter_cols: bool) -> str:
    sec_daily  = sum(hist.get(t["track_id"], {}).get("daily") or 0 for t in tracks)
    sec_str    = sum(hist.get(t["track_id"], {}).get("streams") or 0 for t in tracks)
    sec_flt    = sum(hist.get(t["track_id"], {}).get("filtered_streams") or 0 for t in tracks)
    sec_flt_cnt = sum(1 for t in tracks if hist.get(t["track_id"], {}).get("filtered_streams") is not None)
    sec_daily_flt = sum(
        (hist.get(t["track_id"], {}).get("daily") or 0)
        for t in tracks
        if hist.get(t["track_id"], {}).get("filtered_streams") is not None
    )
    sec_flt_disp = sec_flt if sec_flt_cnt > 0 else None
    sec_rate_disp = (100 - (sec_flt / sec_daily_flt * 100)) if (sec_flt_cnt > 0 and sec_daily_flt > 0) else None
    sec_change = sum(hist.get(t["track_id"], {}).get("change") or 0 for t in tracks)
    sec_yest   = sec_daily - sec_change
    sec_pct    = (sec_change / sec_yest * 100) if sec_yest != 0 else None

    chg_s, pct_s, chg_cls = fmt_chg(sec_change, sec_pct)
    if tracks and all(not hist.get(t["track_id"], {}).get("ever_seen", True) for t in tracks):
        chg_s, pct_s, chg_cls = "NEW", "NEW", "new"
    pct_disp = pct_s or "—"
    chg_chip_cls = _chip_cls(chg_cls)

    if show_filter_cols:
        flt_disp = fmt_optional_num(sec_flt_disp) or "—"
        rate_disp = fmt_rate(sec_rate_disp) or "—"
        filter_chip = _build_totals_chip([
            (flt_disp, ""),
            (rate_disp, "chip-neutral"),
        ])
        extra_cells = f"""
    <div class="tot-chip-wrap sec-filter-wrap">{filter_chip}</div>"""
    else:
        extra_cells = ""

    sec_daily_s, sec_daily_cls = fmt_signed(sec_daily)
    main_chip = _build_totals_chip([
        (sec_daily_s, _chip_cls(sec_daily_cls)),
        (chg_s, chg_chip_cls),
        (pct_disp, chg_chip_cls),
        (fmt_num(sec_str), ""),
    ])
    no_filter_cls = " no-filter" if not show_filter_cols else ""

    if not show_filter_cols:
        return f"""<div class="sec-total{no_filter_cls}" style="--sec-accent:{accent};--sec-bg:{bg}">
    <div class="sec-label">{sec_name}&nbsp;&nbsp;—&nbsp;&nbsp;Total</div>
    <div class="sec-num {sec_daily_cls}" style="grid-column:3">{sec_daily_s}</div>
    <div class="sec-num {chg_cls}" style="grid-column:4">{chg_s}</div>
    <div class="sec-num {chg_cls}" style="grid-column:5">{pct_disp}</div>
    <div class="sec-num" style="grid-column:6">{fmt_num(sec_str)}</div>
</div>
"""

    return f"""<div class="sec-total{no_filter_cls}" style="--sec-accent:{accent};--sec-bg:{bg}">
  <div class="sec-label">{sec_name}&nbsp;&nbsp;—&nbsp;&nbsp;Total</div>
{extra_cells}
  <div class="tot-chip-wrap sec-main-wrap">{main_chip}</div>
</div>
"""


def build_html(
    album_name: str,
    sections: list[dict],
    hist: dict,
    target_date: str,
    cover_uri: str,
    header_uri: str,
    dominant_hex: str,
    section_palette: list[str] | None = None,
    show_filter_cols: bool = False,
    layout: dict | None = None,
    handle_icon_uri: str = "",
    best_day_labels_by_track: dict[str, str] | None = None,
) -> str:
    from datetime import datetime
    date_obj = datetime.strptime(target_date, "%Y-%m-%d")
    date_fmt = f"{date_obj.strftime('%A, %B')} {_ordinal(date_obj.day)}, {date_obj.year}"

    # Base accent RGB used across tinted UI blocks.
    m_dom = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", dominant_hex.lower())
    if m_dom:
        dr, dg, db = int(m_dom.group(1), 16), int(m_dom.group(2), 16), int(m_dom.group(3), 16)
    else:
        dr, dg, db = 29, 185, 84

    # header background
    if header_uri:
        hdr_bg = f"background:url('{header_uri}') center/100% 100% no-repeat;"
        hdr_text_color = "color:#ffffff;"
        hdr_sub_color  = "color:rgba(255,255,255,0.92);"
        hdr_overlay    = '<div class="hdr-overlay"></div>'
    else:
        m = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", dominant_hex.lower())
        if m:
            r, g, b = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)
            h, lightness, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            accent_light = _css_hsl(h * 360, s * 100, 92.0)
            accent_mid   = _css_hsl(h * 360, s * 100, 88.0)
        else:
            accent_light = "#e8f5ee"
            accent_mid   = "#d4f1e0"
        hdr_bg         = f"background:linear-gradient(135deg, {accent_light} 0%, {accent_mid} 100%);"
        hdr_text_color = "color:#101828;"
        hdr_sub_color  = "color:#667085;"
        hdr_overlay    = ""

    # album cover img or placeholder
    if cover_uri:
        cover_html = f'<img class="hdr-cover" src="{cover_uri}" />'
    else:
        cover_html = '<div class="hdr-cover-ph"></div>'

    # handle icon
    if handle_icon_uri:
        handle_icon_html = f'<img class="hdr-handle-icon" src="{handle_icon_uri}" alt="">'
    else:
        handle_icon_html = ""

    # alternate row color based on dominant
    alt_row_css = f"rgba({dr},{dg},{db},0.05)"
    tint_bg_css = f"rgba({dr},{dg},{db},0.08)"
    tint_border_css = f"rgba({dr},{dg},{db},0.18)"

    best_day_labels_by_track = best_day_labels_by_track or {}
    layout = layout or _compute_layout_metrics(sections, show_filter_cols, best_day_labels_by_track)
    row_h = layout["row_h"]
    grid_cols = layout["grid_cols"]
    col_heads_html = layout["col_heads_html"]
    body_width_px = layout["body_width_px"]

    # build song rows + section totals
    rows_html = ""
    total_daily   = 0
    total_streams = 0
    total_change  = 0

    sec_bg_css = f"rgba({dr},{dg},{db},0.14)"

    palette = section_palette or []

    for bi, sec in enumerate(sections):
        _accent, _bg = _edition_css(dominant_hex, bi)
        accent = palette[bi % len(palette)] if palette else dominant_hex
        m_acc = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", accent.lower())
        if m_acc:
            ar, ag, ab = int(m_acc.group(1), 16), int(m_acc.group(2), 16), int(m_acc.group(3), 16)
            bg = f"rgba({ar},{ag},{ab},0.15)"
        else:
            bg = sec_bg_css
        for si, track in enumerate(sec["tracks"]):
            hd = hist.get(track["track_id"], {"daily": None, "change": None, "pct": None, "streams": None})
            if not show_filter_cols:
                hd = {**hd, "filtered_streams": None, "filter_rate": None}
            rows_html += build_song_row_html(si, track, hd, si % 2 != 0, show_filter_cols, best_day_labels_by_track)
        rows_html += build_section_total_html(sec["name"], sec["tracks"], hist, accent, bg, show_filter_cols)

        for t in sec["tracks"]:
            hd = hist.get(t["track_id"], {})
            total_daily   += hd.get("daily") or 0
            total_streams += hd.get("streams") or 0
            total_change  += hd.get("change") or 0
        total_filtered = sum(
            (hist.get(t["track_id"], {}).get("filtered_streams") or 0)
            for sec in sections for t in sec["tracks"]
        )
        total_filtered_count = sum(
            1
            for sec in sections for t in sec["tracks"]
            if hist.get(t["track_id"], {}).get("filtered_streams") is not None
        )
        total_daily_filtered = sum(
            (hist.get(t["track_id"], {}).get("daily") or 0)
            for sec in sections for t in sec["tracks"]
            if hist.get(t["track_id"], {}).get("filtered_streams") is not None
        )

        # grand total
        total_yest = total_daily - total_change
        total_pct = (total_change / total_yest * 100) if total_yest != 0 else None
        tot_chg_s, tot_pct_s, chg_cls = fmt_chg(total_change, total_pct)
        if all(
            not hist.get(t["track_id"], {}).get("ever_seen", True)
            for sec2 in sections for t in sec2["tracks"]
        ):
            tot_chg_s, tot_pct_s, chg_cls = "NEW", "NEW", "new"

        if show_filter_cols:
            total_flt_disp = total_filtered if total_filtered_count > 0 else None
            total_rate_disp = (
                (100 - (total_filtered / total_daily_filtered * 100))
                if (total_filtered_count > 0 and total_daily_filtered > 0)
                else None
            )
            total_flt_text = fmt_optional_num(total_flt_disp) or "—"
            total_rate_text = fmt_rate(total_rate_disp) or "—"
            filter_chip = _build_totals_chip([
                (total_flt_text, ""),
                (total_rate_text, "chip-neutral"),
            ])

            total_daily_s, total_daily_cls = fmt_signed(total_daily)
            total_main_chip = _build_totals_chip([
                (total_daily_s, _chip_cls(total_daily_cls)),
                (tot_chg_s, _chip_cls(chg_cls)),
                (tot_pct_s or "—", _chip_cls(chg_cls)),
                (fmt_num(total_streams), ""),
            ])

            era_html = f"""<div class="era-total">
    <div class="era-label">Total</div>
    <div class="tot-chip-wrap era-filter-wrap">{filter_chip}</div>
    <div class="tot-chip-wrap era-main-wrap">{total_main_chip}</div>
</div>
"""
        else:
            total_daily_s, total_daily_cls = fmt_signed(total_daily)
            era_html = f"""<div class="era-total no-filter">
    <div class="era-label">Total</div>
    <div class="era-num {total_daily_cls}" style="grid-column:3">{total_daily_s}</div>
    <div class="era-num {chg_cls}" style="grid-column:4">{tot_chg_s}</div>
    <div class="era-num {chg_cls}" style="grid-column:5">{tot_pct_s or "—"}</div>
    <div class="era-num" style="grid-column:6">{fmt_num(total_streams)}</div>
</div>
"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
{CSS}
:root {{ --body-w: {body_width_px}px; --alt-row: {alt_row_css}; --tint-bg: {tint_bg_css}; --tint-border: {tint_border_css}; --hdr-date-fg: rgba(255,255,255,.98); --hdr-date-bg: rgba(0,0,0,.22); --hdr-date-br: rgba(255,255,255,.35); --row-h: {row_h}px; --grid-cols: {grid_cols}; }}
</style>
</head><body>
<div class="container">
  <div class="hdr" style="{hdr_bg}">
    {hdr_overlay}
    {cover_html}
    <div class="hdr-text">
      <div class="hdr-title" style="{hdr_text_color}">{album_name}</div>
      <div class="hdr-sub" style="{hdr_sub_color}">Taylor Swift<span class="sep">&middot;</span><span class="hdr-date-chip">{date_fmt}</span></div>
      <div class="hdr-handle" style="color:{dominant_hex}">
        {handle_icon_html}
        <span>{HANDLE}</span>
      </div>
    </div>
  </div>
  {col_heads_html}
  {rows_html}
  {era_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{dominant_hex}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div>
</body></html>"""


# ── Main generate function ─────────────────────────────────────────────────────

TABLE_DARK_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{width:1106px;background:var(--page-bg);color:var(--text);font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}
.dark-card{position:relative;width:1106px;padding:0 8px 10px;overflow:hidden;background:var(--card-bg)}
.dark-card:before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.19;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-size:3px 3px,5px 5px;mix-blend-mode:screen}
.hero{position:relative;height:304px;margin:0 -8px;display:flex;justify-content:center;align-items:flex-start;overflow:hidden;background:var(--hero-bg)}
.hero-img{position:absolute;inset:0;width:100%;height:304px;background-position:var(--hero-pos);background-size:cover;background-repeat:no-repeat;opacity:var(--hero-opacity);filter:var(--hero-filter);-webkit-mask-image:linear-gradient(180deg,#000 0%,#000 55%,rgba(0,0,0,.68) 72%,transparent 100%);mask-image:linear-gradient(180deg,#000 0%,#000 55%,rgba(0,0,0,.68) 72%,transparent 100%)}
.hero:after{content:"";position:absolute;inset:0;background:var(--hero-overlay)}
.hero-date{position:absolute;left:0;right:0;bottom:101px;z-index:2;text-align:center;color:var(--hero-text);font-size:22px;line-height:1;font-weight:900;text-transform:uppercase;text-shadow:0 8px 18px rgba(0,0,0,.78)}
.album-title{position:absolute;left:0;right:0;bottom:var(--title-bottom);z-index:2;text-align:center;font-family:var(--title-font);font-size:var(--title-size);line-height:.9;font-weight:900;letter-spacing:var(--title-spacing);text-transform:var(--title-transform);color:var(--title-color);text-shadow:var(--title-shadow)}
.brand-lock{position:absolute;z-index:3;right:22px;top:20px;display:flex;align-items:center;gap:9px;color:var(--accent);font-size:15px;font-weight:900;text-shadow:0 7px 16px rgba(0,0,0,.75)}
.brand-mark{width:54px;height:54px;object-fit:contain;opacity:.9}
.table{margin-top:-30px}
.table{position:relative;z-index:2;display:grid;grid-template-columns:49px 421px 190px 170px 128px 138px;gap:4px}
.th,.td{min-height:44px;display:flex;align-items:center;justify-content:center;background:var(--cell-bg);box-shadow:inset 0 0 0 2px var(--grid-line)}
.td.alt{background:var(--cell-bg-alt)}
.th{min-height:37px;background:var(--head-bg);color:var(--accent);font-size:18px;font-weight:900}
.th.change-head{grid-column:5/7}
.td{color:var(--cell-text);font-size:20px;font-weight:500}
.rank,.track,.daily,.pct,.delta{font-weight:900}
.rank{color:var(--accent)}
.track{padding:0 15px;text-align:center;font-size:19px}
.daily{color:var(--daily-text)}.pos{color:#1f9d55}.neg{color:#d64545}
.td.total-row{min-height:48px;background:var(--head-bg);color:var(--accent);font-weight:900}
.total-label{grid-column:1/3}
.section-row{grid-column:1/7;min-height:34px;display:grid;grid-template-columns:470px 190px 170px 128px 138px;gap:4px;margin-top:4px}
.section-cell{display:flex;align-items:center;justify-content:center;background:color-mix(in srgb,var(--accent) 16%,var(--head-bg));box-shadow:inset 0 0 0 2px var(--grid-line);color:var(--accent);font-size:15px;font-weight:950;text-transform:uppercase;letter-spacing:.08em}
.section-name{justify-content:flex-start;padding-left:21px}
.section-num{font-size:15px;letter-spacing:0;text-transform:none}
.section-cell.pos{color:#1f9d55}
.section-cell.neg{color:#d64545}
"""


def _album_day_number(album_name: str, sections: list[dict], target_date: str) -> int | None:
    if "tortured poets" in album_name.strip().casefold():
        release_date = date_cls(2024, 4, 19)
    else:
        release_dates = [
            date_cls.fromisoformat(str(track.get("release_date"))[:10])
            for section in sections
            for track in section.get("tracks", [])
            if re.match(r"\d{4}-\d{2}-\d{2}", str(track.get("release_date") or ""))
        ]
        if not release_dates:
            return None
        release_date = min(release_dates)
    return (date_cls.fromisoformat(target_date) - release_date).days + 1


def _css_vars(vars_by_name: dict[str, str]) -> str:
    return ";".join(f"--{name}:{value}" for name, value in vars_by_name.items())


def _hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (value or "").strip())
    if not m:
        return None
    raw = m.group(1)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{max(0, min(255, rgb[0])):02x}{max(0, min(255, rgb[1])):02x}{max(0, min(255, rgb[2])):02x}"


def _mix_hex(a: str, b: str, b_weight: float) -> str:
    ca = _hex_to_rgb(a)
    cb = _hex_to_rgb(b)
    if not ca or not cb:
        return a
    b_weight = max(0.0, min(1.0, b_weight))
    a_weight = 1.0 - b_weight
    return _rgb_to_hex(tuple(int(ca[i] * a_weight + cb[i] * b_weight) for i in range(3)))


def _adjust_hex(value: str, amount: float) -> str:
    rgb = _hex_to_rgb(value)
    if not rgb:
        return value
    if amount >= 0:
        return _rgb_to_hex(tuple(int(c + (255 - c) * amount) for c in rgb))
    factor = 1.0 + amount
    return _rgb_to_hex(tuple(int(c * factor) for c in rgb))


def _apply_header_accent(theme: dict[str, str], header_accent: str | None, *, subtle: bool = False) -> dict[str, str]:
    if not header_accent or not _hex_to_rgb(header_accent):
        return theme
    mixed = _mix_hex(theme.get("accent", "#c0aa8e"), header_accent, 0.24 if subtle else 0.42)
    theme["accent"] = _adjust_hex(mixed, 0.18)
    theme["head-bg"] = _mix_hex(theme.get("head-bg", "#11100e"), header_accent, 0.08 if subtle else 0.12)
    theme["cell-bg"] = _mix_hex(theme.get("cell-bg", "#2d2925"), header_accent, 0.06 if subtle else 0.10)
    theme["cell-bg-alt"] = _mix_hex(theme.get("cell-bg-alt", "#342f2a"), header_accent, 0.08 if subtle else 0.13)
    theme["grid-line"] = _mix_hex(theme.get("grid-line", "#101010"), header_accent, 0.10 if subtle else 0.16)
    return theme


def _table_dark_theme(album_name: str, header_accent: str | None = None, variant: str = "dark") -> dict[str, str]:
    key = album_name.strip().casefold()
    base = {
        "page-bg": "#171512",
        "text": "#f4f2f0",
        "card-bg": "linear-gradient(180deg,#1c1915 0%,#171512 30%,#151310 100%)",
        "hero-bg": "#1c1915",
        "hero-pos": "center 26%",
        "hero-opacity": ".78",
        "hero-filter": "saturate(.42) sepia(.16) contrast(1.02) brightness(.84)",
        "hero-overlay": "linear-gradient(90deg,#1c1915 0%,rgba(28,25,21,.38) 16%,rgba(28,25,21,.16) 50%,rgba(28,25,21,.38) 84%,#1c1915 100%),linear-gradient(180deg,rgba(21,19,16,.05) 0%,rgba(21,19,16,.12) 52%,#171512 100%)",
        "hero-text": "#eee6da",
        "title-font": "'Times New Roman',Georgia,serif",
        "title-size": "43px",
        "title-spacing": "3px",
        "title-transform": "uppercase",
        "title-bottom": "48px",
        "title-color": "#f4eee4",
        "title-shadow": "0 2px 0 #11100e,0 8px 18px rgba(0,0,0,.72)",
        "accent": "#c0aa8e",
        "cell-text": "#f5f4f2",
        "daily-text": "#f7f7f4",
        "cell-bg": "#2d2925",
        "cell-bg-alt": "#342f2a",
        "head-bg": "#11100e",
        "grid-line": "rgba(15,13,11,.62)",
    }
    if key == "taylor swift":
        base.update({
            "page-bg": "#eef8f1",
            "text": "#103829",
            "card-bg": "linear-gradient(180deg,#e7f5ea 0%,#f5fbf4 34%,#edf7f1 100%)",
            "hero-bg": "#e7f5ea",
            "hero-pos": "center 30%",
            "hero-opacity": ".9",
            "hero-filter": "saturate(.86) contrast(1.03) brightness(.98)",
            "hero-overlay": "linear-gradient(90deg,#e7f5ea 0%,rgba(231,245,234,.28) 18%,rgba(231,245,234,.08) 50%,rgba(231,245,234,.28) 84%,#e7f5ea 100%),linear-gradient(180deg,rgba(231,245,234,0) 0%,rgba(231,245,234,.18) 55%,#eef8f1 100%)",
            "hero-text": "#315f50",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "45px",
            "title-spacing": "2px",
            "title-color": "#2d6a54",
            "title-shadow": "0 2px 0 rgba(255,255,255,.8),0 8px 18px rgba(35,82,65,.24)",
            "accent": "#3f8065",
            "cell-text": "#12382b",
            "daily-text": "#0f2d23",
            "cell-bg": "#dceee3",
            "cell-bg-alt": "#d1e6da",
            "head-bg": "#f7fbf7",
            "grid-line": "rgba(63,128,101,.18)",
        })
    elif key.startswith("fearless"):
        base.update({
            "page-bg": "#fff6d8",
            "text": "#4a3512",
            "card-bg": "linear-gradient(180deg,#f6df99 0%,#fff8de 34%,#fff2c7 100%)",
            "hero-bg": "#f6df99",
            "hero-pos": "center 32%",
            "hero-opacity": ".88",
            "hero-filter": "saturate(.9) sepia(.14) contrast(1.02) brightness(.98)",
            "hero-overlay": "linear-gradient(90deg,#f6df99 0%,rgba(246,223,153,.30) 18%,rgba(246,223,153,.08) 50%,rgba(246,223,153,.30) 84%,#f6df99 100%),linear-gradient(180deg,rgba(255,246,216,0) 0%,rgba(255,246,216,.18) 55%,#fff6d8 100%)",
            "hero-text": "#6e4d16",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "48px",
            "title-spacing": "3px",
            "title-color": "#8a641e",
            "title-shadow": "0 2px 0 rgba(255,255,255,.8),0 8px 18px rgba(138,100,30,.26)",
            "accent": "#a47a25",
            "cell-text": "#4a3512",
            "daily-text": "#3b2a0f",
            "cell-bg": "#f7e7b5",
            "cell-bg-alt": "#efd99f",
            "head-bg": "#fff8e2",
            "grid-line": "rgba(164,122,37,.22)",
        })
    elif key.startswith("speak now"):
        base.update({
            "page-bg": "#f4e7ff",
            "text": "#33144d",
            "card-bg": "linear-gradient(180deg,#d8b4fe 0%,#f4e7ff 36%,#ead7ff 100%)",
            "hero-bg": "#d8b4fe",
            "hero-pos": "center 30%",
            "hero-opacity": ".88",
            "hero-filter": "saturate(.92) contrast(1.02) brightness(.94)",
            "hero-overlay": "linear-gradient(90deg,#d8b4fe 0%,rgba(216,180,254,.30) 18%,rgba(216,180,254,.08) 50%,rgba(216,180,254,.30) 84%,#d8b4fe 100%),linear-gradient(180deg,rgba(244,231,255,0) 0%,rgba(244,231,255,.18) 55%,#f4e7ff 100%)",
            "hero-text": "#5b2c83",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "47px",
            "title-spacing": "2px",
            "title-color": "#6d28a8",
            "title-shadow": "0 2px 0 rgba(255,255,255,.75),0 8px 18px rgba(85,36,128,.28)",
            "accent": "#7c3aed",
            "cell-text": "#33144d",
            "daily-text": "#2a103f",
            "cell-bg": "#eadcff",
            "cell-bg-alt": "#dfccf8",
            "head-bg": "#fbf5ff",
            "grid-line": "rgba(124,58,237,.2)",
        })
    elif key.startswith("red"):
        base.update({
            "page-bg": "#210c0c",
            "text": "#fff4ef",
            "card-bg": "linear-gradient(180deg,#3b1111 0%,#220b0b 35%,#160606 100%)",
            "hero-bg": "#3b1111",
            "hero-pos": "center 28%",
            "hero-opacity": ".84",
            "hero-filter": "saturate(.82) sepia(.08) contrast(1.06) brightness(.78)",
            "hero-overlay": "linear-gradient(90deg,#210c0c 0%,rgba(33,12,12,.40) 18%,rgba(33,12,12,.12) 50%,rgba(33,12,12,.44) 84%,#210c0c 100%),linear-gradient(180deg,rgba(24,6,6,.04) 0%,rgba(24,6,6,.18) 55%,#210c0c 100%)",
            "hero-text": "#ffd7ce",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "50px",
            "title-spacing": "5px",
            "title-color": "#f8d2c8",
            "title-shadow": "0 2px 0 #090202,0 9px 19px rgba(0,0,0,.78)",
            "accent": "#d68a7c",
            "cell-text": "#fff4ef",
            "daily-text": "#fff7f3",
            "cell-bg": "#2d1717",
            "cell-bg-alt": "#351d1d",
            "head-bg": "#120505",
            "grid-line": "rgba(8,2,2,.72)",
        })
    elif key.startswith("1989"):
        base.update({
            "page-bg": "#e7f3fb",
            "text": "#17364c",
            "card-bg": "linear-gradient(180deg,#bddcf0 0%,#e7f3fb 34%,#f4efe2 100%)",
            "hero-bg": "#bddcf0",
            "hero-pos": "center 32%",
            "hero-opacity": ".9",
            "hero-filter": "saturate(.82) contrast(1.02) brightness(.98)",
            "hero-overlay": "linear-gradient(90deg,#bddcf0 0%,rgba(189,220,240,.28) 18%,rgba(189,220,240,.08) 50%,rgba(189,220,240,.28) 84%,#bddcf0 100%),linear-gradient(180deg,rgba(231,243,251,0) 0%,rgba(231,243,251,.20) 55%,#e7f3fb 100%)",
            "hero-text": "#315a75",
            "title-font": "'Arial Narrow','Helvetica Neue',Arial,sans-serif",
            "title-size": "58px",
            "title-spacing": "6px",
            "title-color": "#315a75",
            "title-shadow": "0 2px 0 rgba(255,255,255,.8),0 8px 18px rgba(49,90,117,.26)",
            "accent": "#5e8cac",
            "cell-text": "#17364c",
            "daily-text": "#0f2b3f",
            "cell-bg": "#d7eaf5",
            "cell-bg-alt": "#cde2ee",
            "head-bg": "#f7fbfd",
            "grid-line": "rgba(94,140,172,.2)",
        })
    elif key == "lover":
        base.update({
            "page-bg": "#ffe8f3",
            "text": "#5f2245",
            "card-bg": "linear-gradient(180deg,#ffd0e6 0%,#ffe8f3 34%,#dbeafe 100%)",
            "hero-bg": "#ffd0e6",
            "hero-pos": "center 30%",
            "hero-opacity": ".9",
            "hero-filter": "saturate(.9) contrast(1.02) brightness(.98)",
            "hero-overlay": "linear-gradient(90deg,#ffd0e6 0%,rgba(255,208,230,.30) 18%,rgba(255,208,230,.08) 50%,rgba(219,234,254,.34) 84%,#dbeafe 100%),linear-gradient(180deg,rgba(255,232,243,0) 0%,rgba(255,232,243,.20) 55%,#ffe8f3 100%)",
            "hero-text": "#7e315d",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "54px",
            "title-spacing": "3px",
            "title-transform": "none",
            "title-color": "#c0568a",
            "title-shadow": "0 2px 0 rgba(255,255,255,.8),0 8px 18px rgba(192,86,138,.26)",
            "accent": "#c0568a",
            "cell-text": "#5f2245",
            "daily-text": "#501c3a",
            "cell-bg": "#f9dceb",
            "cell-bg-alt": "#f3cfe2",
            "head-bg": "#fff6fb",
            "grid-line": "rgba(192,86,138,.18)",
        })
    elif key == "folklore":
        base.update({
            "page-bg": "#e6e6e2",
            "text": "#272724",
            "card-bg": "linear-gradient(180deg,#cfcfca 0%,#e6e6e2 36%,#d9d9d4 100%)",
            "hero-bg": "#cfcfca",
            "hero-pos": "center 30%",
            "hero-opacity": ".88",
            "hero-filter": "grayscale(1) contrast(1.04) brightness(.92)",
            "hero-overlay": "linear-gradient(90deg,#cfcfca 0%,rgba(207,207,202,.30) 18%,rgba(207,207,202,.08) 50%,rgba(207,207,202,.30) 84%,#cfcfca 100%),linear-gradient(180deg,rgba(230,230,226,0) 0%,rgba(230,230,226,.22) 55%,#e6e6e2 100%)",
            "hero-text": "#3f3f3b",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "52px",
            "title-spacing": "3px",
            "title-transform": "lowercase",
            "title-color": "#2f2f2c",
            "title-shadow": "0 2px 0 rgba(255,255,255,.65),0 8px 18px rgba(47,47,44,.24)",
            "accent": "#5f625d",
            "cell-text": "#272724",
            "daily-text": "#20201d",
            "cell-bg": "#d8d8d3",
            "cell-bg-alt": "#cdcdc8",
            "head-bg": "#f2f2ee",
            "grid-line": "rgba(95,98,93,.2)",
        })
    elif key == "evermore":
        base.update({
            "page-bg": "#2a170d",
            "text": "#fff1dc",
            "card-bg": "linear-gradient(180deg,#4a2a15 0%,#2a170d 36%,#1c0f08 100%)",
            "hero-bg": "#4a2a15",
            "hero-pos": "center 28%",
            "hero-opacity": ".84",
            "hero-filter": "saturate(.78) sepia(.18) contrast(1.05) brightness(.8)",
            "hero-overlay": "linear-gradient(90deg,#2a170d 0%,rgba(42,23,13,.42) 18%,rgba(42,23,13,.12) 50%,rgba(42,23,13,.44) 84%,#2a170d 100%),linear-gradient(180deg,rgba(28,15,8,.04) 0%,rgba(28,15,8,.18) 55%,#2a170d 100%)",
            "hero-text": "#f0c994",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "52px",
            "title-spacing": "2px",
            "title-transform": "lowercase",
            "title-color": "#e2b477",
            "title-shadow": "0 2px 0 #120804,0 9px 19px rgba(0,0,0,.76)",
            "accent": "#d49a5b",
            "cell-text": "#fff1dc",
            "daily-text": "#fff7ed",
            "cell-bg": "#342014",
            "cell-bg-alt": "#3d2618",
            "head-bg": "#130904",
            "grid-line": "rgba(10,4,2,.72)",
        })
    elif key == "reputation":
        base.update({
            "page-bg": "#080808",
            "text": "#f2f2f2",
            "card-bg": "linear-gradient(180deg,#121212 0%,#090909 34%,#050505 100%)",
            "hero-bg": "#080808",
            "hero-pos": "center 28%",
            "hero-opacity": ".86",
            "hero-filter": "grayscale(1) contrast(1.12) brightness(.78)",
            "hero-overlay": "linear-gradient(90deg,#080808 0%,rgba(8,8,8,.44) 17%,rgba(8,8,8,.18) 52%,rgba(8,8,8,.48) 86%,#080808 100%),linear-gradient(180deg,rgba(0,0,0,.02) 0%,rgba(0,0,0,.18) 52%,#080808 100%)",
            "hero-text": "#f0f0f0",
            "title-font": "'Old English Text MT','UnifrakturCook','Cloister Black',Georgia,serif",
            "title-size": "48px",
            "title-spacing": "1px",
            "title-transform": "lowercase",
            "title-bottom": "48px",
            "title-color": "#f4f4f4",
            "title-shadow": "0 2px 0 #000,0 9px 19px rgba(0,0,0,.82)",
            "accent": "#c9c9c9",
            "cell-bg": "#242424",
            "cell-bg-alt": "#2d2d2d",
            "head-bg": "#080808",
            "grid-line": "rgba(0,0,0,.72)",
        })
    elif key == "midnights":
        base.update({
            "page-bg": "#090817",
            "text": "#f2f2ff",
            "card-bg": "linear-gradient(180deg,#11102a 0%,#0b0a1d 35%,#080712 100%)",
            "hero-bg": "#11102a",
            "hero-pos": "center 30%",
            "hero-opacity": ".82",
            "hero-filter": "saturate(.72) hue-rotate(5deg) contrast(1.04) brightness(.8)",
            "hero-overlay": "linear-gradient(90deg,#090817 0%,rgba(9,8,23,.42) 16%,rgba(9,8,23,.14) 50%,rgba(9,8,23,.44) 84%,#090817 100%),linear-gradient(180deg,rgba(7,7,18,.03) 0%,rgba(7,7,18,.16) 52%,#090817 100%)",
            "hero-text": "#dcd9ff",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "48px",
            "title-spacing": "4px",
            "title-transform": "uppercase",
            "title-bottom": "48px",
            "title-color": "#d8ddff",
            "title-shadow": "0 2px 0 #050511,0 9px 19px rgba(0,0,0,.78)",
            "accent": "#a5b4fc",
            "cell-bg": "#211d2e",
            "cell-bg-alt": "#28233a",
            "head-bg": "#080712",
            "grid-line": "rgba(5,5,17,.72)",
        })
    elif key == "the life of a showgirl":
        base.update({
            "page-bg": "#2c1208",
            "text": "#fff7df",
            "card-bg": "linear-gradient(180deg,#6e2b0d 0%,#2c1208 35%,#170804 100%)",
            "hero-bg": "#6e2b0d",
            "hero-pos": "center 30%",
            "hero-opacity": ".86",
            "hero-filter": "saturate(.95) sepia(.12) contrast(1.07) brightness(.82)",
            "hero-overlay": "linear-gradient(90deg,#2c1208 0%,rgba(44,18,8,.38) 18%,rgba(44,18,8,.10) 50%,rgba(44,18,8,.38) 84%,#2c1208 100%),linear-gradient(180deg,rgba(23,8,4,.02) 0%,rgba(23,8,4,.16) 55%,#2c1208 100%)",
            "hero-text": "#ffd47b",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "44px",
            "title-spacing": "3px",
            "title-color": "#ffd47b",
            "title-shadow": "0 2px 0 #160704,0 9px 19px rgba(0,0,0,.76)",
            "accent": "#f59e0b",
            "cell-text": "#fff7df",
            "daily-text": "#fff9ea",
            "cell-bg": "#3a1a0d",
            "cell-bg-alt": "#44200f",
            "head-bg": "#130603",
            "grid-line": "rgba(10,3,1,.72)",
        })
    elif key == "the taylor swift holiday collection":
        base.update({
            "page-bg": "#f7fbf6",
            "text": "#173b28",
            "card-bg": "linear-gradient(180deg,#dbead8 0%,#f7fbf6 36%,#f7e4e0 100%)",
            "hero-bg": "#dbead8",
            "hero-pos": "center 30%",
            "hero-opacity": ".9",
            "hero-filter": "saturate(.82) contrast(1.02) brightness(.98)",
            "hero-overlay": "linear-gradient(90deg,#dbead8 0%,rgba(219,234,216,.30) 18%,rgba(219,234,216,.08) 50%,rgba(247,228,224,.34) 84%,#f7e4e0 100%),linear-gradient(180deg,rgba(247,251,246,0) 0%,rgba(247,251,246,.22) 55%,#f7fbf6 100%)",
            "hero-text": "#315f45",
            "title-font": "Georgia,'Times New Roman',serif",
            "title-size": "39px",
            "title-spacing": "2px",
            "title-color": "#9f2d2d",
            "title-shadow": "0 2px 0 rgba(255,255,255,.85),0 8px 18px rgba(159,45,45,.22)",
            "accent": "#2f7a4e",
            "cell-text": "#173b28",
            "daily-text": "#102d1e",
            "cell-bg": "#e6f0e3",
            "cell-bg-alt": "#dce9d9",
            "head-bg": "#fffafa",
            "grid-line": "rgba(47,122,78,.18)",
        })
    if variant == "light" and key == "reputation":
        base.update({
            "page-bg": "#f1f1f1",
            "text": "#171717",
            "card-bg": "linear-gradient(180deg,#ffffff 0%,#f3f3f3 34%,#e9e9e9 100%)",
            "hero-bg": "#e6e6e6",
            # The stored header art is already cropped so its baked-in
            # blackletter quote sits in the upper band, clear of the overlaid
            # date + album title.
            "hero-pos": "center top",
            "hero-opacity": ".9",
            "hero-filter": "grayscale(1) contrast(1.04) brightness(1.04)",
            "hero-overlay": "linear-gradient(90deg,#e6e6e6 0%,rgba(230,230,230,.42) 17%,rgba(230,230,230,.12) 52%,rgba(230,230,230,.46) 86%,#e6e6e6 100%),linear-gradient(180deg,rgba(241,241,241,0) 0%,rgba(241,241,241,.26) 55%,#f1f1f1 100%)",
            "hero-text": "#2a2a2a",
            "title-font": "'Old English Text MT','UnifrakturCook','Cloister Black',Georgia,serif",
            "title-size": "48px",
            "title-spacing": "1px",
            "title-transform": "lowercase",
            "title-bottom": "48px",
            "title-color": "#121212",
            "title-shadow": "0 2px 0 rgba(255,255,255,.85),0 8px 18px rgba(18,18,18,.24)",
            "accent": "#5a5a5a",
            "cell-text": "#191919",
            "daily-text": "#101010",
            "cell-bg": "#e8e8e8",
            "cell-bg-alt": "#dfdfdf",
            "head-bg": "#fafafa",
            "grid-line": "rgba(0,0,0,.13)",
        })
    if variant == "light" and "tortured poets" in key:
        base.update({
            "page-bg": "#eee9df",
            "text": "#2a2824",
            "card-bg": "linear-gradient(180deg,#d7d0c3 0%,#eee9df 35%,#e6dfd2 100%)",
            "hero-bg": "#d7d0c3",
            "hero-opacity": ".88",
            "hero-filter": "saturate(.36) sepia(.12) contrast(1.02) brightness(.96)",
            "hero-overlay": "linear-gradient(90deg,#d7d0c3 0%,rgba(215,208,195,.34) 18%,rgba(215,208,195,.10) 50%,rgba(215,208,195,.34) 84%,#d7d0c3 100%),linear-gradient(180deg,rgba(238,233,223,0) 0%,rgba(238,233,223,.28) 55%,#eee9df 100%)",
            "hero-text": "#4f493f",
            "title-font": "'Times New Roman',Georgia,serif",
            "title-size": "43px",
            "title-spacing": "3px",
            "title-transform": "uppercase",
            "title-bottom": "48px",
            "title-color": "#35312b",
            "title-shadow": "0 2px 0 rgba(255,255,255,.72),0 8px 18px rgba(64,58,49,.24)",
            "accent": "#756a5a",
            "cell-text": "#2d2924",
            "daily-text": "#24211d",
            "cell-bg": "#dfd8cb",
            "cell-bg-alt": "#d4ccbd",
            "head-bg": "#f7f3eb",
            "grid-line": "rgba(104,94,78,.22)",
        })
    return _apply_header_accent(base, header_accent, subtle=key in MONOCHROME_ALBUM_ACCENTS)


def _format_section_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").replace("_", " ")).strip()
    if not cleaned:
        return "Section"
    words = []
    for word in cleaned.split(" "):
        lower = word.lower()
        if lower in {"3am", "til", "dawn"}:
            words.append(word.upper() if lower == "3am" else word.title())
        elif len(word) <= 3 and word.isupper():
            words.append(word)
        else:
            words.append(word.capitalize())
    return " ".join(words)


def _table_dark_section_row(section: dict, hist: dict) -> str:
    tracks = section.get("tracks", [])
    sec_streams = sum(hist.get(track["track_id"], {}).get("streams") or 0 for track in tracks)
    sec_daily = sum(hist.get(track["track_id"], {}).get("daily") or 0 for track in tracks)
    sec_change = sum(hist.get(track["track_id"], {}).get("change") or 0 for track in tracks)
    sec_yest = sec_daily - sec_change
    sec_pct = (sec_change / sec_yest * 100) if sec_yest else None
    pct_text = "-" if sec_pct is None else f"{sec_pct:+.2f}%"
    state_cls = "pos" if sec_change >= 0 else "neg"
    name = html.escape(_format_section_name(section.get("name") or "Section"))
    return f"""<div class="section-row">
    <div class="section-cell section-name">{name}</div>
    <div class="section-cell section-num">{fmt_comma_num(sec_streams)}</div>
    <div class="section-cell section-num">+{fmt_comma_num(sec_daily)}</div>
    <div class="section-cell section-num {state_cls}">{pct_text}</div>
    <div class="section-cell section-num {state_cls}">{sec_change:+,}</div>
  </div>"""


def build_table_dark_html(
    album_name: str,
    sections: list[dict],
    hist: dict,
    target_date: str,
    header_uri: str,
    handle_icon_uri: str = "",
    header_accent: str | None = None,
    theme_variant: str = "dark",
) -> str:
    from datetime import datetime

    date_obj = datetime.strptime(target_date, "%Y-%m-%d")
    weekday = date_obj.strftime("%A")
    display_date = f"{weekday}, {date_obj.strftime('%B')} {date_obj.day}, {date_obj.year}"
    day_number = _album_day_number(album_name, sections, target_date)
    day_label = f" (DAY {day_number})" if day_number is not None else ""
    theme_vars = _css_vars(_table_dark_theme(album_name, header_accent, theme_variant))
    hero_bg = f"background-image:url('{header_uri}');" if header_uri else ""
    brand_img = f'<img class="brand-mark" src="{handle_icon_uri}" alt="">' if handle_icon_uri else ""
    brand = f'<div class="brand-lock">{brand_img}<span>{HANDLE}</span></div>'
    rows = []
    total_streams = 0
    total_daily = 0
    total_change = 0
    show_sections = len([sec for sec in sections if sec.get("tracks")]) > 1
    idx = 0
    for section in sections:
        tracks = section.get("tracks", [])
        if not tracks:
            continue
        if show_sections:
            rows.append(_table_dark_section_row(section, hist))
        for track in tracks:
            idx += 1
            hdata = hist.get(track["track_id"], {})
            streams = hdata.get("streams")
            daily = hdata.get("daily")
            change = hdata.get("change")
            pct = hdata.get("pct")
            total_streams += streams or 0
            total_daily += daily or 0
            total_change += change or 0
            pct_text = "-" if pct is None else f"{pct:+.2f}%"
            delta_text = "-" if change is None else f"{change:+,}"
            state_cls = "pos" if (change or 0) >= 0 else "neg"
            daily_text = "-" if daily is None else f"+{fmt_comma_num(daily)}"
            title = html.escape(_shorten_title(track.get("title") or track.get("title_clean") or ""))
            alt_cls = " alt" if idx % 2 == 0 else ""
            rows.append(f"""<div class="td rank{alt_cls}">{idx}</div>
    <div class="td track{alt_cls}">{title}</div>
    <div class="td total{alt_cls}">{fmt_comma_num(streams)}</div>
    <div class="td daily{alt_cls}">{daily_text}</div>
    <div class="td pct {state_cls}{alt_cls}">{pct_text}</div>
    <div class="td delta {state_cls}{alt_cls}">{delta_text}</div>""")
    total_yest = total_daily - total_change
    total_pct = (total_change / total_yest * 100) if total_yest else None
    total_pct_text = "-" if total_pct is None else f"{total_pct:+.2f}%"
    total_state_cls = "pos" if total_change >= 0 else "neg"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{TABLE_DARK_CSS}</style></head><body>
<div class="dark-card" style="{theme_vars}">
  <div class="hero"><div class="hero-img" style="{hero_bg}"></div>{brand}<div class="hero-date">{display_date}{day_label}</div><div class="album-title">{html.escape(album_name)}</div></div>
  <div class="table">
    <div class="th">#</div><div class="th">Track</div><div class="th">Total Streams</div><div class="th">Daily Streams</div><div class="th change-head">Change</div>
    {"".join(rows)}
    <div class="td total-row total-label">TOTAL</div><div class="td total-row">{fmt_comma_num(total_streams)}</div><div class="td total-row">+{fmt_comma_num(total_daily)}</div><div class="td total-row {total_state_cls}">{total_pct_text}</div><div class="td total-row {total_state_cls}">{total_change:+,}</div>
  </div>
</div>
</body></html>"""


def album_update_slug(album_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", album_name.lower()).strip("_")


def album_update_out_dir(target_date: str) -> Path:
    return update_streams_dir(target_date)


def all_album_names() -> list[str]:
    names = []
    for path in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict) and payload.get("album"):
                names.append(str(payload["album"]))
        except Exception:
            continue
    return names


def album_update_lock_path(album_name: str, target_date: str) -> Path:
    return album_update_out_dir(target_date) / f"{album_update_slug(album_name)}_update.lock"


def legacy_album_update_lock_path(album_name: str, target_date: str) -> Path:
    return (
        ROOT
        / "history"
        / target_date[:4]
        / target_date[5:7]
        / target_date
        / f"{album_update_slug(album_name)}_update.lock"
    )


def existing_album_update_lock_path(album_name: str, target_date: str) -> Path | None:
    for path in (
        album_update_lock_path(album_name, target_date),
        legacy_album_update_lock_path(album_name, target_date),
    ):
        if path.exists():
            return path
    return None


def album_update_already_posted(album_name: str, target_date: str) -> bool:
    return existing_album_update_lock_path(album_name, target_date) is not None


def _format_best_day_since_label(value: object) -> str | None:
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}$", value):
        d = date_cls.fromisoformat(value)
        return f"{d.strftime('%B')} {_ordinal(d.day)}, {d.year}"
    return None


def _format_best_day_marker_label(row: dict) -> str | None:
    if row.get("is_biggest_day_of_year"):
        return "of the year"
    since_label = _format_best_day_since_label(row.get("best_day_since"))
    if since_label:
        return since_label
    if row.get("is_biggest_day_of_month"):
        return "of the month"
    return None


def _best_day_labels_for_sections(
    sections: list[dict],
    target_date: str,
    *,
    min_days: int = best_day_since.DEFAULT_MIN_DAYS,
) -> dict[str, str]:
    target = date_cls.fromisoformat(target_date)
    base_tracks = best_day_since.load_tracks(include_extras=False)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    history = best_day_since.load_history()

    marked: dict[str, str] = {}
    seen: set[str] = set()
    for section in sections:
        for item in section.get("tracks", []):
            track_id = item.get("track_id")
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)

            track = base_tracks.get(track_id) or all_tracks.get(track_id)
            if track is None:
                continue

            row = best_day_since.compute_best_day_since(track, history.get(track_id) or [], target)
            # This table shows each row's own solo daily/CHG numbers, never a
            # family-combined sum. A "combined" row's record can be set by a
            # different version's streams (e.g. Red (Taylor's Version)) even
            # while this row's own daily is down — showing the star here would
            # contradict the CHG the row displays, so combined records are
            # skipped on this per-album view.
            if (
                row
                and row.get("kind") == "since"
                and best_day_since.passes_filters(row, min_days=min_days)
            ):
                label = _format_best_day_marker_label(row)
                if label:
                    marked[track_id] = label

    return marked


def _best_day_rows_for_sections(
    sections: list[dict],
    target_date: str,
    *,
    min_days: int = best_day_since.DEFAULT_MIN_DAYS,
) -> list[dict]:
    target = date_cls.fromisoformat(target_date)
    base_tracks = best_day_since.load_tracks(include_extras=False)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    history = best_day_since.load_history()

    rows: list[dict] = []
    seen: set[str] = set()
    for section in sections:
        for item in section.get("tracks", []):
            track_id = item.get("track_id")
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)

            track = base_tracks.get(track_id) or all_tracks.get(track_id)
            if track is None:
                continue

            row = best_day_since.compute_best_day_since(track, history.get(track_id) or [], target)
            if (
                row
                and row.get("kind") == "since"
                and best_day_since.passes_filters(row, min_days=min_days)
            ):
                rows.append(row)

    rows.sort(key=best_day_since.sort_key, reverse=True)
    return rows


def generate(
    album_name: str,
    target_date: str | None = None,
    *,
    sort_tracks_by_daily: bool = False,
    style: str = "default",
    header_path: Path | None = None,
    output_suffix: str = "",
) -> Path:
    if target_date is None:
        target_date = get_latest_date()
    print(f"[album_update] Album: {album_name}  Date: {target_date}")

    sections, album_name = load_album_sections(album_name, target_date)
    if not sections:
        raise ValueError(f"Aucune section trouvée pour l'album: {album_name!r}")
    style = effective_album_update_style(album_name, style)
    header_variant = "light" if style == "table-light" else "dark"
    print(f"[album_update] {sum(len(s['tracks']) for s in sections)} tracks dans {len(sections)} section(s)")

    hist = load_history_for_album(sections, target_date)
    if sort_tracks_by_daily:
        sort_album_sections_by_daily_streams(sections, hist)

    best_day_labels_by_track = _best_day_labels_for_sections(sections, target_date)

    show_filter_cols = False
    if ENABLE_FILTERED_CHARTS:
        chart_filtered, has_same_day_chart = load_global_chart_filtered_for_album(sections, target_date)
        if has_same_day_chart:
            for tid, extra in chart_filtered.items():
                base = hist.get(tid, {})
                base["filtered_streams"] = extra.get("filtered_streams")
                base["filter_rate"] = extra.get("filter_rate")
                hist[tid] = base

        show_filter_cols = has_same_day_chart and any(
            (v.get("filtered_streams") is not None) for v in hist.values()
        )

    cover_url  = load_cover_url(album_name)
    header_img = header_path or pick_header_image(album_name, header_variant)
    mono_accent = MONOCHROME_ALBUM_ACCENTS.get(album_name.strip().casefold())

    # Accent color comes from the selected header first; fall back to cover, then default.
    if mono_accent:
        dominant_hex = mono_accent
    elif header_img:
        dominant_hex = _header_accent_color(header_img)
    elif cover_url:
        dominant_hex = _dominant_color_from_url(cover_url)
    else:
        dominant_hex = "#1db954"

    # prefetch cover image
    print("[album_update] Téléchargement de la cover...")
    cover_uri = _url_to_data_uri(cover_url) if cover_url else ""

    table_dark_style = style in {"table-dark", "table-light"}
    table_light_style = style == "table-light"
    layout = _compute_layout_metrics(sections, show_filter_cols, best_day_labels_by_track)
    if table_dark_style:
        hdr_target_w = 2212
        hdr_target_h = 608
    else:
        hdr_target_w = (layout["body_width_px"] - 2 * BODY_PADDING_CSS) * RENDER_DPR
        hdr_target_h = HEADER_HEIGHT_CSS * RENDER_DPR
    header_uri = _prepare_header_for_render(header_img, hdr_target_w, hdr_target_h) if header_img else ""

    if mono_accent:
        section_palette = [mono_accent]
    else:
        section_palette = _section_palette_colors(header_img, max_colors=max(3, len(sections))) if header_img else []

    # Nouveau : icône du handle
    handle_icon_uri = _file_to_data_uri(HANDLE_ICON_PATH)

    if table_dark_style:
        html = build_table_dark_html(
            album_name,
            sections,
            hist,
            target_date,
            header_uri,
            handle_icon_uri=handle_icon_uri,
            header_accent=dominant_hex,
            theme_variant="light" if table_light_style else "dark",
        )
    else:
        html = build_html(
            album_name,
            sections,
            hist,
            target_date,
            cover_uri,
            header_uri,
            dominant_hex,
            section_palette=section_palette,
            show_filter_cols=show_filter_cols,
            layout=layout,
            handle_icon_uri=handle_icon_uri,
            best_day_labels_by_track=best_day_labels_by_track,
        )

    album_slug = album_update_slug(album_name)
    out_dir    = album_update_out_dir(target_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    style_suffix = "_table_light" if table_light_style else ("_table_dark" if table_dark_style else "")
    safe_output_suffix = f"_{_norm(output_suffix)}" if output_suffix else ""
    out_path   = out_dir / f"{album_slug}_update{style_suffix}{safe_output_suffix}.png"
    raw_out_path = out_dir / f"_{album_slug}_update{style_suffix}{safe_output_suffix}_hires.png"
    tmp_html   = out_dir / f"_{album_slug}_tmp{style_suffix}{safe_output_suffix}.html"
    tmp_html.write_text(html, encoding="utf-8")

    print("[album_update] Rendu Playwright...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # High-density render for near-4K width output (880 * 4 = 3520 px).
            render_dpr = 2 if table_dark_style else RENDER_DPR
            page    = browser.new_page(viewport={"width": 1200, "height": 320}, device_scale_factor=render_dpr)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(450)
            page.locator("body").screenshot(path=str(raw_out_path), scale="device")
            browser.close()

        if _PIL:
            try:
                img = Image.open(raw_out_path)
                # Keep native high-res render for maximum detail retention in the default style.
                img.save(out_path, format="PNG", optimize=True)
            finally:
                try:
                    img.close()
                except Exception:
                    pass
        else:
            raw_out_path.replace(out_path)
    finally:
        if raw_out_path.exists():
            raw_out_path.unlink()
        if tmp_html.exists():
            tmp_html.unlink()

    print(f"[album_update] Image générée : {out_path}")
    return out_path


def _build_album_post_text(album_name: str, target_date: str) -> str:
    """Builds the album post text with daily total and biggest gainer/most stable track."""
    from datetime import datetime

    sections, canonical_name = load_album_sections(album_name, target_date)
    if not sections:
        raise ValueError(f"Aucune section trouvée pour l'album: {album_name!r}")

    hist = load_history_for_album(sections, target_date)

    tracks = [t for sec in sections for t in sec["tracks"]]
    total_daily = sum(hist.get(t["track_id"], {}).get("daily") or 0 for t in tracks)

    # Calculate album percentage change
    # change = daily_today - daily_yesterday, so daily_yesterday = daily_today - change
    total_daily_yesterday = total_daily - sum(hist.get(t["track_id"], {}).get("change") or 0 for t in tracks)

    album_pct = None
    if total_daily_yesterday and total_daily_yesterday > 0:
        album_change = total_daily - total_daily_yesterday
        album_pct = (album_change / total_daily_yesterday) * 100

    scored = []
    for t in tracks:
        h = hist.get(t["track_id"], {})
        pct = h.get("pct")
        if pct is None:
            continue
        scored.append({
            "track_id": t.get("track_id"),
            "title": t.get("title") or t.get("title_clean") or "Unknown",
            "pct": pct,
            "daily": h.get("daily") or 0,
        })

    # Rule: if every available % change is negative, pick the least negative as "most stable".
    label = "biggest gainer"
    selected_song = "Unknown"
    track_daily = 0
    track_pct = None

    if scored:
        if all(item["pct"] < 0 for item in scored):
            best = max(scored, key=lambda x: x["pct"])
            label = "most stable"
        else:
            best = max(scored, key=lambda x: x["pct"])
            label = "biggest gainer"
        selected_song = _shorten_title(best["title"])
        track_daily = best.get("daily", 0)
        track_pct = best.get("pct")

    # Format data for tweet
    date_obj = datetime.strptime(target_date, "%Y-%m-%d")
    date_fmt = f"{date_obj.strftime('%A, %B')} {_ordinal(date_obj.day)}, {date_obj.year}"
    total_daily_fmt = f"{int(total_daily):,}"
    track_daily_fmt = f"{int(track_daily):,}"

    # Format album percentage
    album_pct_str = ""
    if album_pct is not None:
        sign = "+" if album_pct >= 0 else "−"
        album_pct_str = f" ({sign}{abs(album_pct):.1f}%)"

    # Format track percentage
    track_pct_str = ""
    if track_pct is not None:
        sign = "+" if track_pct >= 0 else "−"
        track_pct_str = f" ({sign}{abs(track_pct):.1f}%)"

    # TODAY ONLY (2026-04-20): TTPD 2nd anniversary special note
    is_ttpd_anniversary = (
        target_date == "2026-04-19"
        and "tortured poets" in canonical_name.lower()
    )
    if is_ttpd_anniversary:
        first_line = f'📈| "{canonical_name}" received {total_daily_fmt} streams on its second anniversary, April 19th 2026.{album_pct_str}'
    else:
        when = f"on {date_fmt}"
        first_line = f'📈| "{canonical_name}" received {total_daily_fmt} streams {when}.{album_pct_str}'

    return (
        f"{first_line}\n\n"
        f'"{selected_song}" was the {label} with {track_daily_fmt} streams{track_pct_str}.\n\n'
        f"See full update here : {streams_latest_url()} ❤️‍🔥"
    )


_build_album_post_text_base = _build_album_post_text


def _selected_album_post_track(sections: list[dict], target_date: str) -> dict | None:
    hist = load_history_for_album(sections, target_date)
    tracks = [t for sec in sections for t in sec["tracks"]]
    scored = []
    for t in tracks:
        h = hist.get(t["track_id"], {})
        pct = h.get("pct")
        if pct is None:
            continue
        scored.append({
            "track_id": t.get("track_id"),
            "title": t.get("title") or t.get("title_clean") or "Unknown",
            "pct": pct,
        })
    if not scored:
        return None
    return max(scored, key=lambda x: x["pct"])


def _note_song_key(title: str) -> str:
    text = title or ""
    text = re.sub(r"\(taylor'?s version\)|\(tv\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _same_note_song(a: str, b: str) -> bool:
    return bool(_note_song_key(a)) and _note_song_key(a) == _note_song_key(b)


def _format_best_since_long(value: object) -> str:
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}$", value):
        d = date_cls.fromisoformat(value)
        return f"{d.strftime('%B')} {_ordinal(d.day)}, {d.year}"
    return str(value or "before 2025")


def _best_day_post_label(row: dict) -> str:
    if row.get("is_biggest_day_of_year"):
        label = "BIGGEST DAY of the year"
    elif row.get("kind") == "since":
        label = f"BEST DAY since {_format_best_since_long(row.get('best_day_since'))}"
    elif row.get("is_biggest_day_of_month"):
        label = "BIGGEST DAY of the month"
    else:
        label = f"BEST DAY since {_format_best_since_long(row.get('best_day_since'))}"
    # Same rule as best_day_since.row_label: a combined record is set by the
    # summed family total (e.g. original + Taylor's Version), not this track's
    # own streams, so the post text must flag it.
    if row.get("combined"):
        label = f"{label} (combined)"
    return label


def _build_album_post_text(album_name: str, target_date: str) -> str:
    tweet = _build_album_post_text_base(album_name, target_date)
    sections, _canonical_name = load_album_sections(album_name, target_date)
    if not sections:
        return tweet

    best_day_rows = _best_day_rows_for_sections(sections, target_date)
    if not best_day_rows:
        return tweet

    def note_title(title: str) -> str:
        return _shorten_title(title).replace("(Taylor's Version)", "(TV)")

    def note_rank(row: dict) -> tuple[int, int]:
        days_since = row.get("days_since")
        if days_since is None:
            best_since = row.get("best_day_since")
            if isinstance(best_since, str) and re.match(r"\d{4}-\d{2}-\d{2}$", best_since):
                days_since = (date_cls.fromisoformat(target_date) - date_cls.fromisoformat(best_since)).days
        return (int(days_since or 0), int(row.get("daily_streams") or 0))

    best_row = max(best_day_rows, key=note_rank)
    best_label = _best_day_post_label(best_row)
    selected = _selected_album_post_track(sections, target_date)
    selected_title = selected.get("title") if selected else ""
    same_song = (
        bool(selected)
        and (
            selected.get("track_id") == best_row.get("track_id")
            or _same_note_song(selected_title, best_row.get("title") or "")
        )
    )

    if same_song:
        addition = f" It earned its {best_label}."
        lines = tweet.splitlines()
        for i, line in enumerate(lines):
            if f'"{_shorten_title(selected_title)}" was the ' in line:
                lines[i] = f"{line}{addition}"
                return "\n".join(lines)

    note = f'"{note_title(best_row["title"])}" had its {best_label}.'
    marker = "\n\nSee full update here"
    if marker in tweet:
        return tweet.replace(marker, f"\n\n{note}{marker}", 1)
    return f"{tweet}\n\n{note}"


def fit_album_post_text(tweet: str) -> str:
    if len(tweet) <= TWEET_CHAR_LIMIT:
        return tweet
    if "See full update here" in tweet:
        tweet = tweet.split("See full update here", 1)[0].strip()
    if len(tweet) <= TWEET_CHAR_LIMIT:
        return tweet
    lines = [
        line
        for line in tweet.splitlines()
        if not re.search(r"had its (BEST|BIGGEST) DAY", line, re.IGNORECASE)
    ]
    return "\n".join(lines).strip()


def post(album_name: str, image_path: Path, target_date: str) -> bool:
    block_reason = holiday_collection_post_block_reason(album_name, target_date)
    if block_reason:
        print(f"[album_update] Post skipped: {block_reason}")
        return True

    if not TWITTER_SESSION.exists():
        print(f"[album_update] Session Twitter introuvable : {TWITTER_SESSION}")
        return False

    try:
        from core.twitter import post_with_image
    except ImportError as e:
        print(f"[album_update] Impossible d'importer core.twitter: {e}")
        return False

    try:
        tweet = _build_album_post_text(album_name, target_date)
        fitted_tweet = fit_album_post_text(tweet)
        if fitted_tweet != tweet:
            print("[album_update] Tweet shortened to fit X limit.")
        tweet = fitted_tweet
    except Exception as e:
        print(f"[album_update] Fallback tweet (erreur génération texte): {e}")
        from datetime import datetime
        date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
        tweet = f"Taylor Swift · {album_name}\nDaily Streams Update — {date_fmt}"

    print(f"[album_update] Publication Twitter : {tweet[:60]}...")
    ok = post_with_image(tweet, image_path, TWITTER_SESSION)
    if ok:
        print("[album_update] Tweet publié avec succès.")
    else:
        print("[album_update] Échec de la publication Twitter.")
    return ok


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    do_post = "--post" in args and "--no-post" not in args
    style = "default"
    header_arg = None
    all_headers = False
    all_albums = False
    clean_args = []
    for arg in args:
        if arg in ("--post", "--no-post"):
            continue
        if arg == "--all-headers":
            all_headers = True
            continue
        if arg == "--all-albums":
            all_albums = True
            continue
        if arg.startswith("--style="):
            style = arg.split("=", 1)[1].strip() or "default"
            continue
        if arg.startswith("--header="):
            header_arg = arg.split("=", 1)[1].strip() or None
            continue
        clean_args.append(arg)

    album_name  = None if all_albums else (clean_args[0] if len(clean_args) > 0 else None)
    target_date = clean_args[0] if all_albums and clean_args else (clean_args[1] if len(clean_args) > 1 else None)

    if not album_name and not all_albums:
        print("Usage: generate_album_update_image.py <album_name> [date] [--post] [--style=table-dark] [--header=<path-or-name>] [--all-headers]")
        print("       generate_album_update_image.py --all-albums [date] --all-headers --style=table-dark")
        sys.exit(1)

    resolved_date = target_date or get_latest_date()

    if do_post and album_name:
        existing_lock_path = existing_album_update_lock_path(album_name, resolved_date)
        if existing_lock_path is not None:
            lock_path = existing_lock_path
            print(f"[album_update] Déjà posté ({lock_path.name}). Rien à faire.")
            return

    if do_post and album_name:
        block_reason = holiday_collection_post_block_reason(album_name, resolved_date)
        if block_reason:
            print(f"[album_update] Post skipped: {block_reason}")
            return

    if style not in {"default", "table-dark", "table-light"}:
        print(f"Unknown style: {style!r}. Supported styles: default, table-dark, table-light")
        sys.exit(1)

    header_variant = "light" if style == "table-light" else "dark"

    if all_albums or all_headers:
        if do_post:
            print("[album_update] Batch header tests cannot be posted.")
            sys.exit(1)
        targets = all_album_names() if all_albums else [album_name]
        generated: list[Path] = []
        for target_album in targets:
            headers = header_images_for_album(target_album) if all_headers else [resolve_header_arg(target_album, header_arg, header_variant)]
            if not headers:
                print(f"[album_update] Skip {target_album}: aucun header.")
                continue
            for header in headers:
                suffix = f"test_{header.stem}"
                print(f"[album_update] Test header: {target_album} <- {header.name}")
                generated.append(generate(target_album, resolved_date, style=style, header_path=header, output_suffix=suffix))
        print(f"[album_update] {len(generated)} image(s) de test générée(s).")
        for path in generated:
            print(f"  {path}")
        return

    image_path = generate(
        album_name,
        resolved_date,
        style=style,
        header_path=resolve_header_arg(album_name, header_arg, header_variant),
        output_suffix=f"test_{Path(header_arg).stem}" if header_arg else "",
    )

    if do_post:
        if style != "default":
            print("[album_update] Experimental styles cannot be posted directly.")
            sys.exit(1)
        lock_path = album_update_lock_path(album_name, resolved_date)
        ok = post(album_name, image_path, resolved_date)
        if ok:
            lock_path.write_text(f"posted {resolved_date}\n", encoding="utf-8")
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
