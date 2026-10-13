"""Design proposals (2026-10-13): the other daily streams images restyled as Spotify app pages,
same family as the album card (album_page_card.py) but one page type per image:

  recap     -> artist page   (weekend_streams_image: daily total, monthly listeners, Popular, Discography)
  songs     -> playlist page (streams_image: top 20 songs, two columns)
  eras      -> discography grid (albums_image: one cover tile per era + Non-Album + Total)
  gainers   -> Spotify Charts page (daily / weekly gainers, % as the headline)

Preview only: REAL data read through the current generators' loaders (read-only), PNGs go to
cards/ here, nothing is written to snapshots/ and nothing is posted.

  python previews_and_sims/spotify-app-cards/simulate.py [DATE] [recap|songs|eras|gainers ...]
"""
from __future__ import annotations

import html
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPTS = REPO / "collectors/spotify/streams/tools/scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_streams_image as gs  # noqa: E402  (puts collectors/ + streams/ on sys.path)
import generate_albums_image as ga  # noqa: E402
import generate_weekend_streams_image as gw  # noqa: E402
import post_gainer_thread as pg  # noqa: E402
import best_day_since  # noqa: E402
import album_page_card as apc  # noqa: E402
from comp.milestone_card import FONTS_DIR, _cover, milestone_palette  # noqa: E402
from comp.song_card import SPOTIFY_SVG, _tsm_logo_data_uri  # noqa: E402
from comp.discography import display_title_for_album  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = HERE / "cards"


# ── small helpers ────────────────────────────────────────────────────────────
def esc(v) -> str:
    return html.escape(str(v))


def comma(n) -> str:
    return f"{int(n or 0):,}"


