#!/usr/bin/env python3
"""Year-to-date rankings from the full Spotify daily top 200 (every artist).

"Streams" here = Spotify Charts streams: the sum of a title's daily streams on
the days it was IN that region's top 200. A day outside the top 200 counts 0
(the chart does not publish it) — so always say "on Spotify Charts".

Rankings (one region, one year, cumulated up to --as-of):
  songs    every track
  female   tracks whose LEAD artist is a female SOLO artist (db/spotify_charts_full/artist_genders.json);
           all-female groups only with --female-groups (TSM convention: a group is not "female")
  albums   sum of the album's charting tracks (Spotify album of each track_id, enrich.py);
           tracks whose Spotify album is a single are left out unless --include-singles
  artists  sum of the tracks where the artist is LEAD (first credited)

Movement "(+1)" = rank as of --as-of vs rank as of the previous chart date
(--compare-to to pick another date). "(NEW)" = not in the comparison ranking.

Exactness guards (blocking, no partial post):
  - every chart date from Jan 1 to --as-of must exist in raw for the region;
  - female/albums: a title with unknown gender/album whose streams would place
    it inside the displayed range blocks the ranking (run enrich.py first).

    python collectors/spotify/charts/full_charts/stats.py --region global
    python collectors/spotify/charts/full_charts/stats.py --region us --kind female --top 50
    python collectors/spotify/charts/full_charts/stats.py --region global --taylor      # post text preview
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    ALBUM_MERGES_PATH, ARTIST_GENDERS_PATH, CSV_ROOT, TAYLOR_ARTIST_ID, TRACK_MERGES_PATH, TRACKS_META_PATH,
    date_range, load_state, raw_dates, read_json, read_rows, region_title, write_json,
)

KINDS = ("songs", "female", "albums", "artists")
TITLES = {
    "songs": "Most streamed songs",
    "female": "Most streamed songs by female artists",
    "albums": "Most streamed albums",
    "artists": "Most streamed artists",
}


class Blocked(Exception):
    pass


def check_coverage(region: str, year: str, as_of: str) -> None:
    have = set(raw_dates(region, year))
    missing = [d for d in date_range(f"{year}-01-01", as_of) if d not in have]
    if missing:
        not_pub = set(load_state()["not_published"].get(region, []))
        detail = ", ".join(f"{d}{' (404 non publie)' if d in not_pub else ''}" for d in missing[:10])
        more = f" (+{len(missing) - 10})" if len(missing) > 10 else ""
        raise Blocked(f"{region} {year}: {len(missing)} date(s) de chart absente(s) jusqu'au {as_of}: {detail}{more}")


def chart_identity_links(rows: list[dict]) -> list[tuple[str, str]]:
    """Track ids that Spotify Charts itself keeps as ONE chart entry.

    When a song's track_id changes (single -> album version, re-upload...), the
    chart carries the entry over: the new id shows the same entry_date,
    days_on_chart = previous id's count + 1 and, the day after, previous_rank =
    the old id's rank (re-entry after days off: previous_rank -1 and same title).
    Also required: same lead artist (unless one id has wiped metadata), the
    two ids never chart on the same day.
    (entry_date + days alone is NOT enough: all tracks of an album debut the
    same day — BTS 2026-03-20.) Anything looser goes in track_merges.json by hand."""
    by_id: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["track_id"]:
            by_id[r["track_id"]].append(r)
    dates_of = {tid: {r["date"] for r in rs} for tid, rs in by_id.items()}
    lead = {tid: rs[0]["artist_ids"].split(" | ")[0] for tid, rs in by_id.items()}
    # Spotify sometimes serves a carried entry with wiped metadata (no title,
    # "Various Artists"): Self Aware - Temper City, global 2026-07-13..18.
    blank = {tid for tid, rs in by_id.items() if not rs[0]["track_name"]}
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["track_id"] and r["entry_date"] and r["days_on_chart"]:
            index[(r["entry_date"], r["days_on_chart"])].append(r)
    links = []
    for tid, rs in by_id.items():
        for r in rs:
            if not (r["entry_date"] and r["days_on_chart"]):
                continue
            prev_days = str(int(r["days_on_chart"]) - 1)
            day_before = (date.fromisoformat(r["date"]) - timedelta(days=1)).isoformat()
            for cand in index.get((r["entry_date"], prev_days), []):
                other = cand["track_id"]
                same_lead = lead[other] == lead[tid] or other in blank or tid in blank
                if other == tid or cand["date"] >= r["date"] or not same_lead:
                    continue
                if dates_of[other] & dates_of[tid]:
                    continue
                if cand["date"] == day_before:
                    # Carried over day to day: previous_rank must be the old id's rank.
                    ok = r["previous_rank"] == str(cand["rank"])
                else:
                    # Re-entry after days off the chart: same title too.
                    same_title = cand["track_name"].casefold().strip() == r["track_name"].casefold().strip()
                    ok = r["previous_rank"] in ("", "-1") and same_title
                if ok:
                    links.append((tid, other))
    return links


def canonical_ids(rows: list[dict]) -> dict[str, str]:
    """{track_id: canonical track_id} — one chart entry (chart_identity_links +
    track_merges.json) folded into its id with the most streams. Shared with
    export.py (All Artists page) so both always merge the same way."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    pairs = chart_identity_links(rows) + list(read_json(TRACK_MERGES_PATH, {}).items())
    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    streams_of: dict[str, int] = defaultdict(int)
    for r in rows:
        streams_of[r["track_id"]] += r["streams"] or 0
    groups: dict[str, list[str]] = defaultdict(list)
    for tid in {r["track_id"] for r in rows}:
        groups[find(tid)].append(tid)
    canon: dict[str, str] = {}
    for members in groups.values():
        best = max(members, key=lambda t: (streams_of[t], t))
        for m in members:
            canon[m] = best
    return canon


