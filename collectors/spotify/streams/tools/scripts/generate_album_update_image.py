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
HISTORY_PATH    = DB_DIR / "streams_history.csv"
ALBUMS_DIR      = DB_DIR / "discography" / "albums"
COVERS_PATH     = DB_DIR / "discography" / "covers.json"
HEADERS_DIR     = DB_DIR / "discography" / "headers"
CHARTS_GLOBAL_HISTORY_DIR = ROOT.parent / "charts" / "global" / "history"
TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"
HANDLE          = "@swiftiescharts"

sys.path.insert(0, str(ROOT.parent))   # collectors/spotify/ for core.*

INCLUDED_EDITIONS = {"standard", "deluxe", "acoustic", "anthology", "original"}
ENABLE_FILTERED_CHARTS = False

BODY_WIDTH_CSS = 880
BODY_PADDING_CSS = 12
HEADER_HEIGHT_CSS = 110
RENDER_DPR = 4

# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _shorten_title(t: str) -> str:
    t = re.sub(r"\(feat\.\s*", "(ft. ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bDressing Room\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\bRehearsal\b", "Reh.", t, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", t).strip()


def _dominant_color(img_path: Path) -> str:
    if not _PIL:
        return "#1db954"
    try:
        img = Image.open(img_path).convert("RGB")
        return _dominant_color_from_pil(img)
    except Exception:
        return "#1db954"


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
        ct = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")
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

def load_album_sections(album_name: str) -> list[dict]:
    """
    Returns list of sections for the given album, each with:
      {name, tracks: [{track_id, title_clean, version_tag, display_order, image_url}]}
    Only editions in INCLUDED_EDITIONS. Tracks sorted by display_order.
    """
    if not ALBUMS_DIR.exists():
        return []

    target_payload = None
    for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
        try:
            payload = json.loads(album_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and (payload.get("album") or "").lower() == album_name.lower():
            target_payload = payload
            break

    if target_payload is None:
        return [], album_name

    canonical_name = target_payload.get("album") or album_name
    sections = []
    for sec in target_payload.get("sections", []):
        tracks = []
        seen_in_section = set()
        for t in sec.get("tracks", []):
            edition = (t.get("edition") or "").strip().lower()
            if edition not in INCLUDED_EDITIONS:
                continue
            url = (t.get("url") or t.get("spotify_url") or "").strip()
            m = re.search(r"track/([A-Za-z0-9]+)", url)
            if not m:
                continue

            # Some album JSONs can contain accidental duplicate rows for a section.
            # Deduplicate by song family when present, fallback to normalized clean title.
            dedupe_key = (t.get("song_family") or _norm(t.get("title_clean") or t.get("title") or "")).strip().lower()
            if dedupe_key and dedupe_key in seen_in_section:
                continue
            if dedupe_key:
                seen_in_section.add(dedupe_key)

            try:
                display_order = int(t.get("display_order") or 9999)
            except Exception:
                display_order = 9999

            tracks.append({
                "track_id":     m.group(1),
                "title_clean":  (t.get("title_clean") or t.get("title") or "").strip(),
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
        sections.append({"name": name, "tracks": tracks})

    # Sort so "Standard Edition" (or "Standard") always appears first
    def sort_key(sec):
        name_lower = sec["name"].lower()
        if "standard" in name_lower:
            return (0, 0)  # Standard editions first
        else:
            return (1, sec["name"])  # Others alphabetically
    sections.sort(key=sort_key)

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
    all_ids = {t["track_id"] for sec in sections for t in sec["tracks"]}

    today_data: dict[str, dict] = {}
    yest_data:  dict[str, dict] = {}

    with open(HISTORY_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = row["date"]
            if d not in (target_date, yesterday):
                continue
            tid = row["track_id"]
            if tid not in all_ids:
                continue
            entry = {
                "streams":       int(row["streams"] or 0),
                "daily_streams": int(row["daily_streams"] or 0),
            }
            if d == target_date:
                today_data[tid] = entry
            else:
                yest_data[tid] = entry

    result = {}
    for tid in all_ids:
        t = today_data.get(tid)
        if t is None:
            result[tid] = {"streams": None, "daily": None, "change": None, "pct": None}
            continue
        y = yest_data.get(tid, {})
        daily    = t["daily_streams"]
        streams  = t["streams"]
        yest_d   = y.get("daily_streams", 0)
        change   = daily - yest_d
        pct      = (change / yest_d * 100) if yest_d != 0 else None
        result[tid] = {
            "streams": streams,
            "daily":   daily,
            "change":  change,
            "pct":     pct,
        }
    return result


def load_global_chart_filtered_for_album(sections: list[dict], target_date: str) -> tuple[dict[str, dict], bool]:
    """
    Returns ({track_id: {filtered_streams, filter_rate}}, chart_available).
    chart_available is True only when same-date global chart JSON exists and is readable.
    """
    json_path = CHARTS_GLOBAL_HISTORY_DIR / target_date[:4] / target_date[5:7] / target_date / f"ts_chart_{target_date}.json"
    if not json_path.exists():
        return {}, False

    try:
        entries = json.loads(json_path.read_text(encoding="utf-8"))
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
    with open(HISTORY_PATH, newline="", encoding="utf-8") as f:
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
            covers = json.loads(COVERS_PATH.read_text(encoding="utf-8"))
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
                payload = json.loads(album_file.read_text(encoding="utf-8"))
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


def pick_header_image(album_name: str) -> Path | None:
    if not HEADERS_DIR.exists():
        return None

    allowed_exts = {".png", ".jpg", ".jpeg", ".webp"}
    target_raw = (album_name or "").strip().casefold()
    target_norm = _norm(album_name)

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
        folder_images = [
            p for p in selected_dir.iterdir()
            if p.is_file() and p.suffix.lower() in allowed_exts
        ]
        if folder_images:
            return _pick_random_best_quality(folder_images)

    # Legacy fallback: db/discography/headers/<album_name>.<ext>
    flat_candidates = [
        p for p in HEADERS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in allowed_exts
    ]
    for p in sorted(flat_candidates, key=lambda x: x.name.casefold()):
        if p.stem.casefold() == target_raw:
            return p
    for p in sorted(flat_candidates, key=lambda x: x.name.casefold()):
        if _norm(p.stem) == target_norm:
            return p

    return None


def get_latest_date() -> str:
    latest = ""
    with open(HISTORY_PATH, newline="", encoding="utf-8") as f:
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
  padding:12px;
  color:#101828;
}
.container{
    width:100%;
  border-radius:20px;
  overflow:hidden;
  box-shadow:0 10px 30px rgba(16,24,40,.08),0 2px 8px rgba(16,24,40,.05);
  background:#ffffff;
}
/* ── header ── */
.hdr{
  height:110px;
    display:flex;align-items:center;gap:14px;
    padding:0 16px;
  position:relative;overflow:hidden;
  background:linear-gradient(135deg, rgba(29,185,84,.15) 0%, rgba(21,136,62,.08) 100%);
  border-bottom:2px solid rgba(29,185,84,.15);
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
.hdr-handle{font-size:12px;font-weight:700;line-height:1.3}
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
.col-chg{font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:flex-end}
.col-pct{font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:flex-end}
.pos{color:#067647}
.neg{color:#b42318}
.neutral{color:#667085}
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
}
.era-num{
  font-size:14px;font-weight:800;color:rgba(255,255,255,.95);
  display:flex;align-items:center;justify-content:flex-end;
}
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


def _compute_layout_metrics(sections: list[dict], show_filter_cols: bool) -> dict:
    """Compute dynamic grid/body sizing to avoid extra whitespace in final PNG."""
    total_tracks = sum(len(s["tracks"]) for s in sections)
    row_h = max(26, min(52, 26 + (16 - total_tracks) * 2))

    titles = [
        _shorten_title(t.get("title_clean") or "")
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
        col_heads_html = """<div class="col-heads">
    <span class="center">#</span>
    <span>SONG</span>
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
        col_heads_html = """<div class="col-heads">
    <span class="center">#</span>
    <span>SONG</span>
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
    return ""


def _build_totals_chip(items: list[tuple[str, str]]) -> str:
    vals = []
    for value, cls in items:
        class_attr = f" tot-chip-val {cls}".rstrip()
        vals.append(f'<span class="{class_attr}">{value}</span>')
    return f'<div class="tot-chip">{"".join(vals)}</div>'


def build_song_row_html(si: int, track: dict, hdata: dict, alt: bool, show_filter_cols: bool) -> str:
        title = _shorten_title(track["title_clean"])
        daily = hdata.get("daily")
        change = hdata.get("change")
        pct = hdata.get("pct")
        streams = hdata.get("streams")
        f_streams = hdata.get("filtered_streams")
        f_rate = hdata.get("filter_rate")

        daily_s = ("+" + fmt_num(daily)) if daily is not None else "—"
        chg_s, pct_s, chg_cls = fmt_chg(change, pct)

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
        <div class="col-num daily-val daily-col">{daily_s}</div>
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

    main_chip = _build_totals_chip([
        (f"+{fmt_num(sec_daily)}", ""),
        (chg_s, chg_chip_cls),
        (pct_disp, chg_chip_cls),
        (fmt_num(sec_str), ""),
    ])
    no_filter_cls = " no-filter" if not show_filter_cols else ""

    if not show_filter_cols:
        return f"""<div class="sec-total{no_filter_cls}" style="--sec-accent:{accent};--sec-bg:{bg}">
    <div class="sec-label">{sec_name}&nbsp;&nbsp;—&nbsp;&nbsp;Total</div>
    <div class="sec-num" style="grid-column:3">+{fmt_num(sec_daily)}</div>
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
) -> str:
    from datetime import datetime
    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")

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

    # alternate row color based on dominant
    alt_row_css = f"rgba({dr},{dg},{db},0.05)"
    tint_bg_css = f"rgba({dr},{dg},{db},0.08)"
    tint_border_css = f"rgba({dr},{dg},{db},0.18)"

    layout = layout or _compute_layout_metrics(sections, show_filter_cols)
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
            rows_html += build_song_row_html(si, track, hd, si % 2 != 0, show_filter_cols)
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

                total_main_chip = _build_totals_chip([
                    (f"+{fmt_num(total_daily)}", ""),
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
                era_html = f"""<div class="era-total no-filter">
    <div class="era-label">Total</div>
    <div class="era-num" style="grid-column:3">+{fmt_num(total_daily)}</div>
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
            <div class="hdr-handle" style="color:{dominant_hex}">{HANDLE}</div>
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

def generate(album_name: str, target_date: str | None = None) -> Path:
    if target_date is None:
        target_date = get_latest_date()
    print(f"[album_update] Album: {album_name}  Date: {target_date}")

    sections, album_name = load_album_sections(album_name)
    if not sections:
        raise ValueError(f"Aucune section trouvée pour l'album: {album_name!r}")
    print(f"[album_update] {sum(len(s['tracks']) for s in sections)} tracks dans {len(sections)} section(s)")

    hist = load_history_for_album(sections, target_date)
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
    header_img = pick_header_image(album_name)

    # Accent color comes from the selected header first; fall back to cover, then default.
    if header_img:
        dominant_hex = _header_accent_color(header_img)
    elif cover_url:
        dominant_hex = _dominant_color_from_url(cover_url)
    else:
        dominant_hex = "#1db954"

    # prefetch cover image
    print("[album_update] Téléchargement de la cover...")
    cover_uri = _url_to_data_uri(cover_url) if cover_url else ""
    layout = _compute_layout_metrics(sections, show_filter_cols)
    hdr_target_w = (layout["body_width_px"] - 2 * BODY_PADDING_CSS) * RENDER_DPR
    hdr_target_h = HEADER_HEIGHT_CSS * RENDER_DPR
    header_uri = _prepare_header_for_render(header_img, hdr_target_w, hdr_target_h) if header_img else ""

    section_palette = _section_palette_colors(header_img, max_colors=max(3, len(sections))) if header_img else []
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
    )

    album_slug = re.sub(r"[^a-z0-9]+", "_", album_name.lower()).strip("_")
    out_dir    = ROOT / "history" / target_date[:4] / target_date[5:7] / target_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path   = out_dir / f"{album_slug}_update.png"
    raw_out_path = out_dir / f"_{album_slug}_update_hires.png"
    tmp_html   = out_dir / f"_{album_slug}_tmp.html"
    tmp_html.write_text(html, encoding="utf-8")

    print("[album_update] Rendu Playwright...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # High-density render for near-4K width output (880 * 4 = 3520 px).
            page    = browser.new_page(viewport={"width": 1200, "height": 320}, device_scale_factor=RENDER_DPR)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(450)
            page.locator("body").screenshot(path=str(raw_out_path), scale="device")
            browser.close()

        if _PIL:
            try:
                img = Image.open(raw_out_path)
                # Keep native high-res render for maximum detail retention.
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

    sections, canonical_name = load_album_sections(album_name)
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
            "title": t.get("title_clean") or "Unknown",
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
    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
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

    return (
        f'📈| "{canonical_name}" received {total_daily_fmt} streams yesterday, {date_fmt}.{album_pct_str}\n\n'
        f'"{selected_song}" was the {label} with {track_daily_fmt} streams{track_pct_str}.'
    )


def post(album_name: str, image_path: Path, target_date: str) -> bool:
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
    clean_args = [a for a in args if a not in ("--post", "--no-post")]

    album_name  = clean_args[0] if len(clean_args) > 0 else None
    target_date = clean_args[1] if len(clean_args) > 1 else None

    if not album_name:
        print("Usage: generate_album_update_image.py <album_name> [date] [--post]")
        sys.exit(1)

    image_path = generate(album_name, target_date)
    resolved_date = target_date or get_latest_date()

    if do_post:
        album_slug = re.sub(r"[^a-z0-9]+", "_", album_name.lower()).strip("_")
        lock_path  = image_path.parent / f"{album_slug}_update.lock"

        if lock_path.exists():
            print(f"[album_update] Déjà posté ({lock_path.name}). Rien à faire.")
            return

        ok = post(album_name, image_path, resolved_date)
        if ok:
            lock_path.write_text(f"posted {resolved_date}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