def long_date(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{x.strftime('%B')} {x.day}, {x.year}"


_COVERS: dict[str, tuple[str, bytes]] = {}


def cover(url: str | None) -> tuple[str, bytes]:
    if not url:
        return "", b""
    if url not in _COVERS:
        _COVERS[url] = _cover(url)
    return _COVERS[url]


def tone(url: str | None, album: str | None = None) -> str:
    key = (album or "").strip().casefold()
    for name, color in apc.HEADER_OVERRIDES.items():
        if key.startswith(name):
            return color
    return apc.header_color(milestone_palette(cover(url)[1]))


def delta(cur, prev) -> tuple[int | None, float | None]:
    if cur is None or prev is None:
        return None, None
    ch = int(cur) - int(prev)
    return ch, (ch / prev * 100 if prev else None)


def pills(ch, pct, *, size: str = "") -> str:
    """Spotify-green / red pill pair (absolute change + %)."""
    if ch is None:
        return ""
    cls = "up" if ch > 0 else "down" if ch < 0 else "flat"
    arrow = {"up": "▲ ", "down": "▼ "}.get(cls, "")
    p = f'<span class="pill">{arrow}{abs(pct):.1f}%</span>' if pct is not None else ""
    return f'<div class="pills {cls} {size}"><span class="pill">{arrow}{comma(abs(ch))}</span>{p}</div>'


def chg_cell(ch, pct) -> str:
    if ch is None:
        return '<div class="chg flat"><span>–</span></div>'
    cls = "up" if ch > 0 else "down" if ch < 0 else "flat"
    txt = f"+{ch:,}" if ch > 0 else f"{ch:,}"
    small = f"<small>{pct:+.1f}%</small>" if pct is not None else ""
    return f'<div class="chg {cls}"><span>{txt}</span>{small}</div>'


def move_html(rank, prev) -> str:
    if not prev:
        return '<span class="mvm new">NEW</span>'
    d = int(prev) - int(rank)
    if d > 0:
        return f'<span class="mvm up">▲{d}</span>'
    if d < 0:
        return f'<span class="mvm down">▼{-d}</span>'
    return '<span class="mvm flat">=</span>'


def best_tag(label: str | None) -> str:
    if not label:
        return ""
    text = label if label.lower().startswith(("best", "since")) else f"since {label}"
    return f'<span class="bd">★ {esc(text)}</span>'


def best_line(label: str | None) -> str:
    """Best day since: its own (third) line under title + album, never squeezed after the album."""
    return f'<span class="s bdl one">{best_tag(label)}</span>' if label else ""


def foot() -> str:
    sp = SPOTIFY_SVG.replace('class="logo"', 'class="sp" width="38" height="38"').replace('fill="white"', 'fill="#1ed760"')
    return f'<div class="foot"><span class="logo"></span><b>@swiftiescharts</b><span class="src">{sp}</span></div>'


def is_weekend(d: str) -> bool:
    return date.fromisoformat(d).weekday() >= 5


BASE_CSS = """
@font-face{font-family:"TSM Archivo";src:url("%(fonts)s/Archivo.ttf") format("truetype");font-weight:100 900;font-stretch:62%% 125%%}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:%(w)spx;background:#121212}
.card{width:%(w)spx;color:#fff;font-family:"TSM Archivo","Segoe UI",sans-serif;overflow:hidden;position:relative;
  background:linear-gradient(180deg,%(top)s 0%%,color-mix(in srgb,%(top)s 55%%,#121212) 22%%,color-mix(in srgb,%(top)s 16%%,#121212) 48%%,#121212 78%%)}
.one{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kind{font-size:26px;font-weight:600}
.pills{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
.pill{font-size:31px;font-weight:900;padding:7px 15px;border-radius:999px;font-variant-numeric:tabular-nums;white-space:nowrap}
.pills.sm .pill{font-size:22px;padding:4px 11px}
.pills.down .pill{color:#ff7a85;background:rgba(241,94,108,.18)}
.pills.up .pill{color:#1ed760;background:rgba(30,215,96,.16)}
.pills.flat .pill{color:#b3b3b3;background:rgba(255,255,255,.08)}
.vs{margin-top:8px;font-size:21px;color:#b3b3b3;font-weight:600}
.lbl{font-size:22px;color:#b3b3b3;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.big{font-size:64px;font-weight:900;letter-spacing:-.025em;line-height:1.05;margin-top:4px;font-variant-numeric:tabular-nums}
.bd{color:#f5c84c;font-weight:700}
.mvm{display:block;font-size:17px;font-weight:800;line-height:1}
.mvm.up,.up{color:#1ed760} .mvm.down,.down{color:#f15e6c} .mvm.flat,.flat{color:#8a8a8a} .mvm.new{color:#5bbde4}
.foot{padding-top:30px;display:flex;align-items:center;gap:14px;color:#b3b3b3;font-size:24px}
.foot .logo{width:50px;height:50px;background:#fff;-webkit-mask:url(%(logo)s) center/contain no-repeat}
.foot b{color:#fff;font-size:26px}
.foot .src{margin-left:auto;display:flex;align-items:center}
.play{width:96px;height:96px;border-radius:50%%;background:#1ed760;display:grid;place-items:center;flex:none}
.play svg{margin-left:7px}
.actions{display:flex;align-items:center;gap:28px}
.btn{font-size:24px;font-weight:800;border:2px solid rgba(255,255,255,.55);border-radius:999px;padding:12px 30px}
/* track rows (playlist / popular / charts) */
.grid,.trow{display:grid;align-items:center;column-gap:16px;padding:0 12px}
.grid{padding-bottom:14px;border-bottom:1px solid rgba(255,255,255,.14);color:#b3b3b3;font-size:23px;font-weight:600}
.grid .r,.tot,.day,.chg{text-align:right}
.trow{min-height:84px;padding-top:8px;padding-bottom:8px}
.ttl .bdl{margin-top:2px}
.trow .num{text-align:center;color:#b3b3b3;font-size:28px;font-variant-numeric:tabular-nums;line-height:1.1}
.trow img{width:62px;height:62px;border-radius:4px;object-fit:cover}
.ttl{min-width:0;display:flex;flex-direction:column;justify-content:center}
.ttl .t{font-size:28px;font-weight:600;line-height:1.15}
.two{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ttl .s{font-size:20px;color:#b3b3b3;margin-top:3px}
.tot{font-size:25px;color:#b3b3b3;font-variant-numeric:tabular-nums}
.day{font-size:29px;font-weight:700;font-variant-numeric:tabular-nums}
.chg{display:flex;flex-direction:column;align-items:flex-end;font-size:23px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums}
.chg small{font-size:18px;font-weight:600;opacity:.8}
"""

PLAY = '<div class="play"><svg viewBox="0 0 24 24" width="42" height="42"><path d="M6 3.5v17L20.5 12z" fill="#000"/></svg></div>'
SHUFFLE = ('<svg viewBox="0 0 24 24" width="52" height="52" fill="none" stroke="#b3b3b3" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
           '<path d="M3 6h3.5c2 0 3.2 1 4.3 2.6l2.4 3.6c1.1 1.6 2.3 2.8 4.3 2.8H21"/><path d="M3 18h3.5c1.6 0 2.7-.7 3.6-1.8"/>'
           '<path d="M14 7.8c.9-1 2-1.8 3.5-1.8H21"/><path d="M18.5 3.5L21 6l-2.5 2.5"/><path d="M18.5 12.5L21 15l-2.5 2.5"/></svg>')
VERIFIED = ('<svg viewBox="0 0 24 24" width="40" height="40"><path fill="#4cb3ff" d="M10.8 1.7a1.7 1.7 0 0 1 2.4 0l1.6 1.6 2.2-.3a1.7 1.7 0 0 1 1.9 1.4l.4 2.2 2 1.1a1.7 1.7 0 0 1 .7 2.3L21 12l1 2a1.7 1.7 0 0 1-.7 2.3l-2 1.1-.4 2.2a1.7 1.7 0 0 1-1.9 1.4l-2.2-.3-1.6 1.6a1.7 1.7 0 0 1-2.4 0l-1.6-1.6-2.2.3a1.7 1.7 0 0 1-1.9-1.4l-.4-2.2-2-1.1A1.7 1.7 0 0 1 1 14l1-2-1-2a1.7 1.7 0 0 1 .7-2.3l2-1.1.4-2.2A1.7 1.7 0 0 1 6 3l2.2.3z"/>'
            '<path d="M7.5 12.3l3 2.9 6-6.2" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def page(width: int, top: str, body: str, extra_css: str, cls: str = "") -> str:
    css = BASE_CSS % {"fonts": FONTS_DIR.as_uri(), "w": width, "top": top, "logo": _tsm_logo_data_uri()}
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}{extra_css}</style></head>'
            f'<body><div class="card {cls}">{body}</div></body></html>')