def load_tracks(region: str, year: str, *, merge: bool = True):
    """Per-track metadata + daily streams by date. merge=True folds ids that are
    one chart entry (chart_identity_links + track_merges.json) into the id with
    the most streams; merge=False keeps every track_id apart (albums)."""
    rows = read_rows(region, year)
    canon = canonical_ids(rows) if merge else {}
    tracks: dict[str, dict] = {}
    daily: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        tid = canon.get(row["track_id"], row["track_id"])
        if not tid or row["streams"] is None:
            continue
        info = tracks.setdefault(tid, {"track_id": tid, "name": row["track_name"], "artists": row["artists"],
                                       "artist_ids": row["artist_ids"].split(" | ") if row["artist_ids"] else [],
                                       "merged_ids": []})
        if tid == row["track_id"]:  # canonical row wins for the display name
            info.update(name=row["track_name"], artists=row["artists"],
                        artist_ids=row["artist_ids"].split(" | ") if row["artist_ids"] else [])
        elif row["track_id"] not in info["merged_ids"]:
            info["merged_ids"].append(row["track_id"])
        daily[tid][row["date"]] = daily[tid].get(row["date"], 0) + row["streams"]
    return tracks, daily


def totals_until(daily: dict[str, dict[str, int]], until: str) -> dict[str, int]:
    out = {}
    for key, by_day in daily.items():
        total = sum(v for d, v in by_day.items() if d <= until)
        if total:
            out[key] = total
    return out


def rank(totals: dict[str, int]) -> dict[str, int]:
    """Competition ranking (equal totals share a rank)."""
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ranks, prev_total, prev_rank = {}, None, 0
    for i, (key, total) in enumerate(ordered, 1):
        prev_rank = prev_rank if total == prev_total else i
        ranks[key] = prev_rank
        prev_total = total
    return ranks


