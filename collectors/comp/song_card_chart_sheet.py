"""Chart Sheet song card — the real replacement for song_card.py's best_since
style, used by both production callers:

  - post_best_day_since_twitter.py -> kicker "Best day since {date}" (or
    "Best day ever" / "Biggest day of the year..."), with a dimmed callback
    bar + gap marker when there's a specific previous-record date to show.
  - post_weekend_song_gainers.py -> kicker "Weekend Gainer", no callback bar.

Background is the track's own cover art, scaled past the frame and blurred
with a plain CSS filter (no Pillow-side blur pass needed) — Spotify's own
Now Playing screen does the same thing. Card is always 1080x594 CSS px:
both real callers show a kicker row, so there's no shorter "no kicker"
variant in production (song_card_default.py's plain style isn't posted
anywhere today — see collector-comp's CONTEXTE.md).

Design reference: the "Chart Sheet Bloom" Claude Artifact worked out with
the owner (2026-08-26).
"""
from __future__ import annotations

import html
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from .song_card import image_data_uri, slugify, _tsm_logo_data_uri
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from comp.song_card import image_data_uri, slugify, _tsm_logo_data_uri  # type: ignore

__all__ = [
    "render_chart_sheet_card",
    "write_chart_sheet_card_png",
    "format_change_html",
    "slugify",
    "CARD_WIDTH",
    "CARD_HEIGHT",
]

CARD_WIDTH = 1080
CARD_HEIGHT = 594


def _title_font_size(title: str) -> int:
    """Bucketed to fit the ~680px single-line header slot (thumb + gap +
    date badge eat into the 1080px card width). Titles past ~42 chars would
    need to shrink below a legible size to stay on one line, so past that
    point the CSS instead wraps to 2 lines (line-clamp) at a font size sized
    so ~90+ chars still fits without truncating — matches the header row's
    104px thumb height, which stays the tallest element either way."""
    n = len(title)
    if n <= 10:
        return 44
    if n <= 16:
        return 40
    if n <= 22:
        return 36
    if n <= 30:
        return 30
    if n <= 42:
        return 24
    return 22


def _bar_col_html(bar: dict) -> str:
    if bar.get("type") == "gap":
        return (
            '<div class="sc-gap-col">'
            '<div class="sc-bar-wrap sc-gap-wrap"><span class="sc-gap-dots">&middot;&middot;&middot;</span></div>'
            '<div class="sc-bar-date">&nbsp;</div>'
            "</div>"
        )
    col_class = "sc-bar-col historical" if bar.get("dimmed") else "sc-bar-col"
    bar_class = "sc-bar today" if bar.get("today") else "sc-bar"
    date_class = "sc-bar-date today" if bar.get("today") else "sc-bar-date"
    height_pct = max(1.0, min(100.0, float(bar.get("height_pct") or 1)))
    return f"""<div class="{col_class}">
          <div class="sc-bar-wrap"><div class="{bar_class}" style="height:{height_pct:.1f}%"><span class="sc-bar-val">{html.escape(str(bar.get("value_label") or ""))}</span></div></div>
          <div class="{date_class}">{html.escape(str(bar.get("date_label") or ""))}</div>
        </div>"""


def format_change_html(daily_pct_text: str, daily_class: str, weekly_pct_text: str | None = None, weekly_class: str = "flat") -> str:
    """Build the "Change Daily / Weekly" field's inner HTML. Text values are
    pre-formatted by the caller (e.g. "+6.2%") and escaped here; when
    ``weekly_pct_text`` is unavailable the field just shows the daily figure."""
    daily_html = f'<span class="{html.escape(daily_class)}">{html.escape(daily_pct_text)}</span>'
    if not weekly_pct_text:
        return daily_html
    weekly_html = f'<span class="{html.escape(weekly_class)}">{html.escape(weekly_pct_text)}</span>'
    return f'{daily_html}<span class="sep"> / </span>{weekly_html}'


def render_chart_sheet_card(
    *,
    title: str,
    album: str,
    date_text: str,
    kicker_text: str,
    bars: list[dict],
    daily_value_text: str,
    daily_class: str,
    change_text: str,
    total_value_text: str,
    cover_url: str | None,
    footer_left: str,
    footer_right: str,
) -> str:
    """Build the Chart Sheet card HTML. All numeric/text formatting is the
    caller's job (this stays a pure template, like song_card.render_song_card).

    ``bars`` is an ordered list of column dicts, each either:
      {"type": "bar", "date_label": str, "value_label": str,
       "height_pct": float (1-100), "today": bool, "dimmed": bool}
    or a gap-marker column: {"type": "gap"}.
    """
    cover_uri, _cover_bytes = image_data_uri(cover_url)
    cover_style = f'background-image:url("{cover_uri}")' if cover_uri else "background:#22262c"
    logo_uri = _tsm_logo_data_uri()
    logo_html = (
        f'<span class="sc-logo" style="background-image:url(&quot;{logo_uri}&quot;)" role="img" aria-label="Swifties Charts"></span>'
        if logo_uri
        else ""
    )
    bars_html = "\n          ".join(_bar_col_html(bar) for bar in bars)
    title_font_size = _title_font_size(title)

    css = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif}}
