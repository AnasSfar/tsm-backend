"""Debut card styled like a Spotify single page (2026-10-16, Patient Zero acoustic/piano).

Used by post_debut_releases._generate_debut_image for every song debut card (the
old light "Song Update" HTML stays as fallback). One card per song family:
the main version stays on top, the new versions below with a NEW badge, then
an "All versions" row and a one-line band saying what the new versions added.
Exact data only: every number is passed in by post_debut_releases (streams
history rows of the day), nothing is computed from estimates here.

Preview / test: previews_and_sims/patient-zero-versions-debut/simulate.py
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

from album_page_card import comma, esc, header_color, signed
from comp.milestone_card import FONTS_DIR, _cover, milestone_palette
from comp.song_card import SPOTIFY_SVG, _tsm_logo_data_uri

W = 1400


def version_name(title: str, base: str) -> str:
    """'Patient Zero - Acoustic Version' -> 'Acoustic Version'; the main version keeps its title."""
    title = (title or "").strip()
    if base and title.casefold().startswith(base.casefold()):
        rest = title[len(base):].strip()
        rest = re.sub(r"^[-–—:]\s*", "", rest)
        rest = re.sub(r"^[\(\[](.*)[\)\]]$", r"\1", rest).strip()
        if rest:
            return rest
    return title


def title_px(title: str) -> int:
    n = len(title)
    return 120 if n <= 14 else 100 if n <= 20 else 84 if n <= 28 else 68


def row_px(name: str) -> int:
    n = len(name)
    return 42 if n <= 22 else 36 if n <= 28 else 30


def _chg(change: int | None, daily: int, new: bool) -> str:
    if new:
        return '<div class="chg new"><span>NEW</span></div>'
    if change is None:
        return '<div class="chg flat"><span>–</span></div>'
    txt, cls = signed(change)
    prev = daily - change
    small = f"<small>{change / prev * 100:+.1f}%</small>" if prev > 0 else ""
    return f'<div class="chg {cls}"><span>{txt}</span>{small}</div>'


def _row(num: int, row: dict, base: str, target_date: str) -> str:
    name = version_name(row["title"], base)
    if row.get("debut"):
        day = row.get("day") or 1
        d = date.fromisoformat(target_date)
        sub = f"Out {d.strftime('%b')} {d.day}" if day == 1 else f"Day {day}"
        # day 1: the +/- column already says NEW, the badge only from day 2
        badge = '<span class="badge">NEW</span>' if not row.get("new") else ""
    else:
        sub = " · ".join(x for x in ("Original" if name == row["title"] else "", row.get("album") or "") if x)
        badge = ""
    sub_html = f'<span class="sub">{esc(sub)}</span>' if sub else ""
    return f"""
<div class="row{' deb' if row.get('debut') else ''}">
  <div class="num">{num}</div>
  <div class="ttl"><div class="line"><span class="t" style="font-size:{row_px(name)}px">{esc(name)}</span>{badge}</div>{sub_html}</div>
  <div class="tot">{comma(row['total'])}</div>
  <div class="day">{comma(row['daily'])}</div>
  {_chg(row.get('change'), row['daily'], bool(row.get('new')))}
</div>"""


def build(target_date: str, *, title: str, rows: list[dict], cover_url: str | None,
          daily: int, change: int | None, total: int) -> str:
    """rows: main version(s) first, then the debuts. Each row: title, album, total, daily,
    change (None = unknown), new (first day: no previous day), debut (released in the
    debut window), day (debut day number)."""
    cover_uri, cover_bytes = _cover(cover_url) if cover_url else ("", b"")
    top = header_color(milestone_palette(cover_bytes)) if cover_bytes else "#4a4a4a"
    debuts = [r for r in rows if r.get("debut")]
    mains = [r for r in rows if not r.get("debut")]
    d = date.fromisoformat(target_date)
    long_date = f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}, {d.year}"
    day_n = min((r.get("day") or 1) for r in debuts) if debuts else None
    if mains and debuts:
        kind = f"New version{'s' if len(debuts) > 1 else ''}"
    else:
        kind = "Single · Debut"
    if day_n:
        kind += f" · Day {day_n}"
    year = d.year
    n_versions = len(rows)

    list_rows = "".join(_row(i, r, title, target_date) for i, r in enumerate(rows, 1))
    total_html = ""
    if n_versions > 1:
        prev = daily - change if change is not None else 0
        pct = change / prev * 100 if change is not None and prev > 0 else None
        total_html = f"""
