"""Spotify Charts ranking PNG in the album-page card design: one table, many columns.

Presentation only: ranks, movements and exact stream counts come from full_charts/ranking_metrics.py.
Same visual family as streams/tools/scripts/album_page_card.py (cover-tone fade -> #121212,
hero band on top, Archivo, Spotify-style table); one full-width table, never two side columns.
"""
from __future__ import annotations

import colorsys
import html
from pathlib import Path
from datetime import date

from .song_card import SPOTIFY_SVG, _tsm_logo_data_uri

FONT = Path(__file__).resolve().parent / "fonts" / "Archivo.ttf"
TITLES = {"songs": "Top Songs", "female": "Top Female Songs", "debuts": "Biggest Debuts",
          "female_debuts": "Biggest Female Debuts", "albums": "Top Albums", "female_albums": "Top Female Albums"}
WIDTH = 2240  # same width / x1.8 scale as the wide album card (4032 px, under X's 4096 limit)
SCALE = 1.8


def header_tone(accent: str) -> str:
    """Album-card header tone: the cover accent hue, mid saturation, mid value (album_page_card.header_color)."""
    h = accent.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, s, _v = colorsys.rgb_to_hsv(r, g, b)
    r, g, b = colorsys.hsv_to_rgb(hue, min(s, 0.62), 0.50)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def _short(value: str) -> str:
    d = date.fromisoformat(value)
    return f"{d.strftime('%b')} {d.day}"


def _name_px(name: str) -> int:
    n = len(name)
    return 31 if n <= 26 else 28 if n <= 40 else 25 if n <= 60 else 22


def _move(result: dict, entry: dict) -> str:
    if not result.get("compare_to"):
        return ""
    if entry["previous_rank"] is None:
        return '<span class="mvm new">NEW</span>'
    m = entry["movement"]
    if m > 0:
        return f'<span class="mvm up">▲{m}</span>'
    if m < 0:
        return f'<span class="mvm down">▼{-m}</span>'
    return '<span class="mvm flat">=</span>'


def _columns(kind: str, debut_ranking: bool, year: str) -> list[tuple[str, str]]:
    """(header, css class) of the numeric columns, left to right. Every stat is year-only: say it."""
    y = f"in {year}"
    if kind in {"albums", "female_albums"}:
        return [(f"Streams {y}", "main"), (f"Days {y}", "sm"), ("Charted tracks", "sm"),
                (f"Best day {y}", "pk"), (f"First day {y}", "db"), ("Now", "now")]
    if debut_ranking:
        return [("Debut streams", "main"), ("Debut", "db"), (f"Streams {y}", "pk"),
                (f"Days {y}", "sm"), (f"Peak {y}", "sm"), (f"Best day {y}", "pk"), ("Now", "now")]
    return [(f"Streams {y}", "main"), (f"Days {y}", "sm"), (f"Peak {y}", "sm"),
            (f"Best day {y}", "pk"), ("Debut", "db"), ("Now", "now")]


def _cells(entry: dict, kind: str, debut_ranking: bool, year: str) -> list[str]:
    album = kind in {"albums", "female_albums"}
    best = f'{entry["peak_streams_2026"]:,}<small>{_short(entry["peak_streams_date"])}</small>'
    if entry["still_charting"]:
        now = (f'<span class="up">● {entry["current_tracks"]} on chart</span>' if album
               else f'<span class="up">● #{entry["current_rank"]}</span>')
    else:
        now = f'<span class="out">OUT</span><small>last {_short(entry["last_date"])}</small>'
    if album:
        first = f'{entry["first_chart_streams_2026"]:,}<small>{_short(entry["first_chart_date_2026"])}</small>'
        return [f'{entry["streams"]:,}', str(entry["days_2026"]), str(entry["chart_tracks"]), best, first, now]
    if entry["debut_date"]:
        rank = f' · #{entry["debut_rank"]}' if entry["debut_rank"] else ""
        debut = (f'{_short(entry["debut_date"])}{rank}' if debut_ranking
                 else f'{entry["debut_streams"]:,}<small>{_short(entry["debut_date"])}{rank}</small>')
    else:
        debut = f'<span class="flat">Pre-{year}</span>' if entry["debut_before_year"] else '<span class="flat">–</span>'
    peak = f'#{entry["peak_rank_2026"]}'
    if debut_ranking:
        return [f'{entry["debut_streams"]:,}', debut, f'{entry["streams"]:,}', str(entry["days_2026"]), peak, best, now]
    return [f'{entry["streams"]:,}', str(entry["days_2026"]), peak, best, debut, now]


