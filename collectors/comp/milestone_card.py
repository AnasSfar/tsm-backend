"""Spotify total-stream milestone cards (1080x1350 portrait).

Three layouts, picked by the caller:
- ``plaque``  : album milestones (every 1B) — framed award plaque, gold record + engraved plate.
- ``climb``   : song milestones when our history covers ~the whole climb — total-streams curve
                with every milestone step marked (see ``climb_eligible``); gaps in the history
                are bridged with a straight solid segment between the two real totals.
- ``cover``   : every other song milestone — full-bleed cover, huge Bodoni number.

Fonts (Bodoni Moda + Archivo, OFL) are bundled in ``comp/fonts`` and loaded through
file:// @font-face, so rendering never needs the network. Every figure comes from the
caller's exact history; a step-to-step duration ("800M -> 900M: N days") is only shown when
both crossing days are known exactly (the day before each exists in the history).
"""
from __future__ import annotations

import base64
import colorsys
import html
from datetime import date
from io import BytesIO
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from PIL import Image as PilImage
except Exception:  # pragma: no cover
    PilImage = None

from comp.discography import display_title_for_album
from comp.img_fetch import fetch_data_uri
from comp.song_card import SPOTIFY_SVG, _tsm_logo_data_uri

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
CARD_W, CARD_H = 1080, 1350
SONG_STEP = 100_000_000
ALBUM_STEP = 1_000_000_000

# Climb eligibility: our first tracked total must be at most this share of the milestone
# (i.e. we saw ~the whole climb), and history must cover this share of the days since.
CLIMB_MAX_START_RATIO = 0.10
CLIMB_MIN_COVERAGE = 0.70
CLIMB_MIN_POINTS = 60

MONOCHROME_FALLBACK = {"paper": "#e8e7e3", "ink": "#1f2120", "accent": "#9c7c43",
                       "accent_light": "#d9bd84", "mute": "#76777a", "deep": "#2d2f2e",
                       # plaque: charcoal background, smoke vinyl, silver plate
                       "bg_hi": "#2c2d2d", "bg_lo": "#0e0f0f", "vinyl_a": "#3a3c3c", "vinyl_b": "#8d9090",
                       "metal_lo": "#8e9191", "metal_mid": "#c9cccc", "metal_hi": "#f4f5f5", "metal_ink": "#26292a"}


# ── formatting ──────────────────────────────────────────────────────────────

def _esc(value: object) -> str:
    return html.escape(str(value or ""))


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _short(value: int) -> tuple[str, str]:
    """200_000_000 -> ("200", "Million"); 1_500_000_000 -> ("1.5", "Billion")."""
    if value >= 1_000_000_000:
        n = value / 1_000_000_000
        return (f"{n:.1f}".rstrip("0").rstrip("."), "Billion")
    return (f"{value // 1_000_000}", "Million")


def _short_label(value: int) -> str:
    n, unit = _short(value)
    return f"{n}{unit[0]}"


