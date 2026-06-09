from __future__ import annotations

import base64
import html
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

try:
    from PIL import Image as PilImage
    _PIL = True
except ImportError:
    _PIL = False


SPOTIFY_SVG = """<svg class="logo" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/></svg>"""


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


def render_feature_card(
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
) -> str:
    cover_uri, cover_bytes = image_data_uri(cover_url)
    gradient, _accent = cover_palette(cover_bytes)
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
    css = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  width:800px;height:299px;
  background:{gradient};
  position:relative;overflow:hidden;
}}
.layout{{display:flex;height:299px}}
.cover-col{{width:334px;flex-shrink:0;overflow:hidden;border-radius:10px}}
.cover{{width:334px;height:299px;object-fit:cover;display:block}}
.cover-ph{{width:334px;height:299px;background:#1a2a26}}
.info-col{{
  flex:1;display:flex;flex-direction:column;
  justify-content:center;padding:24px 28px 20px 20px;gap:14px;
}}
.hdr-row{{display:flex;align-items:center;gap:8px}}
.logo{{width:26px;height:26px;flex-shrink:0}}
.hdr-label{{
  color:#fff;font-size:12px;font-weight:800;
  letter-spacing:.12em;text-transform:uppercase;
}}
.title{{
  color:#fff;font-size:52px;font-weight:900;
  line-height:1.1;letter-spacing:0;
  white-space:nowrap;overflow:visible;
}}
.subtitle{{color:rgba(255,255,255,.6);font-size:12px;font-weight:500}}
.stats{{display:flex;gap:10px;margin-top:4px}}
.stat{{
  background:rgba(255,255,255,.93);border-radius:18px;
  padding:10px 18px 8px;min-width:90px;
}}
.stat-lbl{{
  font-size:9px;font-weight:800;letter-spacing:.1em;
  text-transform:uppercase;color:#667085;margin-bottom:2px;
}}
.stat-val{{font-size:22px;font-weight:900;color:#0b1f44;line-height:1}}
.chg{{font-size:10px;font-weight:800;margin-top:4px;letter-spacing:.02em}}
.chg.new{{color:#16a34a}}
.chg.re{{color:#0ea5e9}}
.chg.up{{color:#16a34a}}
.chg.down{{color:#dc2626}}
.chg.flat{{color:#9ca3af}}
.extra{{color:rgba(255,255,255,.55);font-size:11px;font-weight:500}}
.ftr{{
  position:absolute;bottom:10px;left:346px;right:20px;
  display:flex;justify-content:space-between;
}}
.ftr-l,.ftr-r{{font-size:10px;color:rgba(255,255,255,.4);font-weight:600}}
"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
<div class="layout">
  <div class="cover-col">{art_html}</div>
  <div class="info-col">
    <div class="hdr-row">
      {logo_svg}
      <span class="hdr-label">{html.escape(eyebrow)}</span>
    </div>
    <div class="title">{html.escape(title)}</div>
    <div class="subtitle">{html.escape(subtitle)}</div>
    <div class="stats">
      {''.join(stat_html)}
    </div>
    {extra_html}
  </div>
</div>
<div class="ftr">
  <span class="ftr-l">{html.escape(footer_left)}</span>
  <span class="ftr-r">{html.escape(footer_right)}</span>
</div>
<script>
(function(){{
  var el=document.querySelector('.title');
  var parent=el.parentElement;
  var size=52;
  el.style.fontSize=size+'px';
  while(el.scrollWidth>parent.offsetWidth&&size>20){{
    size-=1;
    el.style.fontSize=size+'px';
  }}
}})();
</script>
</body></html>"""


def write_feature_card_png(html_text: str, output_path: Path, tmp_path: Path) -> Path:
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
        tmp_path.unlink(missing_ok=True)
    return output_path
