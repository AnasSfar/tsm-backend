from __future__ import annotations

import html
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from .export_frame import add_export_frame
    from .song_card import SPOTIFY_SVG, _tsm_logo_data_uri, image_data_uri, slugify
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from comp.export_frame import add_export_frame  # type: ignore
    from comp.song_card import SPOTIFY_SVG, _tsm_logo_data_uri, image_data_uri, slugify  # type: ignore

try:
    from PIL import Image as PilImage
    _PIL = True
except ImportError:
    _PIL = False


def _title_font_size(title: str) -> int:
    n = len(title or "")
    if n <= 18:
        return 50
    if n <= 26:
        return 40
    if n <= 36:
        return 34
    if n <= 52:
        return 30
    return 27


def _rank_font_size(rank: str) -> int:
    return 48 if len(rank or "") <= 3 else 42


def _format_rank_badge(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.upper() in {"NEW", "RE"}:
        return raw.upper()
    if raw in {"0", "=", "(=)"}:
        return "(=)"
    if raw.startswith("+"):
        return f"(▲ {raw[1:].replace(',', '')})"
    if raw.startswith("-"):
        return f"(▼ {raw[1:].replace(',', '')})"
    return raw


def _stat_by_label(stats: list[dict], label: str) -> dict:
    wanted = label.casefold()
    for stat in stats:
        if str(stat.get("label") or "").casefold() == wanted:
            return stat
    return {}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.strip().lstrip("#")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _luma(rgb: tuple[int, int, int]) -> float:
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255


def _mix(left: tuple[int, int, int], right: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(int(left[i] * (1 - amount) + right[i] * amount) for i in range(3))


def _cover_palette(img_bytes: bytes) -> dict[str, str]:
    fallback = {
        "accent": "#f05e33",
        "accent_dark": "#d84c25",
        "accent_soft": "#ffe8e4",
        "outer_bg": "#eef3f1",
        "border": "#dde6e2",
    }
    if not _PIL or not img_bytes:
        return fallback
    try:
        from io import BytesIO
        import colorsys

        img = PilImage.open(BytesIO(img_bytes)).convert("RGB").resize((120, 120), PilImage.LANCZOS)
        colors = img.quantize(colors=18, method=PilImage.Quantize.MEDIANCUT).convert("RGB").getcolors(120 * 120) or []
        candidates = []
        for count, rgb in colors:
            r, g, b = rgb
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if (r > 242 and g > 242 and b > 242) or (r < 18 and g < 18 and b < 18):
                continue
            if s < 0.10 and (v < 0.25 or v > 0.82):
                continue
            readable = 1.0 - min(abs(v - 0.50) / 0.50, 1.0) * 0.35
            score = (count ** 0.5) * (0.35 + s) * (0.45 + v) * readable
            candidates.append((score, rgb, h, s, v))

        if not candidates:
            return fallback

        _score, rgb, h, s, v = max(candidates, key=lambda item: item[0])
        s = min(0.95, max(0.48, s * 1.25))
        v = min(0.78, max(0.42, v * 0.98))
        rb, gb, bb = colorsys.hsv_to_rgb(h, s, v)
        accent_rgb = (int(rb * 255), int(gb * 255), int(bb * 255))
        if _luma(accent_rgb) > 0.62:
            accent_rgb = _mix(accent_rgb, (0, 0, 0), 0.26)
        accent_dark = _mix(accent_rgb, (0, 0, 0), 0.16)
        accent_soft = _mix(accent_rgb, (255, 255, 255), 0.86)
        outer_bg = _mix(accent_rgb, (241, 245, 244), 0.90)
        border = _mix(accent_rgb, (220, 230, 226), 0.82)
        return {
            "accent": _rgb_to_hex(accent_rgb),
            "accent_dark": _rgb_to_hex(accent_dark),
            "accent_soft": _rgb_to_hex(accent_soft),
            "outer_bg": _rgb_to_hex(outer_bg),
            "border": _rgb_to_hex(border),
        }
    except Exception:
        return fallback


def _brand_html(handle: str) -> str:
    logo_uri = _tsm_logo_data_uri()
    if logo_uri:
        return (
            f'<span class="brand"><img class="tsm-logo" src="{logo_uri}" alt="Swifties Charts" />'
            f"<span>{html.escape(handle)}</span></span>"
        )
    return f'<span class="brand">{html.escape(handle)}</span>'


def render_chart_card(
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
    badge_text: str | None = None,
    layout: str = "wide",
) -> str:
    cover_uri, cover_bytes = image_data_uri(cover_url)
    palette = _cover_palette(cover_bytes)
    art_html = f'<img class="cover" src="{cover_uri}" />' if cover_uri else '<div class="cover cover-ph"></div>'

    rank = _stat_by_label(stats, "Rank")
    streams = _stat_by_label(stats, "Streams")
    rank_value = str(rank.get("value") or "-")
    rank_badge = _format_rank_badge(str(rank.get("badge") or ""))
    rank_badge_class = html.escape(str(rank.get("badge_class") or "flat"))
    streams_value = str(streams.get("value") or "-")
    streams_delta = str(streams.get("delta") or "").strip()
    streams_delta_class = html.escape(str(streams.get("delta_class") or streams.get("badge_class") or "flat"))
    streams_pct = str(streams.get("badge") or "").strip()
    streams_pct_class = html.escape(str(streams.get("badge_class") or "flat"))

    extra_html = f'<div class="extra">{html.escape(extra)}</div>' if extra else ""
    rank_badge_html = (
        f'<span class="rank-badge {rank_badge_class}">{html.escape(rank_badge)}</span>'
        if rank_badge
        else ""
    )
    delta_html = (
        f'<span class="stream-delta {streams_delta_class}">{html.escape(streams_delta)}</span>'
        if streams_delta
        else ""
    )
    pct_html = (
        f'<span class="stream-pct {streams_pct_class}">{html.escape(streams_pct)}</span>'
        if streams_pct
        else ""
    )
    pill_text = badge_text or eyebrow
    footer_date = html.escape(footer_right)

    if layout == "square":
        css = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  width:1080px;height:1080px;
  background:{palette["outer_bg"]};
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  color:#111827;overflow:hidden;
  display:flex;align-items:center;justify-content:center;
}}
.card{{
  position:relative;width:984px;height:984px;
  background:#fff;border:1px solid {palette["border"]};border-radius:42px;
  box-shadow:0 28px 70px rgba(17,24,39,.10);
  overflow:hidden;
}}
.pill{{
  position:absolute;left:50%;top:56px;transform:translateX(-50%);
  display:inline-flex;align-items:center;gap:12px;
  background:{palette["accent"]};color:#fff;border-radius:999px;
  padding:16px 28px 15px;font-size:24px;font-weight:950;
  letter-spacing:.04em;text-transform:uppercase;line-height:1;white-space:nowrap;
}}
.pill .logo{{width:26px;height:26px;flex-shrink:0}}
.cover{{
  position:absolute;left:50%;top:164px;width:340px;height:340px;
  transform:translateX(-50%);
  object-fit:cover;border-radius:34px;
  box-shadow:0 28px 52px rgba(17,24,39,.18);
}}
.cover-ph{{background:#d9e4de}}
.song-line{{
  position:absolute;left:84px;right:84px;top:536px;
  display:flex;align-items:center;justify-content:center;gap:18px;
  text-align:center;min-width:0;
}}
.rank{{
  color:{palette["accent"]};font-size:56px;font-weight:950;
  line-height:1;letter-spacing:0;flex-shrink:0;
}}
.rank-badge{{
  color:{palette["accent"]};font-size:30px;font-weight:950;line-height:1;
  flex-shrink:0;
}}
.rank-badge.new{{
  font-size:24px;border-radius:999px;padding:10px 15px;
  background:{palette["accent_soft"]};color:{palette["accent_dark"]};
}}
.rank-badge.re{{
  font-size:24px;border-radius:999px;padding:10px 15px;
  background:#e8f1ff;color:#2563eb;
}}
.rank-badge.up,.rank-badge.down{{color:{palette["accent"]}}}
.title{{
  color:#101827;font-size:{min(_title_font_size(title) + 2, 46)}px;font-weight:950;
  line-height:1.04;letter-spacing:0;overflow:hidden;min-width:0;
  white-space:normal;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;text-align:left;
}}
.subtitle{{
  position:absolute;left:72px;right:72px;top:636px;
  color:{palette["accent"]};font-size:28px;font-weight:820;line-height:1.1;
  text-align:center;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.extra{{
  position:absolute;left:72px;right:72px;top:666px;
  color:#6b7280;font-size:20px;font-weight:780;text-transform:uppercase;
  letter-spacing:.06em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  text-align:center;
}}
.metric-row{{
  position:absolute;left:72px;right:72px;top:718px;
  display:flex;align-items:center;justify-content:center;white-space:nowrap;
}}
.streams{{
  color:{palette["accent"]};font-size:78px;font-weight:950;line-height:1;
  letter-spacing:.01em;
}}
.streams-label{{
  position:absolute;left:72px;right:72px;top:800px;
  color:#717989;font-size:22px;font-weight:950;letter-spacing:.18em;
  text-transform:uppercase;
  text-align:center;
}}
.change-row{{
  position:absolute;left:72px;right:72px;top:848px;
  display:flex;align-items:center;justify-content:center;gap:22px;
}}
.stream-delta{{
  font-size:34px;font-weight:950;line-height:1;color:#ef4444;
}}
.stream-delta.up{{color:#16a34a}}
.stream-delta.flat{{color:#717989}}
.stream-pct{{
  font-size:25px;font-weight:850;color:#ef4444;background:#fff0f0;
  border-radius:999px;padding:13px 17px;line-height:1;
}}
.stream-pct.up{{color:#16a34a;background:#ecfdf3}}
.stream-pct.flat{{color:#717989;background:#f3f4f6}}
.footer{{
  position:absolute;left:72px;right:72px;bottom:56px;
  display:flex;align-items:center;justify-content:space-between;
  color:#7a8594;font-size:24px;font-weight:780;
}}
.brand{{display:flex;align-items:center;gap:13px}}
.tsm-logo{{
  width:42px;height:42px;border-radius:11px;object-fit:contain;
  background:#111827;padding:7px;
}}
.date{{font-weight:840;color:#8b95a1}}
"""
        return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
  <div class="card">
    <div class="pill">{logo_svg}<span>{html.escape(pill_text)}</span></div>
    {art_html}
    <div class="song-line">
      <span class="rank">{html.escape(rank_value)}</span>
      {rank_badge_html}
      <span class="title">{html.escape(title)}</span>
    </div>
    <div class="subtitle">{html.escape(subtitle)}</div>
    {extra_html}
    <div class="metric-row">
      <span class="streams">{html.escape(streams_value)}</span>
    </div>
    <div class="streams-label">Streams</div>
    <div class="change-row">
      {delta_html}
      {pct_html}
    </div>
    <div class="footer">
      {_brand_html(footer_left)}
      <span class="date">{footer_date}</span>
    </div>
  </div>
</body></html>"""

    css = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  width:920px;height:344px;
  background:{palette["outer_bg"]};
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  color:#111827;overflow:hidden;
  display:flex;align-items:center;justify-content:center;
}}
.card{{
  position:relative;width:888px;height:312px;
  background:#fff;border:1px solid {palette["border"]};border-radius:24px;
  box-shadow:0 18px 38px rgba(17,24,39,.08);
}}
.cover{{
  position:absolute;left:30px;top:54px;width:190px;height:190px;
  object-fit:cover;border-radius:19px;
  box-shadow:0 16px 28px rgba(17,24,39,.18);
}}
.cover-ph{{background:#d9e4de}}
.content{{position:absolute;left:272px;right:36px;top:30px;bottom:30px}}
.pill{{
  display:inline-flex;align-items:center;gap:8px;
  background:{palette["accent"]};color:#fff;border-radius:999px;
  padding:9px 17px 8px;font-size:15px;font-weight:950;
  letter-spacing:.04em;text-transform:uppercase;line-height:1;
}}
.pill .logo{{width:17px;height:17px;flex-shrink:0}}
.title-row{{
  margin-top:25px;display:flex;align-items:flex-start;gap:13px;
  min-width:0;
}}
.rank{{
  color:{palette["accent"]};font-size:{_rank_font_size(rank_value)}px;font-weight:950;
  letter-spacing:0;line-height:1;flex-shrink:0;
}}
.rank-badge{{
  color:{palette["accent"]};font-size:27px;font-weight:950;line-height:1;
  flex-shrink:0;margin-top:12px;
}}
.rank-badge.new{{
  font-size:19px;border-radius:999px;padding:7px 10px;
  background:{palette["accent_soft"]};color:{palette["accent_dark"]};align-self:flex-start;margin-top:8px;
}}
.rank-badge.re{{
  font-size:19px;border-radius:999px;padding:7px 10px;
  background:#e8f1ff;color:#2563eb;align-self:flex-start;margin-top:8px;
}}
.rank-badge.up{{color:{palette["accent"]}}}
.rank-badge.down{{color:{palette["accent"]}}}
.title{{
  color:#101827;font-size:{_title_font_size(title)}px;font-weight:950;
  line-height:1.02;letter-spacing:0;min-width:0;overflow:hidden;
  white-space:normal;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;
}}
.subtitle{{
  margin-top:10px;color:{palette["accent"]};font-size:17px;font-weight:780;
  line-height:1.1;
  max-width:570px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.extra{{
  margin-top:8px;color:#6b7280;font-size:14px;font-weight:750;
  text-transform:uppercase;letter-spacing:.05em;
}}
.metric-row{{
  position:absolute;left:0;right:0;bottom:22px;
  display:flex;align-items:baseline;gap:16px;white-space:nowrap;
}}
.streams{{
  color:{palette["accent"]};font-size:46px;font-weight:950;line-height:1;
  letter-spacing:.01em;
}}
.streams-label{{
  color:#717989;font-size:15px;font-weight:950;letter-spacing:.13em;
  text-transform:uppercase;
}}
.stream-delta{{
  font-size:22px;font-weight:950;line-height:1;color:#ef4444;
}}
.stream-delta.up{{color:#16a34a}}
.stream-delta.flat{{color:#717989}}
.stream-pct{{
  font-size:16px;font-weight:850;color:#ef4444;background:#fff0f0;
  border-radius:999px;padding:8px 11px;line-height:1;
}}
.stream-pct.up{{color:#16a34a;background:#ecfdf3}}
.stream-pct.flat{{color:#717989;background:#f3f4f6}}
.footer{{
  position:absolute;left:30px;right:34px;bottom:13px;
  display:flex;align-items:center;justify-content:space-between;
  color:#7a8594;font-size:13px;font-weight:760;
}}
.brand{{display:flex;align-items:center;gap:8px}}
.tsm-logo{{
  width:24px;height:24px;border-radius:6px;object-fit:contain;
  background:#111827;padding:4px;
}}
.date{{font-weight:800;color:#8b95a1}}
"""

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
  <div class="card">
    {art_html}
    <div class="content">
      <div class="pill">{logo_svg}<span>{html.escape(pill_text)}</span></div>
      <div class="title-row">
        <span class="rank">{html.escape(rank_value)}</span>
        {rank_badge_html}
        <span class="title">{html.escape(title)}</span>
      </div>
      <div class="subtitle">{html.escape(subtitle)}</div>
      {extra_html}
      <div class="metric-row">
        <span class="streams">{html.escape(streams_value)}</span>
        <span class="streams-label">Streams</span>
        {delta_html}
        {pct_html}
      </div>
    </div>
    <div class="footer">
      {_brand_html(footer_left)}
      <span class="date">{footer_date}</span>
    </div>
  </div>
</body></html>"""


def write_chart_card_png(
    html_text: str,
    output_path: Path,
    tmp_path: Path,
    *,
    keep_html: bool = False,
    width: int = 920,
    height: int = 344,
    export_frame: bool = False,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(html_text, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
            page.goto(f"file:///{tmp_path.as_posix()}", wait_until="load")
            page.locator("body").screenshot(path=str(output_path))
            if export_frame:
                add_export_frame(output_path, device_scale_factor=2)
            browser.close()
    finally:
        if not keep_html:
            tmp_path.unlink(missing_ok=True)
    return output_path