<div class="total">
  <div class="num"></div>
  <div class="ttl">All versions</div>
  <div class="tot">{comma(total)}</div>
  <div class="day">{comma(daily)}</div>
  {_chg(change, daily, False) if pct is not None else '<div class="chg flat"><span>–</span></div>'}
</div>"""

    # One-line band: what the new versions added to the song today (exact, from the rows).
    band = ""
    if mains and debuts:
        new_daily = sum(r["daily"] for r in debuts)
        share = f" · {new_daily / daily * 100:.1f}% of the song's streams" if daily > 0 else ""
        what = "New versions" if len(debuts) > 1 else "New version"
        verb = "debuted with" if day_n == 1 else "added"
        band = (f'<div class="band"><span class="si">✦</span><span class="st">{what} {verb} '
                f'<b>{comma(new_daily)}</b> streams{share}</span></div>')

    arrow, pills = "", ""
    if change is not None and daily - change > 0:
        txt, cls = signed(change)
        arrow = {"up": "▲ ", "down": "▼ "}.get(cls, "")
        pct = abs(change / (daily - change) * 100)
        pills = (f'<div class="pills {cls}"><span class="pill">{arrow}{txt.lstrip("+-")}</span>'
                 f'<span class="pill">{arrow}{pct:.2f}%</span></div><div class="vs">vs yesterday'
                 f'{" · all versions" if n_versions > 1 else ""}</div>')
    logo = _tsm_logo_data_uri()
    sp = SPOTIFY_SVG.replace('class="logo"', 'class="sp" width="38" height="38"').replace('fill="white"', 'fill="#1ed760"')
    cover_img = f'<img class="cover" src="{cover_uri}">' if cover_uri else '<div class="cover ph"></div>'
    by_img = f'<img src="{cover_uri}">' if cover_uri else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:"TSM Archivo";src:url("{FONTS_DIR.as_uri()}/Archivo.ttf") format("truetype");font-weight:100 900;font-stretch:62% 125%}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;background:#121212}}
.card{{width:{W}px;color:#fff;font-family:"TSM Archivo","Segoe UI",sans-serif;overflow:hidden;padding:56px 56px 40px;
  background:linear-gradient(180deg,{top} 0%,color-mix(in srgb,{top} 55%,#121212) 30%,color-mix(in srgb,{top} 16%,#121212) 60%,#121212 88%)}}
.hero{{display:flex;gap:48px;align-items:center}}
.cover{{width:400px;height:400px;flex:none;border-radius:10px;object-fit:cover;box-shadow:0 14px 60px rgba(0,0,0,.55)}}
.cover.ph{{background:#2a2a2a}}
.meta{{flex:1;min-width:0}}
.kind{{font-size:30px;font-weight:700}}
.title{{font-weight:900;letter-spacing:-.035em;line-height:1;margin:10px 0 18px;font-stretch:96%;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.by{{display:flex;align-items:center;gap:12px;font-size:30px;color:rgba(255,255,255,.75);white-space:nowrap}}
.by img{{width:46px;height:46px;border-radius:50%;object-fit:cover}}
.by b{{color:#fff;font-weight:800}}
.date{{margin-top:22px;font-size:38px;font-weight:800}}
.today{{margin-top:34px;display:flex;align-items:flex-end;gap:28px;flex-wrap:wrap}}
.today .lbl{{font-size:24px;color:#d0d0d0;font-weight:700;letter-spacing:.06em}}
.today .big{{font-size:96px;font-weight:900;letter-spacing:-.03em;line-height:1}}
.pills{{display:flex;gap:10px}}
.pill{{font-size:34px;font-weight:900;padding:7px 16px;border-radius:999px;font-variant-numeric:tabular-nums;white-space:nowrap}}
.pills.down .pill{{color:#ff7a85;background:rgba(241,94,108,.18)}}
.pills.up .pill{{color:#1ed760;background:rgba(30,215,96,.16)}}
.pills.flat .pill{{color:#b3b3b3;background:rgba(255,255,255,.08)}}
.vs{{margin-top:8px;font-size:24px;color:#b3b3b3;font-weight:600}}
.list{{margin-top:48px}}
.grid,.row,.total{{display:grid;grid-template-columns:56px minmax(0,1fr) 240px 250px 200px;align-items:center;column-gap:18px}}
.grid{{padding:0 14px 14px;border-bottom:1px solid rgba(255,255,255,.16);color:#b3b3b3;font-size:26px;font-weight:600}}
.grid .r,.tot,.day,.chg{{text-align:right}}
.grid .num,.row .num{{text-align:center}}
.row,.total{{padding:0 14px;min-height:108px}}
.row .num{{color:#b3b3b3;font-size:32px;font-variant-numeric:tabular-nums}}
.row.deb{{background:rgba(91,189,228,.08);box-shadow:inset 5px 0 0 #5bbde4;border-radius:8px}}
.ttl{{min-width:0;display:flex;flex-direction:column;justify-content:center}}
.line{{display:flex;align-items:center;gap:14px;min-width:0}}
.t{{font-weight:700;line-height:1.1;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.badge{{flex:none;font-size:22px;font-weight:900;letter-spacing:.08em;color:#0b2530;background:#5bbde4;border-radius:6px;padding:4px 9px}}
.sub{{font-size:26px;color:#b3b3b3;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.tot{{font-size:34px;color:#b3b3b3;font-variant-numeric:tabular-nums}}
.day{{font-size:42px;font-weight:800;font-variant-numeric:tabular-nums}}
.chg{{display:flex;flex-direction:column;align-items:flex-end;font-size:32px;font-weight:800;line-height:1.1;font-variant-numeric:tabular-nums}}
.chg small{{font-size:24px;font-weight:600;opacity:.85}}
.up{{color:#1ed760}} .down{{color:#f15e6c}} .flat{{color:#b3b3b3}} .new{{color:#5bbde4}}
.total{{margin-top:14px;border-top:1px solid rgba(255,255,255,.16)}}
.total .ttl{{display:block;font-size:42px;font-weight:900}}
.total .tot{{color:#fff;font-weight:700}}
.total .day{{font-size:44px;font-weight:900}}
.band{{margin-top:34px;padding-top:28px;border-top:1px solid rgba(255,255,255,.18);display:flex;gap:20px;align-items:baseline;font-size:44px;line-height:1.2}}
.band .si{{color:#5bbde4;flex:none}}
.band .st{{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#e6e6e6}}
.band b{{color:#fff}}
.foot{{margin-top:34px;display:flex;align-items:center;gap:14px;color:#b3b3b3;font-size:26px}}
.foot .logo{{width:52px;height:52px;background:#fff;-webkit-mask:url({logo}) center/contain no-repeat}}
.foot b{{color:#fff;font-size:28px}}
.foot .src{{margin-left:auto;display:flex;align-items:center;gap:10px}}
</style></head><body><div class="card">
<div class="hero">{cover_img}<div class="meta">
  <div class="kind">{esc(kind)}</div>
  <div class="title" style="font-size:{title_px(title)}px">{esc(title)}</div>
  <div class="by">{by_img}<b>Taylor Swift</b><span>• {year}{f' • {n_versions} versions' if n_versions > 1 else ' • Single'}</span></div>
  <div class="date">{esc(long_date)}</div>
</div></div>
<div class="today"><div><div class="lbl">DAILY STREAMS{' · ALL VERSIONS' if n_versions > 1 else ''}</div><div class="big">{comma(daily)}</div></div>
  <div>{pills}</div></div>
<div class="list"><div class="grid"><div class="num">#</div><div>Version</div><div class="r">Plays</div><div class="r">Daily</div><div class="r">+/-</div></div>
{list_rows}{total_html}</div>
{band}
<div class="foot"><span class="logo"></span><b>@swiftiescharts</b><span class="src">{sp}</span></div>
</div></body></html>"""


# Band = always one line: shrink the font (down to 60%) until it fits, then "…".
BAND_FIT_JS = """() => {
  const band = document.querySelector('.band');
  if (!band) return;
  const st = band.querySelector('.st');
  let fs = parseFloat(getComputedStyle(band).fontSize);
  const floor = fs * 0.6;
  while (st.scrollWidth > st.clientWidth && fs > floor) { fs -= 1; band.style.fontSize = fs + 'px'; }
}"""


def render_png(out_path: Path, target_date: str, **kwargs) -> Path:
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(f"_{out_path.stem}.html")
    tmp.write_text(build(target_date, **kwargs), encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": W, "height": 900}, device_scale_factor=2)
            page.goto(tmp.as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            page.evaluate(BAND_FIT_JS)
            page.locator(".card").screenshot(path=str(out_path))
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)
    return out_path