def _long_date(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _short_date(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.strftime('%b')} {d.day}"


def _fit_size(text: str, max_px: int, width_px: int, em_per_char: float) -> int:
    return int(min(max_px, width_px / (em_per_char * max(len(text), 1))))


def _title_size(title: str, sizes: tuple[int, int, int]) -> int:
    n = len(title)
    return sizes[0] if n <= 22 else sizes[1] if n <= 34 else sizes[2]


# ── palette ─────────────────────────────────────────────────────────────────

def _hsv_hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def milestone_palette(img_bytes: bytes) -> dict:
    """paper/ink/mute/deep follow the cover's dominant hue; accent is its most vivid colour.
    Near-monochrome covers (folklore, reputation) get the grey/brass palette."""
    if not PilImage or not img_bytes:
        return dict(MONOCHROME_FALLBACK)
    try:
        img = PilImage.open(BytesIO(img_bytes)).convert("RGB").resize((96, 96))
        colors = img.quantize(colors=12, method=PilImage.Quantize.MEDIANCUT).convert("RGB").getcolors(96 * 96) or []
    except Exception:
        return dict(MONOCHROME_FALLBACK)
    total = sum(c for c, _ in colors) or 1
    hsv = [(c, colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)) for c, (r, g, b) in colors]
    avg_sat = sum(c * s for c, (_h, s, _v) in hsv) / total
    # Pastel covers (Lover) are mostly low-saturation / near-white colours: keep them, only
    # drop true greys and near-black, then pick the hue FAMILY with the most area x saturation
    # (so Lover reads pink, not the small vivid blue of the hair).
    chroma = [(c, h, s, v) for c, (h, s, v) in hsv if s >= 0.10 and v >= 0.15]
    if avg_sat < 0.12 or not chroma:
        return dict(MONOCHROME_FALLBACK)
    import math
    families: dict[int, list] = {}
    for item in chroma:
        families.setdefault(int(item[1] * 12) % 12, []).append(item)  # 30-degree hue families
    weight = lambda fam: sum(c * s for c, _h, s, _v in fam)
    best = max(families.values(), key=weight)
    x = sum(c * s * math.cos(h * 2 * math.pi) for c, h, s, _v in best)
    y = sum(c * s * math.sin(h * 2 * math.pi) for c, h, s, _v in best)
    dom = (math.atan2(y, x) / (2 * math.pi)) % 1.0
    _c, ah, as_, _av = max(best, key=lambda t: t[2])
    return {
        "paper": _hsv_hex(dom, 0.09, 0.94),
        "ink": _hsv_hex(dom, 0.32, 0.17),
        "mute": _hsv_hex(dom, 0.16, 0.50),
        "deep": _hsv_hex(dom, 0.38, 0.23),
        "accent": _hsv_hex(ah, min(max(as_, 0.50), 0.80), 0.62),
        "accent_light": _hsv_hex(ah, min(max(as_ * 0.75, 0.40), 0.65), 0.92),
        # plaque: background + coloured vinyl from the cover, plate = metal tinted by the accent
        "bg_hi": _hsv_hex(dom, 0.45, 0.24),
        "bg_lo": _hsv_hex(dom, 0.55, 0.07),
        "vinyl_a": _hsv_hex(ah, min(max(as_, 0.55), 0.85), 0.45),
        "vinyl_b": _hsv_hex(ah, min(max(as_ * 0.6, 0.30), 0.55), 0.95),
        "metal_lo": _hsv_hex(ah, 0.38, 0.62),
        "metal_mid": _hsv_hex(ah, 0.26, 0.84),
        "metal_hi": _hsv_hex(ah, 0.12, 0.99),
        "metal_ink": _hsv_hex(ah, 0.55, 0.20),
    }


def _cover(url: str | None) -> tuple[str, bytes]:
    uri = fetch_data_uri(url) if url else ""
    if not uri or "," not in uri:
        return "", b""
    try:
        return uri, base64.b64decode(uri.split(",", 1)[1])
    except Exception:
        return uri, b""


# ── history helpers (song cards) ────────────────────────────────────────────

def _clean_points(points: list[dict], stats_date: str) -> list[tuple[date, int]]:
    by_day: dict[str, int] = {}
    for p in points or []:
        d = str(p.get("date") or "")[:10]
        if d and d <= stats_date and p.get("streams") is not None:
            by_day[d] = int(p["streams"])
    return [(date.fromisoformat(d), v) for d, v in sorted(by_day.items())]