.sheet-card{{
  position:relative;width:{CARD_WIDTH}px;height:{CARD_HEIGHT}px;overflow:hidden;color:#EAF2F1;
}}
.sc-bloom-src{{
  position:absolute;inset:-90px;filter:blur(52px) saturate(1.1) brightness(.62);
  {cover_style};background-size:cover;background-position:center;
}}
.sc-scrim{{position:absolute;inset:0;background:
  radial-gradient(circle at 50% 42%,rgba(6,8,8,0) 0%,rgba(6,8,8,.38) 78%),
  linear-gradient(180deg,rgba(6,8,8,.30) 0%,rgba(6,8,8,.20) 40%,rgba(6,8,8,.46) 100%);
}}
.sc-inner{{position:relative;height:100%;padding:48px 56px;display:flex;flex-direction:column}}
.sc-hdr{{display:flex;align-items:center;gap:24px}}
.sc-thumb{{width:104px;height:104px;border-radius:15px;flex-shrink:0;border:1px solid rgba(255,255,255,.22);box-shadow:0 12px 28px rgba(0,0,0,.4);{cover_style};background-size:cover;background-position:center}}
.sc-hdr-text{{min-width:0}}
.sc-title{{
  font-weight:800;font-size:{title_font_size}px;line-height:1.14;letter-spacing:-.01em;
  text-shadow:0 2px 14px rgba(0,0,0,.4);max-width:620px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
}}
.sc-subtitle{{margin-top:8px;font-size:18px;color:rgba(234,242,241,.7);font-weight:500}}
.sc-date{{margin-left:auto;font-size:16px;color:rgba(234,242,241,.7);flex-shrink:0}}
.sc-kicker{{display:flex;align-items:center;gap:10px;margin-top:26px;font-size:18px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:#F0B36A}}
.sc-kicker:before{{content:"\\2605";font-size:17px}}
.sc-bars{{display:flex;align-items:stretch;gap:9px;margin-top:20px}}
.sc-bar-col{{flex:1;display:flex;flex-direction:column}}
.sc-bar-col.historical{{opacity:.5}}
.sc-bar-wrap{{height:180px;display:flex;align-items:flex-end;border-bottom:1px solid rgba(255,255,255,.24)}}
.sc-bar{{position:relative;width:100%;border-radius:4px 4px 0 0;background:rgba(255,255,255,.16)}}
.sc-bar.today{{background:linear-gradient(180deg,#F0B36A 0%,#C97A3C 100%)}}
.sc-bar-val{{
  position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  white-space:nowrap;font-size:10.5px;font-weight:600;
  color:rgba(255,255,255,.88);text-shadow:0 1px 2px rgba(0,0,0,.4);
}}
.sc-bar-date{{margin-top:8px;text-align:center;font-size:9.5px;color:rgba(234,242,241,.5)}}
.sc-bar-date.today{{color:#F0B36A;font-weight:600}}
.sc-gap-col{{flex:0 0 34px}}
.sc-gap-wrap{{justify-content:center;align-items:center}}
.sc-gap-dots{{font-size:16px;font-weight:700;color:rgba(234,242,241,.4);letter-spacing:2px}}
.sc-readout{{display:flex;margin-top:32px}}
.sc-field{{flex:1;padding-right:26px;border-right:1px solid rgba(255,255,255,.2)}}
.sc-field:last-child{{border-right:none;padding-right:0}}
.sc-field:not(:first-child){{padding-left:26px}}
.sc-field-lbl{{font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;color:rgba(234,242,241,.62);font-weight:700}}
.sc-field-val{{margin-top:8px;font-size:32px;font-weight:600;color:#EAF2F1;font-variant-numeric:tabular-nums;text-shadow:0 2px 10px rgba(0,0,0,.35)}}
.sc-field-val .up{{color:#F0B36A}}
.sc-field-val .down{{color:#fca5a5}}
.sc-field-val .sep{{color:rgba(234,242,241,.4);font-weight:500;padding:0 4px}}
.sc-footer{{margin-top:20px;display:flex;justify-content:space-between;align-items:center;font-size:12.5px;color:rgba(234,242,241,.55)}}
.sc-brand{{display:flex;align-items:center;gap:8px}}
.sc-logo{{width:20px;height:20px;background-size:contain;background-repeat:no-repeat;background-position:center}}
"""

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
<div class="sheet-card">
  <div class="sc-bloom-src"></div>
  <div class="sc-scrim"></div>
  <div class="sc-inner">
    <div class="sc-hdr">
      <div class="sc-thumb"></div>
      <div class="sc-hdr-text">
        <div class="sc-title">{html.escape(title)}</div>
        <div class="sc-subtitle">{html.escape(album)}</div>
      </div>
      <div class="sc-date">{html.escape(date_text)}</div>
    </div>
    <div class="sc-kicker">{html.escape(kicker_text)}</div>
    <div class="sc-bars">
      {bars_html}
    </div>
    <div class="sc-readout">
      <div class="sc-field"><div class="sc-field-lbl">Daily Streams</div><div class="sc-field-val"><span class="{html.escape(daily_class)}">{html.escape(daily_value_text)}</span></div></div>
      <div class="sc-field"><div class="sc-field-lbl">Change Daily / Weekly</div><div class="sc-field-val">{change_text}</div></div>
      <div class="sc-field"><div class="sc-field-lbl">Total Streams</div><div class="sc-field-val">{html.escape(total_value_text)}</div></div>
    </div>
    <div class="sc-footer">
      <span class="sc-brand">{logo_html}<span>{html.escape(footer_left)}</span></span>
      <span>{html.escape(footer_right)}</span>
    </div>
  </div>
</div>
</body></html>"""


def write_chart_sheet_card_png(html_text: str, output_path: Path, tmp_path: Path, *, keep_html: bool = False) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(html_text, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": CARD_WIDTH, "height": CARD_HEIGHT}, device_scale_factor=2)
            page.goto(f"file:///{tmp_path.as_posix()}", wait_until="load")
            page.locator(".sheet-card").screenshot(path=str(output_path))
            browser.close()
    finally:
        if not keep_html:
            tmp_path.unlink(missing_ok=True)
    return output_path
