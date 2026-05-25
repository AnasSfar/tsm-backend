#!/usr/bin/env python3
"""
Generate the weekend one-card Spotify streams update.

The card combines:
  - Taylor Swift total daily streams for the stats date
  - top 5 albums / eras by daily streams
  - top 5 songs by daily streams

Output:
  collectors/spotify/streams/history/YYYY/MM/YYYY-MM-DD/weekend_streams_image.png
"""
from __future__ import annotations

import html
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import generate_albums_image
import generate_streams_image


ROOT = generate_streams_image.ROOT
REPO_ROOT = generate_streams_image.REPO_ROOT
FRONTEND_HEADERS_DIR = REPO_ROOT.parent / "tsm-frontend" / "frontend" / "public" / "headers"
HANDLE = generate_streams_image.HANDLE
SPOTIFY_SVG = generate_streams_image.SPOTIFY_SVG

TOP_N = 5


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
        return f"+{delta}", "chg-up"
    if delta < 0:
        return f"-{abs(delta)}", "chg-dn"
    return "=", "chg-eq"


def build_totals(today_rows: list[dict], yesterday_rows: list[dict], week_rows: list[dict]) -> dict:
    return {
        "daily": sum(int(row.get("daily_streams") or 0) for row in today_rows),
        "total": sum(int(row.get("streams") or 0) for row in today_rows),
        "yesterday_daily": sum(int(row.get("daily_streams") or 0) for row in yesterday_rows),
        "week_daily": sum(int(row.get("daily_streams") or 0) for row in week_rows),
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
.chg{font-size:11px;font-weight:900;text-align:center}
.chg-up{color:#067647}.chg-dn{color:#b42318}.chg-eq,.neutral{color:#667085}.chg-new{color:#299fc5}
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
  <div class="chg {chg_cls}">{html.escape(chg_text)}</div>
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
) -> str:
    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    top_album = album_rows[0].get("album") if album_rows else ""
    hdr_style, handle_color = _header_style(top_album)
    body_style = _theme_vars_from_color(handle_color)
    daily_delta, daily_pct, daily_cls = fmt_delta(totals["daily"], totals["yesterday_daily"])
    week_delta, week_pct, week_cls = fmt_delta(totals["daily"], totals["week_daily"])

    albums_html = _row_html("album", album_rows, album_cache, {}, {})
    songs_html = _row_html("song", song_rows, song_cache, song_cover_map, song_track_album_map)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body style="{body_style}">
<div class="container">
  <div class="hdr" {hdr_style}>
    <div class="brand">
      {SPOTIFY_SVG}
      <div>
        <div class="hdr-title">Taylor Swift &middot; Spotify Counter</div>
        <div class="hdr-sub">{date_fmt}</div>
      </div>
    </div>
    <div class="total-panel">
      <div class="total-label">Total daily streams</div>
      <div class="total-value">+{fmt_num(totals["daily"])}</div>
      <div class="total-meta">
        <div><div class="meta-k">Daily chg</div><div class="meta-v {daily_cls}">{daily_delta} {daily_pct}</div></div>
        <div><div class="meta-k">Weekly chg</div><div class="meta-v {week_cls}">{week_delta} {week_pct}</div></div>
        <div><div class="meta-k">All-time</div><div class="meta-v">{fmt_num(totals["total"])}</div></div>
      </div>
    </div>
  </div>
  <div class="sections">
    {_section_html("Top Eras (Combined)", "", albums_html, "Era")}
    {_section_html("Top Songs", "", songs_html, "Song")}
  </div>
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div>
</body></html>"""


def generate(target_date: str | None = None, *, top_n: int = TOP_N) -> Path:
    if target_date is None:
        target_date = generate_streams_image.get_latest_date()
    if top_n <= 0:
        raise ValueError("top_n must be > 0")

    print(f"[weekend_streams_image] Date: {target_date}")

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
    album_today, album_yest, album_week = generate_albums_image.load_history(target_date)
    album_rows = generate_albums_image.build_album_rows(
        album_today,
        album_yest,
        album_week,
        album_track_map,
        album_covers,
    )[:top_n]

    totals = build_totals(song_today, song_yest, song_week)

    print("[weekend_streams_image] Downloading covers...")
    album_cache = generate_albums_image.prefetch_covers(album_rows)
    song_cache = generate_streams_image.prefetch_images(
        song_rows,
        song_cover_map,
        song_track_album_map,
    )
    print(f"[weekend_streams_image] covers={len(album_cache) + len(song_cache)}")

    html_text = build_html(
        target_date=target_date,
        totals=totals,
        album_rows=album_rows,
        song_rows=song_rows,
        album_cache=album_cache,
        song_cache=song_cache,
        song_cover_map=song_cover_map,
        song_track_album_map=song_track_album_map,
    )

    out_dir = ROOT / "history" / target_date[:4] / target_date[5:7] / target_date
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
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    generate(date_arg)
