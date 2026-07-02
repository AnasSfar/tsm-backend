from __future__ import annotations

import argparse
import base64
import html
import random
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

try:
    from PIL import Image as PilImage
    _PIL = True
except ImportError:
    _PIL = False


SPOTIFY_SVG = """<svg class="logo" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/></svg>"""
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
SPOTIFY_STREAMS_DIR = REPO_ROOT / "collectors" / "spotify" / "streams"
SPOTIFY_STREAMS_SCRIPTS_DIR = SPOTIFY_STREAMS_DIR / "tools" / "scripts"
TSM_LOGO_PATHS = (
    REPO_ROOT / "db" / "logo.png",
    REPO_ROOT.parent / "tsm-frontend" / "frontend" / "public" / "icons" / "logo.gif",
    REPO_ROOT.parent / "tsm-frontend" / "icons" / "logo.gif",
)


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return text or "card"


def image_data_uri(url: str | None) -> tuple[str, bytes]:
    if not url:
        return "", b""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as response:
            data = response.read()
            mime = response.headers.get_content_type() or "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(data).decode()}", data
    except Exception:
        return "", b""


def _tsm_logo_data_uri() -> str:
    for path in TSM_LOGO_PATHS:
        if not path.exists():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        mime = "image/gif" if path.suffix.lower() == ".gif" else "image/png"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return ""


def _boost_color(rgb: tuple[int, int, int], *, min_sat: float = 0.48, min_val: float = 0.34) -> tuple[str, str]:
    import colorsys

    r, g, b = rgb
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    s = min(1.0, max(min_sat, s * 1.25))
    v = min(0.86, max(min_val, v * 0.86))
    rb, gb, bb = colorsys.hsv_to_rgb(h, s, v)
    color = f"#{int(rb * 255):02x}{int(gb * 255):02x}{int(bb * 255):02x}"
    return color, f"rgba({int(rb * 255)},{int(gb * 255)},{int(bb * 255)},.42)"


def _deep_color(rgb: tuple[int, int, int]) -> str:
    import colorsys

    r, g, b = rgb
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    s = min(1.0, max(0.42, s * 1.12))
    v = min(0.30, max(0.12, v * 0.46))
    rb, gb, bb = colorsys.hsv_to_rgb(h, s, v)
    return f"#{int(rb * 255):02x}{int(gb * 255):02x}{int(bb * 255):02x}"


def _hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    raw = (value or "").strip().lstrip("#")
    if len(raw) != 6:
        return None
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return None


