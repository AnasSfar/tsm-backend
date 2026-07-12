from __future__ import annotations

import base64
import html
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

try:
    from .export_frame import add_export_frame
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from comp.export_frame import add_export_frame

try:
    from PIL import Image as PilImage
    _PIL = True
except ImportError:
    _PIL = False


SPOTIFY_SVG = """<svg class="logo" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/></svg>"""
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
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


def _boost_color(rgb: tuple[int, int, int], *, min_sat: float = 0.48, min_val: float = 0.34, max_val: float = 0.86) -> tuple[str, str]:
    import colorsys

    r, g, b = rgb
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    s = min(1.0, max(min_sat, s * 1.25))
    v = min(max_val, max(min_val, v * 0.92))
    rb, gb, bb = colorsys.hsv_to_rgb(h, s, v)
    color = f"#{int(rb * 255):02x}{int(gb * 255):02x}{int(bb * 255):02x}"
    return color, f"rgba({int(rb * 255)},{int(gb * 255)},{int(bb * 255)},.42)"


def _deep_color(rgb: tuple[int, int, int], *, min_val: float = 0.22, max_val: float = 0.42) -> str:
    import colorsys

    r, g, b = rgb
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    s = min(1.0, max(0.42, s * 1.12))
    v = min(max_val, max(min_val, v * 0.58))
    rb, gb, bb = colorsys.hsv_to_rgb(h, s, v)
    return f"#{int(rb * 255):02x}{int(gb * 255):02x}{int(bb * 255):02x}"


