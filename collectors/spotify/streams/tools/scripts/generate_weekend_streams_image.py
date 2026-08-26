#!/usr/bin/env python3
"""
Generate the daily one-card Spotify streams recap ("Masthead" card).

Posted every day (weekday runs pass --force-weekday), not only on weekends —
the file / lock / function names keep the historical "weekend" prefix.

The card combines:
  - Taylor Swift total daily streams for the stats date + daily/weekly change
  - top 5 albums / eras by daily streams
  - top 5 songs by daily streams
  - artist monthly listeners (+ world rank) and, when collected, followers

Design: editorial "Masthead" — mirrors the shipped Top Eras post header (the
shared `headers/top_eras/` mosaic + ghost "STREAMS" wordmark + 3px rule in
the photo's dominant colour) over a "ledger" table body (Inter + Big Shoulders
for the rank numerals).

Theme follows the owner's day-of-week rule (see
`comp.tables_image.masthead_theme_for_date`): weekday posts (Mon-Fri) render
the clean **white** ledger theme, weekend posts (Sat/Sun) render the **dark**
one. Pass `masthead_theme=` to `build_html` / `generate` to force a theme.

Output:
  snapshots/spotify_streams/YYYY/MM/YYYY-MM-DD/weekend_streams_image.png
"""
from __future__ import annotations

import html
import json
import sys
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

import generate_albums_image
import generate_streams_image

from comp.tables_image import masthead_theme_for_date


ROOT = generate_streams_image.ROOT
REPO_ROOT = generate_streams_image.REPO_ROOT
FRONTEND_HEADERS_DIR = REPO_ROOT.parent / "tsm-frontend" / "frontend" / "public" / "headers"
ARTIST_PATH = REPO_ROOT / "db" / "discography" / "artist.json"
HANDLE = generate_streams_image.HANDLE
SPOTIFY_SVG = generate_streams_image.SPOTIFY_SVG

TOP_N = 5
EXCLUDED_ERA_ALBUMS = {"Standalone & Extras"}


def fmt_num(value) -> str:
    if value is None:
        return "-"
    return f"{int(value):,}".replace(",", " ")


def fmt_signed(value: int | None) -> str:
    if value is None:
        return "-"
    if value > 0:
        return f"+{fmt_num(value)}"
    if value < 0:
        return f"-{fmt_num(abs(value))}"
    return "="


def fmt_delta(current: int | None, previous: int | None) -> tuple[str, str, str]:
    if current is None or previous is None or previous == 0:
        return "-", "", "neutral"
    delta = current - previous
    pct = delta / previous * 100
    pct_text = f"{pct:+.1f}%"
    if pct_text == "-0.0%":
        pct_text = "+0.0%"
    if delta > 0:
        return fmt_signed(delta), pct_text, "pos"
    if delta < 0:
        return fmt_signed(delta), pct_text, "neg"
    return "=", pct_text, "neutral"


def rank_change(rank: int, previous_rank) -> tuple[str, str]:
    if previous_rank is None:
        return "NEW", "chg-new"
    delta = int(previous_rank) - rank
    if delta > 0:
        return f"&#9650; {delta}", "chg-up"
    if delta < 0:
        return f"&#9660; {abs(delta)}", "chg-dn"
    return "=", "chg-eq"


def load_public_history_rows(target_date: str) -> list[dict]:
    """Load the exact per-track history exported for the public site."""
    path = generate_streams_image.update_streams_dir(target_date) / "site_history.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Public site history is missing for {target_date}: {path}. "
            "Run export_for_web before generating the weekend card."
        )
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid public site history payload for {target_date}: {path}")
    rows: list[dict] = []
    for track_id, value in payload.items():
        if not isinstance(value, dict):
            continue
        rows.append({
            "track_id": track_id,
            "streams": int(value.get("s") or value.get("streams") or 0),
            "daily_streams": int(value.get("d") or value.get("daily_streams") or 0),
        })
    return rows


def build_totals(today_rows: list[dict], yesterday_rows: list[dict], week_rows: list[dict]) -> dict:
    return {
        "daily": sum(int(row.get("daily_streams") or 0) for row in today_rows),
        "total": sum(int(row.get("streams") or 0) for row in today_rows),
        "yesterday_daily": sum(int(row.get("daily_streams") or 0) for row in yesterday_rows),
        "week_daily": sum(int(row.get("daily_streams") or 0) for row in week_rows),
    }


def exclude_non_era_albums(track_map: dict[str, dict]) -> dict[str, dict]:
    excluded = {album.casefold() for album in EXCLUDED_ERA_ALBUMS}
    return {
        track_id: info
        for track_id, info in track_map.items()
        if str(info.get("album") or "").strip().casefold() not in excluded
    }


CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{
  width:1000px;
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  color:#101828;
  background:
    radial-gradient(circle at 18% 10%,var(--theme-glow),transparent 30%),
    radial-gradient(circle at 86% 18%,var(--theme-glow-2),transparent 32%),
    linear-gradient(180deg,var(--theme-wash) 0%,var(--theme-faint) 62%,#f8fafb 100%);
}
.container{overflow:hidden}
.hdr{
  min-height:150px;
  padding:24px 30px;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:24px;
}
.brand{display:flex;align-items:center;gap:18px;min-width:0}
.hdr-logo{width:64px;height:64px;flex-shrink:0}
.hdr-title{color:#fff;font-size:27px;font-weight:850;letter-spacing:0}
.hdr-sub{color:rgba(255,255,255,.88);font-size:15px;margin-top:6px}
.total-panel{
  min-width:390px;
  padding:16px 18px;
  border:1px solid rgba(255,255,255,.26);
  background:rgba(8,14,24,.38);
  backdrop-filter:blur(8px);
}
.total-label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:rgba(255,255,255,.72);font-weight:800}
.total-value{font-size:34px;line-height:1.05;color:#fff;font-weight:900;margin-top:5px}
.total-meta{display:grid;grid-template-columns:1fr 1fr 1.25fr;gap:14px;margin-top:10px}
.meta-k{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:rgba(255,255,255,.6);font-weight:800}
.meta-v{font-size:12px;color:#fff;font-weight:750;margin-top:3px;white-space:nowrap}
.sections{padding:18px 20px 0}
.section{background:rgba(255,255,255,.76);border-top:1px solid rgba(16,24,40,.06)}
.section+.section{margin-top:16px}
.section-title{
  display:flex;align-items:center;justify-content:space-between;
  padding:12px 16px;
  background:rgba(241,245,246,.96);
  border-bottom:1px solid rgba(16,24,40,.07);
}
.section-title h2{font-size:17px;color:#101828;font-weight:900;letter-spacing:0}
.section-title span{font-size:12px;color:#667085;font-weight:750}
.heads,.row{
  display:grid;
  grid-template-columns:48px 44px minmax(240px,1fr) 130px 122px 122px 128px;
  column-gap:10px;
  align-items:center;
}
.heads{
  padding:8px 16px;
  background:rgba(248,250,251,.9);
  border-bottom:1px solid rgba(16,24,40,.06);
}
.heads span{
  font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;color:#667085;
}
.right{text-align:right}
.row{
  min-height:54px;
  padding:7px 16px;
  border-bottom:1px solid rgba(16,24,40,.05);
  background:rgba(255,255,255,.78);
}
.row:nth-child(odd){background:rgba(248,250,251,.82)}
.row.first{
  background:linear-gradient(90deg,#fff6cf 0%,#fffdf5 42%,rgba(255,255,255,.9) 100%);
  border-left:3px solid #ebc44c;
}
.rank{font-size:18px;font-weight:900;color:#0b1f44;text-align:center;letter-spacing:0}
.chg{display:inline-flex;align-items:center;justify-content:center;justify-self:center;min-width:38px;padding:4px 8px;border-radius:20px;font-size:12px;font-weight:900;line-height:1;letter-spacing:.01em;text-align:center}
.chg-up{background:#dcfce7;color:#15803d}.chg-dn{background:#fee2e2;color:#b91c1c}.chg-eq{background:#f1f5f9;color:#64748b}.neutral{color:#667085}.chg-new{background:#dbeafe;color:#1d4ed8;font-size:11px}
.entity{display:flex;align-items:center;gap:11px;min-width:0}
.entity>div{min-width:0}
.art{width:44px;height:44px;border-radius:7px;object-fit:cover;flex-shrink:0;box-shadow:0 2px 8px rgba(0,0,0,.13)}
.art-ph{width:44px;height:44px;border-radius:7px;background:#dde3ea;flex-shrink:0}
.name{font-size:14px;font-weight:800;color:#101828;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.num{font-size:12.5px;color:#344054;font-weight:650;text-align:right}
.num.pos,.meta-v.pos,.pos{color:#067647}
.num.neg,.meta-v.neg,.neg{color:#b42318}
.num.neutral,.meta-v.neutral{color:#667085}
.daily{font-size:13px;color:#101828;font-weight:900}
.delta{display:flex;flex-direction:column;align-items:flex-end;gap:1px}
.delta-main{font-size:12px;font-weight:800}
.delta-pct{font-size:10px;font-weight:650;opacity:.82}
.ftr{
  padding:13px 20px;
  display:flex;justify-content:space-between;align-items:center;
  background:rgba(241,245,246,.96);
  border-top:1px solid rgba(16,24,40,.07);
}
.ftr-handle{font-size:13px;color:#1db954;font-weight:800}
.ftr-date{font-size:13px;color:#667085;font-weight:650}
"""


def _norm_album_key(name: str) -> str:
    return (name or "").strip().lower()


def _frontend_header_for_album(album: str) -> Path | None:
    key = _norm_album_key(album)
    if not key or not FRONTEND_HEADERS_DIR.exists():
        return None

    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = FRONTEND_HEADERS_DIR / f"{key}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    raw = (color or "#1db954").lstrip("#")
    if len(raw) != 6:
        return (29, 185, 84)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _mix(color: str, other: str, amount: float) -> str:
    r1, g1, b1 = _hex_to_rgb(color)
    r2, g2, b2 = _hex_to_rgb(other)
    amount = max(0.0, min(1.0, amount))
    r = round(r1 * (1 - amount) + r2 * amount)
    g = round(g1 * (1 - amount) + g2 * amount)
    b = round(b1 * (1 - amount) + b2 * amount)
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgba(color: str, alpha: float) -> str:
    r, g, b = _hex_to_rgb(color)
    return f"rgba({r},{g},{b},{alpha:.2f})"


def _theme_vars_from_color(color: str) -> str:
    wash = _mix(color, "#ffffff", 0.78)
    faint = _mix(color, "#f8fafb", 0.90)
    glow = _rgba(color, 0.18)
    glow_2 = _rgba(_mix(color, "#101828", 0.18), 0.14)
    return (
        f"--theme-wash:{wash};"
        f"--theme-faint:{faint};"
        f"--theme-glow:{glow};"
        f"--theme-glow-2:{glow_2};"
    )


def _header_style(top_album: str | None = None) -> tuple[str, str]:
    header_img = _frontend_header_for_album(top_album or "") or generate_streams_image._pick_header_image()
    handle_color = "#1db954"
    if not header_img:
        return 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"', handle_color

    handle_color = generate_streams_image._dominant_color(header_img)
    img_url = header_img.as_posix()
    style = (
        "style=\"background-image: linear-gradient(rgba(0,0,0,.48),rgba(0,0,0,.48)),"
        f"url('file:///{img_url}'); background-size:cover; background-position:center;\""
    )
    return style, handle_color


def _masthead_header_style(masthead_theme: str = "dark") -> tuple[str, str]:
    """Header background for the Masthead card: the shared "Top Eras" pool image
    (`headers/top_eras/`) — the same photo as the standalone Top Eras post
    (`generate_albums_image`), not an era-specific frontend header. Returns
    (style_attr, accent_hex); the directional overlay is dark or light-washed
    to match `masthead_theme` (mirrors comp.tables_image's masthead)."""
    overlay = _MH_THEME.get(masthead_theme, _MH_THEME["dark"])["mh-head-overlay"]
    try:
        header_img = generate_albums_image.pick_header_image(
            generate_albums_image._headers_dir_for_top_eras()
        )
    except Exception:
        header_img = None
    if not header_img:
        return 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"', "#1db954"

    accent = generate_albums_image.get_dominant_color(header_img)
    img_url = header_img.as_posix()
    style = (
        'style="background-image:'
        f"{overlay},"
        f"url('file:///{img_url}'); background-size:cover; background-position:center;\""
    )
    return style, accent


def _img_html(src: str, cls: str = "art") -> str:
    if not src:
        return '<div class="art-ph"></div>'
    return f'<img class="{cls}" src="{html.escape(src, quote=True)}" />'


def _row_html(kind: str, rows: list[dict], image_cache: dict[str, str], cover_map: dict, track_album_map: dict) -> str:
    out = []
    for idx, row in enumerate(rows):
        rank = idx + 1
        row_cls = "row first" if rank == 1 else "row"
        chg_text, chg_cls = rank_change(rank, row.get("prev_rank"))

        if kind == "song":
            title = row.get("title") or ""
            daily = row.get("daily_streams")
            yest = row.get("daily_streams_yesterday")
            week = row.get("daily_streams_last_week")
            total = row.get("streams")
            cover_url = generate_streams_image.get_cover_url(row, cover_map, track_album_map)
            cover = image_cache.get(cover_url, cover_url) if cover_url else ""
        else:
            title = row.get("album") or ""
            daily = row.get("daily_streams")
            yest = row.get("yest_daily")
            week = row.get("week_daily")
            total = row.get("streams")
            cover_url = row.get("cover_url") or ""
            cover = image_cache.get(cover_url, cover_url) if cover_url else ""

        daily_delta, daily_pct, daily_cls = fmt_delta(daily, yest)
        week_delta, week_pct, week_cls = fmt_delta(daily, week)

        out.append(f"""<div class="{row_cls}">
  <div class="rank">#{rank}</div>
  <div class="chg {chg_cls}">{chg_text}</div>
  <div class="entity">
    {_img_html(cover)}
    <div>
      <div class="name">{html.escape(title)}</div>
    </div>
  </div>
  <div class="num daily">+{fmt_num(daily)}</div>
  <div class="num {daily_cls}"><div class="delta"><span class="delta-main">{daily_delta}</span>{f'<span class="delta-pct">{daily_pct}</span>' if daily_pct else ''}</div></div>
  <div class="num {week_cls}"><div class="delta"><span class="delta-main">{week_delta}</span>{f'<span class="delta-pct">{week_pct}</span>' if week_pct else ''}</div></div>
  <div class="num">{fmt_num(total)}</div>
</div>""")
    return "\n".join(out)


def _section_html(title: str, subtitle: str, rows_html: str, entity_label: str) -> str:
    subtitle_html = f"<span>{html.escape(subtitle)}</span>" if subtitle else ""
    return f"""<section class="section">
  <div class="section-title"><h2>{html.escape(title)}</h2>{subtitle_html}</div>
  <div class="heads">
    <span>#</span>
    <span>+/-</span>
    <span>{html.escape(entity_label)}</span>
    <span class="right">Daily</span>
    <span class="right">Daily Chg</span>
    <span class="right">Weekly Chg</span>
    <span class="right">Total</span>
  </div>
  {rows_html}
</section>"""


# ---------------------------------------------------------------------------
# "Masthead" renderer — the daily combined recap card.
#
# Everything above (CSS, _row_html, _section_html, _header_style,
# _theme_vars_from_color, SPOTIFY_SVG) stays as-is: post_throwback_thread.py
# and post_debut_releases.py import those helpers and must not change. Only
# build_html() / generate() below switched to the Masthead style.
# ---------------------------------------------------------------------------

MASTHEAD_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Big+Shoulders+Display:wght@800;900&display=swap">'
)

_SANS = "Inter,-apple-system,'Helvetica Neue',Arial,sans-serif"
_DISPLAY = '"Big Shoulders Display",Impact,sans-serif'

# Ledger palettes — the dark set mirrors comp.tables_image._LEDGER_THEME_TOKENS
# ["dark"] so the card reads as the same family as the shipped Top Eras / Top
# Songs post; the light set is the clean white theme shared by all masthead
# cards on weekday posts. Every colour in MASTHEAD_CSS is a `var(--mh-*)` with
# the dark value as its fallback, so a bare render still looks right.
_MH_THEME = {
    "dark": {
        "mh-bg": "#131417",
        "mh-panel": "#0f1013",
        "mh-border": "#232428",
        "mh-text": "#eef0f2",
        "mh-strong": "#ffffff",
        "mh-muted": "#9aa0ab",
        "mh-faint": "#5a5e66",
        "mh-row-alt": "rgba(255,255,255,.022)",
        "mh-pos": "#6fcf9a",
        "mh-neg": "#e08a7d",
        "mh-neutral": "#7d828b",
        "mh-ghost": "rgba(255,255,255,.40)",
        "mh-head-bg": "#14181f",
        "mh-head-text": "#ffffff",
        "mh-logo-bg": "#ffffff",
        "mh-logo-fill": "#121417",
        "mh-head-overlay": (
            "linear-gradient(100deg,rgba(9,10,13,.88) 0%,"
            "rgba(9,10,13,.58) 45%,rgba(9,10,13,.74) 100%)"
        ),
    },
    "light": {
        "mh-bg": "#ffffff",
        "mh-panel": "#f4f6f8",
        "mh-border": "#e6e9ee",
        "mh-text": "#1a1d24",
        "mh-strong": "#0b0d12",
        "mh-muted": "#667085",
        "mh-faint": "#98a2b3",
        "mh-row-alt": "rgba(16,24,40,.028)",
        "mh-pos": "#067647",
        "mh-neg": "#b42318",
        "mh-neutral": "#667085",
        "mh-ghost": "rgba(16,24,40,.12)",
        "mh-head-bg": "#e9edf1",
        "mh-head-text": "#0b0d12",
        "mh-logo-bg": "#12141a",
        "mh-logo-fill": "#ffffff",
        "mh-head-overlay": (
            "linear-gradient(100deg,rgba(250,250,251,.90) 0%,"
            "rgba(250,250,251,.60) 45%,rgba(250,250,251,.80) 100%)"
        ),
    },
}


def _mh_theme_vars(masthead_theme: str) -> str:
    theme = _MH_THEME.get(masthead_theme, _MH_THEME["dark"])
    return "".join(f"--{k}:{v};" for k, v in theme.items() if k != "mh-head-overlay")


MASTHEAD_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{
  width:1000px;
  background:var(--mh-bg,#131417);
  color:var(--mh-text,#eef0f2);
  font-family:__SANS__;
}
.mh{overflow:hidden}
.mh-head{position:relative;height:150px;overflow:hidden;background:var(--mh-head-bg,#14181f)}
.mh-ghost{
  position:absolute;right:2px;bottom:-14px;z-index:2;
  font-family:__DISPLAY__;font-weight:900;
  font-size:104px;line-height:1;letter-spacing:.005em;white-space:nowrap;
  color:var(--mh-ghost,rgba(255,255,255,.40));pointer-events:none;
}
.mh-head-in{position:absolute;z-index:3;left:26px;top:0;bottom:0;display:flex;align-items:center;gap:15px;color:var(--mh-head-text,#fff)}
.mh-logo{
  width:42px;height:42px;border-radius:50%;background:var(--mh-logo-bg,#fff);flex-shrink:0;
  display:flex;align-items:center;justify-content:center;box-shadow:0 2px 10px rgba(0,0,0,.4);
}
.mh-logo .hdr-logo{width:22px;height:22px}
.mh-logo .hdr-logo path{fill:var(--mh-logo-fill,#121417)}
.mh-kicker{font-size:11px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;opacity:.88}
.mh-head-in h1{font-size:28px;font-weight:700;margin-top:6px;line-height:1.05;letter-spacing:-.01em}
.mh-head-in p{margin-top:5px;font-size:13px;font-weight:500;opacity:.82}
.mh-rule{position:absolute;left:0;right:0;bottom:0;height:3px;background:var(--accent,#1db954);z-index:4}

.mh-band{display:flex;align-items:stretch;border-bottom:1px solid var(--mh-border,#232428);background:var(--mh-panel,#0f1013)}
.mh-band-main{flex:1;padding:18px 26px 16px;min-width:0}
.mh-lbl{font-size:10px;font-weight:700;letter-spacing:.11em;text-transform:uppercase;color:var(--mh-muted,#9aa0ab);display:block}
.mh-big{font-size:40px;font-weight:800;line-height:1.05;letter-spacing:-.015em;font-variant-numeric:tabular-nums;margin:4px 0 11px;color:var(--mh-strong,#fff)}
.mh-chg{display:flex;gap:22px;flex-wrap:wrap}
.mh-chg .v{font-size:13.5px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:3px;white-space:nowrap;display:block;color:var(--mh-text,#eef0f2)}
.mh-chg .v.pos{color:var(--mh-pos,#6fcf9a)}.mh-chg .v.neg{color:var(--mh-neg,#e08a7d)}.mh-chg .v.neutral{color:var(--mh-neutral,#7d828b)}
.mh-band-side{flex:1;display:flex;align-items:stretch;justify-content:center;border-left:1px solid var(--mh-border,#232428)}
.mh-astat{
  min-width:174px;padding:16px 22px;
  display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;
}
.mh-astat+.mh-astat{border-left:1px solid var(--mh-border,#232428)}
.mh-astat .v{font-size:21px;font-weight:800;font-variant-numeric:tabular-nums;margin-top:5px;color:var(--mh-strong,#fff)}
.mh-astat .sub{font-size:11.5px;font-weight:600;margin-top:4px}
.pos{color:var(--mh-pos,#6fcf9a)}.neg{color:var(--mh-neg,#e08a7d)}.neutral{color:var(--mh-neutral,#7d828b)}

.mh-sec-h{display:flex;align-items:baseline;justify-content:center;gap:10px;padding:15px 26px 0}
.mh-sec-h h2{font-size:16px;font-weight:800;letter-spacing:.01em;color:var(--mh-strong,#fff)}
.mh-sec-h span{font-size:10px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--mh-muted,#9aa0ab)}
.mh-thd,.mh-tr{
  display:grid;
  grid-template-columns:40px 44px minmax(200px,1fr) 128px 124px 124px 138px;
  column-gap:12px;align-items:center;
}
.mh-thd{padding:8px 26px;margin-top:8px;background:var(--mh-panel,#0f1013);border-top:1px solid var(--mh-border,#232428);border-bottom:1px solid var(--mh-border,#232428)}
.mh-thd span{font-size:9.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--mh-strong,#fff);display:flex;align-items:center}
.mh-thd .r{justify-content:flex-end}
.mh-tr .n{text-align:right}
.mh-tr{padding:9px 26px;border-bottom:1px solid var(--mh-border,#232428)}
.mh-tr.odd{background:var(--mh-row-alt,rgba(255,255,255,.022))}
.mh-tr.first{background:linear-gradient(90deg,var(--accent-wash,rgba(29,185,84,.16)),transparent 62%)}
.mh-pos{font-family:__DISPLAY__;font-weight:900;font-size:24px;letter-spacing:-.02em;text-align:center;color:var(--mh-text,#eef0f2)}
.mh-tr.first .mh-pos{color:var(--accent-ink,#1db954)}
.mh-mv{font-size:10px;font-weight:700;letter-spacing:.03em;color:var(--mh-faint,#5a5e66);text-align:center}
.mh-mv.up{color:var(--mh-pos,#6fcf9a)}.mh-mv.dn{color:var(--mh-neg,#e08a7d)}.mh-mv.new{color:#5b8fd6}
.mh-ent{display:flex;align-items:center;gap:12px;min-width:0}
.mh-art{width:40px;height:40px;border-radius:6px;object-fit:cover;flex-shrink:0;box-shadow:0 2px 8px rgba(0,0,0,.35)}
.mh-art-ph{width:40px;height:40px;border-radius:6px;background:var(--mh-border,#232428);flex-shrink:0}
.mh-name{
  font-size:14px;font-weight:700;line-height:1.18;color:var(--mh-text,#eef0f2);
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
}
.mh-n{font-size:12.5px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--mh-muted,#9aa0ab)}
.mh-n.big{font-size:13.5px;font-weight:800;color:var(--mh-text,#eef0f2)}
.mh-n b{display:block;font-weight:700}
.mh-n i{display:block;font-style:normal;font-size:10px;font-weight:600;opacity:.8}
.mh-n.pos,.mh-n.pos b,.mh-n.pos i{color:var(--mh-pos,#6fcf9a)}
.mh-n.neg,.mh-n.neg b,.mh-n.neg i{color:var(--mh-neg,#e08a7d)}
.mh-n.neutral,.mh-n.neutral b,.mh-n.neutral i{color:var(--mh-neutral,#7d828b)}
.mh-ftr{
  display:flex;justify-content:space-between;align-items:center;
  padding:13px 26px;background:var(--mh-panel,#0f1013);border-top:1px solid var(--mh-border,#232428);
  font-size:12px;font-weight:600;color:var(--mh-muted,#8b9099);
}
.mh-ftr b{color:var(--accent-ink,#1db954)}
""".replace("__SANS__", _SANS).replace("__DISPLAY__", _DISPLAY)


def _mh_move(rank: int, prev_rank) -> tuple[str, str]:
    if prev_rank is None:
        return "NEW", "new"
    delta = int(prev_rank) - int(rank)
    if delta > 0:
        return f"&#9650; {delta}", "up"
    if delta < 0:
        return f"&#9660; {abs(delta)}", "dn"
    return "&ndash;", "eq"


def _mh_rows_html(kind: str, rows: list[dict], image_cache: dict[str, str],
                  cover_map: dict, track_album_map: dict) -> str:
    out = []
    for idx, row in enumerate(rows):
        rank = idx + 1
        row_cls = "mh-tr first" if rank == 1 else ("mh-tr odd" if idx % 2 else "mh-tr")
        mv_txt, mv_cls = _mh_move(rank, row.get("prev_rank"))

        if kind == "song":
            title = row.get("title") or ""
            daily = row.get("daily_streams")
            yest = row.get("daily_streams_yesterday")
            week = row.get("daily_streams_last_week")
            cover_url = generate_streams_image.get_cover_url(row, cover_map, track_album_map)
        else:
            title = row.get("album") or ""
            daily = row.get("daily_streams")
            yest = row.get("yest_daily")
            week = row.get("week_daily")
            cover_url = row.get("cover_url") or ""
        total = row.get("streams")
        cover = image_cache.get(cover_url, cover_url) if cover_url else ""

        d_main, d_pct, d_cls = fmt_delta(daily, yest)
        w_main, w_pct, w_cls = fmt_delta(daily, week)
        art = (
            f'<img class="mh-art" src="{html.escape(cover, quote=True)}" />'
            if cover else '<div class="mh-art-ph"></div>'
        )
        out.append(f"""<div class="{row_cls}">
  <div class="mh-pos">{rank}</div>
  <div class="mh-mv {mv_cls}">{mv_txt}</div>
  <div class="mh-ent">{art}<span class="mh-name">{html.escape(title)}</span></div>
  <div class="mh-n big n">+{fmt_num(daily)}</div>
  <div class="mh-n n {d_cls}"><b>{d_main}</b>{f'<i>{d_pct}</i>' if d_pct else ''}</div>
  <div class="mh-n n {w_cls}"><b>{w_main}</b>{f'<i>{w_pct}</i>' if w_pct else ''}</div>
  <div class="mh-n n">{fmt_num(total)}</div>
</div>""")
    return "\n".join(out)


def _mh_section_html(entity_label: str, rows_html: str) -> str:
    return f"""<div class="mh-thd">
    <span>#</span><span>+/-</span><span>{html.escape(entity_label)}</span>
    <span class="r">Daily</span><span class="r">&Delta; Day</span><span class="r">&Delta; Week</span><span class="r">Total</span>
  </div>
  {rows_html}"""


def _load_artist_stats() -> dict:
    """Read db/discography/artist.json (refreshed each run before finalize).
    Followers keys are absent until artist_metadata.py has scraped them once —
    the card simply omits the Followers cell while that is the case."""
    try:
        data = json.loads(ARTIST_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    def _int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return {
        "listeners": _int(data.get("monthly_listeners")),
        "listeners_prev": _int(data.get("previous_monthly_listeners")),
        "rank": _int(data.get("monthly_rank")),
        "rank_prev": _int(data.get("previous_monthly_rank")),
        "followers": _int(data.get("followers")),
        "followers_prev": _int(data.get("previous_followers")),
    }


def _mh_artist_stats_html(stats: dict) -> str:
    """Artist stat cells for the top band (right of the daily-total block).
    Monthly listeners whenever present; Followers only when artist.json carries
    it — otherwise the cell is simply absent (no placeholder)."""
    if not stats or stats.get("listeners") is None:
        return ""

    listeners = stats["listeners"]
    listeners_prev = stats.get("listeners_prev")
    rank = stats.get("rank")

    if listeners_prev:
        num, pct, cls = fmt_delta(listeners, listeners_prev)
        piece = num + (f" &middot; {pct}" if pct else "")
    else:
        cls, piece = "neutral", ""
    if rank:
        rmove = ""
        rank_prev = stats.get("rank_prev")
        if rank_prev:
            rd = int(rank_prev) - int(rank)
            rmove = " &#9650;" if rd > 0 else " &#9660;" if rd < 0 else ""
        piece = (piece + " &middot; " if piece else "") + f"#{rank} world{rmove}"
    listeners_sub = f'<span class="sub {cls}">{piece}</span>' if piece else ""

    cells = [
        '<div class="mh-astat"><span class="mh-lbl">Monthly listeners</span>'
        f'<span class="v">{fmt_num(listeners)}</span>{listeners_sub}</div>'
    ]

    followers = stats.get("followers")
    if followers is not None:
        followers_prev = stats.get("followers_prev")
        followers_sub = ""
        if followers_prev:
            num, _pct, cls = fmt_delta(followers, followers_prev)
            followers_sub = f'<span class="sub {cls}">{num} today</span>'
        cells.append(
            '<div class="mh-astat"><span class="mh-lbl">Followers</span>'
            f'<span class="v">{fmt_num(followers)}</span>{followers_sub}</div>'
        )

    return "".join(cells)


def build_html(
    *,
    target_date: str,
    totals: dict,
    album_rows: list[dict],
    song_rows: list[dict],
    album_cache: dict[str, str],
    song_cache: dict[str, str],
    song_cover_map: dict,
    song_track_album_map: dict,
    artist_stats: dict | None = None,
    masthead_theme: str | None = None,
) -> str:
    # Weekday posts (Mon-Fri) -> white ledger theme; weekend posts -> dark.
    if masthead_theme is None:
        masthead_theme = masthead_theme_for_date(target_date)
    theme_vars = _mh_theme_vars(masthead_theme)

    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    hdr_style, accent = _masthead_header_style(masthead_theme)
    if masthead_theme == "light":
        # Lightened accent-ink is invisible on white — darken it instead, and
        # dial the row-1 wash back so it does not overpower the white body.
        accent_ink = _mix(accent, "#101828", 0.34)
        accent_wash = _rgba(accent, 0.14)
    else:
        accent_ink = _mix(accent, "#ffffff", 0.55)
        accent_wash = _rgba(accent, 0.24)
    d_main, d_pct, d_cls = fmt_delta(totals["daily"], totals["yesterday_daily"])
    w_main, w_pct, w_cls = fmt_delta(totals["daily"], totals["week_daily"])

    albums_html = _mh_rows_html("album", album_rows, album_cache, {}, {})
    songs_html = _mh_rows_html("song", song_rows, song_cache, song_cover_map, song_track_album_map)
    astats_html = _mh_artist_stats_html(artist_stats or {})

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
{MASTHEAD_FONTS}
<style>{MASTHEAD_CSS}</style></head>
<body style="{theme_vars}--accent:{accent};--accent-ink:{accent_ink};--accent-wash:{accent_wash}">
<div class="mh">
  <div class="mh-head" {hdr_style}>
    <div class="mh-ghost">STREAMS</div>
    <div class="mh-head-in">
      <div class="mh-logo">{SPOTIFY_SVG}</div>
      <div>
        <div class="mh-kicker">Taylor Swift &middot; Spotify</div>
        <h1>Streams Recap</h1>
        <p>{date_fmt}</p>
      </div>
    </div>
    <div class="mh-rule"></div>
  </div>
  <div class="mh-band">
    <div class="mh-band-main">
      <span class="mh-lbl">Total daily streams</span>
      <span class="mh-big">+{fmt_num(totals["daily"])}</span>
      <div class="mh-chg">
        <div><span class="mh-lbl">Daily change</span><span class="v {d_cls}">{d_main}{f' &middot; {d_pct}' if d_pct else ''}</span></div>
        <div><span class="mh-lbl">Weekly change</span><span class="v {w_cls}">{w_main}{f' &middot; {w_pct}' if w_pct else ''}</span></div>
        <div><span class="mh-lbl">All-time</span><span class="v">{fmt_num(totals["total"])}</span></div>
      </div>
    </div>
    {f'<div class="mh-band-side">{astats_html}</div>' if astats_html else ''}
  </div>
  <div class="mh-sec-h"><h2>Top Eras</h2><span>combined</span></div>
  {_mh_section_html("Era", albums_html)}
  <div class="mh-sec-h"><h2>Top Songs</h2></div>
  {_mh_section_html("Song", songs_html)}
  <div class="mh-ftr"><b>{HANDLE}</b><span>{date_fmt}</span></div>
</div>
</body></html>"""


def generate(
    target_date: str | None = None,
    *,
    top_n: int = TOP_N,
    masthead_theme: str | None = None,
) -> Path:
    if target_date is None:
        target_date = generate_streams_image.get_latest_date()
    if top_n <= 0:
        raise ValueError("top_n must be > 0")

    theme = masthead_theme or masthead_theme_for_date(target_date)
    print(f"[weekend_streams_image] Date: {target_date} (theme: {theme})")

    song_db = generate_streams_image.load_song_db()
    song_cover_map = generate_streams_image.load_covers()
    song_track_album_map = generate_streams_image.load_track_album_map()
    song_today, song_yest, song_week = generate_streams_image.load_history(target_date)
    if not song_today:
        raise ValueError(f"No streams data for {target_date}")

    song_rows = generate_streams_image.build_top_n(
        song_today,
        song_yest,
        song_week,
        song_db,
        top_n,
    )

    album_covers = generate_albums_image.load_covers()
    album_track_map = generate_albums_image.load_album_track_map()
    album_track_map = exclude_non_era_albums(album_track_map)
    album_today, album_yest, album_week = generate_albums_image.load_history(target_date)
    album_rows = generate_albums_image.build_album_rows(
        album_today,
        album_yest,
        album_week,
        album_track_map,
        album_covers,
    )[:top_n]

    target_day = date_cls.fromisoformat(target_date)
    public_today = load_public_history_rows(target_date)
    public_yest = load_public_history_rows(str(target_day - timedelta(days=1)))
    public_week = load_public_history_rows(str(target_day - timedelta(days=7)))
    totals = build_totals(public_today, public_yest, public_week)

    print("[weekend_streams_image] Downloading covers...")
    album_cache = generate_albums_image.prefetch_covers(album_rows)
    song_cache = generate_streams_image.prefetch_images(
        song_rows,
        song_cover_map,
        song_track_album_map,
    )
    print(f"[weekend_streams_image] covers={len(album_cache) + len(song_cache)}")

    artist_stats = _load_artist_stats()
    print(
        "[weekend_streams_image] artist: "
        f"listeners={artist_stats.get('listeners')} rank={artist_stats.get('rank')} "
        f"followers={artist_stats.get('followers')}"
    )

    html_text = build_html(
        target_date=target_date,
        totals=totals,
        album_rows=album_rows,
        song_rows=song_rows,
        album_cache=album_cache,
        song_cache=song_cache,
        song_cover_map=song_cover_map,
        song_track_album_map=song_track_album_map,
        artist_stats=artist_stats,
        masthead_theme=theme,
    )

    out_dir = generate_streams_image.update_streams_dir(target_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "weekend_streams_image.png"
    tmp_html = out_dir / "_weekend_streams_tmp.html"
    tmp_html.write_text(html_text, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1000, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()

    print(f"[weekend_streams_image] Image generated: {out_path}")
    return out_path


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    theme_arg: str | None = None
    for flag in ("--light", "--dark"):
        if flag in args:
            theme_arg = flag.lstrip("-")
            args.remove(flag)
    date_arg = args[0] if args else None
    generate(date_arg, masthead_theme=theme_arg)