def group_daily(tracks, daily, key_of) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Fold track daily streams into groups; key_of(track) -> group key, None = unknown."""
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unknown = []
    for tid, by_day in daily.items():
        key = key_of(tracks[tid])
        if key is None:
            unknown.append(tid)
            continue
        if key == "":
            continue  # explicitly excluded (e.g. non-female lead)
        for d, v in by_day.items():
            grouped[key][d] += v
    return grouped, unknown


def build(region: str, year: str, kind: str, as_of: str, compare_to: str | None, top: int,
          include_singles: bool = False, taylor_window: bool = False, female_groups: bool = False) -> dict:
    """taylor_window: the exactness window ends at Taylor's lowest entry within
    the top `top` (what a Taylor-filtered post shows) instead of rank `top`."""
    # Albums keep every track_id apart: a single version's chart days belong to the single.
    tracks, daily = load_tracks(region, year, merge=kind != "albums")
    genders = read_json(ARTIST_GENDERS_PATH, {})
    meta = read_json(TRACKS_META_PATH, {}) if kind == "albums" else {}
    album_merges = read_json(ALBUM_MERGES_PATH, {})
    labels: dict[str, dict] = {}

    if kind == "songs":
        grouped, unknown = daily, []
        for tid, t in tracks.items():
            labels[tid] = {"name": t["name"], "artists": t["artists"], "artist_ids": t["artist_ids"]}
    elif kind == "female":
        def key_of(t):
            lead = t["artist_ids"][0] if t["artist_ids"] else ""
            info = genders.get(lead) or {}
            if not info.get("gender"):
                return None
            # TSM convention (artists chart "female" filter): a group, all-female or
            # not, is not a "female artist" - unless --female-groups.
            female = info["gender"] == "F" and (female_groups or info.get("type") != "GROUP")
            return t["track_id"] if female else ""
        grouped, unknown = group_daily(tracks, daily, key_of)
        for tid in grouped:
            t = tracks[tid]
            labels[tid] = {"name": t["name"], "artists": t["artists"], "artist_ids": t["artist_ids"]}
    elif kind == "albums":
        def key_of(t):
            m = meta.get(t["track_id"])
            if not m:
                return None
            if m.get("album_type") == "single" and not include_singles:
                return ""
            aid = album_merges.get(m["album_id"], m["album_id"])
            if aid not in labels or aid == m["album_id"]:
                labels[aid] = {"name": m["album_name"], "artists": " | ".join(a["name"] for a in m["album_artists"]),
                               "artist_ids": [a["id"] for a in m["album_artists"]], "album_type": m["album_type"]}
            return aid
        grouped, unknown = group_daily(tracks, daily, key_of)
    elif kind == "artists":
        names = {}
        for t in tracks.values():
            for aid, name in zip(t["artist_ids"], t["artists"].split(" | ")):
                names.setdefault(aid, name)
        grouped, unknown = group_daily(tracks, daily, lambda t: t["artist_ids"][0] if t["artist_ids"] else None)
        for aid in grouped:
            labels[aid] = {"name": names.get(aid, aid), "artists": names.get(aid, aid), "artist_ids": [aid]}
    else:
        raise ValueError(kind)

    now = totals_until(grouped, as_of)
    ranks_now = rank(now)
    ranks_prev = rank(totals_until(grouped, compare_to)) if compare_to else {}

    shown = sorted(ranks_now, key=lambda k: (ranks_now[k], k))
    cutoff_total = now[shown[min(top, len(shown)) - 1]] if shown else 0
    if taylor_window:
        ts = [now[k] for k in shown if ranks_now[k] <= top and TAYLOR_ARTIST_ID in labels.get(k, {}).get("artist_ids", [])]
        cutoff_total = min(ts) if ts else cutoff_total
    if unknown:
        unknown_totals = totals_until({t: daily[t] for t in unknown}, as_of)
        # A song is its own unit (female): only unknowns that could reach the window
        # matter. An unresolved track could add streams to ANY album: all must be known.
        floor = 0 if kind == "albums" else cutoff_total
        blocking = sorted((t for t, v in unknown_totals.items() if v >= floor), key=lambda t: -unknown_totals[t])
        if blocking:
            what = "genre de l'artiste principal" if kind == "female" else "album"
            sample = "; ".join(f"{tracks[t]['name']} - {tracks[t]['artists']} ({unknown_totals[t]:,})" for t in blocking[:8])
            raise Blocked(f"{kind}: {len(blocking)} titre(s) sans {what} pouvant changer le classement "
                          f"-> lancer enrich.py. Ex: {sample}")

    entries = []
    for key in shown:
        prev = ranks_prev.get(key)
        entries.append({
            "key": key, "rank": ranks_now[key], "streams": now[key],
            "previous_rank": prev,
            "movement": None if prev is None else prev - ranks_now[key],
            **labels.get(key, {"name": key, "artists": "", "artist_ids": []}),
        })
    return {"region": region, "year": year, "kind": kind, "as_of": as_of, "compare_to": compare_to,
            "count": len(entries), "unknown_below_cutoff": len(unknown), "entries": entries}


def audit_duplicates(region: str, year: str) -> list[list[dict]]:
    """Same title (case-folded) + same lead artist under several track_ids. Only a
    candidate list: a merge goes into track_merges.json after checking it is the
    same recording (not a remix / sped up / live)."""
    tracks, daily = load_tracks(region, year)  # chart-identity + explicit merges already applied
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for tid, t in tracks.items():
        lead = t["artist_ids"][0] if t["artist_ids"] else ""
        groups[(t["name"].casefold().strip(), lead)].append(
            {"track_id": tid, "name": t["name"], "artists": t["artists"], "streams": sum(daily[tid].values()),
             "days": len(daily[tid])})
    out = [sorted(g, key=lambda x: -x["streams"]) for g in groups.values() if len(g) > 1]
    return sorted(out, key=lambda g: -sum(x["streams"] for x in g))


def is_taylor(entry: dict) -> bool:
    return TAYLOR_ARTIST_ID in (entry.get("artist_ids") or [])


def fmt_move(entry: dict) -> str:
    if entry["previous_rank"] is None:
        return "(NEW)"
    m = entry["movement"]
    return "(=)" if m == 0 else f"({m:+d})"


def post_text(result: dict, region_name: str, *, limit: int | None = None) -> str:
    rows = [e for e in result["entries"] if is_taylor(e)]
    if limit:
        rows = [e for e in rows if e["rank"] <= limit]
    where = "" if result["region"] == "global" else f" in {region_name}"
    head = f"{TITLES[result['kind']]} in {result['year']} on Spotify Charts{where}:"
    lines = [f"#{e['rank']} {fmt_move(e)} {e['name']}" for e in rows]
    return head + "\n\n" + "\n".join(lines) if lines else head + "\n\n(aucun titre de Taylor)"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Year-to-date rankings from the full Spotify daily top 200.")
    p.add_argument("--region", default="global", help="Chart region code (default global)")
    p.add_argument("--year", default=str(date.today().year))
    p.add_argument("--kind", nargs="+", choices=KINDS, default=list(KINDS))
    p.add_argument("--as-of", default=None, help="Cumulate up to this chart date (default: latest raw date)")
    p.add_argument("--compare-to", default=None, help="Movement reference date (default: chart date before --as-of)")
    p.add_argument("--top", type=int, default=50, help="Rows printed / exactness window (default 50)")
    p.add_argument("--female-groups", action="store_true", help="female: also count all-female groups (default: solo women only)")
    p.add_argument("--include-singles", action="store_true", help="albums: also count tracks whose Spotify album is a single")
    p.add_argument("--taylor", action="store_true", help="Print the Taylor-filtered post text instead of the table")
    p.add_argument("--taylor-limit", type=int, default=None, help="--taylor: only ranks <= N")
    p.add_argument("--audit-duplicates", action="store_true", help="List same title + lead artist under several track_ids")
    p.add_argument("--write", action="store_true", help="Write db/spotify_charts_full/stats/<region>_<year>_<kind>.json")
    args = p.parse_args()

    if args.audit_duplicates:
        groups = audit_duplicates(args.region, args.year)
        print(f"[AUDIT] {len(groups)} groupe(s) candidat(s) ({args.region} {args.year})")
        for g in groups:
            print("  " + " || ".join(f"{x['track_id']} {x['name']} — {x['artists']} ({x['streams']:,}, {x['days']} j)" for x in g))
        return 0

    dates = raw_dates(args.region, args.year)
    if not dates:
        print(f"[BLOCKED] aucune donnee brute pour {args.region} {args.year}")
        return 2
    as_of = args.as_of or dates[-1]
    compare_to = args.compare_to or next((d for d in reversed(dates) if d < as_of), None)
    name = region_title(args.region, args.year)
    code = 0
    for kind in args.kind:
        top = (args.taylor_limit or 10**9) if args.taylor else args.top
        try:
            check_coverage(args.region, args.year, as_of)
            result = build(args.region, args.year, kind, as_of, compare_to, top, args.include_singles,
                           taylor_window=args.taylor, female_groups=args.female_groups)
        except Blocked as exc:
            print(f"[BLOCKED] {exc}")
            code = 2
            continue
        if args.write:
            write_json(CSV_ROOT / "stats" / f"{args.region}_{args.year}_{kind}.json", result, indent=None)
        print(f"\n=== {TITLES[kind]} — {name} {args.year} (au {as_of}, mouvement vs {compare_to}) ===")
        if args.taylor:
            print(post_text(result, name, limit=args.taylor_limit))
            continue
        for e in result["entries"][: args.top]:
            star = " *" if is_taylor(e) else ""
            label = e["name"] if kind == "artists" else f"{e['name']} — {e['artists']}"
            print(f"#{e['rank']:<4} {fmt_move(e):<7} {e['streams']:>14,}  {label}{star}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