def exact_crossings(points: list[tuple[date, int]], step: int = SONG_STEP) -> dict[int, date]:
    """{threshold: day} for every threshold whose crossing day is known exactly
    (the point right before it is the previous calendar day)."""
    out: dict[int, date] = {}
    for (d0, v0), (d1, v1) in zip(points, points[1:]):
        if v1 <= v0:
            continue
        m = (v0 // step + 1) * step
        while m <= v1:
            if (d1 - d0).days == 1:
                out[m] = d1
            m += step
    return out


def climb_eligible(points: list[dict], milestone: int, stats_date: str) -> bool:
    pts = _clean_points(points, stats_date)
    if len(pts) < CLIMB_MIN_POINTS or pts[-1][0].isoformat() != stats_date:
        return False
    if pts[0][1] > milestone * CLIMB_MAX_START_RATIO:
        return False
    span = (pts[-1][0] - pts[0][0]).days + 1
    return len(pts) / span >= CLIMB_MIN_COVERAGE


def previous_step_days(points: list[dict], milestone: int, stats_date: str) -> tuple[int, int] | None:
    """(previous threshold, days it took to go from it to ``milestone``), exact crossings only."""
    prev = milestone - SONG_STEP
    if prev <= 0:
        return None
    cross = exact_crossings(_clean_points(points, stats_date))
    if prev not in cross or milestone not in cross:
        return None
    return prev, (cross[milestone] - cross[prev]).days


# ── shared CSS ──────────────────────────────────────────────────────────────

def _font_css() -> str:
    base = FONTS_DIR.as_uri()
    return f"""
@font-face{{font-family:"TSM Bodoni";src:url("{base}/BodoniModa.ttf") format("truetype");font-weight:400 900;font-style:normal}}
@font-face{{font-family:"TSM Bodoni";src:url("{base}/BodoniModa-Italic.ttf") format("truetype");font-weight:400 900;font-style:italic}}
@font-face{{font-family:"TSM Numerals";src:url("{base}/PlayfairDisplay.ttf") format("truetype");font-weight:400 900;font-style:normal}}
@font-face{{font-family:"TSM Archivo";src:url("{base}/Archivo.ttf") format("truetype");font-weight:100 900;font-stretch:62% 125%}}
"""


BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1080px;height:1350px;background:#111}
.card{position:relative;width:1080px;height:1350px;overflow:hidden;font-family:"TSM Archivo","Segoe UI",sans-serif;
  --display:"TSM Bodoni",Didot,Georgia,serif;--numerals:"TSM Numerals","TSM Bodoni",Georgia,serif}
.clamp2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.foot{position:absolute;left:72px;right:72px;bottom:56px;display:flex;align-items:center;gap:18px;font-size:30px;font-weight:600}
.foot .logo{width:64px;height:64px;flex:none;background:currentColor;
  -webkit-mask:var(--logo) center/contain no-repeat;mask:var(--logo) center/contain no-repeat}
.foot .date{margin-left:auto;font-weight:500}
svg.sp{flex:none;display:inline-block;vertical-align:middle}
"""


SPOTIFY_GREEN = "#1DB954"


def _spotify_icon(size: int, color: str = SPOTIFY_GREEN) -> str:
    return (SPOTIFY_SVG.replace('class="logo"', f'class="sp" width="{size}" height="{size}"')
            .replace('fill="white"', f'fill="{color}"'))


def _foot(stats_date: str) -> str:
    logo = _tsm_logo_data_uri()
    # db/logo.png is white on transparent: used as a mask so it takes the footer colour
    # (stays visible on the light Climb card too).
    mark = f'<span class="logo" style="--logo:url({logo})"></span>' if logo else ""
    return (f'<div class="foot">{mark}<span>@swiftiescharts</span>'
            f'<span class="date">{_long_date(stats_date)}</span></div>')


def _page(css: str, body: str, palette: dict) -> str:
    vars_ = ";".join(f"--{k.replace('_', '-')}:{v}" for k, v in palette.items())
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{_font_css()}{BASE_CSS}{css}</style></head>'
            f'<body><div class="card" style="{vars_}">{body}</div></body></html>')


# ── A. Plaque (albums) ──────────────────────────────────────────────────────

PLAQUE_CSS = """
.card{background:radial-gradient(120% 90% at 50% 30%,var(--bg-hi) 0%,var(--bg-lo) 72%)}
.frame{position:absolute;inset:34px;border:2px solid var(--metal-mid);opacity:.7;box-shadow:inset 0 0 0 10px rgba(0,0,0,.35),inset 0 0 0 12px var(--metal-lo)}
.eyebrow{position:absolute;top:96px;left:0;right:0;display:flex;align-items:center;justify-content:center;gap:22px;font-size:27px;letter-spacing:.42em;font-weight:700;color:var(--metal-mid);text-transform:uppercase}
.art{position:absolute;top:160px;left:0;right:0;height:560px}
.disc{position:absolute;top:10px;left:430px;width:540px;height:540px;border-radius:50%;
  background:radial-gradient(circle at 50% 50%,var(--bg-lo) 0 4%,var(--vinyl-b) 4.4% 17%,var(--vinyl-a) 17.4% 18%,transparent 18.4%),
    repeating-radial-gradient(circle at 50% 50%,rgba(0,0,0,.20) 0 1px,transparent 1px 5px),
    conic-gradient(from 30deg,var(--vinyl-a),var(--vinyl-b),var(--vinyl-a),var(--vinyl-b),var(--vinyl-a),var(--vinyl-b),var(--vinyl-a));
  box-shadow:0 20px 50px rgba(0,0,0,.6)}
.cover{position:absolute;top:10px;left:120px;width:540px;height:540px;object-fit:cover;background:var(--bg-hi);
  box-shadow:0 24px 60px rgba(0,0,0,.65),0 0 0 10px #f2ead8,0 0 0 12px rgba(0,0,0,.2)}
.plate{position:absolute;left:120px;right:120px;top:784px;height:360px;padding:26px 40px;text-align:center;color:var(--metal-ink);
  background:linear-gradient(160deg,var(--metal-lo) 0%,var(--metal-hi) 22%,var(--metal-mid) 45%,var(--metal-hi) 62%,var(--metal-lo) 85%,var(--metal-mid) 100%);
  box-shadow:0 18px 40px rgba(0,0,0,.55),inset 0 0 0 3px rgba(255,255,255,.35),inset 0 0 0 7px rgba(0,0,0,.18);
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px}
.big{font-family:var(--numerals);font-weight:900;font-variant-numeric:lining-nums;line-height:1;letter-spacing:-.01em;text-shadow:0 1px 0 rgba(255,255,255,.45);white-space:nowrap}
.unit{font-size:24px;letter-spacing:.34em;font-weight:800;text-transform:uppercase}
.rule{width:120px;height:2px;background:currentColor;opacity:.5;margin:10px 0 6px}
.ttl{font-family:var(--display);font-style:italic;font-weight:600;line-height:1.05}
.meta{font-size:28px;font-weight:600}
.foot{color:var(--metal-mid)}
"""


def render_album_plaque(*, album: str, cover_url: str | None, milestone: int, rank: int, stats_date: str) -> str:
    cover_uri, cover_bytes = _cover(cover_url)
    name = display_title_for_album(album)
    big = f"{milestone:,}"
    cover = f'<img class="cover" src="{cover_uri}" alt="">' if cover_uri else '<div class="cover"></div>'
    body = f"""<div class="frame"></div>
<div class="eyebrow">{_spotify_icon(38)}<span>Spotify Album Milestone</span></div>
<div class="art"><div class="disc"></div>{cover}</div>
<div class="plate">
  <div class="big" style="font-size:{_fit_size(big, 96, 760, 0.66)}px">{big}</div>
  <div class="unit">Streams on Spotify</div>
  <div class="rule"></div>
  <div class="ttl clamp2" style="font-size:{_title_size(name, (60, 48, 40))}px">{_esc(name)}</div>
  <div class="meta">Taylor Swift · {_ordinal(rank)} album to get here</div>
</div>
{_foot(stats_date)}"""
    return _page(PLAQUE_CSS, body, milestone_palette(cover_bytes))


# ── B. Cover Story (songs) ──────────────────────────────────────────────────

COVER_CSS = """
.card{background:var(--deep);color:#fff}
.cover{position:absolute;top:0;left:0;width:1080px;height:1080px;object-fit:cover}
.fade{position:absolute;top:420px;left:0;right:0;height:660px;background:linear-gradient(to bottom,transparent,var(--deep) 92%)}
.tag{position:absolute;top:56px;left:72px;padding:12px 22px;display:flex;align-items:center;gap:14px;background:var(--paper);color:var(--ink);font-size:24px;font-weight:800;letter-spacing:.22em;text-transform:uppercase}
.stack{position:absolute;left:58px;right:72px;bottom:292px;display:flex;flex-direction:column;gap:30px}
.num{display:flex;align-items:flex-end;gap:26px}
.num .n{font-family:var(--numerals);font-weight:900;font-variant-numeric:lining-nums;line-height:.78;letter-spacing:-.03em;color:var(--paper);text-shadow:0 10px 40px rgba(0,0,0,.35)}
.num .u{font-size:40px;font-weight:800;letter-spacing:.2em;text-transform:uppercase;line-height:1.15;padding-bottom:12px;color:var(--paper)}
.num .u span{display:block;color:var(--accent-light)}
.ttl{padding-left:14px;font-family:var(--display);font-style:italic;font-weight:600;line-height:1.05}
.facts{position:absolute;left:72px;right:72px;top:1080px;display:grid;grid-template-columns:repeat(3,1fr);border-top:2px solid rgba(255,255,255,.25);padding-top:22px}
.facts div{display:flex;flex-direction:column;gap:4px;padding-right:16px;min-width:0}
.facts div+div{border-left:2px solid rgba(255,255,255,.18);padding-left:24px}
.facts small{font-size:22px;letter-spacing:.16em;text-transform:uppercase;color:rgba(255,255,255,.7);font-weight:700}
.facts b{font-size:40px;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
.foot{color:rgba(255,255,255,.85)}
"""


def render_song_cover_story(*, title: str, cover_url: str | None, milestone: int, total: int, daily: int | None,
                            rank: int, stats_date: str, prev_step: tuple[int, int] | None) -> str:
    cover_uri, cover_bytes = _cover(cover_url)
    palette = milestone_palette(cover_bytes)
    n, unit = _short(milestone)
    n_size = 380 if len(n) <= 3 else 300
    if prev_step:
        middle = (f"Since {_short_label(prev_step[0])}", f"{prev_step[1]:,} days")
    elif daily is not None:
        middle = (f"On {_short_date(stats_date)}", f"+{daily:,}")
    else:
        middle = None
    facts = [("Taylor's songs", f"{_ordinal(rank)} ever")] + ([middle] if middle else []) + [("Exact total", f"{total:,}")]
    facts_html = "".join(f"<div><small>{_esc(a)}</small><b>{_esc(b)}</b></div>" for a, b in facts)
    cover = f'<img class="cover" src="{cover_uri}" alt="">' if cover_uri else ""
    body = f"""{cover}<div class="fade"></div>
<div class="tag">{_spotify_icon(30)}<span>New milestone</span></div>
<div class="stack"><div class="num"><div class="n" style="font-size:{n_size}px">{n}</div><div class="u">{unit}<span>Streams</span></div></div>
<div class="ttl clamp2" style="font-size:{_title_size(title, (66, 54, 44))}px">“{_esc(title)}”</div></div>
<div class="facts" style="grid-template-columns:repeat({len(facts)},1fr)">{facts_html}</div>
{_foot(stats_date)}"""
    return _page(COVER_CSS, body, palette)


# ── C. The Climb (songs with ~complete history) ─────────────────────────────

CLIMB_CSS = """
.card{background:var(--paper);color:var(--ink)}
.hd{position:absolute;top:64px;left:72px;right:72px;display:flex;gap:28px;align-items:center}
.hd img,.hd .ph{width:150px;height:150px;object-fit:cover;box-shadow:0 10px 24px rgba(0,0,0,.18);flex:none;background:var(--mute)}
.hd .t{min-width:0}
.hd .ttl{font-family:var(--display);font-style:italic;font-weight:600;line-height:1.05}
.hd .meta{font-size:30px;color:var(--mute);font-weight:600;margin-top:6px}
.head{position:absolute;top:262px;left:72px;right:72px}
.head .n{font-family:var(--numerals);font-weight:900;font-variant-numeric:lining-nums;line-height:1;letter-spacing:-.02em;white-space:nowrap}
.head .s{font-size:32px;font-weight:600;margin-top:10px;display:flex;align-items:center;gap:14px}
.head .s em{font-style:normal;color:var(--accent);font-weight:800}
svg.chart{position:absolute;top:500px;left:40px;width:1000px;height:540px;overflow:visible}
.stats{position:absolute;left:72px;right:72px;top:1080px;display:grid;gap:24px}
.stats div{border-top:4px solid var(--ink);padding-top:14px;display:flex;flex-direction:column;gap:2px;min-width:0}
.stats div:first-child{border-color:var(--accent)}
.stats small{font-size:22px;letter-spacing:.14em;text-transform:uppercase;color:var(--mute);font-weight:700}
.stats b{font-size:38px;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
.stats b.sm{font-size:28px;white-space:normal;line-height:1.15}
.stats .when{font-size:24px;font-weight:700;color:var(--accent)}
.foot{color:var(--ink)}
.foot .date{color:var(--mute)}
"""


def _grid_step(milestone: int) -> int:
    for step in (100_000_000, 200_000_000, 250_000_000, 500_000_000, 1_000_000_000):
        if milestone / step <= 10:
            return step
    return 1_000_000_000 * -(-milestone // 10_000_000_000)


def _climb_svg(points: list[tuple[date, int]], milestone: int, palette: dict) -> str:
    W, H, L, R, T, B = 1000, 540, 104, 36, 20, 58
    t0, t1 = points[0][0], points[-1][0]
    span = max((t1 - t0).days, 1)
    y1 = milestone * 1.06
    X = lambda d: L + (d - t0).days / span * (W - L - R)
    Y = lambda v: T + (1 - v / y1) * (H - T - B)
    ink, acc, mute, paper = palette["ink"], palette["accent"], palette["mute"], palette["paper"]
    font = 'font-family="TSM Archivo, sans-serif"'
    out: list[str] = []
    step = _grid_step(milestone)
    m = step
    while m <= milestone:
        top = m == milestone
        y = Y(m)
        if not top and Y(m) - Y(milestone) < 40:  # too close to the milestone line (e.g. 3B under 3.2B)
            m += step
            continue
        dash = "" if top else 'stroke-dasharray="6 8"'
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{acc if top else mute}" '
                   f'stroke-opacity="{.9 if top else .35}" stroke-width="{3 if top else 1.5}" {dash}/>')
        out.append(f'<text x="{L - 16}" y="{y + 9:.1f}" text-anchor="end" font-size="26" font-weight="{800 if top else 600}" '
                   f'fill="{acc if top else mute}" {font}>{_short_label(m)}</text>')
        m += step
    if milestone % step:
        y = Y(milestone)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{acc}" stroke-width="3"/>')
        out.append(f'<text x="{L - 16}" y="{y + 9:.1f}" text-anchor="end" font-size="26" font-weight="800" fill="{acc}" {font}>{_short_label(milestone)}</text>')
    years = range(t0.year + 1, t1.year + 1)
    label_years = [yr for yr in years if (t1 - date(yr, 1, 1)).days > 40]
    if len(label_years) > 7:
        label_years = label_years[::2]
    for yr in label_years:
        out.append(f'<text x="{X(date(yr, 1, 1)):.1f}" y="{H - 14}" text-anchor="middle" font-size="24" font-weight="600" fill="{mute}" {font}>{yr}</text>')

    # One continuous line (owner 2026-10-09): gaps in our history are old (2020-2022) and the
    # total is cumulative, so the straight segment between two real totals is drawn solid.
    # Thinned to ~weekly points, keeping both ends of every gap.
    keep = {0, len(points) - 1}
    for i in range(1, len(points)):
        if (points[i][0] - points[i - 1][0]).days > 7:
            keep |= {i - 1, i}
    thin = [p for i, p in enumerate(points) if i % 7 == 0 or i in keep]
    base = Y(0)
    line = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in thin)
    if len(thin) > 1:
        out.append(f'<polygon points="{X(thin[0][0]):.1f},{base:.1f} {line} {X(thin[-1][0]):.1f},{base:.1f}" fill="{acc}" fill-opacity=".14"/>')
        out.append(f'<polyline points="{line}" fill="none" stroke="{ink}" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>')
    # Step markers sit where the drawn line crosses each threshold (visual only — no date is
    # printed; the "800M -> 900M: N days" stat still uses exact crossings only).
    for (d0, v0), (d1, v1) in zip(points, points[1:]):
        if v1 <= v0:
            continue
        m = (v0 // step + 1) * step
        while m <= min(v1, milestone):
            x = X(d0) + (X(d1) - X(d0)) * (m - v0) / (v1 - v0)
            y = Y(m)
            if m == milestone:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="20" fill="{acc}" fill-opacity=".25"/>'
                           f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" fill="{acc}" stroke="{paper}" stroke-width="4"/>')
            else:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="{paper}" stroke="{ink}" stroke-width="4"/>')
            m += step
    return f'<svg class="chart" viewBox="0 0 {W} {H}">{"".join(out)}</svg>'


def render_song_climb(*, title: str, album: str | None, cover_url: str | None, milestone: int, total: int,
                      daily: int | None, rank: int, stats_date: str, history_points: list[dict],
                      next_expected: tuple[str, str] | None = None) -> str:
    cover_uri, cover_bytes = _cover(cover_url)
    palette = milestone_palette(cover_bytes)
    points = _clean_points(history_points, stats_date)
    prev = previous_step_days(history_points, milestone, stats_date)
    big = f"{milestone:,}"
    stats: list[str] = []
    if prev:
        stats.append(f"<div><small>{_short_label(prev[0])} → {_short_label(milestone)}</small><b>{prev[1]:,} days</b></div>")
    else:
        first = points[0][0]
        stats.append(f"<div><small>Tracked since</small><b>{first.strftime('%b')} {first.year}</b></div>")
    if daily is not None:
        stats.append(f"<div><small>On {_short_date(stats_date)}</small><b>+{daily:,}</b></div>")
    if next_expected:
        stats.append(f'<div><small>Next expected</small><b class="sm clamp2">{_esc(next_expected[0])}</b>'
                     f'<span class="when">{_short_date(next_expected[1])}</span></div>')
    else:
        stats.append(f"<div><small>Exact total</small><b>{total:,}</b></div>")
    album_name = display_title_for_album(album) if album else ""
    meta = "Taylor Swift" + (f" · {_esc(album_name)}" if album_name else "")
    cover = f'<img src="{cover_uri}" alt="">' if cover_uri else '<div class="ph"></div>'
    body = f"""<div class="hd">{cover}<div class="t"><div class="ttl clamp2" style="font-size:{_title_size(title, (60, 50, 42))}px">“{_esc(title)}”</div><div class="meta">{meta}</div></div></div>
<div class="head"><div class="n" style="font-size:{_fit_size(big, 132, 936, 0.66)}px">{big}</div>
<div class="s">{_spotify_icon(36)}<span>streams on Spotify · <em>{_ordinal(rank)}</em> Taylor Swift song to get here</span></div></div>
{_climb_svg(points, milestone, palette)}
<div class="stats" style="grid-template-columns:repeat({len(stats)},1fr)">{"".join(stats)}</div>
{_foot(stats_date)}"""
    return _page(CLIMB_CSS, body, palette)


# ── output ──────────────────────────────────────────────────────────────────

def write_milestone_png(html_text: str, output_path: Path, tmp_path: Path, *, keep_html: bool = False) -> Path:
    """Render at 1080x1350 (x2). The temp HTML must stay on disk during the render:
    fonts load from file:// URLs relative to nothing, so any folder works."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(html_text, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": CARD_W, "height": CARD_H}, device_scale_factor=2)
            page.goto(tmp_path.resolve().as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            page.locator(".card").screenshot(path=str(output_path))
            browser.close()
    finally:
        if not keep_html:
            tmp_path.unlink(missing_ok=True)
    return output_path