def build_html(result: dict, entries: list[dict], *, region_name: str, scope: str,
               top: int, page_number: int = 1, page_count: int = 1, spotlight=()) -> str:
    """spotlight: keys of the rows a post is about (blue rail). A spotlighted entry ranked
    below the top N of an overall card comes after a "…" gap row, at its real rank."""
    esc = lambda value: html.escape(str(value), quote=True)
    kind, year = result["kind"], result["year"]
    d = date.fromisoformat(result["as_of"])
    album = kind in {"albums", "female_albums"}
    debut_ranking = result["metric"] == "debut_streams"
    lead = entries[0]
    tone = lead.get("tone", "#315b49")
    logo = _tsm_logo_data_uri()
    spotify = SPOTIFY_SVG.replace('class="logo"', 'class="sp"')

    cols = _columns(kind, debut_ranking, year)
    widths = {"main": "250px", "sm": "120px", "pk": "230px", "db": "250px", "now": "170px"}
    template = "86px minmax(0,1fr) " + " ".join(widths[c] for _, c in cols)
    head = "".join(f'<div class="{c} r">{esc(h)}</div>' for h, c in cols)

    rows = []
    for entry in entries:
        name = str(entry["name"])
        art = f'<img src="{esc(entry["art"])}" alt="">' if entry.get("art") else '<div class="ph"></div>'
        cells = "".join(f'<div class="{c} r">{v}</div>'
                        for (_, c), v in zip(cols, _cells(entry, kind, debut_ranking, year)))
        hl = " spot" if entry["key"] in spotlight else " taylor" if scope == "all" and entry["is_taylor"] else ""
        if scope == "all" and entry["rank"] > top and rows and "gap" not in rows[-1]:
            rows.append('<div class="row gap"><div class="num">…</div></div>')
        rows.append(f'''<div class="row{hl}"><div class="num">{entry["rank"]}{_move(result, entry)}</div>
          <div class="ttl">{art}<div class="tx"><div class="t clamp2" style="font-size:{_name_px(name)}px">{esc(name)}</div>
          <div class="a">{esc(str(entry["artists"]).replace(" | ", ", "))}</div></div></div>{cells}</div>''')

    charting = sum(1 for e in entries if e["still_charting"] and (scope != "all" or e["rank"] <= top))
    taylor_n = sum(1 for e in entries if e["is_taylor"])
    noun = "album" if album else "song"
    if scope == "taylor":
        by = f'<b>Taylor Swift</b> · {len(entries)} {noun}{"s" if len(entries) > 1 else ""} · all entries'
    else:
        by = f'Top {top} · <b>Taylor Swift</b> highlighted ({taylor_n})'
    source = ("Charting tracks summed · no official album ranks" if album
              else "Female solo lead artists" if kind.startswith("female") else "All artists")
    comparison = f"Rank change vs {_short(result['compare_to'])}" if result.get("compare_to") else "No comparison"
    pages = f" · {page_number}/{page_count}" if page_count > 1 else ""
    full_date = f"{d.strftime('%B')} {d.day}, {d.year}"

    return f'''<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:"TSM Archivo";src:url("{FONT.as_uri()}") format("truetype");font-weight:100 900;font-stretch:62% 125%}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{WIDTH}px;background:#121212}}
.card{{width:{WIDTH}px;color:#fff;font-family:"TSM Archivo","Segoe UI",sans-serif;overflow:hidden;padding:56px 56px 40px;
  background:linear-gradient(180deg,{tone} 0%,color-mix(in srgb,{tone} 55%,#121212) 22%,color-mix(in srgb,{tone} 18%,#121212) 48%,#121212 80%)}}
.clamp2{{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.hero{{display:flex;align-items:flex-end;gap:44px;margin-bottom:40px}}
.cover{{width:330px;height:330px;flex:none;border-radius:10px;object-fit:cover;box-shadow:0 14px 60px rgba(0,0,0,.55)}}
.meta{{flex:1;min-width:0}}
.kind{{font-size:26px;font-weight:600}}
h1{{white-space:nowrap;font-size:104px;font-weight:900;letter-spacing:-.035em;line-height:1;margin:8px 0 22px;font-stretch:96%;text-transform:uppercase}}
.by{{font-size:28px;color:rgba(255,255,255,.75)}} .by b{{color:#fff;font-weight:800}}
.src{{font-size:23px;color:rgba(255,255,255,.62);margin-top:10px}}
.stat{{flex:none;display:flex;flex-direction:column;align-items:flex-end;gap:22px;text-align:right}}
.lbl{{font-size:22px;color:#b3b3b3;font-weight:700;letter-spacing:.06em;text-transform:uppercase}}
.big{{font-size:64px;font-weight:900;letter-spacing:-.025em;line-height:1.05;margin-top:4px}}
.pill{{display:inline-block;margin-top:14px;font-size:29px;font-weight:900;padding:7px 15px;border-radius:999px;color:#1ed760;background:rgba(30,215,96,.16)}}
.play{{width:96px;height:96px;border-radius:50%;background:#1ed760;display:grid;place-items:center}}
.play svg{{margin-left:7px}}
.grid,.row{{display:grid;grid-template-columns:{template};align-items:center;column-gap:18px;padding:0 14px}}
.grid{{align-items:end;line-height:1.15;padding-bottom:14px;border-bottom:1px solid rgba(255,255,255,.14);color:#b3b3b3;font-size:23px;font-weight:600}}
.grid .num{{text-align:center}} .r{{text-align:right}}
.row{{min-height:92px;padding-top:10px;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,.06)}}
.row.spot{{background:rgba(91,189,228,.16);box-shadow:inset 5px 0 0 #5bbde4;border-radius:6px}}
.row.gap{{min-height:44px;border-bottom:none}}
.row.taylor{{background:rgba(30,215,96,.12);box-shadow:inset 5px 0 0 #1ed760;border-radius:6px}}
.num{{text-align:center;color:#b3b3b3;font-size:30px;font-variant-numeric:tabular-nums;line-height:1.05}}
.mvm{{display:block;font-size:19px;font-weight:800;margin-top:4px}}
.ttl{{display:flex;align-items:center;gap:18px;min-width:0}}
.ttl img,.ph{{width:66px;height:66px;border-radius:4px;object-fit:cover;flex:none}} .ph{{background:rgba(255,255,255,.08)}}
.tx{{min-width:0}} .t{{font-weight:650;line-height:1.12}}
.a{{font-size:22px;color:#b3b3b3;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.main{{font-size:30px;font-weight:800;font-variant-numeric:tabular-nums}}
.sm{{font-size:28px;font-weight:600;color:#e6e6e6;font-variant-numeric:tabular-nums}}
.pk,.db,.now{{font-size:26px;font-weight:600;color:#e6e6e6;font-variant-numeric:tabular-nums;line-height:1.15}}
.grid .main,.grid .sm,.grid .pk,.grid .db,.grid .now{{font-size:23px;font-weight:600;color:#b3b3b3}}
.row small{{display:block;font-size:19px;font-weight:600;color:#b3b3b3}}
.now{{font-weight:800}}
.up{{color:#1ed760}} .down{{color:#ff7a85}} .new{{color:#5bbde4}} .flat{{color:#b3b3b3}} .out{{color:#ff7a85}}
.foot{{margin-top:34px;display:flex;align-items:center;gap:14px;color:#b3b3b3;font-size:24px}}
.foot .logo{{width:50px;height:50px;background:#fff;-webkit-mask:url({logo}) center/contain no-repeat}}
.foot b{{color:#fff;font-size:26px}}
.foot .note{{margin-left:auto;text-align:right}}
.sp{{width:46px;height:46px;flex:none}} .foot .sp{{fill:#1ed760}}
</style></head><body><div class="card">
<div class="hero">{f'<img class="cover" src="{esc(lead["art"])}" alt="">' if lead.get("art") else ""}
  <div class="meta"><div class="kind">Spotify Charts · {esc(region_name)} · Year to date</div>
  <h1>{TITLES[kind]} {year}</h1><div class="by">{by}</div>
  <div class="src">Jan 1 – {_short(result["as_of"])} · Streams inside the Daily Top 200 · {source}</div></div>
  <div class="stat"><div><div class="lbl">Charts through</div><div class="big">{full_date}</div>
  <div class="pill">● {charting} still charting</div></div>
  <div class="play"><svg width="44" height="44" viewBox="0 0 24 24"><path d="M7 3v18l14-9z"/></svg></div></div>
</div>
<div class="grid"><div class="num">#</div><div>{'Album' if album else 'Title'}</div>{head}</div>
{''.join(rows)}
<div class="foot"><span class="logo"></span><b>@swiftiescharts</b>
  <span class="note">{esc(comparison)} · days and peaks: {year} only{pages}</span>{spotify}</div>
</div></body></html>'''