# ── data (same loaders as the posted images) ─────────────────────────────────
def song_rows(d: str, n: int) -> tuple[list[dict], dict, dict]:
    today, yest, week = gs.load_history(d)
    rows = gs.build_top_n(today, yest, week, gs.load_song_db(), n)
    cover_map, ta_map = gs.load_covers(), gs.load_track_album_map()
    try:
        labels = best_day_since.best_day_marker_labels([r["track_id"] for r in rows], date.fromisoformat(d))
    except Exception as exc:
        print(f"best-day markers skipped: {exc}")
        labels = {}
    for r in rows:
        r["cover_url"] = gs.get_cover_url(r, cover_map, ta_map)
        r["cover"] = cover(r["cover_url"])[0]
        r["album_raw"] = ta_map.get(gs._norm(r["title"]), "") or ""
        r["album"] = display_title_for_album(r["album_raw"])
        r["best"] = labels.get(r["track_id"])
    return rows, cover_map, ta_map


def era_rows(d: str, *, only_eras: bool = False) -> tuple[list[dict], dict]:
    tmap = ga.load_album_track_map()
    if only_eras:
        tmap = gw.exclude_non_era_albums(tmap)
    today, yest, week = ga.load_history(d)
    rows = ga.build_album_rows(today, yest, week, tmap, ga.load_covers(), target_date=d)
    labels = ga._album_best_day_labels(rows, d)
    for r in rows:
        r["cover"] = cover(r.get("cover_url"))[0]
        r["collage"] = [cover(u)[0] for u in r.get("collage_covers") or []]
        r["best"] = labels.get(r["album"])
    return rows, ga.load_public_total_row(d)


def song_title_parts(title: str, album: str = "") -> tuple[str, str]:
    raw = apc.VAULT_RE.sub("", title)
    if apc.TV_RE.search(album or ""):  # redundant next to a Taylor's Version album name
        raw = apc.TV_RE.sub("", raw)
    m = apc.FEAT_RE.search(raw)
    return (raw[:m.start()], m.group(1)) if m else (raw, "")


def title_span(main: str) -> str:
    """Long titles wrap on two smaller lines instead of being cut."""
    if len(main) <= 24:
        return f'<span class="t one">{esc(main)}</span>'
    return f'<span class="t two" style="font-size:{24 if len(main) > 34 else 26}px">{esc(main)}</span>'


import day_theme  # noqa: E402  (theme of the day: timeline event -> image + colour)


def event_color(d: str) -> str | None:
    """Timeline anniversary: the event's era colour for every non-album image (owner rule)."""
    t = day_theme.theme_for(d)
    return t.color if t.applies_to_all else None


