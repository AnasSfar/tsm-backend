#!/usr/bin/env python3
"""Event posts built on the yearly Spotify Charts rankings (owner decision 2026-10-17).

Female rankings first. Three post types, all from exact verified daily Top 200 rows:

1. female_debut_record   another female solo artist debuts high (debut-day rank <= --debut-max-rank)
                         and Taylor still holds the #1 female debut of the year -> female debuts
                         top 15 card (the newcomer shown at its real rank, blue rail).
2. female_song_milestone a Taylor song enters the top 50/25/15/10/5/3 or reaches #1 of the
                         Top Female Songs of the year -> top 15 card (threshold <= 15) or her
                         all-entries card, the song on a blue rail.
3. weekly_recap          chart of a Sunday (posted Monday): thread Top Female Songs, Top Female
                         Albums, Top Songs, Top Albums, rank change vs the previous Sunday.

Dry run by default: cards + captions in previews_and_sims/spotify-chart-rankings/events/<date>/.
--post posts on @swiftiescharts, one lock per event (snapshots/spotify_charts_full/ranking_posts/).

    python collectors/spotify/charts/full_charts/post_ranking_events.py                 # latest chart, dry run
    python collectors/spotify/charts/full_charts/post_ranking_events.py --as-of 2026-10-16
    python collectors/spotify/charts/full_charts/post_ranking_events.py --post
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ROOT, raw_dates, read_json, ARTIST_GENDERS_PATH, region_title
import stats
from ranking_metrics import build_rankings, female_status, song_metrics, album_metrics
from generate_ranking_images import artwork, verify_source

sys.path.insert(0, str(ROOT / "collectors"))
from comp.spotify_ranking_page import build_html, render
from comp.discography import _norm, build_track_album_map
from twitter.albums import album_emoji
from twitter.links import chart_song_url, site_url

MILESTONES = (50, 25, 15, 10, 5, 3, 1)
ALL_ARTISTS_URL = site_url("spotifycharts/charts/all-artists")
DRY_DIR = ROOT / "previews_and_sims" / "spotify-chart-rankings" / "events"
POST_DIR = ROOT / "snapshots" / "spotify_charts_full" / "ranking_posts"
POST_PRIORITY = 1  # same lane as the chart tweets (data-rules priority table)


def day_label(day: str, today: date) -> str:
    """Owner date format: 'Monday (Jun 5, 2027)', 'yesterday, ...' only when true."""
    d = date.fromisoformat(day)
    label = f"{d.strftime('%A')} ({d.strftime('%b')} {d.day}, {d.year})"
    return f"yesterday, {label}" if d == today - timedelta(days=1) else f"on {label}"


def short_day(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.strftime('%A')} ({d.strftime('%b')} {d.day}, {d.year})"


class Context:
    def __init__(self, region: str, year: str, as_of: str, previous: str):
        self.region, self.year, self.as_of, self.previous = region, year, as_of, previous
        self.rows = verify_source(region, year, as_of)
        self.pools = {"songs": song_metrics(self.rows, year)}
        self.albums = build_track_album_map(ROOT / "db" / "discography")

    def ranking(self, kind, scope, *, compare_to=None, include=()):
        if kind in {"albums", "female_albums"} and "albums" not in self.pools:
            self.pools["albums"] = album_metrics(self.rows, self.year)
        return build_rankings(self.rows, region=self.region, year=self.year, as_of=self.as_of,
                              compare_to=compare_to or self.previous, kind=kind, scope=scope,
                              top=15, pools=self.pools, include_keys=include)

    def emoji(self, title: str, album: bool = False) -> str:
        found = title if album else self.albums.get(_norm(title))
        return album_emoji(found, fallback="") if found else ""

    def card(self, result, scope, out: Path, spotlight=()) -> Path:
        doc = build_html(result, artwork(result["entries"]), region_name=region_title(self.region, self.year),
                         scope=scope, top=15, spotlight=set(spotlight))
        return render(doc, out)


def prefix(emoji: str) -> str:
    return f"📈 | {emoji} " if emoji else "📈 | "


def female_debut_record(ctx: Context, today: date, max_rank: int) -> list[dict]:
    genders = read_json(ARTIST_GENDERS_PATH, {})
    newcomers, unknown = [], []
    for item in ctx.pools["songs"].values():
        debut = item["debut"]
        if not debut or debut["date"] != ctx.as_of or debut["rank"] > max_rank:
            continue
        if any(a == "06HL4z0CvFAxyc27GXpf02" for a in item["artist_ids"]):
            continue
        status = female_status(item, genders)
        if status is None:
            unknown.append(item)
        elif status:
            newcomers.append(item)
    if unknown:
        # Cannot tell whether it is a female debut: never guess, the event waits for enrich.
        print("[BLOCKED] female_debut_record: unclassified lead artist for " +
              "; ".join(f'{i["name"]} / {i["artists"]}' for i in unknown))
        return []
    if not newcomers:
        return []
    best = max(newcomers, key=lambda i: i["debut"]["streams"])
    result = ctx.ranking("female_debuts", "all", include=[best["key"]])
    if result.get("skipped"):
        return []
    entries = result["entries"]
    record = entries[0]
    if not record["is_taylor"] or record["rank"] != 1:
        print(f'[SKIP] female_debut_record: #1 female debut is {record["name"]} / {record["artists"]}')
        return []
    new = next(e for e in entries if e["key"] == best["key"])
    taylor_top = sum(1 for e in entries if e["is_taylor"] and e["rank"] <= 15)
    text = (f'{prefix(ctx.emoji(record["name"]))}"{record["name"]}" is still the biggest female debut of '
            f'{ctx.year} on the Global Spotify chart, with {record["debut_streams"]:,} streams '
            f'on {short_day(record["debut_date"])}.\n\n'
            f'{new["artists"].split(" | ")[0]}\'s "{new["name"]}" debuted at #{new["debut_rank"]} with '
            f'{new["debut_streams"]:,} streams {day_label(ctx.as_of, today)}, '
            f'#{new["rank"]} among this year\'s female debuts.')
    if taylor_top > 1:
        text += f"\n\nTaylor Swift holds {taylor_top} of the top 15 female debuts of {ctx.year}."
    text += f"\n\n{ALL_ARTISTS_URL}"
    return [{"slug": f"female_debut_record_{best['key']}", "kind": "female_debut_record",
             "posts": [(text, ("female_debuts", "all", result, [best["key"]]))]}]


def female_song_milestones(ctx: Context) -> list[dict]:
    result = ctx.ranking("female", "taylor")
    if result.get("skipped"):
        return []
    events = []
    for entry in result["entries"]:
        before = entry["previous_rank"]
        crossed = [t for t in MILESTONES if entry["rank"] <= t and (before is None or before > t)]
        if not crossed:
            continue
        t = min(crossed)
        name, rank = entry["name"], entry["rank"]
        if t == 1:
            body = (f'"{name}" is now the most streamed song by a female artist of {ctx.year} on the Global '
                    f'Spotify chart, with {entry["streams"]:,} streams earned on the chart.')
        else:
            body = (f'"{name}" enters the top {t} of the Top Female Songs of {ctx.year} on the Global '
                    f'Spotify chart, now #{rank} with {entry["streams"]:,} streams earned on the chart.')
        text = f"{prefix(ctx.emoji(name))}{body}\n\n{chart_song_url(entry['key'], region=ctx.region)}"
        card = (("female", "all", None, [entry["key"]]) if t <= 15
                else ("female", "taylor", result, [entry["key"]]))
        events.append({"slug": f"female_song_top{t}_{entry['key']}", "kind": "female_song_milestone",
                       "posts": [(text, card)]})
    return events


def weekly_recap(ctx: Context, dates: list[str]) -> list[dict]:
    if date.fromisoformat(ctx.as_of).weekday() != 6:
        return []
    week_before = (date.fromisoformat(ctx.as_of) - timedelta(days=7)).isoformat()
    if week_before not in dates:
        print(f"[BLOCKED] weekly_recap: no chart on {week_before} to compare with")
        return []
    order = (("female", "Top Female Songs"), ("female_albums", "Top Female Albums"),
             ("songs", "Top Songs"), ("albums", "Top Albums"))
    lines, posts = [], []
    for kind, label in order:
        result = ctx.ranking(kind, "all", compare_to=week_before)
        if result.get("skipped"):
            continue
        best = next(e for e in result["entries"] if e["is_taylor"])
        move = best["movement"]
        moved = "" if move in (None, 0) else f" (▲{move})" if move > 0 else f" (▼{-move})"
        crown = "👑 " if best["rank"] == 1 else ""
        lines.append(f'{crown}#{best["rank"]}{moved} {label}: "{best["name"]}"')
        posts.append((f"{label} of {ctx.year} on the Global Spotify chart, vs last week:", (kind, "all", result, [])))
    if not posts:
        return []
    head = (f"🧵 | Taylor Swift in the {ctx.year} Global Spotify chart rankings, through "
            f"{short_day(ctx.as_of)}:\n\n" + "\n".join(lines) + f"\n\n{ALL_ARTISTS_URL}")
    posts[0] = (head, posts[0][1])
    return [{"slug": f"weekly_recap_{ctx.as_of}", "kind": "weekly_recap", "posts": posts}]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", default="global")
    p.add_argument("--year", default=str(date.today().year))
    p.add_argument("--as-of", help="chart date (default: latest collected)")
    p.add_argument("--debut-max-rank", type=int, default=10,
                   help="a newcomer must debut at this Global rank or better (default 10)")
    p.add_argument("--only", nargs="+", choices=("female_debut_record", "female_song_milestone", "weekly_recap"))
    p.add_argument("--post", action="store_true", help="really post on @swiftiescharts (default: dry run)")
    args = p.parse_args()
    if args.region != "global":
        p.error("events are defined for the Global chart only")
    dates = raw_dates(args.region, args.year)
    as_of = args.as_of or (dates[-1] if dates else None)
    if not as_of or as_of not in dates:
        print(f"[BLOCKED] no {args.region} chart for {as_of}")
        return 2
    previous = next((d for d in reversed(dates) if d < as_of), None)
    if previous != (date.fromisoformat(as_of) - timedelta(days=1)).isoformat():
        print(f"[BLOCKED] previous chart {previous} is not the day before {as_of}: movements would be wrong")
        return 2
    try:
        ctx = Context(args.region, args.year, as_of, previous)
        today = date.today()
        found = []
        if not args.only or "female_debut_record" in args.only:
            found += female_debut_record(ctx, today, args.debut_max_rank)
        if not args.only or "female_song_milestone" in args.only:
            found += female_song_milestones(ctx)
        if not args.only or "weekly_recap" in args.only:
            found += weekly_recap(ctx, dates)
    except stats.Blocked as exc:
        print(f"[BLOCKED] {exc}")
        return 2

    out_dir = (POST_DIR if args.post else DRY_DIR) / as_of
    out_dir.mkdir(parents=True, exist_ok=True)
    if not found:
        print(f"[NONE] no ranking event on {as_of}")
    summary = []
    for event in found:
        lock = out_dir / f"{event['slug']}.posted.lock"
        if args.post and lock.exists():
            print(f"[SKIP] {event['slug']}: already posted")
            continue
        posts = []
        for i, (text, (kind, scope, result, spot)) in enumerate(event["posts"], 1):
            if result is None:
                result = ctx.ranking(kind, scope)
            png = ctx.card(result, scope, out_dir / f"{event['slug']}-{i}.png", spotlight=spot)
            posts.append((text, png))
        summary.append({"slug": event["slug"], "kind": event["kind"],
                        "posts": [{"text": t, "image": p.name} for t, p in posts]})
        for text, png in posts:
            print(f"\n[{event['kind']}] {png.name}\n{text}")
        if args.post:
            sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
            from core.twitter import post_image_thread, post_with_image
            from twitter.sessions import default_twitter_session
            session = default_twitter_session(ROOT)
            skip = lambda: lock.exists()
            ok = (post_with_image(posts[0][0], posts[0][1], session, skip_if=skip, priority=POST_PRIORITY)
                  if len(posts) == 1 else post_image_thread(posts, session, skip_if=skip, priority=POST_PRIORITY))
            if not ok:
                print(f"[ERROR] {event['slug']}: post failed")
                return 1
            lock.write_text(today.isoformat(), encoding="utf-8")  # right after each post (data-rules)
    (out_dir / "events.json").write_text(json.dumps({"as_of": as_of, "compare_to": previous, "events": summary},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