def _rgb_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _luma(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def _cover_badge_colors(img_bytes: bytes, avoid_hex: str) -> tuple[str, str]:
    if not _PIL or not img_bytes:
        return "#d7b46a", "#111827"
    try:
        from io import BytesIO
        import colorsys

        avoid_rgb = _hex_to_rgb(avoid_hex) or (29, 185, 84)
        img = PilImage.open(BytesIO(img_bytes)).convert("RGB").resize((90, 90), PilImage.LANCZOS)
        colors = img.quantize(colors=16, method=PilImage.Quantize.MEDIANCUT).convert("RGB").getcolors(90 * 90) or []
        candidates = []
        for count, rgb in colors:
            r, g, b = rgb
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if (r > 238 and g > 238 and b > 238) or (r < 20 and g < 20 and b < 20):
                continue
            distance = _rgb_distance(rgb, avoid_rgb)
            distance_bonus = min(distance / 120, 1.0)
            score = (count ** 0.5) * (0.35 + s) * (0.35 + v) * (0.65 + distance_bonus)
            candidates.append((score, rgb, s, v))
        if not candidates:
            return "#d7b46a", "#111827"

        _score, rgb, s, v = max(candidates, key=lambda item: item[0])
        h, _s, _v = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        s = min(1.0, max(0.42, s * 1.35))
        v = min(0.82, max(0.42, v * 1.05))
        rb, gb, bb = colorsys.hsv_to_rgb(h, s, v)
        final_rgb = (int(rb * 255), int(gb * 255), int(bb * 255))
        bg = f"#{final_rgb[0]:02x}{final_rgb[1]:02x}{final_rgb[2]:02x}"
        text = "#101828" if _luma(final_rgb) > 0.58 else "#ffffff"
        return bg, text
    except Exception:
        return "#d7b46a", "#111827"


def cover_palette(img_bytes: bytes) -> tuple[str, str]:
    if not _PIL or not img_bytes:
        return ("linear-gradient(135deg,#1db954 0%,#0f7f3d 100%)", "#1db954")
    try:
        from io import BytesIO
        import colorsys

        img = PilImage.open(BytesIO(img_bytes)).convert("RGB").resize((120, 120), PilImage.LANCZOS)
        colors = img.quantize(colors=14, method=PilImage.Quantize.MEDIANCUT).convert("RGB").getcolors(120 * 120) or []
        candidates = []
        for count, color in colors:
            r, g, b = color
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            hue = h * 360
            if (r > 235 and g > 235 and b > 235) or (r < 18 and g < 18 and b < 18):
                continue
            mud_penalty = 0.6 if 35 <= hue <= 78 and s < 0.62 else 0.0
            score = (count ** 0.5) * (0.35 + s) * (0.35 + v) - mud_penalty
            candidates.append({"rgb": color, "hue": hue, "sat": s, "val": v, "score": score})
        if not candidates:
            candidates = [{"rgb": (29, 185, 84), "hue": 141, "sat": 0.84, "val": 0.73, "score": 1}]

        total = sum(count for count, _color in colors) or 1
        neutral_weight = 0
        weighted_sat = 0.0
        weighted_val = 0.0
        for count, color in colors:
            r, g, b = color
            _h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            weighted_sat += s * count
            weighted_val += v * count
            if s < 0.18:
                neutral_weight += count
        neutral_ratio = neutral_weight / total
        avg_sat = weighted_sat / total
        avg_val = weighted_val / total
        if neutral_ratio >= 0.70 or avg_sat < 0.16:
            base = max(16, min(42, int(avg_val * 58)))
            mid = max(24, min(58, base + 18))
            primary = f"#{mid:02x}{mid:02x}{mid:02x}"
            secondary = f"#{base:02x}{base:02x}{base + 4:02x}"
            accent_candidates = [c for c in candidates if c["sat"] >= 0.28 and 0.18 <= c["val"] <= 0.78]
            if accent_candidates:
                _accent, glow = _boost_color(max(accent_candidates, key=lambda c: c["score"])["rgb"], min_sat=0.36, min_val=0.22)
            else:
                glow = "rgba(255,255,255,.10)"
            gradient = (
                f"radial-gradient(circle at 76% 18%,{glow} 0%,rgba(16,18,24,0) 24%),"
                f"linear-gradient(135deg,{primary} 0%,{secondary} 100%)"
            )
            return (gradient, primary)

        cool = [c for c in candidates if 150 <= c["hue"] <= 220 and c["sat"] >= 0.24]
        warm = [c for c in candidates if (0 <= c["hue"] <= 32 or 330 <= c["hue"] <= 360) and c["sat"] >= 0.32]
        primary_choice = max(cool or candidates, key=lambda c: c["score"] + (0.8 if 165 <= c["hue"] <= 200 else 0))
        secondary_choice = max(warm or candidates, key=lambda c: c["score"] + (0.5 if c in warm else 0))
        primary, _ = _boost_color(primary_choice["rgb"], min_sat=0.58, min_val=0.40)
        secondary = _deep_color(secondary_choice["rgb"])
        _glow_color, glow = _boost_color(secondary_choice["rgb"], min_sat=0.52, min_val=0.30)
        gradient = (
            f"radial-gradient(circle at 76% 18%,{glow} 0%,rgba(16,24,40,0) 28%),"
            f"linear-gradient(135deg,{primary} 0%,{secondary} 100%)"
        )
        return (gradient, primary)
    except Exception:
        return ("linear-gradient(135deg,#1db954 0%,#0f7f3d 100%)", "#1db954")


def _title_font_size(title: str) -> int:
    n = len(title)
    if n <= 13:
        return 52
    if n <= 16:
        return 44
    if n <= 19:
        return 38
    if n <= 23:
        return 32
    if n <= 28:
        return 26
    return 22


def _best_since_title_font_size(title: str) -> int:
    n = len(title)
    if n <= 13:
        return 48
    if n <= 18:
        return 42
    if n <= 24:
        return 35
    if n <= 32:
        return 29
    return 24


def render_song_card(
    *,
    title: str,
    eyebrow: str,
    subtitle: str,
    stats: list[dict],
    cover_url: str | None,
    footer_left: str,
    footer_right: str,
    extra: str = "",
    logo_svg: str = SPOTIFY_SVG,
    best_since: bool = False,
    combined_versions: bool = False,
) -> str:
    cover_uri, cover_bytes = image_data_uri(cover_url)
    gradient, accent = cover_palette(cover_bytes)
    badge_bg, badge_text = _cover_badge_colors(cover_bytes, accent)
    art_html = f'<img class="cover" src="{cover_uri}" />' if cover_uri else '<div class="cover-ph"></div>'
    stat_html = []
    for stat in stats:
        badge = stat.get("badge") or ""
        badge_class = stat.get("badge_class") or "flat"
        badge_html = f'<div class="chg {html.escape(badge_class)}">{html.escape(str(badge))}</div>' if badge else ""
        stat_html.append(
            f"""<div class="stat">
        <div class="stat-lbl">{html.escape(str(stat.get("label") or ""))}</div>
        <div class="stat-val">{html.escape(str(stat.get("value") or "-"))}</div>
        {badge_html}
      </div>"""
        )
    extra_html = f'<div class="extra">{html.escape(extra)}</div>' if extra else ""
    subtitle_html = f'<div class="subtitle">{html.escape(subtitle)}</div>' if best_since and subtitle else ""
    mode_badge_text = "COMBINED VERSIONS" if combined_versions else footer_right
    mode_badge_html = f'<span class="mode-badge">{html.escape(mode_badge_text)}</span>' if mode_badge_text else ""
    tsm_logo_uri = _tsm_logo_data_uri()
    footer_left_html = html.escape(footer_left)
    if tsm_logo_uri:
        footer_left_html = (
            f'<span class="ftr-brand"><img class="tsm-logo" src="{tsm_logo_uri}" alt="TSM" />'
            f"<span>{html.escape(footer_left)}</span></span>"
        )
    body_class = "best-since" if best_since else "default"
    if best_since:
        css = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  width:800px;height:299px;
  background:{gradient};
  position:relative;overflow:hidden;color:#fff;
}}
body:before{{
  content:"";position:absolute;inset:0;
  background:
    linear-gradient(90deg,rgba(4,10,16,.74) 0%,rgba(4,10,16,.52) 49%,rgba(4,10,16,.14) 100%),
    radial-gradient(circle at 18% 85%,rgba(255,255,255,.16),rgba(255,255,255,0) 34%);
}}
.layout{{height:299px;position:relative;z-index:1}}
.cover-col{{
  position:absolute;right:24px;top:23px;width:252px;height:252px;
  overflow:hidden;border-radius:26px;
  box-shadow:0 24px 50px rgba(0,0,0,.42),0 0 0 1px rgba(255,255,255,.18);
}}
.cover,.cover-ph{{width:252px;height:252px;object-fit:cover;display:block}}
.cover-ph{{background:#172421}}
.info-col{{
  position:absolute;left:28px;top:22px;bottom:26px;width:470px;
  display:flex;flex-direction:column;justify-content:center;gap:12px;
}}
.hdr-row{{display:flex;align-items:center;gap:9px;width:100%}}
.logo{{width:26px;height:26px;flex-shrink:0}}
.hdr-label{{
  color:rgba(255,255,255,.92);font-size:12px;font-weight:900;
  letter-spacing:.12em;text-transform:uppercase;
}}
.mode-badge{{
  margin-left:auto;color:rgba(255,255,255,.88);
  background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);
  border-radius:999px;padding:6px 10px;
  font-size:10px;font-weight:900;letter-spacing:.08em;text-transform:uppercase;
  white-space:nowrap;
}}
.title{{
  color:#fff;font-size:{_best_since_title_font_size(title)}px;font-weight:950;
  line-height:1.04;letter-spacing:0;
  max-width:455px;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden;
  text-shadow:0 3px 18px rgba(0,0,0,.28);
  margin-bottom:-4px;
}}
.subtitle{{
  width:max-content;max-width:440px;
  color:{badge_text};background:{badge_bg};
  font-size:13px;font-weight:900;border-radius:999px;
  padding:7px 13px;display:flex;align-items:center;gap:8px;
  text-transform:uppercase;
  box-shadow:0 12px 28px rgba(0,0,0,.20),0 0 0 1px rgba(255,255,255,.18);
}}
.subtitle:before{{
  content:"\\2605";display:inline-flex;align-items:center;justify-content:center;
  width:20px;height:20px;border-radius:999px;
  color:{badge_bg};background:{badge_text};font-size:12px;line-height:1;
}}
.stats{{display:flex;gap:10px;margin-top:2px}}
.stat{{
  background:rgba(7,14,22,.58);border:1px solid rgba(255,255,255,.20);
  border-radius:16px;padding:11px 15px 10px;min-width:132px;
  box-shadow:0 12px 30px rgba(0,0,0,.18);
}}
.stat-lbl{{
  font-size:9px;font-weight:900;letter-spacing:.1em;
  text-transform:uppercase;color:rgba(255,255,255,.58);margin-bottom:4px;
}}
.stat-val{{font-size:24px;font-weight:950;color:#fff;line-height:1}}
.chg{{font-size:10px;font-weight:900;margin-top:5px;letter-spacing:.02em}}
.chg.new,.chg.re,.chg.up{{color:#7ee787}}
.chg.down{{color:#fca5a5}}
.chg.flat{{color:rgba(255,255,255,.52)}}
.extra{{
  color:rgba(255,255,255,.76);font-size:13px;font-weight:750;
  max-width:430px;margin-top:0;margin-bottom:2px;
}}
.ftr{{
  position:absolute;bottom:10px;left:30px;right:30px;z-index:2;
  display:flex;justify-content:space-between;
}}
.ftr-l,.ftr-r{{font-size:10px;color:rgba(255,255,255,.52);font-weight:700}}
.ftr-brand{{display:flex;align-items:center;gap:6px}}
.tsm-logo{{width:18px;height:18px;object-fit:contain;border-radius:4px}}
"""
    else:
        css = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  width:800px;height:299px;
  background:{gradient};
  position:relative;overflow:hidden;color:#fff;
}}
body:before{{
  content:"";position:absolute;inset:0;
  background:
    linear-gradient(90deg,rgba(4,10,16,.74) 0%,rgba(4,10,16,.52) 49%,rgba(4,10,16,.14) 100%),
    radial-gradient(circle at 18% 85%,rgba(255,255,255,.16),rgba(255,255,255,0) 34%);
}}
.layout{{height:299px;position:relative;z-index:1}}
.cover-col{{
  position:absolute;right:24px;top:23px;width:252px;height:252px;
  overflow:hidden;border-radius:26px;
  box-shadow:0 24px 50px rgba(0,0,0,.42),0 0 0 1px rgba(255,255,255,.18);
}}
.cover,.cover-ph{{width:252px;height:252px;object-fit:cover;display:block}}
.cover-ph{{background:#172421}}
.info-col{{
  position:absolute;left:28px;top:22px;bottom:26px;width:470px;
  display:flex;flex-direction:column;justify-content:center;gap:12px;
}}
.hdr-row{{display:flex;align-items:center;gap:9px;width:100%}}
.logo{{width:26px;height:26px;flex-shrink:0}}
.hdr-label{{
  color:rgba(255,255,255,.92);font-size:12px;font-weight:900;
  letter-spacing:.12em;text-transform:uppercase;
}}
.mode-badge{{
  margin-left:auto;color:rgba(255,255,255,.88);
  background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);
  border-radius:999px;padding:6px 10px;
  font-size:10px;font-weight:900;letter-spacing:.08em;text-transform:uppercase;
  white-space:nowrap;
}}
.title{{
  color:#fff;font-size:{_best_since_title_font_size(title)}px;font-weight:950;
  line-height:1.04;letter-spacing:0;
  max-width:455px;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden;
  text-shadow:0 3px 18px rgba(0,0,0,.28);
  margin-bottom:-4px;
}}
.stats{{display:flex;gap:10px;margin-top:2px}}
.stat{{
  background:rgba(7,14,22,.58);border:1px solid rgba(255,255,255,.20);
  border-radius:16px;padding:11px 15px 10px;min-width:132px;
  box-shadow:0 12px 30px rgba(0,0,0,.18);
}}
.stat-lbl{{
  font-size:9px;font-weight:900;letter-spacing:.1em;
  text-transform:uppercase;color:rgba(255,255,255,.58);margin-bottom:4px;
}}
.stat-val{{font-size:24px;font-weight:950;color:#fff;line-height:1}}
.chg{{font-size:10px;font-weight:900;margin-top:5px;letter-spacing:.02em}}
.chg.new,.chg.re,.chg.up{{color:#7ee787}}
.chg.down{{color:#fca5a5}}
.chg.flat{{color:rgba(255,255,255,.52)}}
.extra{{
  color:rgba(255,255,255,.76);font-size:13px;font-weight:750;
  max-width:430px;margin-top:0;margin-bottom:2px;
}}
.ftr{{
  position:absolute;bottom:10px;left:30px;right:30px;z-index:2;
  display:flex;justify-content:space-between;
}}
.ftr-l,.ftr-r{{font-size:10px;color:rgba(255,255,255,.52);font-weight:700}}
.ftr-brand{{display:flex;align-items:center;gap:6px}}
.tsm-logo{{width:18px;height:18px;object-fit:contain;border-radius:4px}}
"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body class="{body_class}">
<div class="layout">
  <div class="cover-col">{art_html}</div>
  <div class="info-col">
    <div class="hdr-row">
      {logo_svg}
      <span class="hdr-label">{html.escape(eyebrow)}</span>
      {mode_badge_html}
    </div>
    <div class="title">{html.escape(title)}</div>
    {extra_html}
    {subtitle_html}
    <div class="stats">
      {''.join(stat_html)}
    </div>
  </div>
</div>
<div class="ftr">
  <span class="ftr-l">{footer_left_html}</span>
  <span class="ftr-r">{html.escape(footer_right)}</span>
</div>
</body></html>"""


def write_song_card_png(html_text: str, output_path: Path, tmp_path: Path, *, keep_html: bool = False) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(html_text, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 800, "height": 299}, device_scale_factor=2)
            page.goto(f"file:///{tmp_path.as_posix()}", wait_until="load")
            page.locator("body").screenshot(path=str(output_path))
            browser.close()
    finally:
        if not keep_html:
            tmp_path.unlink(missing_ok=True)
    return output_path


def _fmt_int(value: int | None) -> str:
    return "?" if value is None else f"{int(value):,}"


def _fmt_signed_int(value: int | None) -> str:
    return "?" if value is None else f"+{int(value):,}"


def _fmt_pct(current: int | None, previous: int | None) -> str:
    if current is None or previous is None or previous <= 0:
        return "+0.0%"
    return f"{(current - previous) / previous * 100:+.1f}%"


def _badge_class(text: str) -> str:
    if text in {"+0.0%", "0.0%"}:
        return "flat"
    if text.startswith("+"):
        return "up"
    if text.startswith("-"):
        return "down"
    return "flat"


def _preview_imports():
    for path in (SPOTIFY_STREAMS_DIR, SPOTIFY_STREAMS_SCRIPTS_DIR, REPO_ROOT / "collectors"):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
    import best_day_since
    import spotlight

    return best_day_since, spotlight


def _latest_preview_date(best_day_since) -> str:
    history = best_day_since.load_history()
    latest = best_day_since.latest_history_date(history)
    if latest is None:
        raise SystemExit("No streams history date found for preview generation.")
    print(f"[preview] Using latest streams date: {latest.isoformat()}", flush=True)
    return latest.isoformat()


def _pick_real_track(best_day_since, spotlight, stats_date: str, title: str | None) -> tuple[dict, int, int | None, int | None]:
    tracks = spotlight.load_all_tracks()
    print(f"[preview] Loaded {len(tracks)} track(s).", flush=True)
    track = spotlight.find_track(title, tracks) if title else None
    if track is None:
        print(f"[preview] Picking a random track with real stream data for {stats_date}...", flush=True)
        target_day = datetime.strptime(stats_date, "%Y-%m-%d").date()
        previous_day = target_day - timedelta(days=1)
        history = best_day_since.load_history()
        candidates = []
        for candidate in tracks:
            point_by_day = {point.day: point for point in history.get(candidate["track_id"], [])}
            current = point_by_day.get(target_day)
            if current is None or current.total is None or current.daily is None or current.daily <= 0:
                continue
            previous = point_by_day.get(previous_day)
            candidates.append((
                candidate,
                current.total,
                current.daily,
                previous.daily if previous else None,
            ))
        if not candidates:
            raise SystemExit(f"No track with real stream data found for {stats_date}.")
        picked = random.choice(candidates)
        print(f"[preview] Selected default track: {picked[0]['title']}", flush=True)
        return picked

    total_today, _total_yesterday, daily_today, daily_yesterday, _daily_last_week = (
        spotlight.load_history_for_tracks([track["track_id"]], stats_date)
    )
    if total_today is None or daily_today is None:
        raise SystemExit(f"No stream data found for {track['title']} on {stats_date}.")
    print(f"[preview] Selected default track: {track['title']}", flush=True)
    return track, total_today, daily_today, daily_yesterday


def _pick_real_best_since(best_day_since, spotlight, stats_date: str, title: str | None) -> tuple[dict, dict, int, int | None]:
    print(f"[preview] Searching best-day-since rows for {stats_date}...", flush=True)
    base_tracks = best_day_since.load_tracks(include_extras=False)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    history = best_day_since.load_history()
    target = datetime.strptime(stats_date, "%Y-%m-%d").date()
    spotlight_tracks = {track["track_id"]: track for track in spotlight.load_all_tracks()}

    rows = []
    seen_families: set[str] = set()
    for track_id, track in base_tracks.items():
        if title and title.casefold() not in track.title.casefold():
            continue
        family = (track.song_family or track_id).strip()
        if family in seen_families:
            continue
        seen_families.add(family)
        row = best_day_since.compute_best_day_since_combined(
            track,
            best_day_since.combined_tracks_for(all_tracks.get(track_id, track), all_tracks),
            history,
            target,
        )
        if row and row.get("kind") == "since" and best_day_since.passes_filters(row, min_days=1):
            rows.append(row)

    if not rows and title:
        raise SystemExit(f"No best-day-since row found for {title} on {stats_date}.")
    if not rows:
        raise SystemExit(f"No best-day-since rows found for {stats_date}.")

    row = random.choice(rows) if not title else sorted(rows, key=best_day_since.sort_key, reverse=True)[0]
    print(
        f"[preview] Selected best-since track: {row['title']} "
        f"({best_day_since.row_label(row)})",
        flush=True,
    )
    track = spotlight_tracks.get(row["track_id"])
    if not track:
        raise SystemExit(f"Track missing in spotlight DB: {row['title']} [{row['track_id']}]")

    track_ids = row.get("combined_track_ids") or [row["track_id"]]
    total_today, _total_yesterday, _daily_today, daily_yesterday, _daily_last_week = (
        spotlight.load_history_for_tracks(track_ids, stats_date)
    )
    if total_today is None:
        raise SystemExit(f"Missing total streams for {row['title']} on {stats_date}.")
    return row, track, total_today, daily_yesterday


def _render_preview(*, best_since: bool, output_dir: Path, keep_html: bool, title: str | None, stats_date: str | None) -> Path:
    mode = "best_since" if best_since else "default"
    print(f"[preview] Building {mode} preview...", flush=True)
    best_day_since, spotlight = _preview_imports()
    target_date = stats_date or _latest_preview_date(best_day_since)
    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    covers = spotlight.load_covers()
    print(f"[preview] Loaded {len(covers)} cover fallback(s).", flush=True)

    if best_since:
        row, track, total_today, daily_yesterday = _pick_real_best_since(
            best_day_since,
            spotlight,
            target_date,
            title,
        )
        label = best_day_since.row_label(row)
        daily = int(row["daily_streams"])
        pct = _fmt_pct(daily, daily_yesterday)
        cover_url = spotlight.get_cover_url(track, covers)
        card_title = track.get("title") or row["title"]
        subtitle = label
        stats = [
            {"label": "Daily Streams", "value": _fmt_signed_int(daily), "badge": pct, "badge_class": _badge_class(pct)},
            {"label": "Total Streams", "value": _fmt_int(total_today), "badge": "Since release", "badge_class": "flat"},
        ]
        extra = track.get("album") or row.get("album") or ""
        combined_versions = bool(row.get("combined"))
    else:
        track, total_today, daily_today, daily_yesterday = _pick_real_track(best_day_since, spotlight, target_date, title)
        pct = _fmt_pct(daily_today, daily_yesterday)
        cover_url = spotlight.get_cover_url(track, covers)
        card_title = track["title"]
        subtitle = ""
        stats = [
            {"label": "Daily Streams", "value": _fmt_signed_int(daily_today), "badge": pct, "badge_class": _badge_class(pct)},
            {"label": "Total Streams", "value": _fmt_int(total_today), "badge": "Since release", "badge_class": "flat"},
        ]
        extra = track.get("album") or track.get("artist") or ""
        combined_versions = False

    html_text = render_song_card(
        title=card_title,
        eyebrow="Spotify Streams",
        subtitle=subtitle,
        stats=stats,
        cover_url=cover_url,
        footer_left="@tsmuseum13",
        footer_right=date_text,
        extra=extra,
        best_since=best_since,
        combined_versions=combined_versions,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(card_title)
    out_path = output_dir / f"song_card_{mode}_{slug}.png"
    html_path = output_dir / f"song_card_{mode}_{slug}.html"
    print(f"[preview] Writing HTML: {html_path}", flush=True)
    print(f"[preview] Rendering PNG: {out_path}", flush=True)
    return write_song_card_png(html_text, out_path, html_path, keep_html=keep_html)


def _title_case(title: str) -> str:
    return "long" if len(title or "") >= 30 else "short"


def _write_preview_card(
    *,
    output_dir: Path,
    keep_html: bool,
    case_slug: str,
    card_title: str,
    date_text: str,
    stats: list[dict],
    cover_url: str,
    extra: str,
    subtitle: str = "",
    best_since: bool = False,
    combined_versions: bool = False,
) -> Path:
    html_text = render_song_card(
        title=card_title,
        eyebrow="Spotify Streams",
        subtitle=subtitle,
        stats=stats,
        cover_url=cover_url,
        footer_left="@tsmuseum13",
        footer_right=date_text,
        extra=extra,
        best_since=best_since,
        combined_versions=combined_versions,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(card_title)
    out_path = output_dir / f"song_card_{case_slug}_{slug}.png"
    html_path = output_dir / f"song_card_{case_slug}_{slug}.html"
    print(f"[preview] Writing HTML: {html_path}", flush=True)
    print(f"[preview] Rendering PNG: {out_path}", flush=True)
    return write_song_card_png(html_text, out_path, html_path, keep_html=keep_html)


def _best_since_rows_for_date(best_day_since, stats_date: str) -> list[dict]:
    base_tracks = best_day_since.load_tracks(include_extras=False)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    history = best_day_since.load_history()
    target = datetime.strptime(stats_date, "%Y-%m-%d").date()

    rows = []
    seen_families: set[str] = set()
    for track_id, track in base_tracks.items():
        family = (track.song_family or track_id).strip()
        if family in seen_families:
            continue
        seen_families.add(family)
        row = best_day_since.compute_best_day_since_combined(
            track,
            best_day_since.combined_tracks_for(all_tracks.get(track_id, track), all_tracks),
            history,
            target,
        )
        if row and row.get("kind") == "since" and best_day_since.passes_filters(row, min_days=1):
            rows.append(row)
    return rows


def _default_candidates_for_date(best_day_since, spotlight, stats_date: str, best_track_ids: set[str]) -> list[dict]:
    target_day = datetime.strptime(stats_date, "%Y-%m-%d").date()
    previous_day = target_day - timedelta(days=1)
    history = best_day_since.load_history()
    tracks = spotlight.load_all_tracks()
    covers = spotlight.load_covers()
    candidates = []
    for track in tracks:
        point_by_day = {point.day: point for point in history.get(track["track_id"], [])}
        current = point_by_day.get(target_day)
        if current is None or current.total is None or current.daily is None or current.daily <= 0:
            continue
        if track["track_id"] in best_track_ids:
            continue
        previous = point_by_day.get(previous_day)
        candidates.append({
            "track": track,
            "total_today": current.total,
            "daily_today": current.daily,
            "daily_yesterday": previous.daily if previous else None,
            "cover_url": spotlight.get_cover_url(track, covers),
        })
    if not candidates and best_track_ids:
        print("[preview] No non-best-day default candidates found; allowing all real stream tracks.", flush=True)
        return _default_candidates_for_date(best_day_since, spotlight, stats_date, set())
    return candidates


def _pick_case(candidates: list[dict], *, title_case: str) -> dict | None:
    matching = [item for item in candidates if _title_case(item["title"]) == title_case]
    return random.choice(matching) if matching else None


def _render_preview_gallery(
    *,
    output_dir: Path,
    keep_html: bool,
    stats_date: str | None,
    only_best_since: bool,
) -> list[Path]:
    print("[preview] Building full song_card preview gallery...", flush=True)
    best_day_since, spotlight = _preview_imports()
    target_date = stats_date or _latest_preview_date(best_day_since)
    date_text = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    covers = spotlight.load_covers()
    spotlight_tracks = {track["track_id"]: track for track in spotlight.load_all_tracks()}
    paths: list[Path] = []

    best_rows = _best_since_rows_for_date(best_day_since, target_date)
    best_track_ids = {row["track_id"] for row in best_rows}
    print(f"[preview] Found {len(best_rows)} best-day-since row(s).", flush=True)

    if not only_best_since:
        default_candidates = _default_candidates_for_date(best_day_since, spotlight, target_date, best_track_ids)
        print(f"[preview] Found {len(default_candidates)} default non-best candidate(s).", flush=True)
        default_items = [
            {
                **candidate,
                "title": candidate["track"].get("title") or "",
            }
            for candidate in default_candidates
        ]
        for case in ("short", "long"):
            selected = _pick_case(default_items, title_case=case)
            if not selected:
                print(f"[preview] Skip default_not_best_{case}: no real candidate.", flush=True)
                continue
            track = selected["track"]
            daily = selected["daily_today"]
            pct = _fmt_pct(daily, selected["daily_yesterday"])
            paths.append(_write_preview_card(
                output_dir=output_dir,
                keep_html=keep_html,
                case_slug=f"default_not_best_{case}",
                card_title=track["title"],
                date_text=date_text,
                stats=[
                    {"label": "Daily Streams", "value": _fmt_signed_int(daily), "badge": pct, "badge_class": _badge_class(pct)},
                    {"label": "Total Streams", "value": _fmt_int(selected["total_today"]), "badge": "Since release", "badge_class": "flat"},
                ],
                cover_url=selected["cover_url"],
                extra=track.get("album") or track.get("artist") or "",
            ))

    best_items = []
    for row in best_rows:
        track = spotlight_tracks.get(row["track_id"])
        if not track:
            continue
        title = track.get("title") or row["title"]
        best_items.append({
            "row": row,
            "track": track,
            "title": title,
            "combined": bool(row.get("combined")),
        })

    for combined in (False, True):
        combined_label = "combined" if combined else "solo"
        for case in ("short", "long"):
            matching = [
                item for item in best_items
                if item["combined"] == combined and _title_case(item["title"]) == case
            ]
            if not matching:
                print(f"[preview] Skip best_since_{combined_label}_{case}: no real candidate.", flush=True)
                continue
            item = random.choice(matching)
            row = item["row"]
            track = item["track"]
            track_ids = row.get("combined_track_ids") or [row["track_id"]]
            total_today, _total_yesterday, _daily_today, daily_yesterday, _daily_last_week = (
                spotlight.load_history_for_tracks(track_ids, target_date)
            )
            if total_today is None:
                print(f"[preview] Skip best_since_{combined_label}_{case}: missing total streams.", flush=True)
                continue
            daily = int(row["daily_streams"])
            label = best_day_since.row_label(row)
            pct = _fmt_pct(daily, daily_yesterday)
            paths.append(_write_preview_card(
                output_dir=output_dir,
                keep_html=keep_html,
                case_slug=f"best_since_{combined_label}_{case}",
                card_title=item["title"],
                date_text=date_text,
                subtitle=label,
                stats=[
                    {"label": "Daily Streams", "value": _fmt_signed_int(daily), "badge": pct, "badge_class": _badge_class(pct)},
                    {"label": "Total Streams", "value": _fmt_int(total_today), "badge": "Since release", "badge_class": "flat"},
                ],
                cover_url=spotlight.get_cover_url(track, covers),
                extra=track.get("album") or row.get("album") or "",
                best_since=True,
                combined_versions=combined,
            ))

    return paths


def _clear_previous_previews(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    removed = 0
    for pattern in ("song_card_*.png", "song_card_*.html"):
        for path in output_dir.glob(pattern):
            if not path.is_file():
                continue
            path.unlink()
            removed += 1
    if removed:
        print(f"[preview] Deleted {removed} previous song card preview file(s).", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a preview song card PNG/HTML.")
    parser.add_argument("--best-since", action="store_true", help="Generate only the best-day-since card design.")
    parser.add_argument("--title", help="Use a real track matching this title instead of automatic random selection.")
    parser.add_argument("--date", help="Stats date YYYY-MM-DD. Defaults to latest date in streams history.")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "previews"),
        help="Directory for generated previews.",
    )
    parser.add_argument("--keep-html", action="store_true", default=True, help="Keep the generated HTML preview.")
    parser.add_argument("--no-keep-html", action="store_false", dest="keep_html", help="Delete the temporary HTML.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    print(f"[preview] Output dir: {output_dir}", flush=True)
    _clear_previous_previews(output_dir)
    if args.title:
        path = _render_preview(
            best_since=args.best_since,
            output_dir=output_dir,
            keep_html=args.keep_html,
            title=args.title,
            stats_date=args.date,
        )
        print(f"Generated preview: {path}")
        return

    paths = _render_preview_gallery(
        output_dir=output_dir,
        keep_html=args.keep_html,
        stats_date=args.date,
        only_best_since=args.best_since,
    )
    print(f"[preview] Generated {len(paths)} preview file(s).", flush=True)
    for path in paths:
        print(f"Generated preview: {path}", flush=True)


if __name__ == "__main__":
    main()