# ── 1. Recap = artist page (landscape: artist column left, Popular + Top eras right) ──
def recap(d: str) -> str:
    W = 2240
    songs, *_ = song_rows(d, 5)
    eras, _tot = era_rows(d, only_eras=True)
    eras = [e for e in eras if e.get("rank")][:5]
    dd = date.fromisoformat(d)
    pub = [gw.load_public_history_rows(str(x)) for x in (dd, dd - timedelta(days=1), dd - timedelta(days=7))]
    t = gw.build_totals(*pub)
    stats = gw._load_artist_stats()
    theme = day_theme.theme_for(d, top_era_album=eras[0]["album"] if eras else None)
    head, top = theme.image, theme.color
    print(f"[theme] {d}: {theme.mode} · {theme.era} · {head.name} · {top} · {theme.caption or ''}")
    caption = f'<div class="evt">{esc(theme.caption)}</div>' if theme.caption else ""

    day_ch, day_pct = delta(t["daily"], t["yesterday_daily"])
    wk_ch, wk_pct = delta(t["daily"], t["week_daily"])
    ml_ch, ml_pct = delta(stats.get("listeners"), stats.get("listeners_prev"))
    fo_ch, _ = delta(stats.get("followers"), stats.get("followers_prev"))

    def chip(ch, pct=None) -> str:  # small change pill next to an artist stat
        if ch is None:
            return ""
        if not ch:
            return '<span class="chip flat">=</span>'
        cls = "up" if ch > 0 else "down"
        p = f" ({pct:+.1f}%)" if pct is not None else ""
        return f'<span class="chip {cls}">{"▲" if ch > 0 else "▼"}{comma(abs(ch))}{p}</span>'

    rank, rank_prev = stats.get("rank"), stats.get("rank_prev")
    rank_chip = ""
    if rank and rank_prev:
        mv = rank_prev - rank
        rank_chip = (f'<span class="chip up">▲{mv}</span>' if mv > 0 else
                     f'<span class="chip down">▼{-mv}</span>' if mv < 0 else '<span class="chip flat">=</span>')
    astats = [f'<div class="as"><b>{comma(stats.get("listeners"))}</b> monthly listeners {chip(ml_ch, ml_pct)}</div>']
    if rank:
        astats.append(f'<div class="as"><b class="g">#{rank} in the world</b> {rank_chip}</div>')
    if stats.get("followers"):
        astats.append(f'<div class="as"><b>{comma(stats["followers"])}</b> followers {chip(fo_ch)}</div>')

    pop = []
    for i, s in enumerate(songs, 1):
        main, feat = song_title_parts(s["title"], s["album"])
        ch, pct = delta(s["daily_streams"], s.get("daily_streams_yesterday"))
        sub = " · ".join(x for x in (esc(s["album"]), esc(feat)) if x)
        pop.append(f"""<div class="trow"><div class="num">{i}{move_html(i, s.get('prev_rank'))}</div><img src="{s['cover']}">
<div class="ttl">{title_span(main)}<span class="s one">{sub}</span>{best_line(s['best'])}</div>
<div class="tot">{comma(s['streams'])}</div><div class="day">{comma(s['daily_streams'])}</div>{chg_cell(ch, pct)}</div>""")
    tiles = []
    for e in eras:
        ch, pct = delta(e["daily_streams"], e.get("yest_daily"))
        tiles.append(f"""<div class="tile"><div class="art"><img src="{e['cover']}"><span class="rk">{e['rank']}{move_html(e['rank'], e.get('prev_rank'))}</span></div>
<div class="tn one">{esc(display_title_for_album(e['album']))}</div>
<div class="td">{comma(e['daily_streams'])}</div>{pills(ch, pct, size='sm')}</div>""")

    body = f"""
<div class="left">
  <div class="portrait"><img src="{head.as_uri()}">
    <div class="who"><div class="ver">{VERIFIED}<span>Verified Artist</span></div><div class="name">Taylor Swift</div>{caption}</div>
  </div>
  <div class="astats">{''.join(astats)}</div>
  <div class="today"><div class="lbl">Streams on {esc(long_date(d))}</div><div class="big">{comma(t['daily'])}</div>
    <div class="cmp"><div>{pills(day_ch, day_pct)}<div class="vs">vs yesterday</div></div>
    <div>{pills(wk_ch, wk_pct)}<div class="vs">vs last {dd.strftime("%A")}</div></div></div>
    <div class="alltime"><b>{comma(t['total'])}</b> all-time streams</div></div>
  <div class="actions">{PLAY}<span class="btn">Following</span>{SHUFFLE}</div>
  {foot()}
</div>
<div class="right">
  <h2>Popular today</h2>
  <div class="grid"><div class="num">#</div><div></div><div>Title</div><div class="r">Plays</div><div class="r">Daily</div><div class="r">+/-</div></div>
  {''.join(pop)}
  <h2 class="h2b">Top eras today</h2>
  <div class="tiles">{''.join(tiles)}</div>
</div>"""
    css = """
.card{display:grid;grid-template-columns:640px minmax(0,1fr);column-gap:64px;padding:56px 56px 44px;
  /* same fade as the album card: photo colour -> Spotify black */
  background:linear-gradient(180deg,%(top)s 0%,color-mix(in srgb,%(top)s 55%,#121212) 26%,color-mix(in srgb,%(top)s 18%,#121212) 55%,#121212 85%)}
.left,.right{position:relative;z-index:1;min-width:0}
.left{display:flex;flex-direction:column}
.portrait{position:relative;width:640px;height:700px;border-radius:12px;overflow:hidden;box-shadow:0 18px 70px rgba(0,0,0,.5)}
.portrait img{width:100%;height:100%;object-fit:cover;object-position:center 25%}
.portrait::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,0) 40%,rgba(0,0,0,.55) 70%,rgba(0,0,0,.82) 100%)}
.who{position:absolute;left:34px;right:24px;bottom:28px;z-index:1}
.ver{display:flex;align-items:center;gap:12px;font-size:26px;font-weight:600}
.name{font-size:104px;font-weight:900;letter-spacing:-.04em;line-height:.95;margin-top:8px}
.evt{margin-top:16px;font-size:25px;font-weight:700;line-height:1.3;background:rgba(0,0,0,.45);border-radius:10px;padding:10px 14px;display:inline-block}
.astats{margin-top:26px;display:flex;flex-direction:column;gap:10px}
.as{font-size:27px;color:rgba(255,255,255,.85);display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.as b{color:#fff;font-weight:900;font-variant-numeric:tabular-nums}
.as b.g{color:#1ed760}
.chip{font-size:21px;font-weight:800;padding:3px 12px;border-radius:999px;font-variant-numeric:tabular-nums;background:rgba(0,0,0,.3)}
.chip.up{color:#1ed760} .chip.down{color:#ff7a85} .chip.flat{color:#d0d0d0}
.today{margin-top:30px}
.today .pill{font-size:24px;padding:5px 11px}
.cmp{display:flex;gap:18px}
.cmp .pills{flex-wrap:nowrap;gap:8px}
.alltime{margin-top:18px;font-size:26px;color:#d0d0d0}
.alltime b{color:#fff;font-weight:900}
.actions{margin-top:30px}
.foot{margin-top:auto}
h2{font-size:52px;font-weight:900;margin:0 0 20px;letter-spacing:-.01em}
h2.h2b{margin-top:44px}
/* Popular today: big rows, the column has room */
.grid,.trow{grid-template-columns:56px 96px minmax(0,1fr) 270px 210px 180px;column-gap:22px}
.grid{font-size:26px}
.trow{min-height:136px;padding-top:8px;padding-bottom:8px}
.trow img{width:96px;height:96px;border-radius:6px}
.trow .num{font-size:36px}
.trow .mvm{font-size:21px}
.ttl .t{font-size:40px}
.ttl .t.two{font-size:33px !important}
.ttl .s{font-size:26px;margin-top:4px}
.tot{font-size:31px}
.day{font-size:40px;font-weight:800}
.chg{font-size:30px}
.chg small{font-size:23px}
.tiles{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:24px}
.tile{background:rgba(255,255,255,.06);border-radius:10px;padding:18px 18px 20px;min-width:0}
.art{position:relative}
.art img{width:100%;aspect-ratio:1;border-radius:6px;object-fit:cover;box-shadow:0 10px 30px rgba(0,0,0,.4);display:block}
.rk{position:absolute;left:10px;top:10px;background:rgba(0,0,0,.75);border-radius:999px;font-size:28px;font-weight:900;padding:4px 14px;display:flex;align-items:center;gap:8px}
.rk .mvm{font-size:19px}
.tn{font-size:28px;font-weight:800;margin-top:14px}
.td{font-size:38px;font-weight:900;margin-top:4px;font-variant-numeric:tabular-nums}
""".replace("%(top)s", top)
    return page(W, top, body, css)