# The big page title stays on one line: shrink it (floor 56px) until it fits its column.
TITLE_FIT_JS = """() => {
  const h = document.querySelector('h1');
  if (!h) return;
  let px = parseFloat(getComputedStyle(h).fontSize);
  while (h.scrollWidth > h.clientWidth && px > 56) { px -= 2; h.style.fontSize = px + 'px'; }
}"""


def render(doc: str, out: Path) -> Path:
    from playwright.sync_api import sync_playwright
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    html_path = out.with_suffix(".html")
    html_path.write_text(doc, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": WIDTH, "height": 900}, device_scale_factor=SCALE)
        page.goto(html_path.as_uri(), wait_until="load")
        page.evaluate("document.fonts.ready")
        page.evaluate(TITLE_FIT_JS)
        broken = page.locator("img").evaluate_all("imgs => imgs.filter(i => !i.complete || !i.naturalWidth).length")
        overflow = page.evaluate(f"document.documentElement.scrollWidth > {WIDTH}")
        clipped = page.evaluate("[...document.querySelectorAll('.row > .r')].filter(e => e.scrollWidth > e.clientWidth + 1).length")
        if broken or overflow or clipped:
            browser.close()
            raise ValueError(f"Ranking layout blocked: {broken} broken images; horizontal overflow={overflow}; "
                             f"{clipped} clipped cells")
        page.locator(".card").screenshot(path=str(out))
        browser.close()
    return out
