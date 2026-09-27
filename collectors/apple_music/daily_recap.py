"""Daily recap thread of an album on Apple Music + iTunes (owner 2026-09-26).

Generates ONLY — never posts: the owner posts the thread by hand. Output =
4 cards (Apple Music songs, Apple Music albums, iTunes songs, iTunes albums)
+ thread.txt with the 4 tweet texts, in tools/cards/daily_recap/<date>/.

Counts are the day's CUMULATIVE (owner's choice): per country, the song's best
rank over every hourly cycle of the US day (00:00-24:00 New York) (all its Apple ids merged by
title) — "#1" = countries where it was #1 at least once that day. Buckets are
exclusive for #1/#2/#3 (a country counts once, at its best rank) and
inclusive for Top 10 / Charting. Peak = best rank of the day on the key chart
(Global for Apple Music songs, US otherwise). Real ranks only, nothing
inferred.

  python collectors/apple_music/daily_recap.py                 # last finished US day, Showgirl
  python collectors/apple_music/daily_recap.py --date 2026-09-25
  python collectors/apple_music/daily_recap.py --album showgirl --out-dir previews_and_sims/x
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(REPO_ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.discography import (  # noqa: E402
    load_release_dates,
    resolve_album_filter,
    song_key_candidates,
    song_name_key,
)
from collectors.spotify.core.data_paths import apple_music_charts_dir, itunes_charts_dir  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")
# The recap day is a US day (owner 2026-09-26: "entre minuit US time et minuit
# d'apres"): 00:00 -> 24:00 New York = 06:00 -> 06:00 Paris. Snapshot files are
# per Paris date, so one US day spans two of them.
US_TZ = ZoneInfo("America/New_York")
OUT_ROOT = HERE / "tools" / "cards" / "daily_recap"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SOURCES = [
    {"id": "am_songs", "platform": "Apple Music", "kind": "song", "chart": "Songs",
     "csv": lambda d: apple_music_charts_dir(d) / "apple_music_country_charts.csv",
     "peak_csv": lambda d: apple_music_charts_dir(d) / "apple_music_global.csv", "peak_region": None,
     "peak_label": "Global peak", "site": "applemusic", "new_songs_only": True},
    {"id": "am_albums", "platform": "Apple Music", "kind": "album", "chart": "Albums",
     "csv": lambda d: apple_music_charts_dir(d) / "apple_music_country_albums.csv",
     "peak_csv": None, "peak_region": "us", "peak_label": "US peak", "site": "applemusic"},
    {"id": "it_songs", "platform": "iTunes", "kind": "song", "chart": "Songs",
     "csv": lambda d: itunes_charts_dir(d) / "itunes_top_songs.csv",
     "peak_csv": None, "peak_region": "us", "peak_label": "US peak", "site": "itunes"},
    {"id": "it_albums", "platform": "iTunes", "kind": "album", "chart": "Albums",
     "csv": lambda d: itunes_charts_dir(d) / "itunes_top_albums.csv",
     "peak_csv": None, "peak_region": "us", "peak_label": "US peak", "site": "itunes"},
]


def album_family(name: str) -> str:
    """Edition-free album name (post_new_release_progression's rule, + the
    " + Acoustic Collection" / " + \"A Look Behind the Curtain\"" editions):
    "The Life of a Showgirl: The Encore" -> "the life of a showgirl"."""
    return re.split(r"\s*[:(\[]|\s\+\s", str(name or "").strip().lower(), maxsplit=1)[0].strip()


def _rank(value: object) -> int | None:
    try:
        rank = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def _us_day_window(us_date: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(us_date, "%Y-%m-%d").replace(tzinfo=US_TZ)
    return start, (start + timedelta(days=1)).replace(tzinfo=US_TZ)


def _scraped(value: object) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=PARIS)  # old rows without offset = Paris


def _read(path_for, us_date: str) -> list[dict]:
    """Rows of every cycle scraped during the US day `us_date` (two Paris-dated
    snapshot files)."""
    start, end = _us_day_window(us_date)
    out: list[dict] = []
    for paris_day in sorted({start.astimezone(PARIS).date(), (end - timedelta(seconds=1)).astimezone(PARIS).date()}):
        path = path_for(paris_day.strftime("%Y-%m-%d"))
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                at = _scraped(r.get("scraped_at"))
                if at is not None and start <= at < end:
                    out.append(r)
    return out


def _matches(row: dict, kind: str, family: str, song_keys: set[str]) -> bool:
    if kind == "album":
        return album_family(row.get("album_name")) == family
    return any(k in song_keys for k in song_key_candidates(row.get("song_name")))


def new_song_keys(song_keys: set[str]) -> set[str]:
    """Keys of the album's NEWEST songs = catalog release date equal to the
    album's latest one (edition-normalised by load_release_dates): the 4
    Encore songs for Showgirl on 2026-09-26."""
    rel = load_release_dates()
    dates = {k: rel[k][:10] for k in song_keys if rel.get(k)}
    latest = max(dates.values(), default="")
    return {k for k, d in dates.items() if d == latest}


def build_source(source: dict, chart_date: str, family: str, song_keys: set[str], album_display: str) -> dict:
    kind = source["kind"]
    if source.get("new_songs_only"):
        # Owner 2026-09-26: Apple Music songs = the new songs only.
        song_keys = new_song_keys(song_keys)
    name_field = "album_name" if kind == "album" else "song_name"
    rows = [r for r in _read(source["csv"], chart_date) if _matches(r, kind, family, song_keys)]
    cycles = sorted({str(r.get("scraped_at") or "") for r in rows if r.get("scraped_at")})

    items: dict[str, dict] = {}
    for r in rows:
        rank = _rank(r.get("rank"))
        if rank is None:
            continue
        # Albums: every edition/id is ONE album (Apple swapped the Encore for
        # the standard id at #1 at 01:00 on 2026-09-26 — same album).
        key = family if kind == "album" else song_name_key(r.get(name_field))
        item = items.setdefault(key, {"names": {}, "images": {}, "best": {}, "album": r.get("album_name") or ""})
        name = str(r.get(name_field) or "").strip()
        item["names"][name] = item["names"].get(name, 0) + 1
        if r.get("image_url"):
            # Album artwork beats the single's ("Patient Zero - Single" on iTunes):
            # rows released on the album itself weigh far more in the cover vote.
            weight = 1000 if album_family(r.get("album_name")) == family else 1
            item["images"][r["image_url"]] = item["images"].get(r["image_url"], 0) + weight
        cc = str(r.get("country") or "").lower()
        if cc and (cc not in item["best"] or rank < item["best"][cc]):
            item["best"][cc] = rank

    peaks: dict[str, int] = {}
    if source["peak_csv"]:
        for r in _read(source["peak_csv"], chart_date):
            if not _matches(r, kind, family, song_keys):
                continue
            rank = _rank(r.get("rank"))
            key = family if kind == "album" else song_name_key(r.get(name_field))
            if rank and (key not in peaks or rank < peaks[key]):
                peaks[key] = rank
    else:
        for key, item in items.items():
            if source["peak_region"] in item["best"]:
                peaks[key] = item["best"][source["peak_region"]]

    out: list[dict] = []
    for key, item in items.items():
        best = item["best"].values()
        out.append({
            "name": album_display if kind == "album" else max(item["names"], key=item["names"].get),
            "album": item["album"],
            "image_url": max(item["images"], key=item["images"].get) if item["images"] else "",
            "n1": sum(1 for r in best if r == 1),
            "n2": sum(1 for r in best if r == 2),
            "n3": sum(1 for r in best if r == 3),
            "top10": sum(1 for r in best if r <= 10),
            "charting": len(item["best"]),
            "peak": peaks.get(key),
            "countries_n1": {cc for cc, r in item["best"].items() if r == 1},
            "countries": set(item["best"]),
            "points": sum(worldwide_points(r) for r in best),
        })
    out.sort(key=lambda e: (-e["n1"], -e["n2"], -e["n3"], -e["top10"], -e["charting"],
                            e["peak"] or 999, e["name"].lower()))
    return {**source, "items": out, "cycles": cycles}


def worldwide_points(rank: int) -> float:
    """Worldwide iTunes chart points of one country's best rank of the day:
    500/rank^0.75 (TS Top Songs' curve) with NO market weight (owner
    2026-09-26: every country counts the same, kworb style)."""
    return 500 / rank ** 0.75


def render_worldwide(src: dict, chart_date: str, album_name: str, album_display: str, out_path: Path) -> list[dict]:
    """Worldwide iTunes songs chart of the day: the album's songs ranked by
    worldwide points (sum over every country of worldwide_points)."""
    items = sorted(src["items"], key=lambda e: (-e["points"], e["name"].lower()))
    body = "".join(
        _item_html(i, e, _stat("Points", round(e["points"]), gold=i == 1) + _stat("#1", e["n1"], gold=True)
                   + _stat("Top 10", e["top10"]) + _stat("Countries", e["charting"]))
        for i, e in enumerate(items, 1)
    )
    _recap_page(src, chart_date, album_name, album_display, "Worldwide",
                "points = Σ 500 / rank^0.75 of each country's best rank of the day, no market weight",
                body, out_path)
    return items


def worldwide_tweet(items: list[dict], album_display: str, chart_date: str) -> str:
    from collectors.twitter.links import amcharts_url

    day = datetime.strptime(chart_date, "%Y-%m-%d")
    head = f'🌍 | Worldwide iTunes chart of "{album_display}" songs — {day.strftime("%b")} {day.day} (US time):'
    lines = [f'#{i} "{e["name"]}" — {round(e["points"]):,} pts' for i, e in enumerate(items[:5], 1)]
    while lines:
        tweet = f"{head}\n\n" + "\n".join(lines) + f"\n\n{amcharts_url('itunes')}"
        if _weighted(tweet) <= 275:
            return tweet
        lines.pop()
    return f"{head}\n\n{amcharts_url('itunes')}"


def _day_label(chart_date: str, cycles: list[str]) -> str:
    day = datetime.strptime(chart_date, "%Y-%m-%d")
    label = f"{day.strftime('%b')} {day.day}, {day.year}"
    label += " (US time)"
    today = datetime.now(US_TZ).strftime("%Y-%m-%d")
    if chart_date == today and cycles:
        last = _scraped(cycles[-1]).astimezone(US_TZ)
        label += f" · through {last.hour % 12 or 12}:{last.minute:02d} {'AM' if last.hour < 12 else 'PM'} ET"
    return label


def _stat(label: str, value, gold: bool = False, prefix: str = "") -> str:
    cls = "stat" + (" stat-one" if gold and value else "") + ("" if value else " stat-zero")
    shown = f"{prefix}{value:,}" if value else "–"
    return f'<div class="{cls}"><div class="stat-label">{label}</div><div class="stat-value">{shown}</div></div>'


def _item_html(i: int | None, e: dict, stats: str) -> str:
    from collectors.comp.tables_image import url_to_data_uri

    cover = url_to_data_uri(e["image_url"]) if e["image_url"] else ""
    art = f'<img class="item-cover" src="{cover}" />' if cover else ""
    rank = f'<span class="item-rank">{i}</span>' if i else ""
    return (f'<div class="item"><div class="item-head">{rank}{art}<span class="item-title">'
            f'{html.escape(e["name"])}</span></div>'
            f'<div class="stats" style="grid-template-columns:repeat({stats.count("stat-label")}, 1fr)">{stats}</div></div>')


def _recap_page(src: dict, chart_date: str, album_name: str, album_display: str, chart_label: str,
                when_sub: str, body: str, out_path: Path) -> None:
    import post_new_release_progression as prog

    platform = "apple_music" if src["platform"] == "Apple Music" else "itunes"
    doc = prog.rank_card_page(
        image_url=prog._album_cover_url(album_name, ""), title=album_display, subtitle="Taylor Swift",
        platform=platform, chart_label=chart_label,
        when_main=f"{_day_label(chart_date, src['cycles'])} · Daily recap", when_sub=when_sub,
        body_html=body, footer_left=f'{html.escape(prog.PLATFORMS[platform]["footer"])} · {_day_text(chart_date)}',
    )
    prog._render_card_png(doc, out_path, scale=2)


def render(src: dict, chart_date: str, album_name: str, album_display: str, out_path: Path) -> None:
    """Recap cards 1-4: one block per song/album — countries at #1 / #2 / #3,
    top 10, charting (best rank of the day per country) + peak on the key chart."""
    items = src["items"]
    if not items:
        body = '<div class="item"><div class="item-title">No entries today</div></div>'
    else:
        body = "".join(
            _item_html(None, e, _stat("#1", e["n1"], gold=True) + _stat("#2", e["n2"]) + _stat("#3", e["n3"])
                       + _stat("Top 10", e["top10"]) + _stat("Charting", e["charting"])
                       + _stat(src["peak_label"], e["peak"], gold=e["peak"] == 1, prefix="#"))
            for e in items
        )
    _recap_page(src, chart_date, album_name, album_display, src["chart"],
                "countries counted at their best rank of the day", body, out_path)


def _weighted(text: str) -> int:
    # Same guard as post_new_release_progression: link = 23, emoji = 2, and
    # never over 280 raw chars (twitter.py refuses those).
    urls = re.findall(r"https?://\S+", text)
    x = len(text) + sum(23 - len(u) for u in urls) + sum(1 for ch in text if ord(ch) > 0x2000)
    return max(x, len(text) - 5)


def _countries(n: int) -> str:
    return f"{n} {'country' if n == 1 else 'countries'}"


def tweet_for(src: dict, album_display: str, emoji: str, opener: str | None) -> str:
    from collectors.twitter.links import amcharts_url

    items = src["items"]
    what = f"{src['platform']} {src['chart'].lower()}"
    head = opener or f"{emoji} | {what}:"
    if not items:
        return f"{head}\n\nNo entries today."
    lead = items[0]
    lines = []
    if lead["n1"]:
        line = f'"{lead["name"]}" hit #1 in {_countries(lead["n1"])}'
        others = [e for e in items[1:3] if e["n1"]]
        if others:
            line += ", " + ", ".join(f'"{e["name"]}" in {e["n1"]}' for e in others)
        lines.append(line + ".")
    else:
        lines.append(f'"{lead["name"]}" reached the top 10 in {_countries(lead["top10"])}.')
    any_n1 = set().union(*(e["countries_n1"] for e in items))
    charting = set().union(*(e["countries"] for e in items))
    if len(items) > 1:
        tail = f"{len(items)} songs charted in {_countries(len(charting))}"
        lines.append(tail + (f", #1 in {_countries(len(any_n1))} overall." if any_n1 else "."))
    else:
        lines.append(f"Top 10 in {_countries(lead['top10'])}, charting in {_countries(lead['charting'])}.")
    tweet = f"{head}\n\n" + "\n".join(lines) + f"\n\n{amcharts_url(src['site'])}"
    if _weighted(tweet) > 275 and lead["n1"]:
        lines[0] = f'"{lead["name"]}" hit #1 in {_countries(lead["n1"])}.'
        tweet = f"{head}\n\n" + "\n".join(lines) + f"\n\n{amcharts_url(src['site'])}"
    if _weighted(tweet) > 275:
        tweet = tweet.rsplit("\n\n", 1)[0]
    return tweet


# --- Per-song recap cards (owner 2026-09-26: "le peak rank dans chaque country") ---

SONG_SOURCES = {
    "apple_music": [
        (lambda d: apple_music_charts_dir(d) / "apple_music_global.csv", "global"),
        (lambda d: apple_music_charts_dir(d) / "apple_music_country_charts.csv", None),
    ],
    "itunes": [(lambda d: itunes_charts_dir(d) / "itunes_top_songs.csv", None)],
    # Album card (owner 2026-09-26): Top Albums per country, no Global album chart.
    "apple_music_albums": [(lambda d: apple_music_charts_dir(d) / "apple_music_country_albums.csv", None)],
}


def song_matcher(key: str):
    return lambda r: key in song_key_candidates(r.get("song_name"))


def album_matcher(family: str):
    """Every edition/id of the album (same rule as build_source's albums)."""
    return lambda r: album_family(r.get("album_name")) == family


def _song_best_by_region(platform: str, day: str, match) -> tuple[dict[str, int], str]:
    """{region: best rank of that US day} for one song (all its Apple ids,
    matched by title) or one album (all editions) + one cover URL seen that day."""
    best: dict[str, int] = {}
    image = ""
    for path_for, fixed_region in SONG_SOURCES[platform]:
        for r in _read(path_for, day):
            if not match(r):
                continue
            rank = _rank(r.get("rank"))
            region = fixed_region or str(r.get("country") or "").lower()
            if rank is None or not region:
                continue
            if region not in best or rank < best[region]:
                best[region] = rank
            image = image or str(r.get("image_url") or "")
    return best, image


def song_placements(platform: str, chart_date: str, match, release_day: str) -> tuple[list[dict], str]:
    """One placement per region where the song charted today: rank = best rank
    of the day, peak = best rank since its release day (our CSVs, real data
    only). NEW = first day on that chart since release; NEW PEAK = today beats
    every earlier day."""
    from core.card_theme import country_label

    today, image = _song_best_by_region(platform, chart_date, match)
    before: dict[str, int] = {}
    if release_day:
        day = datetime.strptime(release_day, "%Y-%m-%d")
        end = datetime.strptime(chart_date, "%Y-%m-%d")
        while day < end:
            best, _img = _song_best_by_region(platform, day.strftime("%Y-%m-%d"), match)
            for region, rank in best.items():
                before[region] = min(rank, before.get(region, rank))
            day += timedelta(days=1)
    placements = []
    for region, rank in today.items():
        prior = before.get(region)
        placements.append({
            "region": region,
            "label": "Global" if region == "global" else country_label(region),
            "rank": rank,
            "peak": min(rank, prior) if prior else rank,
            "is_new": prior is None and chart_date != release_day,
            "new_peak": prior is not None and rank < prior,
            "delta": None,
        })
    return placements, image


def song_tweet(platform_label: str, title: str, placements: list[dict], emoji: str, day_text: str, site: str) -> str:
    from collectors.twitter.links import amcharts_url

    countries = [p for p in placements if p["region"] != "global"]
    n1 = sum(1 for p in countries if p["rank"] == 1)
    top10 = sum(1 for p in countries if p["rank"] <= 10)
    parts = []
    if n1:
        parts.append(f"#1 in {_countries(n1)}")
    if top10:
        parts.append(f"top 10 in {top10 if parts else _countries(top10)}")
    parts.append(f"charting in {len(countries) if parts else _countries(len(countries))}")
    body = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    line = f'"{title}" on {platform_label} — {day_text}: {body}'
    glob = next((p for p in placements if p["region"] == "global"), None)
    if glob:
        line += f" (Global peak today: #{glob['rank']})"
    return f"{emoji} | {line}.\n\n{amcharts_url(site)}"


def song_recap_html(track: dict, placements: list[dict], platform: str, chart_date: str,
                    show_new_peaks: bool, chart_label: str = "") -> str:
    """Per-song / album daily recap card = the live posts' rank-groups card
    (post_new_release_progression.rank_groups_card_html), rank = best rank of
    the day, ★ = new peak since release (never on release day)."""
    import post_new_release_progression as prog

    day_text = _day_text(chart_date)
    footer = f'{html.escape(prog.PLATFORMS[prog._brand_platform(platform)]["footer"])} · {day_text}'
    if show_new_peaks:
        footer += ' · <span class="star">★</span> new peak'
    return prog.rank_groups_card_html(
        title=track["title"], subtitle=track.get("subtitle") or "", image_url=track.get("image_url") or "",
        platform=platform, placements=placements, chart_label=chart_label,
        when_main=f"{day_text} · Daily recap", when_sub="best rank of the day in each country (US time)",
        footer_left=footer, show_new_peaks=show_new_peaks,
    )


def _day_text(chart_date: str) -> str:
    day = datetime.strptime(chart_date, "%Y-%m-%d")
    return f"{day.strftime('%b')} {day.day}, {day.year}"


def render_song_cards(chart_date: str, song_keys: set[str], album_name: str, album_display: str, emoji: str,
                      out_dir: Path, start_index: int) -> list[tuple[Path, str]]:
    """Per new song and platform: song_recap_html (countries grouped by their
    best rank of the day)."""
    import post_new_release_progression as prog
    from core.discography import iter_catalog_tracks

    rel = load_release_dates()
    titles = {song_name_key(t.get("title")): str(t.get("title")) for t in iter_catalog_tracks() if t.get("title")}
    day = datetime.strptime(chart_date, "%Y-%m-%d")
    day_text = f"{day.strftime('%b')} {day.day} recap"
    songs = []
    release_days = {k: str(rel.get(k) or "")[:10] for k in new_song_keys(song_keys)}
    for key in release_days:
        per = {}
        for platform in ("apple_music", "itunes"):
            placements, image = song_placements(platform, chart_date, song_matcher(key), release_days[key])
            if placements:
                per[platform] = (placements, image)
        if per:
            am = per.get("apple_music", ([], ""))[0]
            n1 = sum(1 for p in am if p["region"] != "global" and p["rank"] == 1)
            songs.append((-n1, -len(am), key, per))
    posts = []
    idx = start_index
    # Album card first (owner 2026-09-26: "comme pour Patient Zero ... pour l'album sur Apple Music"):
    # Top Albums of every country, all editions, release day = the newest edition's (the Encore's).
    album_release = max(release_days.values(), default="")
    placements, image = song_placements("apple_music_albums", chart_date, album_matcher(album_family(album_name)),
                                        album_release)
    if placements:
        track = {"title": album_display, "image_url": prog._album_cover_url(album_name, image),
                 "subtitle": "Taylor Swift · Album (all editions)"}
        html_doc = song_recap_html(track, placements, "apple_music_albums", chart_date,
                                   show_new_peaks=chart_date > album_release, chart_label="Albums")
        png = out_dir / f"{idx:02d}_album_apple_music.png"
        prog._render_card_png(html_doc, png, scale=2)
        posts.append((png, song_tweet("the Apple Music albums chart", album_display, placements, emoji, day_text,
                                      "applemusic")))
        print(f"[daily_recap] album {album_display} [Apple Music]: {len(placements)} region(s) -> {png}")
        idx += 1
    for _n1, _n, key, per in sorted(songs, key=lambda s: s[:3]):
        title = titles.get(key, key)
        release_day = release_days[key]
        for platform, (placements, image) in per.items():
            track = {"title": title, "image_url": prog._album_cover_url(album_name, image),
                     "subtitle": album_display}
            # ★ new peaks only after release day (there every rank is the peak).
            html_doc = song_recap_html(track, placements, platform, chart_date,
                                       show_new_peaks=chart_date > release_day)
            png = out_dir / f"{idx:02d}_{re.sub(r'[^a-z0-9]+', '_', key).strip('_')}_{platform}.png"
            prog._render_card_png(html_doc, png, scale=2)
            label = prog.PLATFORMS[platform]["label"]
            posts.append((png, song_tweet(label, title, placements, emoji, day_text,
                                          prog.PLATFORMS[platform]["site_source"])))
            print(f"[daily_recap] song {title} [{label}]: {len(placements)} region(s) -> {png}")
            idx += 1
    return posts


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate (never post) the daily Apple Music + iTunes recap thread.")
    parser.add_argument("--date", default=None,
                        help="US day YYYY-MM-DD, 00:00-24:00 New York (default: yesterday US = last finished day)")
    parser.add_argument("--album", default="showgirl", help="Album (name, file slug or unique substring)")
    parser.add_argument("--out-dir", default=None, help="Default: tools/cards/daily_recap/<date>/")
    parser.add_argument("--no-song-cards", action="store_true", help="Skip the per-song cards (4 recap posts only)")
    args = parser.parse_args()

    from collectors.comp.discography import display_title_for_album
    from collectors.twitter.albums import album_emoji

    # Default = the last FINISHED US day (owner 2026-09-26: "le 26 n'est pas encore fini");
    # a still-running day only with an explicit --date.
    chart_date = args.date or (datetime.now(US_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    album_name, song_keys = resolve_album_filter(args.album)
    family = album_family(album_name)
    album_display = display_title_for_album(album_name)
    emoji = album_emoji(album_name)
    out_dir = Path(args.out_dir) if args.out_dir else OUT_ROOT / chart_date
    out_dir.mkdir(parents=True, exist_ok=True)
    # Drop this script's own previous outputs: a stale "06_*.png" from an older
    # numbering next to the new one could get posted by mistake.
    for old in out_dir.iterdir():
        if old.is_file() and (re.match(r"^\d+_.+\.png$", old.name) or old.name == "thread.txt"):
            old.unlink()

    day = datetime.strptime(chart_date, "%Y-%m-%d")
    opener = f'🧵 | "{album_display}" on Apple Music & iTunes — {day.strftime("%B")} {day.day} recap.\n\n{emoji} | Apple Music songs:'
    posts = []
    for i, source in enumerate(SOURCES, 1):
        src = build_source(source, chart_date, family, song_keys, album_display)
        if len(src["cycles"]) < 3:
            print(f"[daily_recap] WARNING {src['id']}: the album only appears in {len(src['cycles'])} "
                  f"cycle(s) today ({', '.join(c[11:16] for c in src['cycles']) or 'none'}) — "
                  "check the source before posting this one")
        png = out_dir / f"{i}_{src['id']}.png"
        render(src, chart_date, album_name, album_display, png)
        tweet = tweet_for(src, album_display, emoji, opener if i == 1 else None)
        posts.append((png, tweet))
        print(f"[daily_recap] {src['id']}: {len(src['items'])} item(s), {len(src['cycles'])} cycle(s) -> {png}")
        if src["id"] == "it_songs":
            it_songs = src

    # Worldwide iTunes chart of the album's songs (owner 2026-09-26), right after the 4 recap posts.
    ww_png = out_dir / f"{len(posts) + 1}_itunes_worldwide.png"
    ww_items = render_worldwide(it_songs, chart_date, album_name, album_display, ww_png)
    posts.append((ww_png, worldwide_tweet(ww_items, album_display, chart_date)))
    print(f"[daily_recap] itunes worldwide: {len(ww_items)} song(s) -> {ww_png}")

    if not args.no_song_cards:
        posts += render_song_cards(chart_date, song_keys, album_name, album_display, emoji, out_dir, len(posts) + 1)

    text = "\n\n".join(
        f"===== POST {i}/{len(posts)} — image: {png.name} ({_weighted(t)}/280) =====\n{t}"
        for i, (png, t) in enumerate(posts, 1)
    )
    (out_dir / "thread.txt").write_text(text + "\n", encoding="utf-8")
    print(f"\n{text}\n\n[daily_recap] thread -> {out_dir / 'thread.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