# ── 2. Top songs = playlist page ─────────────────────────────────────────────
def songs(d: str, n: int = 20) -> str:
    W = 2240
    rows, *_ = song_rows(d, n)
    # #1 song's cover, with the same era overrides as the album card / Top Eras / Gainers
    top = tone(rows[0]["cover_url"], rows[0]["album_raw"]) if rows else tone(None)
    top = event_color(d) or top
    weekly = is_weekend(d)
    total_daily = sum(r["daily_streams"] or 0 for r in rows)
    uniq = list(dict.fromkeys(r["cover"] for r in rows if r["cover"]))  # 4 different covers, like Spotify
    mosaic = "".join(f'<img src="{u}">' for u in (uniq * 4)[:4])
    out = []
    for r in rows:
        main, feat = song_title_parts(r["title"], r["album"])
        prev = r.get("daily_streams_last_week") if weekly else r.get("daily_streams_yesterday")
        ch, pct = delta(r["daily_streams"], prev)
        sub = " · ".join(x for x in (esc(r["album"]), esc(feat)) if x)
        out.append(f"""<div class="trow"><div class="num">{r['rank']}{move_html(r['rank'], r.get('prev_rank'))}</div><img src="{r['cover']}">
<div class="ttl">{title_span(main)}<span class="s one">{sub}</span>{best_line(r['best'])}</div>
<div class="tot">{comma(r['streams'])}</div><div class="day">{comma(r['daily_streams'])}</div>{chg_cell(ch, pct)}</div>""")
    half = -(-len(out) // 2)
    head = (f'<div class="grid"><div class="num">#</div><div></div><div>Title</div><div class="r">Plays</div>'
            f'<div class="r">Daily</div><div class="r">{"vs last wk" if weekly else "+/-"}</div></div>')
    cols = "".join(f'<div class="col">{head}{"".join(c)}</div>' for c in (out[:half], out[half:]))
    body = f"""<div class="hero"><div class="mosaic">{mosaic}</div>
<div class="meta"><div class="kind">Public Playlist</div><div class="title">Daily Top Songs</div>
<div class="desc">Taylor Swift's {len(rows)} most streamed songs on Spotify · {esc(long_date(d))}</div>
<div class="by"><span class="lg"></span><b>Swifties Charts</b><span>• {len(rows)} songs • {comma(total_daily)} streams today</span></div></div>
<div class="actions">{PLAY}{SHUFFLE}</div></div>
<div class="cols">{cols}</div>{foot()}"""
    css = """
.card{padding:56px 56px 40px}
.hero{display:flex;align-items:flex-end;gap:48px;margin-bottom:44px}
.mosaic{width:340px;height:340px;flex:none;display:grid;grid-template-columns:1fr 1fr;border-radius:8px;overflow:hidden;box-shadow:0 14px 60px rgba(0,0,0,.55)}
.mosaic img{width:170px;height:170px;object-fit:cover}
.meta{flex:1;min-width:0}
.title{font-size:120px;font-weight:900;letter-spacing:-.04em;line-height:1;margin:8px 0 18px}
.desc{font-size:27px;color:rgba(255,255,255,.75)}
.by{display:flex;align-items:center;gap:12px;font-size:26px;color:rgba(255,255,255,.75);margin-top:14px}
.by b{color:#fff}
.lg{width:40px;height:40px;background:#fff;-webkit-mask:url(%(logo)s) center/contain no-repeat}
.cols{display:grid;grid-template-columns:1fr 1fr;column-gap:64px}
.grid,.trow{grid-template-columns:52px 62px minmax(0,1fr) 200px 160px 140px}
""".replace("%(logo)s", _tsm_logo_data_uri())
    return page(W, top, body, css)


def _COVERS_BYTES(uri: str) -> bytes:
    for u, (data_uri, b) in _COVERS.items():
        if data_uri == uri:
            return b
    return b""


# ── 3. Top eras = discography grid ───────────────────────────────────────────
def eras(d: str) -> str:
    W = 2000
    rows, total = era_rows(d)
    weekly = is_weekend(d)
    ranked = [r for r in rows if r.get("rank")]
    top = event_color(d) or tone(ranked[0].get("cover_url"), ranked[0]["album"])
    tiles = []
    for r in rows:
        prev = r.get("week_daily") if weekly else r.get("yest_daily")
        ch, pct = delta(r["daily_streams"], prev)
        if r.get("collage"):
            art = '<div class="collage">' + "".join(f'<img src="{u}">' for u in r["collage"][:4]) + "</div>"
        else:
            art = f'<img src="{r["cover"]}">'
        badge = (f'<span class="rk">{r["rank"]}<i>{move_html(r["rank"], r.get("prev_rank"))}</i></span>' if r.get("rank") else "")
        tiles.append(f"""<div class="tile{' na' if not r.get('rank') else ''}"><div class="art">{art}{badge}</div>
<div class="tn one">{esc(display_title_for_album(r['album']))}</div>
<div class="td">{comma(r['daily_streams'])}</div>{pills(ch, pct, size='sm')}
<div class="tt">{comma(r['streams'])} plays</div>{f'<div class="tb">{best_tag(r["best"])}</div>' if r.get('best') else ''}</div>""")
    prev = total.get("week_daily") if weekly else total.get("yest_daily")
    ch, pct = delta(total["daily_streams"], prev)
    tiles.append(f"""<div class="tile total"><div class="lbl">Total today</div><div class="tv">{comma(total['daily_streams'])}</div>
{pills(ch, pct)}<div class="vs">{'vs last ' + date.fromisoformat(d).strftime('%A') if weekly else 'vs yesterday'}</div>
<div class="lbl" style="margin-top:26px">All-time</div><div class="tt2">{comma(total['streams'])}</div></div>""")
    body = f"""<div class="hero"><div class="meta"><div class="kind">Discography · {'Weekly' if weekly else 'Daily'} update</div>
<div class="title">Eras on Spotify</div>
<div class="by"><b>Taylor Swift</b><span>• {len(ranked)} eras • ranked by streams on {esc(long_date(d))}</span></div></div>
<div class="actions">{PLAY}{SHUFFLE}</div></div>
<div class="tiles">{''.join(tiles)}</div>{foot()}"""
    css = """
.card{padding:56px 56px 40px}
.hero{display:flex;align-items:flex-end;justify-content:space-between;gap:40px;margin-bottom:44px}
.title{font-size:120px;font-weight:900;letter-spacing:-.04em;line-height:1;margin:8px 0 18px}
.by{font-size:27px;color:rgba(255,255,255,.75);display:flex;gap:12px}
.by b{color:#fff}
.tiles{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:24px}
.tile{background:rgba(255,255,255,.05);border-radius:10px;padding:16px 16px 18px;min-width:0}
.art{position:relative}
.art>img,.collage{width:100%;aspect-ratio:1;border-radius:6px;object-fit:cover;box-shadow:0 10px 30px rgba(0,0,0,.4);display:block}
.collage{display:grid;grid-template-columns:1fr 1fr;overflow:hidden}
.collage img{width:100%;aspect-ratio:1;object-fit:cover}
.rk{position:absolute;left:10px;top:10px;background:rgba(0,0,0,.75);border-radius:999px;font-size:30px;font-weight:900;padding:4px 16px;display:flex;align-items:center;gap:10px}
.rk i{font-style:normal}.rk .mvm{font-size:20px}
.tn{font-size:28px;font-weight:800;margin-top:14px;white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;min-height:2.3em;line-height:1.15}
.td{font-size:40px;font-weight:900;margin-top:2px;font-variant-numeric:tabular-nums}
.tt{font-size:21px;color:#b3b3b3;margin-top:10px;font-variant-numeric:tabular-nums}
.tb{font-size:20px;margin-top:6px}
.tile.na .tn{color:#d6d6d6}
.tile.total{background:rgba(30,215,96,.08);border:2px solid rgba(30,215,96,.35);display:flex;flex-direction:column;justify-content:center;padding:26px}
.tv{font-size:54px;font-weight:900;font-variant-numeric:tabular-nums;margin-top:4px}
.tt2{font-size:34px;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums}
.foot{margin-top:10px}
"""
    return page(W, top, body, css)


# ── 4. Gainers = Spotify Charts page ─────────────────────────────────────────
def gainers(d: str, period: str = "daily") -> str:
    W = 1720
    days = 1 if period == "daily" else 7
    picked = pg._pick_gainers(d, compare_days=days, limit=10, min_baseline=1000)
    cover_map, ta_map = gs.load_covers(), gs.load_track_album_map()
    hist = pg.history_store.HistoryIndex.load()
    try:
        labels = best_day_since.best_day_marker_labels([r["track_id"] for r in picked], date.fromisoformat(d))
    except Exception:
        labels = {}
    rows = []
    top = "#1b8a4a"  # fallback when there is no gainer cover
    for i, r in enumerate(picked, 1):
        tr = r["track"]
        entry = {**tr, "track_id": r["track_id"], "title": tr.get("title") or r["track_id"]}
        url = gs.get_cover_url(entry, cover_map, ta_map)
        img = cover(url)[0]
        album = display_title_for_album(ta_map.get(gs._norm(entry["title"]), "") or "")
        if i == 1 and url:  # chart colour = the #1 gainer's cover, like the album / playlist headers
            top = tone(url, ta_map.get(gs._norm(entry["title"]), ""))
        main, feat = song_title_parts(entry["title"], album)
        sub = " · ".join(x for x in (esc(album), esc(feat)) if x)
        total = hist.get_total_for_date(r["track_id"], d)
        rows.append(f"""<div class="trow"><div class="num">{i}</div><img src="{img}">
<div class="ttl">{title_span(main)}<span class="s one">{sub}</span>{best_line(labels.get(r['track_id']))}</div>
<div class="gain"><span class="pill">▲ {r['pct']:.1f}%</span></div>
<div class="day">{comma(r['daily_today'])}</div><div class="chg up"><span>+{comma(r['gain'])}</span><small class="plays">{comma(total)} plays</small></div></div>""")
    top = event_color(d) or top
    title = "Daily Gainers" if period == "daily" else "Weekly Gainers"
    vs = "vs yesterday" if period == "daily" else f"vs last {date.fromisoformat(d).strftime('%A')}"
    chart_cover = f"""<div class="ccover"><div class="cc-top">{SPOTIFY_SVG.replace('class="logo"', 'width="44" height="44"')}</div>
<div class="cc-t">{title.split()[0]}<br>{title.split()[1]}</div><div class="cc-s">Taylor Swift</div></div>"""
    body = f"""<div class="side">{chart_cover}<div class="kind" style="margin-top:34px">Chart · {'Daily' if period == 'daily' else 'Weekly'} update</div>
<div class="desc">Taylor Swift's biggest % gains {vs}<br>{esc(long_date(d))}</div>
<div class="actions" style="margin-top:34px">{PLAY}{SHUFFLE}</div>{foot()}</div>
<div class="list"><div class="grid"><div class="num">#</div><div></div><div>Title</div><div class="r">Gain</div><div class="r">Daily</div><div class="r">Gained</div></div>
{''.join(rows)}</div>"""
    css = """
.card{display:grid;grid-template-columns:460px minmax(0,1fr);column-gap:56px;padding:56px 56px 40px}
.side{display:flex;flex-direction:column}
.ccover{width:460px;height:460px;border-radius:10px;padding:34px;display:flex;flex-direction:column;
  background:linear-gradient(135deg,%(top)s,color-mix(in srgb,%(top)s 40%%,#000));box-shadow:0 14px 60px rgba(0,0,0,.55)}
.cc-t{margin-top:auto;font-size:92px;font-weight:900;line-height:.95;letter-spacing:-.04em}
.cc-s{font-size:28px;font-weight:700;margin-top:16px;color:rgba(255,255,255,.85)}
.title{font-size:84px;font-weight:900;letter-spacing:-.035em;line-height:1;margin:8px 0 18px}
.desc{font-size:27px;color:rgba(255,255,255,.8);line-height:1.4;margin-top:10px}
.foot{margin-top:auto}
.list{align-self:center;min-width:0}
.grid,.trow{grid-template-columns:44px 62px minmax(0,1fr) 170px 150px 190px}
.trow{min-height:92px;padding-top:8px;padding-bottom:8px}
.gain{text-align:right}
.chg small.plays{color:#b3b3b3;opacity:1}
.gain .pill{font-size:28px;color:#1ed760;background:rgba(30,215,96,.16)}
""".replace("%%", "%").replace("%(top)s", top)
    return page(W, top, body, css)


# ── render ───────────────────────────────────────────────────────────────────
def render(doc: str, out: Path, scale: float = 2) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"_{out.stem}.html")
    tmp.write_text(doc, encoding="utf-8")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg_ = b.new_page(viewport={"width": 2240, "height": 900}, device_scale_factor=scale)
            pg_.goto(tmp.as_uri(), wait_until="load")
            pg_.evaluate("document.fonts.ready")
            pg_.locator(".card").screenshot(path=str(out))
            b.close()
    finally:
        tmp.unlink(missing_ok=True)
    print(out)


