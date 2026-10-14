"""Album update card styled like a Spotify album page (live since 2026-10-13).

Used by generate_album_update_image.generate() for the daily card, the weekend
"vs last week" card and the same-album overtake card (old table renderers stay as
fallback, see PAGE_STYLE there). Exact data only: every number comes from the
same loaders as before (load_album_sections / load_history_for_album / TOTAL ERA
scope / best_day_since); facts of the day -> album_page_facts.py.

Preview / test: previews_and_sims/spotify-album-page-card/simulate.py
"""
from __future__ import annotations

import colorsys
import html
import re
import sys
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

import generate_album_update_image as g
import album_page_facts as facts_mod
from comp.milestone_card import FONTS_DIR, _cover, milestone_palette
from comp.song_card import SPOTIFY_SVG, _tsm_logo_data_uri

REPO = Path(__file__).resolve().parents[5]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))  # collectors.youtube (music video badges)

W = 1720       # standard layout (cover column + one list)
W_WIDE = 2240  # albums with more than WIDE_FROM tracks: top band + two list columns
WIDE_FROM = 21  # rows = tracks + edition subtotals
MV_ICON = ('<svg viewBox="0 0 16 16" width="26" height="26"><rect x="1" y="2" width="14" height="12" rx="1.5" '
           'fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M6.5 5.5v5l4-2.5z" fill="currentColor"/></svg>')
FEAT_RE = re.compile(r"\s*[\(\[]((?:feat\.|with)\s[^\)\]]+)[\)\]]\s*$", re.I)


def esc(v) -> str:
    return html.escape(str(v))


def comma(n) -> str:
    return f"{int(n):,}"


def signed(n) -> tuple[str, str]:
    n = int(n or 0)
    return (f"+{n:,}" if n > 0 else f"{n:,}" if n < 0 else "0"), ("up" if n > 0 else "down" if n < 0 else "flat")


def pct_of(change: int, daily: int) -> float | None:
    prev = daily - change
    return change / prev * 100 if prev else None


# Covers whose auto colour reads wrong (owner feedback 2026-10-13): monochrome eras pick up a
# warm skin/gold tone, debut comes out blue. Header top colour, faded to #121212 below.
HEADER_OVERRIDES = {
    "folklore": "#6b6d70",                       # grey
    "reputation": "#3a3a3c",                     # black / charcoal
    "taylor swift": "#2f7a66",                   # debut green
    "the tortured poets department": "#8a7a66",  # beige / taupe
}


def header_color(palette: dict) -> str:
    """Spotify-like header tone: the cover's accent hue, mid saturation, mid value."""
    h = palette["accent"].lstrip("#")
    r, gg, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, s, _v = colorsys.rgb_to_hsv(r, gg, b)
    r, gg, b = colorsys.hsv_to_rgb(hue, min(s, 0.62), 0.50)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(gg * 255), round(b * 255))


def title_px(title: str, wide: bool = False) -> int:
    n = len(title)
    if wide:  # the band is ~1100px wide: two lines max
        size = 104 if n <= 22 else 88 if n <= 32 else 72
    else:
        size = 96 if n <= 12 else 80 if n <= 22 else 68 if n <= 28 else 58
    # a single word can't wrap: shrink until the longest one fits the 460px column (~0.64em/char, weight 900)
    longest = max((len(w) for w in title.split()), default=1)
    return min(size, int(460 / (longest * 0.64)))


def row_title_px(title: str) -> int:
    n = len(title)
    return 30 if n <= 24 else 27 if n <= 30 else 24


def sums(tracks: list[dict], hist: dict) -> tuple[int, int, int]:
    hs = [hist.get(t["track_id"]) or {} for t in tracks]
    return (sum(h.get("streams") or 0 for h in hs), sum(h.get("daily") or 0 for h in hs),
            sum(g._change_vs_yesterday(h) for h in hs))


def chg_cell(change: int, pct: float | None, new: bool = False) -> str:
    txt, cls = ("NEW", "new") if new else signed(change)
    small = f"<small>{pct:+.1f}%</small>" if pct is not None and not new else ""
    return f'<div class="chg {cls}"><span>{txt}</span>{small}</div>'


TV_RE = re.compile(r"\s*\(Taylor['’]s Version\)", re.I)
VAULT_RE = re.compile(r"\s*[\(\[]From The Vault[\)\]]", re.I)