def _rgba_from_rgb(rgb: tuple[int, int, int], alpha: float) -> str:
    r, g, b = rgb
    return f"rgba({r},{g},{b},{alpha:.2f})"


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
            if (r > 242 and g > 242 and b > 242) or (r < 12 and g < 12 and b < 12):
                continue
            if s < 0.10 and (v < 0.18 or v > 0.86):
                continue
            mud_penalty = 0.6 if 35 <= hue <= 78 and s < 0.62 else 0.0
            readable_val_bonus = 1.0 - min(abs(v - 0.56) / 0.56, 1.0) * 0.30
            score = (count ** 0.5) * (0.35 + s) * (0.35 + v) * readable_val_bonus - mud_penalty
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
            base = max(24, min(48, int(avg_val * 70)))
            mid = max(48, min(82, base + 28))
            primary = f"#{mid:02x}{mid:02x}{mid:02x}"
            secondary = f"#{base:02x}{base:02x}{base + 4:02x}"
            accent_candidates = [c for c in candidates if c["sat"] >= 0.28 and 0.18 <= c["val"] <= 0.78]
            if accent_candidates:
                accent_rgb = max(accent_candidates, key=lambda c: c["score"])["rgb"]
                _accent, glow = _boost_color(accent_rgb, min_sat=0.38, min_val=0.34, max_val=0.74)
            else:
                glow = "rgba(255,255,255,.16)"
            gradient = (
                f"radial-gradient(circle at 76% 18%,{glow} 0%,rgba(16,18,24,0) 30%),"
                f"radial-gradient(circle at 8% 92%,rgba(255,255,255,.14) 0%,rgba(255,255,255,0) 28%),"
                f"linear-gradient(135deg,{primary} 0%,{secondary} 100%)"
            )
            return (gradient, primary)

        vivid = [c for c in candidates if c["sat"] >= 0.22 and 0.18 <= c["val"] <= 0.88]
        cool = [c for c in vivid if 150 <= c["hue"] <= 225 and c["sat"] >= 0.24]
        warm = [c for c in vivid if (0 <= c["hue"] <= 35 or 325 <= c["hue"] <= 360) and c["sat"] >= 0.28]
        primary_pool = cool or vivid or candidates
        primary_choice = max(primary_pool, key=lambda c: c["score"] + (0.55 if 165 <= c["hue"] <= 205 else 0))
        contrast_pool = [
            c for c in (warm or vivid or candidates)
            if _rgb_distance(c["rgb"], primary_choice["rgb"]) >= 34
        ] or warm or vivid or candidates
        secondary_choice = max(contrast_pool, key=lambda c: c["score"] + (0.4 if c in warm else 0))
        primary, _ = _boost_color(primary_choice["rgb"], min_sat=0.50, min_val=0.46, max_val=0.78)
        secondary = _deep_color(secondary_choice["rgb"], min_val=0.24, max_val=0.40)
        _glow_color, glow = _boost_color(secondary_choice["rgb"], min_sat=0.46, min_val=0.36, max_val=0.72)
        primary_glow = _rgba_from_rgb(primary_choice["rgb"], 0.24)
        gradient = (
            f"radial-gradient(circle at 76% 18%,{glow} 0%,rgba(16,24,40,0) 32%),"
            f"radial-gradient(circle at 10% 92%,{primary_glow} 0%,rgba(16,24,40,0) 30%),"
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
        return 55
    if n <= 18:
        return 48
    if n <= 24:
        return 40
    if n <= 32:
        return 33
    return 28


def _body_gap(title: str, *, has_extra: bool, has_subtitle: bool) -> int:
    """Smaller gap when the title is likely to wrap or extra rows are present,
    larger gap when the body block is short, so the block stays visually
    balanced without ever growing enough to crowd the footer."""
    n = len(title)
    base = 16 if n <= 16 else 14 if n <= 24 else 10 if n <= 34 else 8
    rows = (1 if has_extra else 0) + (1 if has_subtitle else 0)
    return max(7, base - rows * 2)


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
    badge_text: str | None = None,
) -> str:
    cover_uri, cover_bytes = image_data_uri(cover_url)
    gradient, accent = cover_palette(cover_bytes)
    badge_bg, badge_fg = _cover_badge_colors(cover_bytes, accent)
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
    title_html = f'<div class="title">{html.escape(title)}</div>'
    subtitle_html = f'<div class="subtitle">{html.escape(subtitle)}</div>' if best_since and subtitle else ""
    body_gap = _body_gap(title, has_extra=bool(extra), has_subtitle=bool(subtitle_html))
    mode_badge_text = badge_text if badge_text else ("COMBINED VERSIONS" if combined_versions else footer_right)
    mode_badge_html = f'<span class="mode-badge">{html.escape(mode_badge_text)}</span>' if mode_badge_text else ""
    tsm_logo_uri = _tsm_logo_data_uri()
    footer_left_html = html.escape(footer_left)
    if tsm_logo_uri:
        footer_left_html = (
            f'<span class="ftr-brand"><img class="tsm-logo" src="{tsm_logo_uri}" alt="Swifties Charts" />'
            f"<span>{html.escape(footer_left)}</span></span>"
        )
    body_class = "best-since" if best_since else "default"
    if best_since:
        css = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  width:920px;height:344px;
  background:{gradient};
  position:relative;overflow:hidden;color:#fff;
}}
body:before{{
  content:"";position:absolute;inset:0;
  background:
    linear-gradient(90deg,rgba(4,10,16,.62) 0%,rgba(4,10,16,.42) 49%,rgba(4,10,16,.08) 100%),
    radial-gradient(circle at 18% 85%,rgba(255,255,255,.20),rgba(255,255,255,0) 36%);
}}
.layout{{height:344px;position:relative;z-index:1}}
.cover-col{{
  position:absolute;right:12px;top:12px;width:321px;height:321px;
  overflow:hidden;border-radius:30px;
  box-shadow:0 24px 50px rgba(0,0,0,.42),0 0 0 1px rgba(255,255,255,.18);
}}
.cover,.cover-ph{{width:321px;height:321px;object-fit:cover;display:block}}
.cover-ph{{background:#172421}}
.info-col{{
  position:absolute;left:32px;top:24px;bottom:22px;width:529px;
  display:flex;flex-direction:column;
}}
.hdr-row{{display:flex;align-items:center;gap:12px;width:100%;flex-shrink:0}}
.body-col{{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;gap:{body_gap}px;margin-top:7px}}
.logo{{width:33px;height:33px;flex-shrink:0}}
.hdr-label{{
  color:rgba(255,255,255,.92);font-size:15px;font-weight:900;
  letter-spacing:.12em;text-transform:uppercase;
}}
.mode-badge{{
  margin-left:auto;color:rgba(255,255,255,.88);
  background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);
  border-radius:999px;padding:8px 13px;
  font-size:13px;font-weight:900;letter-spacing:.08em;text-transform:uppercase;
  white-space:nowrap;
}}
.title{{
  color:#fff;font-size:{_best_since_title_font_size(title)}px;font-weight:950;
  line-height:1.14;letter-spacing:0;flex-shrink:0;
  max-width:523px;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden;
  text-shadow:0 3px 18px rgba(0,0,0,.28);
  margin-bottom:-5px;
}}
.subtitle{{
  width:max-content;max-width:505px;
  color:{badge_fg};background:{badge_bg};
  font-size:15px;font-weight:900;border-radius:999px;
  padding:8px 15px;display:flex;align-items:center;gap:9px;
  text-transform:uppercase;
  box-shadow:0 12px 28px rgba(0,0,0,.20),0 0 0 1px rgba(255,255,255,.18);
}}
.subtitle:before{{
  content:"\\2605";display:inline-flex;align-items:center;justify-content:center;
  width:23px;height:23px;border-radius:999px;
  color:{badge_bg};background:{badge_fg};font-size:14px;line-height:1;
}}
.stats{{display:flex;gap:12px;margin-top:2px}}
.stat{{
  background:rgba(7,14,22,.58);border:1px solid rgba(255,255,255,.20);
  border-radius:18px;padding:10px 16px 9px;min-width:152px;
  box-shadow:0 12px 30px rgba(0,0,0,.18);
}}
.stat-lbl{{
  font-size:10px;font-weight:900;letter-spacing:.1em;
  text-transform:uppercase;color:rgba(255,255,255,.58);margin-bottom:4px;
}}
.stat-val{{font-size:28px;font-weight:950;color:#fff;line-height:1}}
.chg{{font-size:12px;font-weight:900;margin-top:5px;letter-spacing:.02em}}
.chg.new,.chg.re,.chg.up{{color:#7ee787}}
.chg.down{{color:#fca5a5}}
.chg.flat{{color:rgba(255,255,255,.52)}}
.extra{{
  color:rgba(255,255,255,.76);font-size:15px;font-weight:750;
  max-width:495px;margin-top:0;margin-bottom:2px;
}}
.ftr{{
  position:absolute;bottom:12px;left:35px;right:35px;z-index:2;
  display:flex;justify-content:space-between;
}}
.ftr-l,.ftr-r{{font-size:12px;color:rgba(255,255,255,.52);font-weight:700}}
.ftr-brand{{display:flex;align-items:center;gap:7px}}
.tsm-logo{{width:21px;height:21px;object-fit:contain;border-radius:4px}}
"""
    else:
        css = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  width:920px;height:344px;
  background:{gradient};
  position:relative;overflow:hidden;color:#fff;
}}
body:before{{
  content:"";position:absolute;inset:0;
  background:
    linear-gradient(90deg,rgba(4,10,16,.62) 0%,rgba(4,10,16,.42) 49%,rgba(4,10,16,.08) 100%),
    radial-gradient(circle at 18% 85%,rgba(255,255,255,.20),rgba(255,255,255,0) 36%);
}}
.layout{{height:344px;position:relative;z-index:1}}
.cover-col{{
  position:absolute;right:12px;top:12px;width:321px;height:321px;
  overflow:hidden;border-radius:30px;
  box-shadow:0 24px 50px rgba(0,0,0,.42),0 0 0 1px rgba(255,255,255,.18);
}}
.cover,.cover-ph{{width:321px;height:321px;object-fit:cover;display:block}}
.cover-ph{{background:#172421}}
.info-col{{
  position:absolute;left:32px;top:24px;bottom:22px;width:529px;
  display:flex;flex-direction:column;
}}
.hdr-row{{display:flex;align-items:center;gap:12px;width:100%;flex-shrink:0}}
.body-col{{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;gap:{body_gap}px;margin-top:7px}}
.logo{{width:33px;height:33px;flex-shrink:0}}
.hdr-label{{
  color:rgba(255,255,255,.92);font-size:15px;font-weight:900;
  letter-spacing:.12em;text-transform:uppercase;
}}
.mode-badge{{
  margin-left:auto;color:rgba(255,255,255,.88);
  background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);
  border-radius:999px;padding:8px 13px;
  font-size:13px;font-weight:900;letter-spacing:.08em;text-transform:uppercase;
  white-space:nowrap;
}}
.title{{
  color:#fff;font-size:{_best_since_title_font_size(title)}px;font-weight:950;
  line-height:1.14;letter-spacing:0;flex-shrink:0;
  max-width:523px;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden;
  text-shadow:0 3px 18px rgba(0,0,0,.28);
  margin-bottom:-5px;
}}
.stats{{display:flex;gap:12px;margin-top:2px}}
.stat{{
  background:rgba(7,14,22,.58);border:1px solid rgba(255,255,255,.20);
  border-radius:18px;padding:10px 16px 9px;min-width:152px;
  box-shadow:0 12px 30px rgba(0,0,0,.18);
}}
.stat-lbl{{
  font-size:10px;font-weight:900;letter-spacing:.1em;
  text-transform:uppercase;color:rgba(255,255,255,.58);margin-bottom:4px;
}}
.stat-val{{font-size:28px;font-weight:950;color:#fff;line-height:1}}
.chg{{font-size:12px;font-weight:900;margin-top:5px;letter-spacing:.02em}}
.chg.new,.chg.re,.chg.up{{color:#7ee787}}
.chg.down{{color:#fca5a5}}
.chg.flat{{color:rgba(255,255,255,.52)}}
.extra{{
  color:rgba(255,255,255,.76);font-size:15px;font-weight:750;
  max-width:495px;margin-top:0;margin-bottom:2px;
}}
.ftr{{
  position:absolute;bottom:12px;left:35px;right:35px;z-index:2;
  display:flex;justify-content:space-between;
}}
.ftr-l,.ftr-r{{font-size:12px;color:rgba(255,255,255,.52);font-weight:700}}
.ftr-brand{{display:flex;align-items:center;gap:7px}}
.tsm-logo{{width:21px;height:21px;object-fit:contain;border-radius:4px}}
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
    <div class="body-col">
      {title_html}
      {extra_html}
      {subtitle_html}
      <div class="stats">
        {''.join(stat_html)}
      </div>
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
            page = browser.new_page(viewport={"width": 920, "height": 344}, device_scale_factor=2)
            page.goto(f"file:///{tmp_path.as_posix()}", wait_until="load")
            page.locator("body").screenshot(path=str(output_path))
            add_export_frame(output_path, device_scale_factor=2)
            browser.close()
    finally:
        if not keep_html:
            tmp_path.unlink(missing_ok=True)
    return output_path