def main() -> None:
    args = sys.argv[1:]
    # --theme=YYYY-MM-DD: real stats of DATE, theme of the day computed for another stats date
    # (preview of the timeline themes only; files get a _theme-<date> suffix)
    theme_d = next((a.split("=", 1)[1] for a in args if a.startswith("--theme=")), None)
    args = [a for a in args if not a.startswith("--")]
    d = next((a for a in args if a[:2] == "20"), None) or gs.get_latest_date()
    which = [a for a in args if a[:2] != "20"] or ["recap", "songs", "eras", "gainers"]
    sfx = d
    if theme_d:
        real = day_theme.theme_for
        day_theme.theme_for = lambda _d, **k: real(theme_d, **k)
        sfx = f"{d}_theme-{theme_d}"
    if "recap" in which:
        render(recap(d), OUT / f"1_recap_artist_page_{sfx}.png", scale=1.8)
    if "songs" in which:
        render(songs(d), OUT / f"2_top_songs_playlist_{sfx}.png", scale=1.8)
    if "eras" in which:
        render(eras(d), OUT / f"3_top_eras_discography_{sfx}.png")
    if "gainers" in which:
        render(gainers(d, "daily"), OUT / f"4_gainers_daily_charts_{sfx}.png")
        render(gainers(d, "weekly"), OUT / f"4_gainers_weekly_charts_{sfx}.png")


if __name__ == "__main__":
    main()