# ── Official music videos, from the YouTube collector (no manual list) ─────────
# A video counts when the collector classifies it as a main-channel "videos" upload
# (video_category: not topic, not a short, not live/BTS/lyric/audio...) AND its title is
# either explicitly "(Official [Music] Video)" or the bare Vevo-era "Taylor Swift - Song"
# (only feat. / Taylor's Version / From The Vault allowed around the song name).
_MV_ALLOWED = re.compile(r"\s*[\(\[](?:(?:feat\.?|ft\.?|with)\s[^\)\]]*|taylor['’]s version|from the vault|"
                         r"official (?:music )?video)[\)\]]", re.I)
_MV_TRAIL_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?)\s.*$", re.I)


def _song_key(name: str) -> str:
    from collectors.youtube.core.title_groups import title_key
    return title_key(_MV_TRAIL_FEAT.sub("", _MV_ALLOWED.sub("", name)).strip())


def load_music_videos() -> dict[bool, set[str]]:
    """{is_taylors_version: {song key}} for every official music video in db/youtube_views_history.csv."""
    import csv
    from collectors.youtube.core.config import DISCOGRAPHY_SONGS_PATH, TOOLS_JSON_DIR, VIDEO_GROUPS_PATH
    from collectors.youtube.core.title_groups import (load_manual_groups, load_song_catalog,
                                                     load_video_categories, video_category)
    catalog = load_song_catalog(DISCOGRAPHY_SONGS_PATH)
    ctx = dict(catalog=catalog, catalog_keys={e["title_key"] for e in catalog},
               manual_groups=load_manual_groups(VIDEO_GROUPS_PATH),
               overrides=load_video_categories(TOOLS_JSON_DIR / "video_categories.json"))
    rows: dict[str, dict] = {}
    with open(REPO / "db/youtube_views_history.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows[row["video_id"]] = row  # last row = latest title/metadata
    out: dict[bool, set[str]] = {True: set(), False: set()}
    for row in rows.values():
        title = row.get("title") or ""
        if row.get("channel") == "topic" or " - " not in title or video_category(row, **ctx) != "videos":
            continue
        film = re.search(r"^(.*?)\s+-\s+(.+?):\s*the short film$", title, re.I)
        if film:  # "All Too Well: The Short Film" = the video of the 10 Minute Version (Taylor's Version)
            out[True].add(_song_key(f"{film.group(2)} (10 Minute Version)"))
            continue
        rest = _MV_ALLOWED.sub("", title)
        official = re.search(r"official (?:music )?video", title, re.I)
        bare = "(" not in rest and "[" not in rest and rest.count(" - ") == 1
        if official or bare:
            out[bool(TV_RE.search(title))].add(_song_key(title.split(" - ", 1)[1]))
    return out


_MV_CACHE: dict[bool, set[str]] | None = None


def has_music_video(track_title: str, tv_album: bool) -> bool:
    global _MV_CACHE
    if _MV_CACHE is None:
        try:
            _MV_CACHE = load_music_videos()
        except Exception as exc:  # badges are decoration: never block the card
            print(f"[album_page] music video lookup skipped: {exc}")
            _MV_CACHE = {True: set(), False: set()}
    tv = tv_album or bool(TV_RE.search(track_title))
    return _song_key(VAULT_RE.sub("", TV_RE.sub("", track_title))) in _MV_CACHE[tv]


def track_row(num: int, track: dict, h: dict, mv: bool = False, tv_album: bool = False, best: str | None = None,
              *, banner: str = "", move: int | None = None, hl: str = "") -> str:
    raw = track.get("title") or ""
    vault = bool(VAULT_RE.search(raw))
    raw = VAULT_RE.sub("", raw)
    if tv_album:  # redundant on a Taylor's Version album, like the album title already says
        raw = TV_RE.sub("", raw)
    m = FEAT_RE.search(raw)
    main, feat = (raw[:m.start()], m.group(1)) if m else (raw, "")
    daily = h.get("daily")
    sub = ([esc(feat)] if feat else []) + (["From The Vault"] if vault else [])
    if best:
        text = best_day_text(best)
        if sub:  # next to a feat. / From The Vault: shorter wording so the date is never cut
            text = text.replace("Best day since", "since")
            sub = [x.replace("feat. ", "ft. ", 1) for x in sub]
        sub.append(f'<span class="bd">★ {esc(text)}</span>')
    small = ' style="font-size:18px"' if best and len(sub) > 1 else ""
    feat_html = f'<span class="feat"{small}>{" · ".join(sub)}</span>' if sub else ""
    mv_html = f'<span class="mv">{MV_ICON}</span>' if mv else ""
    if banner:  # announced, not out yet: no number at all (data-rules n°1)
        cells = f'<div class="ann">{esc(banner)}</div>'
    else:
        cells = (f'<div class="tot">{comma(h.get("streams") or 0)}</div>'
                 f'<div class="day">{comma(daily) if daily is not None else "–"}</div>'
                 f'{chg_cell(h.get("change") or 0, h.get("pct"), h.get("ever_seen") is False)}')
    move_html = ""
    if move:
        move_html = f'<span class="mvm {"up" if move > 0 else "down"}">{"▲" if move > 0 else "▼"}{abs(move)}</span>'
    return f"""
<div class="row{' ' + hl if hl else ''}">
  <div class="num">{num}{move_html}</div>
  <div class="ttl"><div class="line">{mv_html}<span class="t {'clamp2' if len(main) > 32 else 'one'}" style="font-size:{row_title_px(main)}px;line-height:1.05">{esc(main)}</span></div>{feat_html}</div>
  {cells}
</div>"""


def best_day_text(since: str) -> str:
    """Always the date (owner rule 2026-10-13: never "of the year" / "of the month" alone)."""
    d = date.fromisoformat(since)
    return f"Best day since {d.strftime('%b')} {d.day}, {d.year}"


def album_best_text(row: dict) -> str:
    if row.get("kind") == "best_ever":
        return "Biggest day ever"
    return f"Best day since {g._format_best_day_since_label(row.get('best_day_since')) or row.get('best_day_since')}"


def split_title(title: str) -> str:
    """'You're Losing Me (From The Vault)' -> title + grey sub-line, so it never gets cut."""
    m = re.match(r"^(.*?)\s*[\(\[](.+)[\)\]]\s*$", title)
    if not m:
        return f'<span class="t one">{esc(title)}</span>'
    return f'<span class="t one">{esc(m.group(1))}</span><span class="feat">{esc(m.group(2))}</span>'


DISC_SVG = ('<svg viewBox="0 0 24 24" width="28" height="28"><circle cx="12" cy="12" r="10.5" fill="none" '
            'stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>')


def disc_row(i: int, section: dict, hist: dict) -> str:
    total, daily, change = sums(section["tracks"], hist)
    if section.get("announced"):
        cells = f'<div class="ann">{esc(section.get("announced_text") or "COMING SOON")}</div>'
    else:
        cells = f'<div class="tot">{comma(total)}</div><div class="day">{comma(daily)}</div>{chg_cell(change, pct_of(change, daily))}'
    return f"""
<div class="disc">
  <div class="num">{DISC_SVG}</div>
  <div class="ttl">{esc(g._format_section_name(re.sub(r'[ _]tv$', '', section['name'], flags=re.I)))} <span class="disc-n">Disc {i}</span></div>
  {cells}
</div>"""


def split_rows(rows: list[str]) -> list[list[str]]:
    """Two balanced columns, cut right after an edition subtotal when that stays balanced."""
    half = len(rows) / 2
    cuts = [i + 1 for i, r in enumerate(rows[:-1]) if 'class="disc"' in r]
    best = min(cuts, key=lambda c: abs(c - half), default=None)
    cut = best if best is not None and abs(best - half) <= 4 else -(-len(rows) // 2)
    return [rows[:cut], rows[cut:]]


# ── Era extras = prod TOTAL ERA scope (generate_album_update_image, 2026-10-13) ──
# = Top Eras scope: the whole album catalogue outside the tracklist, minus Spotify merge
# losers. Here we only split it for display: a real song outside the tracklist (vault /
# standalone single / soundtrack, not a remix or an "extra" upload) gets its own row.
STANDALONE_TAGS = {"standalone", "soundtrack", "vault__standalone", "featured__feature"}


def era_tracks(album: str, target_date: str, album_ids: set[str], era_ids: set[str]) -> tuple[list[dict], list[dict]]:
    import json
    payload = None
    for path in sorted(g.ALBUMS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and (data.get("album") or "").casefold() == album.casefold():
            payload = data
            break
    if not payload:
        return [], []
    seen: set[str] = set()
    groups: dict[str, dict] = {}
    extras: list[dict] = []
    for section in payload.get("sections", []):
        for t in section.get("tracks", []):
            m = re.search(r"track/([A-Za-z0-9]+)", t.get("url") or "")
            if not m or m.group(1) in album_ids or m.group(1) in seen or m.group(1) not in era_ids:
                continue
            release = (t.get("release_date") or "")[:10]
            if release and release > target_date:
                continue
            seen.add(m.group(1))
            item = {"track_id": m.group(1), "release_date": t.get("release_date") or "", "title": t.get("title") or ""}
            if (t.get("version_tag") or "") in STANDALONE_TAGS and t.get("role") != "extra":
                fam = t.get("song_family") or item["title"]
                grp = groups.setdefault(fam, {"title": t.get("base_title") or t.get("title_clean") or item["title"], "tracks": []})
                grp["tracks"].append(item)
            else:
                extras.append(item)
    return list(groups.values()), extras


def weekly_view(hist: dict) -> dict:
    """Weekend card (pipeline weekly_only): the change columns compare with the same weekday
    last week. Track not out a week ago -> NEW, its whole daily counts as the change."""
    out = {}
    for tid, h in hist.items():
        new = h.get("ever_seen") is False or bool(h.get("new_vs_week"))
        out[tid] = {**h, "change": h.get("weekly_change"), "pct": h.get("weekly_pct"), "ever_seen": not new}
    return out


def build(album: str, target_date: str, *, weekly: bool = False, overtake: bool = False,
          highlight: dict[str, str] | None = None) -> str:
    """highlight: {track_id: "overtaker"|"passed"} from post_song_overtakes (overtake card);
    without it the overtake card highlights every row that moved."""
    sections, album = g.load_album_sections(album, target_date)
    if not sections:
        raise ValueError(f"album not found in catalogue: {album!r}")
    hist = g.load_history_for_album(sections, target_date)
    if weekly:
        hist = weekly_view(hist)
    day_number = g._album_day_number(album, sections, target_date)
    display = g.display_title_for_album(album)
    cover_uri, cover_bytes = _cover(g.load_cover_url(album))
    top = HEADER_OVERRIDES.get(album.strip().casefold()) or header_color(milestone_palette(cover_bytes))

    tracks = g._album_total_tracks(sections)
    total, daily, change = sums(tracks, hist)
    # weekly change vs the same weekday last week (daily card only): exact only — every
    # album track needs its weekly_change, else no block (never an estimate)
    week_changes = [(hist.get(t["track_id"]) or {}).get("weekly_change") for t in tracks]
    week_change = sum(week_changes) if tracks and not weekly and all(c is not None for c in week_changes) else None
    pct = pct_of(change, daily)
    chg_txt, chg_cls = signed(change)
    years = sorted({(s.get("release_date") or "")[:4] for s in sections if s.get("release_date")})
    year = years[0] if years else ""

    g.drop_merged_era_extras(sections, hist)
    era_ids = {t["track_id"] for t in g._display_total_tracks(sections)} if g._has_era_context(sections) else set()
    standalones, extras = era_tracks(album, target_date, {t["track_id"] for t in tracks}, era_ids)
    era_hist = g.load_history_for_album([{"tracks": [t for grp in standalones for t in grp["tracks"]] + extras}],
                                        target_date) if (standalones or extras) else {}
    if weekly:
        era_hist = weekly_view(era_hist)

    tv_album = bool(TV_RE.search(album))
    # Album page = only the tracks that count in the album total (era extras / karaoke /
    # standalone singles are skipped, like the editions Spotify doesn't list).
    sections = [dict(sec, tracks=[t for t in sec["tracks"] if g._counts_in_album_total(t)]) for sec in sections]
    sections = [sec for sec in sections if sec["tracks"]]
    # Best day since: same records as the current album card (song rows) and its tweet (album).
    best_labels = {r["track_id"]: r["best_day_since"] for r in g._best_day_rows_for_sections(sections, target_date)
                   if r.get("track_id") and re.match(r"\d{4}-\d{2}-\d{2}$", str(r.get("best_day_since") or ""))}
    album_best = g._album_best_day_row(album, album, target_date)
    rows, n = [], 0
    if overtake:
        # Same-album overtake (post_song_overtakes): one list ranked by all-time total,
        # ▲/▼ vs yesterday's total position, movers highlighted. No edition subtotals.
        pool = sorted(tracks, key=lambda t: -((hist.get(t["track_id"]) or {}).get("streams") or 0))
        moves = g._flat_total_movement(pool, hist)
        if not highlight and not any(moves.values()):
            raise ValueError(f"no same-album overtake on {target_date} for {album}")
        for t in pool:
            n += 1
            mvv = moves.get(t["track_id"]) or 0
            rows.append(track_row(n, t, hist.get(t["track_id"]) or {}, has_music_video(t.get("title") or "", tv_album),
                                  tv_album, best_labels.get(t["track_id"]), move=mvv,
                                  hl=_overtake_class(t["track_id"], mvv, highlight)))
    else:
        for i, sec in enumerate(sections, 1):
            for t in sec["tracks"]:
                n += 1
                banner = (sec.get("announced_text") or "COMING SOON") if t.get("announced") else ""
                rows.append(track_row(n, t, hist.get(t["track_id"]) or {}, has_music_video(t.get("title") or "", tv_album),
                                      tv_album, best_labels.get(t["track_id"]), banner=banner))
            if len(sections) > 1:  # edition subtotal closes its own tracks
                rows.append(disc_row(i, sec, hist))
    total_row = f"""
<div class="total">
  <div class="num"></div>
  <div class="ttl">Total album</div>
  <div class="tot">{comma(total)}</div>
  <div class="day">{comma(daily)}</div>
  {chg_cell(change, pct)}
</div>"""
    if standalones or extras:
        for grp in standalones:  # a real song outside the tracklist: its own row
            st, sd, sc = sums(grp["tracks"], era_hist)
            total_row += f"""
<div class="extra song">
  <div class="num">+</div>
  <div class="ttl">{split_title(grp['title'])}</div>
  <div class="tot">{comma(st)}</div>
  <div class="day">{comma(sd)}</div>
  {chg_cell(sc, pct_of(sc, sd))}
</div>"""
        if extras:
            x_total, x_daily, x_change = sums(extras, era_hist)
            total_row += f"""
<div class="extra">
  <div class="num">+</div>
  <div class="ttl">Era extras <span class="disc-n">{len(extras)} track{'s' if len(extras) > 1 else ''}</span></div>
  <div class="tot">{comma(x_total)}</div>
  <div class="day">{comma(x_daily)}</div>
  {chg_cell(x_change, pct_of(x_change, x_daily))}
</div>"""
        era_all = tracks + [t for grp in standalones for t in grp["tracks"]] + extras
        merged = {**era_hist, **hist}
        e_total, e_daily, e_change = sums(era_all, merged)
        total_row += f"""
<div class="total era">
  <div class="num"></div>
  <div class="ttl">Total era</div>
  <div class="tot">{comma(e_total)}</div>
  <div class="day">{comma(e_daily)}</div>
  {chg_cell(e_change, pct_of(e_change, e_daily))}
</div>"""
    wide = len(rows) >= WIDE_FROM
    width = W_WIDE if wide else W
    if wide:
        cols = split_rows(rows)
        # totals strip spans both columns (below the two lists)
    else:
        cols = [rows + [total_row]]

    d = date.fromisoformat(target_date)
    long_date = f"{d.strftime('%B')} {d.day}, {d.year}"
    logo = _tsm_logo_data_uri()
    sp = SPOTIFY_SVG.replace('class="logo"', 'class="sp" width="38" height="38"').replace('fill="white"', 'fill="#1ed760"')
    check = ('<svg viewBox="0 0 24 24" width="56" height="56"><circle cx="12" cy="12" r="12" fill="#1ed760"/>'
             '<path d="M6.6 12.4l3.6 3.5 7.2-7.6" fill="none" stroke="#000" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    shuffle = ('<svg viewBox="0 0 24 24" width="52" height="52" fill="none" stroke="#b3b3b3" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
               '<path d="M3 6h3.5c2 0 3.2 1 4.3 2.6l2.4 3.6c1.1 1.6 2.3 2.8 4.3 2.8H21"/><path d="M3 18h3.5c1.6 0 2.7-.7 3.6-1.8"/>'
               '<path d="M14 7.8c.9-1 2-1.8 3.5-1.8H21"/><path d="M18.5 3.5L21 6l-2.5 2.5"/><path d="M18.5 12.5L21 15l-2.5 2.5"/></svg>')

    chg_head = "vs last wk" if weekly else "+/-"
    grid_head = f'<div class="grid"><div class="num">#</div><div>Title</div><div class="r">Plays</div><div class="r">Daily</div><div class="r">{chg_head}</div></div>'
    lists = "".join(f'<div class="list">{grid_head}{"".join(c)}</div>' for c in cols)
    kind = "Album · New overtake" if overtake else ("Album · Weekly update" if weekly else "Album · Daily update")
    if day_number:
        kind += f" · Day {day_number:,}"
    meta = f"""<div class="kind">{kind}</div>
  <div class="album clamp3" style="font-size:{title_px(display, wide)}px">{esc(display)}</div>
  <div class="by"><img src="{cover_uri}"><b>Taylor Swift</b><span>• {year} • {len(tracks)} songs</span></div>"""
    actions = f"""<div class="actions">
    <div class="play"><svg viewBox="0 0 24 24" width="42" height="42"><path d="M6 3.5v17L20.5 12z" fill="#000"/></svg></div>
    {shuffle}{check}
  </div>"""
    arrow = {"up": "▲ ", "down": "▼ "}.get(chg_cls, "")
    pct_pill = f'<span class="pill">{arrow}{abs(pct):.2f}%</span>' if pct is not None else ""
    album_best_html = f'<div class="abd">★ {esc(album_best_text(album_best))}</div>' if album_best else ""
    vs = f"vs last {d.strftime('%A')}" if weekly else "vs yesterday"
    # Weekly change: only when positive, only in the side column (classic layout). When the
    # side column ends up taller than the list, the list is centred vertically (.list CSS).
    week_html = ""
    if not wide and week_change is not None and week_change > 0:
        w_base = (daily or 0) - week_change
        w_pct = f'<span class="pill">▲ {week_change / w_base * 100:.2f}%</span>' if w_base > 0 else ""
        week_html = (f'<div class="wk"><div class="pills up"><span class="pill">▲ {comma(week_change)}</span>{w_pct}</div>'
                     f'<div class="vs">vs last {d.strftime("%A")}</div></div>')
    today = f"""<div class="today"><div class="lbl">STREAMS ON {esc(long_date.upper())}</div>
    <div class="big">{comma(daily)}</div>
    <div class="pills {chg_cls}"><span class="pill">{arrow}{chg_txt.lstrip('+-')}</span>{pct_pill}</div>
    <div class="vs">{vs}</div>{week_html}{album_best_html}</div>"""
    # album_facts sorts by relevance: keep only the best two in every layout.
    facts = [] if overtake else facts_mod.album_facts(album, target_date, hist, tracks)[:2]
    facts_html = f'<div class="facts">{"".join(f.html() for f in facts)}</div>' if facts else ""
    foot = f'<div class="foot"><span class="logo"></span><b>@swiftiescharts</b><span class="src">{sp}</span></div>'
    if wide:
        body = (f'<div class="hero"><img class="cover" src="{cover_uri}"><div class="meta">{meta}{facts_html}</div>'
                f'<div class="stat">{today}{actions}</div></div>{lists}<div class="totals">{total_row}</div>{foot}')
    else:
        body = f'<div class="side"><img class="cover" src="{cover_uri}">{meta}{actions}{today}{facts_html}{foot}</div>{lists}'

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:"TSM Archivo";src:url("{FONTS_DIR.as_uri()}/Archivo.ttf") format("truetype");font-weight:100 900;font-stretch:62% 125%}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{width}px;background:#121212}}
/* one continuous fade, header colour -> Spotify black: no seam between header and list */
.card{{width:{width}px;color:#fff;font-family:"TSM Archivo","Segoe UI",sans-serif;overflow:hidden;
  background:linear-gradient(180deg,{top} 0%,color-mix(in srgb,{top} 55%,#121212) 26%,color-mix(in srgb,{top} 18%,#121212) 55%,#121212 85%);
  display:grid;grid-template-columns:460px minmax(0,1fr);column-gap:56px;padding:56px 56px 40px}}
.clamp2{{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.clamp3{{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
.side{{display:flex;flex-direction:column}}
.cover{{width:460px;height:460px;border-radius:10px;object-fit:cover;box-shadow:0 14px 60px rgba(0,0,0,.55)}}
.kind{{margin-top:34px;font-size:26px;font-weight:600}}
.album{{font-weight:900;letter-spacing:-.035em;line-height:1;margin:8px 0 22px;font-stretch:96%}}
.by{{display:flex;align-items:center;gap:12px;font-size:26px;color:rgba(255,255,255,.75);white-space:nowrap}}
.by img{{width:42px;height:42px;border-radius:50%;object-fit:cover}}
.by b{{color:#fff;font-weight:800}}
.actions{{display:flex;align-items:center;gap:28px;margin-top:34px}}
.play{{width:96px;height:96px;border-radius:50%;background:#1ed760;display:grid;place-items:center;flex:none}}
.play svg{{margin-left:7px}}
.today{{margin-top:34px}}
.today .lbl{{font-size:22px;color:#b3b3b3;font-weight:700;letter-spacing:.06em}}
.today .big{{font-size:64px;font-weight:900;letter-spacing:-.025em;line-height:1.05;margin-top:4px}}
.pills{{display:flex;flex-wrap:wrap;gap:10px;margin-top:14px}}
.pill{{font-size:31px;font-weight:900;letter-spacing:-.01em;padding:7px 15px;border-radius:999px;font-variant-numeric:tabular-nums;white-space:nowrap}}
.pills.down .pill{{color:#ff7a85;background:rgba(241,94,108,.18)}}
.pills.up .pill{{color:#1ed760;background:rgba(30,215,96,.16)}}
.pills.flat .pill{{color:#b3b3b3;background:rgba(255,255,255,.08)}}
.bd{{color:#f5c84c;font-weight:700}}
.ann{{grid-column:3/6;text-align:center;font-size:22px;font-weight:800;letter-spacing:.08em;color:#fff;
  background:rgba(255,255,255,.10);border-radius:999px;padding:8px 14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.row.ov-up{{background:rgba(30,215,96,.14);box-shadow:inset 4px 0 0 #1ed760;border-radius:6px}}
.row.ov-down{{background:rgba(241,94,108,.14);box-shadow:inset 4px 0 0 #f15e6c;border-radius:6px}}
.mvm{{display:block;font-size:17px;font-weight:800;line-height:1}}
.mvm.up{{color:#1ed760}} .mvm.down{{color:#ff7a85}}
.abd{{display:inline-block;margin-top:14px;font-size:27px;font-weight:800;color:#f5c84c;background:rgba(245,200,76,.14);padding:7px 16px;border-radius:999px}}
.vs{{margin-top:10px;font-size:21px;color:#b3b3b3;font-weight:600}}
.wk{{margin-top:22px}}
.facts{{margin-top:26px;display:flex;flex-direction:column;gap:12px}}
.fact{{display:flex;gap:12px;align-items:baseline;font-size:23px;line-height:1.25;color:#d9d9d9}}
.fact .fi{{flex:none;width:24px;text-align:center;color:#1ed760;font-weight:900}}
.fact b{{color:#fff;font-weight:800}}
.wide .facts{{margin-top:18px;gap:8px}}
.wide .fact{{font-size:22px}}
.foot{{margin-top:auto;padding-top:30px;display:flex;align-items:center;gap:14px;color:#b3b3b3;font-size:24px}}
.foot .logo{{width:50px;height:50px;background:#fff;-webkit-mask:url({logo}) center/contain no-repeat}}
.foot b{{color:#fff;font-size:26px}}
.foot .src{{margin-left:auto;display:flex;align-items:center;gap:10px}}
.list{{min-width:0}}
.card:not(.wide) .list{{align-self:center}}
/* wide: hero as a top band, lists side by side, footer full width */
.card.wide{{grid-template-columns:1fr 1fr}}
.wide .hero{{grid-column:1/-1;display:flex;align-items:flex-end;gap:44px;margin-bottom:40px}}
.wide .cover{{width:330px;height:330px;flex:none}}
.wide .meta{{flex:1;min-width:0}}
.wide .kind{{margin-top:0}}
.wide .album{{margin:6px 0 18px}}
.wide .stat{{flex:none;display:flex;flex-direction:column;align-items:flex-end;gap:22px}}
.wide .actions,.wide .today{{margin-top:0}}
.wide .today{{text-align:right}}
.wide .pills{{justify-content:flex-end}}
.wide .foot{{grid-column:1/-1}}
.wide .totals{{grid-column:1/-1;margin-top:8px}}
.wide .totals .total,.wide .totals .extra{{grid-template-columns:52px minmax(0,1fr) 200px 166px 140px}}
.wide .grid,.wide .row,.wide .disc,.wide .total,.wide .extra{{grid-template-columns:52px minmax(0,1fr) 200px 166px 140px}}
.grid,.row,.disc,.total,.extra{{display:grid;grid-template-columns:52px minmax(0,1fr) 220px 184px 150px;align-items:center;column-gap:14px}}
.grid{{padding:0 12px 14px;border-bottom:1px solid rgba(255,255,255,.14);color:#b3b3b3;font-size:23px;font-weight:600}}
.grid .r,.tot,.day,.chg{{text-align:right}}
.grid .num{{text-align:center}}
.row,.disc,.total,.extra{{padding:0 12px;height:62px}}
.row .num{{text-align:center;color:#b3b3b3;font-size:26px;font-variant-numeric:tabular-nums}}
.ttl{{min-width:0;display:flex;flex-direction:column;justify-content:center}}
.ttl .t{{font-weight:600;line-height:1.12}}
.one{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ttl .feat{{font-size:20px;color:#b3b3b3;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.line{{display:flex;align-items:center;gap:12px;min-width:0}}
.mv{{flex:none;display:flex;color:#b3b3b3}}
.tot{{font-size:26px;color:#b3b3b3;font-variant-numeric:tabular-nums}}
.day{{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}}
.chg{{display:flex;flex-direction:column;align-items:flex-end;font-size:23px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums}}
.chg small{{font-size:18px;font-weight:600;opacity:.8}}
.up{{color:#1ed760}} .down{{color:#f15e6c}} .flat{{color:#b3b3b3}} .new{{color:#5bbde4}}
.disc{{margin:2px 0 18px;height:58px;border-top:1px solid rgba(255,255,255,.08)}}
.disc .num{{color:#b3b3b3;display:grid;place-items:center}}
.disc .ttl{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:27px;font-weight:800}}
.disc .disc-n{{margin-left:12px;font-size:20px;font-weight:600;color:#b3b3b3;text-transform:uppercase;letter-spacing:.08em}}
.disc .tot{{font-size:23px}} .disc .day{{font-size:24px;color:#b3b3b3}}
.total{{margin-top:14px;height:76px;border-top:1px solid rgba(255,255,255,.14)}}
.total .ttl{{display:block;font-size:30px;font-weight:900}}
.total .tot{{font-size:27px;color:#fff;font-weight:700}}
.total .day{{font-size:30px;font-weight:900}}
.extra{{height:58px}}
.extra .num{{text-align:center;color:#b3b3b3;font-size:26px;font-weight:700}}
.extra .ttl{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:26px;font-weight:700;color:#d6d6d6}}
.extra .disc-n{{margin-left:12px;font-size:20px;font-weight:600;color:#b3b3b3}}
.extra .tot{{font-size:23px}} .extra .day{{font-size:24px;color:#b3b3b3}}
.total.era{{margin-top:0}}
.extra.song .ttl{{color:#fff;display:flex;flex-direction:column;justify-content:center;white-space:normal}}
</style></head><body><div class="card{' wide' if wide else ''}">
{body}
</div></body></html>"""


def _overtake_class(track_id: str, move: int, highlight: dict[str, str] | None) -> str:
    if highlight:
        role = highlight.get(track_id)
        return "ov-up" if role == "overtaker" else "ov-down" if role == "passed" else ""
    return "ov-up" if move > 0 else "ov-down" if move < 0 else ""


def render_png(album: str, target_date: str, out_path: Path, *, weekly: bool = False, overtake: bool = False,
               highlight: dict[str, str] | None = None) -> Path:
    """Write the card PNG. The temp HTML sits next to it during the render (fonts are file://)."""
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(f"_{out_path.stem}.html")
    doc = build(album, target_date, weekly=weekly, overtake=overtake, highlight=highlight)
    wide = 'class="card wide"' in doc
    tmp.write_text(doc, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # x2, but the wide card stays under X's 4096 px (2240 x 1.8 = 4032)
            page = browser.new_page(viewport={"width": W_WIDE, "height": 900}, device_scale_factor=1.8 if wide else 2)
            page.goto(tmp.as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            page.evaluate(facts_mod.TRIM_JS)
            page.locator(".card").screenshot(path=str(out_path))
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)
    return out_path
