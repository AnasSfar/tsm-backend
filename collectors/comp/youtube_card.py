"""YouTube video card — dedicated template (not song_card).

YouTube video titles are full sentences (e.g. "Taylor Swift Performance -
The Icon Sessions at the Grammy Museum"), unlike the short song titles
song_card.py is tuned for, so this gets its own layout instead of overloading
song_card's rarely-used "default" style. Shared, non-visual helpers
(thumbnail fetch/base64, palette extraction from the thumbnail, slugify, the
Playwright HTML->PNG renderer, the TSM footer logo) are reused from
song_card.py rather than duplicated.
"""
from __future__ import annotations

import html

from .song_card import (
    _tsm_logo_data_uri,
    cover_palette,
    image_data_uri,
    slugify,
    write_song_card_png,
)

__all__ = ["render_youtube_card", "slugify", "write_song_card_png"]

YOUTUBE_LOGO_SVG = (
    '<svg class="logo" viewBox="0 0 28 20" xmlns="http://www.w3.org/2000/svg">'
    '<path fill="#FF0000" d="M27.4 3.1c-.3-1.2-1.3-2.1-2.5-2.4C22.7 0 14 0 14 0S5.3 0 3.1.7'
    'C1.9 1 .9 1.9.6 3.1 0 5.3 0 10 0 10s0 4.7.6 6.9c.3 1.2 1.3 2.1 2.5 2.4C5.3 20 14 20 14 20'
    's8.7 0 10.9-.7c1.2-.3 2.2-1.2 2.5-2.4.6-2.2.6-6.9.6-6.9s0-4.7-.6-6.9z"/>'
    '<path fill="#fff" d="M11 14.5l7-4.5-7-4.5z"/></svg>'
)


def _title_font_size(title: str) -> int:
    n = len(title)
    if n <= 20:
        return 56
    if n <= 32:
        return 46
    if n <= 45:
        return 41
    if n <= 60:
        return 35
    if n <= 80:
        return 30
    return 25


def render_youtube_card(
    *,
    title: str,
    stat_label: str,
    stat_value: str,
    cover_url: str | None,
    footer_left: str,
    badge_text: str = "",
    release_date_text: str = "",
) -> str:
    cover_uri, cover_bytes = image_data_uri(cover_url)
    gradient, _accent = cover_palette(cover_bytes)
    art_html = f'<img class="cover" src="{cover_uri}" />' if cover_uri else '<div class="cover-ph"></div>'
    badge_html = f'<span class="mode-badge">{html.escape(badge_text)}</span>' if badge_text else ""
    release_html = (
        f'<div class="release">Released {html.escape(release_date_text)}</div>' if release_date_text else ""
    )
    tsm_logo_uri = _tsm_logo_data_uri()
    footer_left_html = html.escape(footer_left)
    if tsm_logo_uri:
        footer_left_html = (
            f'<span class="ftr-brand"><img class="tsm-logo" src="{tsm_logo_uri}" alt="Swifties Charts" />'
            f"<span>{html.escape(footer_left)}</span></span>"
        )

    css = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  width:920px;height:480px;
  background:{gradient};
  position:relative;overflow:hidden;color:#fff;
}}
body:before{{
  content:"";position:absolute;inset:0;
  background:
    linear-gradient(90deg,rgba(4,10,16,.62) 0%,rgba(4,10,16,.42) 49%,rgba(4,10,16,.08) 100%),
    radial-gradient(circle at 18% 85%,rgba(255,255,255,.20),rgba(255,255,255,0) 36%);
}}
.layout{{height:480px;position:relative;z-index:1}}
.cover-col{{
  position:absolute;right:20px;top:40px;width:400px;height:400px;
  overflow:hidden;border-radius:30px;
  box-shadow:0 24px 50px rgba(0,0,0,.42),0 0 0 1px rgba(255,255,255,.18);
}}
.cover,.cover-ph{{width:400px;height:400px;object-fit:cover;display:block}}
.cover-ph{{background:#172421}}
.info-col{{
  position:absolute;left:32px;top:36px;bottom:42px;width:450px;
  display:flex;flex-direction:column;
}}
.hdr-row{{display:flex;align-items:center;gap:12px;width:100%;flex-shrink:0}}
.body-wrap{{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center}}
.body{{display:flex;flex-direction:column;gap:18px}}
.logo{{width:33px;height:20px;flex-shrink:0}}
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
  color:#fff;font-size:{_title_font_size(title)}px;font-weight:950;
  line-height:1.18;letter-spacing:0;flex-shrink:0;
  max-width:442px;display:-webkit-box;-webkit-line-clamp:4;
  -webkit-box-orient:vertical;overflow:hidden;
  text-shadow:0 3px 18px rgba(0,0,0,.28);
}}
.stat{{
  background:rgba(7,14,22,.58);border:1px solid rgba(255,255,255,.20);
  border-radius:22px;padding:26px 26px 24px;width:100%;
  box-shadow:0 12px 30px rgba(0,0,0,.18);
  text-align:center;
}}
.stat-lbl{{
  font-size:14px;font-weight:900;letter-spacing:.1em;
  text-transform:uppercase;color:rgba(255,255,255,.58);margin-bottom:10px;
}}
.stat-val{{font-size:46px;font-weight:950;color:#fff;line-height:1;white-space:nowrap}}
.release{{
  font-size:14px;font-weight:600;color:rgba(255,255,255,.55);
  letter-spacing:.01em;
}}
.ftr{{
  position:absolute;bottom:16px;left:35px;right:35px;z-index:2;
  display:flex;justify-content:space-between;
}}
.ftr-l{{font-size:12px;color:rgba(255,255,255,.52);font-weight:700}}
.ftr-brand{{display:flex;align-items:center;gap:7px}}
.tsm-logo{{width:21px;height:21px;object-fit:contain;border-radius:4px}}
"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
<div class="layout">
  <div class="cover-col">{art_html}</div>
  <div class="info-col">
    <div class="hdr-row">
      {YOUTUBE_LOGO_SVG}
      <span class="hdr-label">YouTube</span>
      {badge_html}
    </div>
    <div class="body-wrap">
      <div class="body">
        <div class="title">{html.escape(title)}</div>
        <div class="stat">
          <div class="stat-lbl">{html.escape(stat_label)}</div>
          <div class="stat-val">{html.escape(stat_value)}</div>
        </div>
        {release_html}
      </div>
    </div>
  </div>
</div>
<div class="ftr">
  <span class="ftr-l">{footer_left_html}</span>
</div>
</body></html>"""
